from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from core.services import contest_catalog


class _MockResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class ContestCatalogTests(SimpleTestCase):
    def tearDown(self):
        contest_catalog._load_ac_resources.cache_clear()
        super().tearDown()

    def test_get_ac_contests_falls_back_to_s3_when_primary_fails(self):
        responses = [
            _MockResponse(403, {"message": "Forbidden"}),
            _MockResponse(
                200,
                [
                    {
                        "id": "abc999",
                        "title": "AtCoder Beginner Contest 999",
                        "start_epoch_second": 1706745600,
                        "duration_second": 7200,
                    }
                ],
            ),
        ]

        with patch("core.services.contest_catalog.requests.get", side_effect=responses) as get_mock:
            contests = contest_catalog.get_ac_contests(2024)

        self.assertEqual(len(contests), 1)
        self.assertEqual(contests[0]["contest_id"], "abc999")
        self.assertEqual(str(contests[0]["start_time"].year), "2024")
        self.assertEqual(get_mock.call_args_list[0].args[0], contest_catalog.AC_CONTESTS_URLS[0])
        self.assertEqual(get_mock.call_args_list[1].args[0], contest_catalog.AC_CONTESTS_URLS[1])

    def test_get_ac_contest_problems_falls_back_for_all_resource_files(self):
        contest_catalog._load_ac_resources.cache_clear()

        responses = [
            _MockResponse(403, {"message": "Forbidden"}),
            _MockResponse(200, [{"contest_id": "abc001", "problem_id": "abc001_a"}]),
            _MockResponse(403, {"message": "Forbidden"}),
            _MockResponse(200, [{"id": "abc001_a", "title": "A - Product"}]),
        ]

        with patch("core.services.contest_catalog.requests.get", side_effect=responses) as get_mock:
            problems = contest_catalog.get_ac_contest_problems("abc001")

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["problem_id"], "abc001_a")
        self.assertEqual(problems[0]["index"], "A")
        self.assertEqual(problems[0]["name"], "A - Product")
        self.assertEqual(get_mock.call_args_list[0].args[0], contest_catalog.AC_CONTEST_PROBLEM_URLS[0])
        self.assertEqual(get_mock.call_args_list[1].args[0], contest_catalog.AC_CONTEST_PROBLEM_URLS[1])
        self.assertEqual(get_mock.call_args_list[2].args[0], contest_catalog.AC_PROBLEMS_URLS[0])
        self.assertEqual(get_mock.call_args_list[3].args[0], contest_catalog.AC_PROBLEMS_URLS[1])
