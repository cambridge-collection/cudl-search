import os
import sys
import unittest
import urllib.parse
from unittest.mock import patch

import requests
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from frontend import main


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"responseHeader": {"params": {}}}


class GetItemsFacetLimitTests(unittest.IsolatedAsyncioTestCase):
    async def solr_query(self, **kwargs):
        calls = []

        async def fake_call_solr(method, url, **call_kwargs):
            calls.append(call_kwargs["params"])
            return FakeResponse()

        with patch.object(main, "call_solr", fake_call_solr):
            await main.get_items(
                q=None, fq=["collection-slug:newton"], sort=None, start=None, rows=8,
                **kwargs
            )
        url = requests.Request("GET", "http://solr/", params=calls[0]).prepare().url
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    async def test_facet_limit_is_passed_to_solr(self):
        for limit in (-1, 1, 200, 201):
            query = await self.solr_query(facet_limit=limit)
            self.assertEqual(query["facet.limit"], [str(limit)])

    async def test_absent_facet_limit_is_not_sent_to_solr(self):
        query = await self.solr_query(facet_limit=None)
        self.assertNotIn("facet.limit", query)
        self.assertEqual(query["fq"], ["collection-slug:newton"])

    async def test_out_of_range_facet_limit_is_rejected(self):
        for limit in (-2, 0, 202):
            with self.assertRaises(HTTPException) as ctx:
                await self.solr_query(facet_limit=limit)
            self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
