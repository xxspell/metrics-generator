import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("ACCESS_TOKEN", "test")
os.environ.setdefault("USER_NAME", "test")

import today  # noqa: E402


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return "<html>502 Bad Gateway</html>"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.page_sizes = []

    def post(self, url, json, headers):
        self.page_sizes.append(json["variables"]["first"])
        status = self.statuses.pop(0)
        payload = {"data": {"repository": {"defaultBranchRef": {"target": {"history": {
            "totalCount": 0, "edges": [], "pageInfo": {"endCursor": None, "hasNextPage": False}}}}}}}
        return FakeResponse(status, payload if status == 200 else None)


class TestRecursiveLocRetries(unittest.TestCase):
    def run_loc(self, session):
        with mock.patch.object(today.asyncio, "sleep", new=mock.AsyncMock()), \
             mock.patch.object(today, "force_close_file", new=mock.AsyncMock()) as closed:
            result = asyncio.run(today.recursive_loc(session, "o", "r", [], []))
        return result, closed

    def test_server_errors_are_retried_with_smaller_pages(self):
        session = FakeSession([502, 502, 200])
        result, closed = self.run_loc(session)
        self.assertEqual(result, (0, 0, 0))
        self.assertEqual(session.page_sizes, [100, 50, 25], "heavy history pages are split on 5xx")
        closed.assert_not_called()

    def test_gives_up_after_repeated_server_errors(self):
        session = FakeSession([502] * 10)
        with self.assertRaises(Exception):
            self.run_loc(session)

    def test_client_errors_fail_immediately(self):
        session = FakeSession([401, 200])
        with self.assertRaises(Exception):
            self.run_loc(session)
        self.assertEqual(len(session.page_sizes), 1)


if __name__ == "__main__":
    unittest.main()
