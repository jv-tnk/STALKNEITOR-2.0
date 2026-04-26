from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from core.services import contest_catalog


class _MockResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class ContestCatalogTests(SimpleTestCase):
    def tearDown(self):
        contest_catalog._load_ac_resources.cache_clear()
        contest_catalog._get_cf_problemset_map.cache_clear()
        super().tearDown()

    def test_get_cf_contest_problems_falls_back_to_problemset_when_standings_requires_auth(self):
        responses = [
            _MockResponse(400, {"status": "FAILED", "comment": "contestId: You have to be authenticated to use this method"}),
            _MockResponse(
                200,
                {
                    "status": "OK",
                    "result": {
                        "problems": [
                            {
                                "contestId": 2222,
                                "index": "B",
                                "name": "Second Problem",
                                "rating": 1200,
                                "tags": ["greedy"],
                            },
                            {
                                "contestId": 2222,
                                "index": "A",
                                "name": "First Problem",
                                "rating": 800,
                                "tags": ["implementation"],
                            },
                            {
                                "contestId": 9999,
                                "index": "A",
                                "name": "Other Contest",
                                "rating": 900,
                                "tags": [],
                            },
                        ]
                    },
                },
            ),
        ]

        with patch("core.services.contest_catalog.tracked_get", side_effect=responses) as get_mock:
            problems = contest_catalog.get_cf_contest_problems("2222")

        self.assertEqual([problem["index"] for problem in problems], ["A", "B"])
        self.assertEqual(problems[0]["name"], "First Problem")
        self.assertEqual(problems[0]["rating"], 800)
        self.assertEqual(problems[1]["tags"], ["greedy"])
        self.assertEqual(get_mock.call_args_list[0].args[0], f"{contest_catalog.CF_API_BASE}/contest.standings")
        self.assertEqual(get_mock.call_args_list[1].args[0], contest_catalog.CF_PROBLEMSET_URL)

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

        with patch("core.services.contest_catalog.tracked_get", side_effect=responses) as get_mock:
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

        with patch("core.services.contest_catalog.tracked_get", side_effect=responses) as get_mock:
            problems = contest_catalog.get_ac_contest_problems("abc001")

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["problem_id"], "abc001_a")
        self.assertEqual(problems[0]["index"], "A")
        self.assertEqual(problems[0]["name"], "A - Product")
        self.assertEqual(get_mock.call_args_list[0].args[0], contest_catalog.AC_CONTEST_PROBLEM_URLS[0])
        self.assertEqual(get_mock.call_args_list[1].args[0], contest_catalog.AC_CONTEST_PROBLEM_URLS[1])
        self.assertEqual(get_mock.call_args_list[2].args[0], contest_catalog.AC_PROBLEMS_URLS[0])
        self.assertEqual(get_mock.call_args_list[3].args[0], contest_catalog.AC_PROBLEMS_URLS[1])

    def test_get_ac_contest_problems_falls_back_to_official_tasks_page(self):
        contest_catalog._load_ac_resources.cache_clear()
        tasks_html = """
        <html>
          <body>
            <table>
              <tbody>
                <tr>
                  <td class="text-center no-break"><a href="/contests/abc999/tasks/abc999_a">A</a></td>
                  <td><a href="/contests/abc999/tasks/abc999_a">Product</a></td>
                </tr>
                <tr>
                  <td class="text-center no-break"><a href="/contests/abc999/tasks/abc999_b">B</a></td>
                  <td><a href="/contests/abc999/tasks/abc999_b">Editorials</a></td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """
        responses = [
            _MockResponse(403, {"message": "Forbidden"}),
            _MockResponse(403, {"message": "Forbidden"}),
            _MockResponse(200, tasks_html),
        ]

        with patch("core.services.contest_catalog.tracked_get", side_effect=responses) as get_mock:
            problems = contest_catalog.get_ac_contest_problems("abc999")

        self.assertEqual(len(problems), 2)
        self.assertEqual(problems[0]["problem_id"], "abc999_a")
        self.assertEqual(problems[0]["index"], "A")
        self.assertEqual(problems[0]["name"], "Product")
        self.assertEqual(problems[1]["problem_id"], "abc999_b")
        self.assertEqual(get_mock.call_args_list[2].args[0], contest_catalog.AC_CONTEST_TASKS_URL.format(contest_id="abc999"))
