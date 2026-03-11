from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Contest
from core.tasks import contests_problems_scheduler


class ContestSchedulerTests(TestCase):
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
