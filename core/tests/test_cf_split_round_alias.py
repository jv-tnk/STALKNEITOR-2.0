from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Contest, ContestProblem, ProblemRatingCache
from core.tasks import _apply_clist_result, _heal_conflicting_cf_cache_entries


class CFSplitRoundAliasTests(TestCase):
    def setUp(self):
        start = timezone.now() - timedelta(days=1)
        self.div1 = Contest.objects.create(
            platform="CF",
            contest_id="2196",
            title="Codeforces Round 1079 (Div. 1)",
            start_time=start,
            duration_seconds=7200,
            year=start.year,
            division="Div1",
        )
        self.div2 = Contest.objects.create(
            platform="CF",
            contest_id="2197",
            title="Codeforces Round 1079 (Div. 2)",
            start_time=start,
            duration_seconds=7200,
            year=start.year,
            division="Div2",
        )

    def test_not_found_uses_sibling_alias_rating(self):
        p_div1 = ContestProblem.objects.create(
            contest=self.div1,
            platform="CF",
            order=1,
            index_label="A",
            name="Game with a Fraction",
            problem_url="https://codeforces.com/contest/2196/problem/A",
            rating_status="OK",
        )
        p_div2 = ContestProblem.objects.create(
            contest=self.div2,
            platform="CF",
            order=3,
            index_label="C",
            name="Game with a Fraction",
            problem_url="https://codeforces.com/contest/2197/problem/C",
            rating_status="MISSING",
        )

        cache_div1 = ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=p_div1.problem_url,
            clist_problem_id="268923",
            clist_rating=1096,
            effective_rating=1096,
            rating_source="clist",
            status="OK",
            rating_fetched_at=timezone.now(),
        )
        self.assertEqual(cache_div1.effective_rating, 1096)

        cache_div2 = ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=p_div2.problem_url,
            status="TEMP_FAIL",
        )
        result = _apply_clist_result(cache_div2, {"status": "NOT_FOUND"})
        cache_div2.refresh_from_db()
        p_div2.refresh_from_db()

        self.assertEqual(result, "OK")
        self.assertEqual(cache_div2.status, "OK")
        self.assertEqual(cache_div2.effective_rating, 1096)
        self.assertEqual(p_div2.rating_status, "OK")

    def test_conflict_healer_keeps_safe_split_round_duplicates(self):
        p_div1 = ContestProblem.objects.create(
            contest=self.div1,
            platform="CF",
            order=1,
            index_label="A",
            name="Game with a Fraction",
            problem_url="https://codeforces.com/contest/2196/problem/A",
            rating_status="OK",
        )
        p_div2 = ContestProblem.objects.create(
            contest=self.div2,
            platform="CF",
            order=3,
            index_label="C",
            name="Game with a Fraction",
            problem_url="https://codeforces.com/contest/2197/problem/C",
            rating_status="OK",
        )

        ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=p_div1.problem_url,
            clist_problem_id="268923",
            clist_rating=1096,
            effective_rating=1096,
            rating_source="clist",
            status="OK",
            rating_fetched_at=timezone.now(),
        )
        ProblemRatingCache.objects.create(
            platform="CF",
            problem_url=p_div2.problem_url,
            clist_problem_id="268923",
            clist_rating=1096,
            effective_rating=1096,
            rating_source="clist",
            status="OK",
            rating_fetched_at=timezone.now(),
        )

        healed = _heal_conflicting_cf_cache_entries(max_problem_ids=10)
        self.assertEqual(healed["conflicting_problem_ids"], 0)
        self.assertEqual(healed["conflicting_urls"], 0)

        p_div1.refresh_from_db()
        p_div2.refresh_from_db()
        self.assertEqual(p_div1.rating_status, "OK")
        self.assertEqual(p_div2.rating_status, "OK")
