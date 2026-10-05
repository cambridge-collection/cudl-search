import os
import sys
import time
import unittest
from unittest.mock import patch

import anyio
import requests
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from frontend import main


class SolrCallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for name in ("SOLR_READ_LIMITER", "SOLR_WRITE_LIMITER"):
            limiter_patch = patch.object(main, name, anyio.CapacityLimiter(1))
            limiter_patch.start()
            self.addCleanup(limiter_patch.stop)

    async def test_call_solr_runs_off_event_loop_and_limits_concurrency(self):
        active_calls = 0
        max_active_calls = 0
        event_loop_progressed = False

        def slow_request(url, timeout):
            nonlocal active_calls, max_active_calls
            self.assertEqual(timeout, main.SOLR_WRITE_TIMEOUT)
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            time.sleep(0.05)
            active_calls -= 1
            return url

        async def mark_event_loop_progress():
            nonlocal event_loop_progressed
            await anyio.sleep(0.01)
            event_loop_progressed = True

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(main.call_solr, slow_request, "first")
            task_group.start_soon(main.call_solr, slow_request, "second")
            task_group.start_soon(mark_event_loop_progress)

        self.assertTrue(event_loop_progressed)
        self.assertEqual(max_active_calls, 1)
        self.assertEqual(main.SOLR_WRITE_LIMITER.borrowed_tokens, 0)

    async def test_call_solr_returns_503_when_admission_times_out(self):
        holding_limiter = anyio.Event()
        release_limiter = anyio.Event()

        async def hold_limiter():
            async with main.SOLR_WRITE_LIMITER:
                holding_limiter.set()
                await release_limiter.wait()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(hold_limiter)
            await holding_limiter.wait()
            with patch.object(main, "SOLR_ADMISSION_TIMEOUT", 0.01), patch.object(
                main.logger, "warning"
            ) as warning:
                with self.assertRaises(HTTPException) as raised:
                    await main.call_solr(lambda url, timeout: None, "unused")
            release_limiter.set()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail, "Solr request capacity exhausted"
        )
        self.assertEqual(raised.exception.headers, {"Retry-After": "0.01"})
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1:3], ("write", "unused"))
        self.assertEqual(main.SOLR_WRITE_LIMITER.borrowed_tokens, 0)

    async def test_call_solr_releases_limiter_when_request_fails(self):
        def failing_request(url, timeout):
            raise requests.ConnectionError("failed")

        with self.assertRaises(requests.ConnectionError):
            await main.call_solr(failing_request, "unused")

        self.assertEqual(main.SOLR_WRITE_LIMITER.borrowed_tokens, 0)

    async def test_call_solr_admits_write_while_read_limiter_is_held(self):
        async with main.SOLR_READ_LIMITER:
            with patch.object(main, "SOLR_ADMISSION_TIMEOUT", 0.01):
                result = await main.call_solr(lambda url, timeout: url, "write")

        self.assertEqual(result, "write")

    async def test_call_solr_uses_read_timeout_for_get(self):
        def fake_get(url, timeout):
            return timeout

        with patch.object(main.requests, "get", fake_get):
            result = await main.call_solr(main.requests.get, "read")

        self.assertEqual(result, main.SOLR_READ_TIMEOUT)

    async def test_call_solr_uses_read_limiter_for_get(self):
        def fake_get(url, timeout):
            return url

        holding_limiter = anyio.Event()
        release_limiter = anyio.Event()

        async def hold_limiter():
            async with main.SOLR_READ_LIMITER:
                holding_limiter.set()
                await release_limiter.wait()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(hold_limiter)
            await holding_limiter.wait()
            with patch.object(main.requests, "get", fake_get), patch.object(
                main, "SOLR_ADMISSION_TIMEOUT", 0.01
            ), patch.object(main.logger, "warning") as warning:
                with self.assertRaises(HTTPException) as raised:
                    await main.call_solr(main.requests.get, "read")
            release_limiter.set()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(warning.call_args.args[1:3], ("read", "read"))
        self.assertEqual(main.SOLR_WRITE_LIMITER.borrowed_tokens, 0)


if __name__ == "__main__":
    unittest.main()
