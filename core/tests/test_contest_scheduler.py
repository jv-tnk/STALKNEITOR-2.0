from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Contest, PerfilAluno
from core.tasks import contests_problems_scheduler, sync_contest_problems, sync_contests


class ContestSchedulerTests(TestCase):
    @patch("core.tasks.sync_contest_problems.delay")
    @patch("core.tasks.get_cf_contests")
    def test_future_contest_delays_first_problem_sync_until_after_start(self, contests_mock, delay_mock):
        now = timezone.now()
        future_start = now + timedelta(hours=4)
        contests_mock.return_value = [
            {
                "contest_id": "2050",
                "title": "Codeforces Round Future",
                "start_time": future_start,
                "duration_seconds": 7200,
                "phase": "BEFORE",
                "is_gym": False,
            }
        ]

        sync_contests("CF", future_start.year)

        contest = Contest.objects.get(platform="CF", contest_id="2050")
        self.assertEqual(contest.problems_sync_status, "NEW")
        self.assertIsNotNone(contest.problems_next_sync_at)
        self.assertGreaterEqual(contest.problems_next_sync_at, future_start)
        delay_mock.assert_not_called()

    @override_settings(CONTEST_PROBLEMS_START_BUFFER_MINUTES=5)
    @patch("core.tasks.sync_contest_problems.delay")
    @patch("core.tasks.get_cf_contests")
    def test_recently_started_contest_waits_for_start_buffer(self, contests_mock, delay_mock):
        now = timezone.now()
        start_time = now - timedelta(minutes=1)
        contests_mock.return_value = [
            {
                "contest_id": "2053",
                "title": "Codeforces Round Just Started",
                "start_time": start_time,
                "duration_seconds": 7200,
                "phase": "CODING",
                "is_gym": False,
            }
        ]

        result = sync_contests("CF", start_time.year)

        contest = Contest.objects.get(platform="CF", contest_id="2053")
        self.assertEqual(contest.problems_sync_status, "NEW")
        self.assertIsNotNone(contest.problems_next_sync_at)
        self.assertGreater(contest.problems_next_sync_at, timezone.now())
        delay_mock.assert_not_called()
        self.assertEqual(result["problem_sync_enqueued"], 0)

    @patch("core.tasks.sync_contest_problems.delay")
    @patch("core.tasks.get_cf_contests")
    def test_started_contest_enqueues_problem_sync_during_catalog_refresh(self, contests_mock, delay_mock):
        now = timezone.now()
        start_time = now - timedelta(hours=1)
        contests_mock.return_value = [
            {
                "contest_id": "2051",
                "title": "Codeforces Round Started",
                "start_time": start_time,
                "duration_seconds": 7200,
                "phase": "FINISHED",
                "is_gym": False,
            }
        ]

        result = sync_contests("CF", start_time.year)

        contest = Contest.objects.get(platform="CF", contest_id="2051")
        self.assertEqual(contest.problems_sync_status, "NEW")
        self.assertIsNotNone(contest.problems_next_sync_at)
        self.assertLessEqual(contest.problems_next_sync_at, timezone.now())
        delay_mock.assert_called_once_with("CF", "2051")
        self.assertEqual(result["problem_sync_enqueued"], 1)

    @patch("core.tasks.sync_contest_problems.delay")
    @patch("core.tasks.get_cf_contests")
    def test_catalog_requeues_synced_contest_that_has_no_problem_rows(self, contests_mock, delay_mock):
        start_time = timezone.now() - timedelta(days=1)
        Contest.objects.create(
            platform="CF",
            contest_id="2052",
            title="Codeforces Round Empty",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
            problems_next_sync_at=None,
        )
        contests_mock.return_value = [
            {
                "contest_id": "2052",
                "title": "Codeforces Round Empty",
                "start_time": start_time,
                "duration_seconds": 7200,
                "phase": "FINISHED",
                "is_gym": False,
            }
        ]

        result = sync_contests("CF", start_time.year)

        delay_mock.assert_called_once_with("CF", "2052")
        self.assertEqual(result["problem_sync_enqueued"], 1)

    @patch("core.tasks._set_task_health")
    @patch("core.tasks.sync_contest_problems.delay")
    def test_scheduler_requeues_due_failed_contests(self, delay_mock, _health_mock):
        start_time = timezone.now() - timedelta(days=10)
        Contest.objects.create(
            platform="AC",
            contest_id="abc999",
            title="AtCoder Beginner Contest 999",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="FAILED",
            problems_next_sync_at=timezone.now() - timedelta(minutes=1),
        )

        result = contests_problems_scheduler(
            max_cf_per_run=0,
            max_ac_per_run=5,
            recent_days=2,
        )

        delay_mock.assert_called_once_with("AC", "abc999")
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(result["ac"], 1)

    @patch("core.tasks._set_task_health")
    @patch("core.tasks.sync_contest_problems.delay")
    def test_scheduler_prioritizes_started_contests_with_no_problems_even_if_marked_synced(self, delay_mock, _health_mock):
        start_time = timezone.now() - timedelta(hours=2)
        Contest.objects.create(
            platform="CF",
            contest_id="1999",
            title="Codeforces Round 1999",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
            problems_next_sync_at=None,
        )

        result = contests_problems_scheduler(
            max_cf_per_run=1,
            max_ac_per_run=0,
            recent_days=0,
        )

        delay_mock.assert_called_once_with("CF", "1999")
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(result["cf"], 1)

    @patch("core.tasks._release_lock")
    @patch("core.tasks._acquire_lock", return_value=True)
    @patch("core.tasks.get_cf_contest_problems", return_value=[])
    def test_problem_sync_releases_lock_after_no_problem_result(self, _problems_mock, _lock_mock, release_mock):
        start_time = timezone.now() - timedelta(hours=1)
        Contest.objects.create(
            platform="CF",
            contest_id="2060",
            title="Codeforces Round Empty",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="NEW",
            problems_next_sync_at=None,
        )

        result = sync_contest_problems("CF", "2060")

        self.assertEqual(result["status"], "no_problems")
        release_mock.assert_called_once_with("sync_contest_problems:CF:2060")


class ContestAutoSyncViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewer", password="StrongPass123!")
        PerfilAluno.objects.create(user=self.user)

    @patch("core.tasks.sync_contest_problems.delay")
    def test_contest_detail_auto_enqueues_sync_for_due_empty_contest(self, delay_mock):
        start_time = timezone.now() - timedelta(hours=1)
        contest = Contest.objects.create(
            platform="CF",
            contest_id="1777",
            title="Codeforces Round 1777",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="FAILED",
            problems_next_sync_at=timezone.now() - timedelta(minutes=1),
        )

        self.client.login(username="viewer", password="StrongPass123!")
        response = self.client.get(
            reverse("contest_detail", kwargs={"platform": "cf", "contest_id": contest.contest_id})
        )

        self.assertEqual(response.status_code, 200)
        delay_mock.assert_called_once_with("CF", "1777")
