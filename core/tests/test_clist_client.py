from unittest.mock import patch

from django.test import SimpleTestCase

from core.services.clist_client import ClistClient


class ClistClientTests(SimpleTestCase):
    def test_cf_tries_problemset_variant_before_not_found(self):
        with patch.object(
            ClistClient,
            "_request",
            side_effect=[
                {"status": "OK", "payload": {"objects": []}},
                {
                    "status": "OK",
                    "payload": {
                        "objects": [
                            {
                                "id": 123,
                                "rating": 1450,
                                "url": "https://codeforces.com/problemset/problem/2197/C",
                            }
                        ]
                    },
                },
            ],
        ) as request_mock:
            result = ClistClient.fetch_problem_rating(
                "CF",
                "https://codeforces.com/contest/2197/problem/C",
                problem_name="Game with a Fraction",
            )

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["rating"], 1450)
        self.assertEqual(request_mock.call_count, 2)

    def test_cf_does_not_use_ambiguous_name_fallback(self):
        def _empty_ok(_params):
            return {"status": "OK", "payload": {"objects": []}}

        with patch.object(ClistClient, "_request", side_effect=_empty_ok) as request_mock:
            result = ClistClient.fetch_problem_rating(
                "CF",
                "https://codeforces.com/contest/2197/problem/C",
                problem_name="Game with a Fraction",
            )

        self.assertEqual(result, {"status": "NOT_FOUND"})
        for call in request_mock.call_args_list:
            params = call.args[0]
            self.assertNotIn("name", params)
            self.assertIn("url", params)

    def test_ac_can_still_use_name_fallback(self):
        with patch.object(
            ClistClient,
            "_request",
            side_effect=[
                {"status": "OK", "payload": {"objects": []}},
                {
                    "status": "OK",
                    "payload": {
                        "objects": [
                            {
                                "id": 991,
                                "rating": 1800,
                                "url": "https://atcoder.jp/contests/abc300/tasks/abc300_e",
                            }
                        ]
                    },
                },
            ],
        ) as request_mock:
            result = ClistClient.fetch_problem_rating(
                "AC",
                "https://atcoder.jp/contests/abc300/tasks/abc300_e",
                problem_name="Dice Product 3",
            )

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["rating"], 1800)
        self.assertEqual(request_mock.call_count, 2)
        self.assertIn("name", request_mock.call_args_list[1].args[0])
