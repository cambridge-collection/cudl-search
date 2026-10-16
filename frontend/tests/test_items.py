import os
import sys
import unittest
import urllib.parse
from unittest.mock import patch

import requests
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from frontend import main


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"responseHeader": {"params": {}}}


def request_with_query(query_string=""):
    return Request({"type": "http", "query_string": query_string.encode()})


class GetItemsTestCase(unittest.IsolatedAsyncioTestCase):
    async def solr_query(self, query_string="", rows=8, facet=None, facet_limit=None,
                         fq=("collection-slug:newton",)):
        calls = []

        async def fake_call_solr(method, url, **call_kwargs):
            calls.append(call_kwargs["params"])
            return FakeResponse()

        with patch.object(main, "call_solr", fake_call_solr):
            await main.get_items(
                request=request_with_query(query_string),
                q=None, fq=list(fq), sort=None, start=None,
                rows=rows, facet=facet, facet_limit=facet_limit,
            )
        url = requests.Request("GET", "http://solr/", params=calls[0]).prepare().url
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


class GetItemsFacetLimitTests(GetItemsTestCase):
    async def test_facet_limit_is_passed_to_solr(self):
        for limit in (-1, 1, 201, 5000):
            query = await self.solr_query(facet_limit=limit)
            self.assertEqual(query["facet.limit"], [str(limit)])

    async def test_absent_facet_limit_is_not_sent_to_solr(self):
        query = await self.solr_query(facet_limit=None)
        self.assertNotIn("facet.limit", query)
        self.assertEqual(query["fq"], ["collection-slug:newton"])

    async def test_out_of_range_facet_limit_is_rejected(self):
        for limit in (-2, 0):
            with self.assertRaises(HTTPException) as ctx:
                await self.solr_query(facet_limit=limit)
            self.assertEqual(ctx.exception.status_code, 422)

    async def test_zero_rows_is_passed_to_solr(self):
        query = await self.solr_query(rows=0)
        self.assertEqual(query["rows"], ["0"])

    async def test_unsupported_rows_still_become_20(self):
        query = await self.solr_query(rows=5)
        self.assertEqual(query["rows"], ["20"])

    async def test_facet_is_passed_to_solr_only_when_sent(self):
        query = await self.solr_query(facet=False)
        self.assertEqual(query["facet"], ["False"])
        query = await self.solr_query(facet=None)
        self.assertNotIn("facet", query)

    async def test_field_facet_limits_are_passed_to_solr(self):
        query = await self.solr_query(
            "f.facet-subjects.facet.limit=-1&f.facet-origin-place.facet.limit=201"
        )
        self.assertEqual(query["f.facet-subjects.facet.limit"], ["-1"])
        self.assertEqual(query["f.facet-origin-place.facet.limit"], ["201"])
        query = await self.solr_query("f.facet-collection-1.facet.limit=5")
        self.assertEqual(query["f.facet-collection-1.facet.limit"], ["5"])

    async def test_other_field_params_are_not_sent_to_solr(self):
        query = await self.solr_query("f.facet-subjects.facet.sort=index")
        self.assertNotIn("f.facet-subjects.facet.sort", query)

    async def test_bad_field_facet_limit_names_are_rejected(self):
        for name in ("f.bad.facet.limit", "f.facet-subjects;x.facet.limit",
                     "f.subjects.facet.limit"):
            with self.assertRaises(HTTPException) as ctx:
                await self.solr_query(name + "=-1")
            self.assertEqual(ctx.exception.status_code, 422)

    async def test_bad_field_facet_limit_values_are_rejected(self):
        for value in ("-2", "0", "x", ""):
            with self.assertRaises(HTTPException) as ctx:
                await self.solr_query("f.facet-subjects.facet.limit=" + value)
            self.assertEqual(ctx.exception.status_code, 422)


class GetItemsCollectionFacetTests(GetItemsTestCase):
    async def test_base_facet_fields_are_sent_without_a_collection_filter(self):
        query = await self.solr_query()
        self.assertEqual(query["facet.field"], main.ITEM_FACET_FIELDS)
        self.assertIn("facet-collection-0", query["facet.field"])
        self.assertIn("facet-collection-prefixes-1", query["facet.field"])
        self.assertNotIn("facet-collection-1", query["facet.field"])
        self.assertEqual(query["f.facet-collection-0.facet.limit"], ["-1"])
        self.assertEqual(query["f.facet-collection-prefixes-1.facet.limit"], ["-1"])
        self.assertNotIn("f.facet-collection.facet.limit", query)

    async def test_collection_level_filter_adds_the_next_level(self):
        query = await self.solr_query(
            fq=['facet-collection-0:"Royal Greenwich Observatory Archives"']
        )
        self.assertEqual(
            query["facet.field"],
            main.ITEM_FACET_FIELDS + ["facet-collection-1", "facet-collection-prefixes-2"],
        )
        self.assertEqual(query["f.facet-collection-1.facet.limit"], ["-1"])
        self.assertEqual(query["f.facet-collection-prefixes-2.facet.limit"], ["-1"])

    async def test_each_collection_level_filter_adds_its_next_level_once(self):
        query = await self.solr_query(fq=[
            'facet-collection-0:"Royal Greenwich Observatory Archives"',
            'facet-collection-0:"Treasures of the Library"',
            'facet-collection-1:"Royal Greenwich Observatory Archives::Papers of Nevil Maskelyne"',
        ])
        self.assertEqual(
            query["facet.field"],
            main.ITEM_FACET_FIELDS + [
                "facet-collection-1", "facet-collection-prefixes-2",
                "facet-collection-2", "facet-collection-prefixes-3",
            ],
        )

    async def test_global_facet_limit_does_not_truncate_collection_fields(self):
        query = await self.solr_query(
            facet_limit=10, fq=['facet-collection-0:"Thomas Hobson"']
        )
        self.assertEqual(query["facet.limit"], ["10"])
        for field in ("facet-collection-0", "facet-collection-prefixes-1",
                      "facet-collection-1", "facet-collection-prefixes-2"):
            self.assertEqual(query["f.%s.facet.limit" % field], ["-1"])

    async def test_explicit_field_limit_overrides_collection_default(self):
        query = await self.solr_query("f.facet-collection-0.facet.limit=5")
        self.assertEqual(query["f.facet-collection-0.facet.limit"], ["5"])


if __name__ == "__main__":
    unittest.main()
