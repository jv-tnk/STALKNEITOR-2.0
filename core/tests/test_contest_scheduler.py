from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Contest, ContestProblem, PerfilAluno, ProblemRatingCache, RatingFetchJob, ScoreEvent, Submissao
from core.tasks import (
    _hydrate_provisional_ratings,
    contests_problems_scheduler,
    process_rating_fetch_jobs,
    ratings_backfill_scheduler,
    sync_contest_problems,
    sync_contests,
)


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

    @patch("core.tasks._release_lock")
    @patch("core.tasks._acquire_lock", return_value=True)
    @patch("core.tasks.get_cf_contest_problems")
    def test_problem_sync_promotes_official_cf_rating_over_old_cache_status(self, problems_mock, _lock_mock, release_mock):
        start_time = timezone.now() - timedelta(days=1)
        contest = Contest.objects.create(
            platform="CF",
            contest_id="2063",
            title="Codeforces Round Cache Heal",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="NEW",
        )
        problem_url = "https://codeforces.com/contest/2063/problem/A"
        old_fetch_time = timezone.now() - timedelta(days=5)
        ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=problem_url,
            status="NOT_FOUND",
            rating_fetched_at=old_fetch_time,
        )
        problems_mock.return_value = [
            {
                "index": "A",
                "name": "Official Rating Heal",
                "rating": 1700,
                "tags": ["dp"],
            }
        ]

        result = sync_contest_problems("CF", "2063")

        problem = ContestProblem.objects.get(contest=contest, problem_url=problem_url)
        cache = ProblemRatingCache.objects.get(problem_url=problem_url)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(problem.rating_status, "OK")
        self.assertEqual(problem.cf_rating, 1700)
        self.assertEqual(cache.status, "OK")
        self.assertEqual(cache.effective_rating, 1700)
        self.assertEqual(cache.rating_source, "cf")
        self.assertGreater(cache.rating_fetched_at, old_fetch_time)
        release_mock.assert_called_once_with("sync_contest_problems:CF:2063")

    @patch("core.tasks.update_scores_for_problem_url")
    @patch("core.tasks._get_cf_problemset_map")
    def test_ratings_backfill_hydrates_cf_rating_from_official_problemset(self, problemset_mock, _scores_mock):
        start_time = timezone.now() - timedelta(days=1)
        contest = Contest.objects.create(
            platform="CF",
            contest_id="2061",
            title="Codeforces Round Rated",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
        )
        problem = ContestProblem.objects.create(
            contest=contest,
            platform="CF",
            order=1,
            index_label="A",
            name="Official Rating Problem",
            problem_url="https://codeforces.com/contest/2061/problem/A",
            rating_status="MISSING",
        )
        problemset_mock.return_value = {
            "2061:A": {"contest_id": "2061", "index": "A", "name": "Official Rating Problem", "rating": 1300, "tags": []}
        }

        result = ratings_backfill_scheduler(limit=5, cooldown_minutes=0)

        problem.refresh_from_db()
        cache = ProblemRatingCache.objects.get(problem_url=problem.problem_url)
        self.assertEqual(result["cf_ratings_hydrated"], 1)
        self.assertEqual(problem.cf_rating, 1300)
        self.assertEqual(problem.rating_status, "OK")
        self.assertEqual(cache.effective_rating, 1300)
        self.assertEqual(cache.rating_source, "cf")
        self.assertFalse(RatingFetchJob.objects.filter(problem_url=problem.problem_url).exists())

    @patch("core.tasks._get_cf_problemset_map", return_value={})
    def test_ratings_backfill_does_not_stale_official_cf_rating(self, _problemset_mock):
        start_time = timezone.now() - timedelta(days=10)
        contest = Contest.objects.create(
            platform="CF",
            contest_id="2064",
            title="Codeforces Round Official Stale",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
        )
        problem = ContestProblem.objects.create(
            contest=contest,
            platform="CF",
            order=1,
            index_label="A",
            name="Official Rating Stays",
            problem_url="https://codeforces.com/contest/2064/problem/A",
            cf_rating=1400,
            rating_status="OK",
            rating_last_ok_at=timezone.now() - timedelta(days=5),
        )
        ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=problem.problem_url,
            cf_rating=1400,
            effective_rating=1400,
            rating_source="cf",
            status="OK",
            rating_fetched_at=timezone.now() - timedelta(days=5),
        )

        ratings_backfill_scheduler(limit=5, cooldown_minutes=0)

        problem.refresh_from_db()
        self.assertEqual(problem.rating_status, "OK")
        self.assertFalse(RatingFetchJob.objects.filter(problem_url=problem.problem_url).exists())

    @override_settings(ENABLE_PROVISIONAL_RATINGS=True)
    def test_provisional_backfill_estimates_atcoder_and_marks_score_event(self):
        start_time = timezone.now() - timedelta(days=1)
        contest = Contest.objects.create(
            platform="AC",
            contest_id="abc300",
            title="AtCoder Beginner Contest 300",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
        )
        problem_url = "https://atcoder.jp/contests/abc300/tasks/abc300_d"
        ContestProblem.objects.create(
            contest=contest,
            platform="AC",
            order=4,
            index_label="D",
            name="AABCC",
            problem_url=problem_url,
            rating_status="MISSING",
        )
        user = User.objects.create_user(username="partial_ac", password="StrongPass123!")
        student = PerfilAluno.objects.create(user=user)
        submission = Submissao.objects.create(
            aluno=student,
            plataforma="AC",
            contest_id="abc300",
            problem_index="D",
            verdict="AC",
            submission_time=timezone.now(),
            problem_name="abc300_d",
            external_id="partial-ac-1",
        )
        event = ScoreEvent.objects.create(
            aluno=student,
            platform="AC",
            submission=submission,
            problem_url=problem_url,
            solved_at=submission.submission_time,
        )

        result = _hydrate_provisional_ratings(limit=10)

        cache = ProblemRatingCache.objects.get(problem_url=problem_url)
        event.refresh_from_db()
        self.assertEqual(result["hydrated"], 1)
        self.assertEqual(cache.rating_source, "provisional")
        self.assertEqual(cache.effective_rating, cache.provisional_rating)
        self.assertIsNotNone(cache.provisional_confidence)
        self.assertEqual(event.raw_rating, cache.provisional_rating)
        self.assertEqual(event.rating_source, "provisional")
        self.assertTrue(event.rating_is_provisional)
        self.assertEqual(event.points_cf_raw, 0)
        self.assertGreater(event.points_ac_raw, 0)

    @override_settings(ENABLE_PROVISIONAL_RATINGS=True)
    @patch("core.tasks.ClistClient.fetch_problem_rating")
    def test_real_clist_rating_replaces_provisional_score_event(self, clist_mock):
        start_time = timezone.now() - timedelta(days=1)
        contest = Contest.objects.create(
            platform="AC",
            contest_id="abc301",
            title="AtCoder Beginner Contest 301",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
        )
        problem_url = "https://atcoder.jp/contests/abc301/tasks/abc301_e"
        ContestProblem.objects.create(
            contest=contest,
            platform="AC",
            order=5,
            index_label="E",
            name="Pac-Takahashi",
            problem_url=problem_url,
            rating_status="QUEUED",
        )
        user = User.objects.create_user(username="partial_replace_ac", password="StrongPass123!")
        student = PerfilAluno.objects.create(user=user)
        submission = Submissao.objects.create(
            aluno=student,
            plataforma="AC",
            contest_id="abc301",
            problem_index="E",
            verdict="AC",
            submission_time=timezone.now(),
            problem_name="abc301_e",
            external_id="partial-ac-2",
        )
        event = ScoreEvent.objects.create(
            aluno=student,
            platform="AC",
            submission=submission,
            problem_url=problem_url,
            solved_at=submission.submission_time,
        )
        _hydrate_provisional_ratings(limit=10)
        event.refresh_from_db()
        self.assertTrue(event.rating_is_provisional)
        clist_mock.return_value = {"status": "OK", "rating": 1600, "problem_id": "abc301_e"}
        job = RatingFetchJob.objects.create(
            platform="AC",
            problem_url=problem_url,
            status="QUEUED",
        )

        result = process_rating_fetch_jobs(limit=1)

        cache = ProblemRatingCache.objects.get(problem_url=problem_url)
        event.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(job.status, "DONE")
        self.assertEqual(cache.clist_rating, 1600)
        self.assertEqual(cache.effective_rating, 1600)
        self.assertEqual(cache.rating_source, "clist")
        self.assertEqual(event.raw_rating, 1600)
        self.assertEqual(event.rating_source, "clist")
        self.assertFalse(event.rating_is_provisional)

    @patch("core.tasks.update_scores_for_problem_url")
    @patch("core.tasks.ClistClient.fetch_problem_rating")
    def test_rating_fetch_job_uses_existing_cf_rating_without_clist(self, clist_mock, _scores_mock):
        start_time = timezone.now() - timedelta(days=1)
        contest = Contest.objects.create(
            platform="CF",
            contest_id="2062",
            title="Codeforces Round Cached",
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            problems_sync_status="SYNCED",
        )
        problem = ContestProblem.objects.create(
            contest=contest,
            platform="CF",
            order=1,
            index_label="A",
            name="Cached Official Rating",
            problem_url="https://codeforces.com/contest/2062/problem/A",
            cf_rating=1500,
            rating_status="QUEUED",
        )
        job = RatingFetchJob.objects.create(
            platform="CF",
            problem_url=problem.problem_url,
            status="QUEUED",
        )

        result = process_rating_fetch_jobs(limit=1)

        problem.refresh_from_db()
        job.refresh_from_db()
        cache = ProblemRatingCache.objects.get(problem_url=problem.problem_url)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(job.status, "DONE")
        self.assertEqual(problem.rating_status, "OK")
        self.assertEqual(cache.effective_rating, 1500)
        clist_mock.assert_not_called()


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
