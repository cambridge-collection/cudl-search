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


class GetItemsFacetLimitTests(unittest.IsolatedAsyncioTestCase):
    async def solr_query(self, query_string="", rows=8, facet=None, facet_limit=None):
        calls = []

        async def fake_call_solr(method, url, **call_kwargs):
            calls.append(call_kwargs["params"])
            return FakeResponse()

        with patch.object(main, "call_solr", fake_call_solr):
            await main.get_items(
                request=request_with_query(query_string),
                q=None, fq=["collection-slug:newton"], sort=None, start=None,
                rows=rows, facet=facet, facet_limit=facet_limit,
            )
        url = requests.Request("GET", "http://solr/", params=calls[0]).prepare().url
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

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


if __name__ == "__main__":
    unittest.main()
