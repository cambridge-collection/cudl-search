import os
import sys
import unittest
import urllib.parse
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from frontend import main


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"responseHeader": {"params": {}}}


class GetCollectionsTopLevelTests(unittest.IsolatedAsyncioTestCase):
    async def solr_query(self, q=None, fq=None, sort=None, start=None, rows=None,
                         topLevel=False):
        calls = []

        async def fake_call_solr(method, url, **call_kwargs):
            calls.append(call_kwargs["params"])
            return FakeResponse()

        with patch.object(main, "call_solr", fake_call_solr):
            await main.get_collections(
                q=q, fq=fq, spellcheck=None, facet=None, omitHeader=None,
                echoParams=None, hl=None, sort=sort, start=start, rows=rows,
                topLevel=topLevel,
            )
        url = requests.Request("GET", "http://solr/", params=calls[0]).prepare().url
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    async def test_top_level_supplies_defaults(self):
        query = await self.solr_query(topLevel=True)
        self.assertEqual(query["q"], ["*:*"])
        self.assertEqual(query["fq"], [main.TOP_LEVEL_COLLECTIONS_FQ])
        self.assertEqual(query["fl"], [main.TOP_LEVEL_COLLECTIONS_FL])
        self.assertEqual(query["sort"], ["name.full_s asc"])
        self.assertEqual(query["rows"], ["1000"])
        self.assertNotIn("start", query)

    async def test_top_level_keeps_caller_values(self):
        query = await self.solr_query(
            q=["name.full:newton"], fq=["isReleased:true"], sort="id desc",
            start="8", rows=8, topLevel=True,
        )
        self.assertEqual(query["q"], ["name.full:newton"])
        self.assertEqual(query["fq"], ["isReleased:true", main.TOP_LEVEL_COLLECTIONS_FQ])
        self.assertEqual(query["sort"], ["id desc"])
        self.assertEqual(query["start"], ["8"])
        self.assertEqual(query["rows"], ["8"])

    async def test_top_level_unsupported_rows_still_become_20(self):
        query = await self.solr_query(rows=1000, topLevel=True)
        self.assertEqual(query["rows"], ["20"])

    async def test_without_top_level_nothing_is_added(self):
        query = await self.solr_query()
        for param in ("q", "fq", "fl", "sort"):
            self.assertNotIn(param, query)
        self.assertEqual(query["rows"], ["20"])
