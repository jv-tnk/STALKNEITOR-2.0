from datetime import datetime, timezone
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from core.services.api_client import AtCoderClient


class _MockResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class AtCoderClientTests(SimpleTestCase):
    def test_get_user_info_uses_official_history_directly(self):
        payload = [
            {"NewRating": 800},
            {"NewRating": 1200},
            {"NewRating": 1000},
        ]

        with patch(
            "core.services.api_client.tracked_get",
            return_value=_MockResponse(200, payload),
        ) as get_mock:
            info, error = AtCoderClient.get_user_info_detailed("tourist")

        self.assertIsNone(error)
        self.assertEqual(info, {"rating": 1000, "max_rating": 1200})
        get_mock.assert_called_once_with(
            "https://atcoder.jp/users/tourist/history/json",
            timeout=10,
        )

    def test_get_user_info_reports_empty_official_history(self):
        with patch(
            "core.services.api_client.tracked_get",
            return_value=_MockResponse(200, []),
        ):
            info, error = AtCoderClient.get_user_info_detailed("new_user")

        self.assertIsNone(info)
        self.assertEqual(error, "Usuário sem histórico de rating.")

    def test_get_submissions_returns_empty_when_kenkoooo_is_forbidden(self):
        with patch(
            "core.services.api_client.tracked_get",
            return_value=_MockResponse(403, {"message": "Forbidden"}),
        ) as get_mock:
            submissions = AtCoderClient.get_submissions("tourist")

        self.assertEqual(submissions, [])
        self.assertEqual(
            get_mock.call_args.args[0],
            "https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions",
        )

    def test_get_submissions_parses_kenkoooo_payload(self):
        payload = [
            {
                "id": 12345,
                "epoch_second": 1700000000,
                "problem_id": "abc300_d",
                "contest_id": "abc300",
                "result": "AC",
            }
        ]

        with patch(
            "core.services.api_client.tracked_get",
            return_value=_MockResponse(200, payload),
        ):
            submissions = AtCoderClient.get_submissions(
                "tourist",
                since=datetime.fromtimestamp(1699999990, tz=timezone.utc),
            )

        self.assertEqual(
            submissions,
            [
                {
                    "platform": "AC",
                    "contest_id": "abc300",
                    "problem_index": "D",
                    "problem_id": "abc300_d",
                    "verdict": "AC",
                    "submission_time": datetime.fromtimestamp(1700000000, tz=timezone.utc),
                    "external_id": "12345",
                }
            ],
        )
