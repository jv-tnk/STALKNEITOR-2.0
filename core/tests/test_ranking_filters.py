from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import Contest, PerfilAluno, ScoreEvent, Submissao
from core.views import (
    _apply_points_event_filters,
    _build_points_rows_from_events,
    _normalize_ranking_contest_type,
)


class RankingPointsFilterTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user(
            username="ranking_student",
            password="StrongPass123!",
        )
        self.student = PerfilAluno.objects.create(
            user=self.user,
            handle_codeforces="ranking_cf",
            handle_atcoder="ranking_ac",
        )

    def _create_contest(
        self,
        *,
        platform: str,
        contest_id: str,
        title: str,
        category: str = "Other",
        division: str = "Other",
    ) -> Contest:
        start_time = self.now - timedelta(days=2)
        return Contest.objects.create(
            platform=platform,
            contest_id=contest_id,
            title=title,
            start_time=start_time,
            duration_seconds=7200,
            year=start_time.year,
            category=category,
            division=division,
        )

    def _create_event(
        self,
        *,
        platform: str,
        contest_id: str,
        index: str,
        points: int,
        provisional: bool = False,
    ) -> ScoreEvent:
        submission = Submissao.objects.create(
            aluno=self.student,
            plataforma=platform,
            contest_id=contest_id,
            problem_index=index,
            verdict="OK" if platform == "CF" else "AC",
            submission_time=self.now,
            problem_name=f"{contest_id}_{index}",
            external_id=f"{platform}-{contest_id}-{index}",
        )
        problem_url = (
            f"https://codeforces.com/contest/{contest_id}/problem/{index}"
            if platform == "CF"
            else f"https://atcoder.jp/contests/{contest_id}/tasks/{contest_id}_{index.lower()}"
        )
        return ScoreEvent.objects.create(
            aluno=self.student,
            platform=platform,
            submission=submission,
            problem_url=problem_url,
            solved_at=submission.submission_time,
            raw_rating=points,
            points_cf_raw=points if platform == "CF" else 0,
            points_ac_raw=points if platform == "AC" else 0,
            points_general_cf_equiv=points,
            rating_used_cf_equiv=points,
            rating_source="provisional" if provisional else "clist",
            rating_is_provisional=provisional,
            points_awarded=points,
            contest_platform=platform,
            contest_id=contest_id,
            reason="first_ac",
        )

    def _points_for(self, contest_type: str, *, exclude_provisional: bool = False):
        events = _apply_points_event_filters(
            ScoreEvent.objects.all(),
            category="overall",
            contest_type=contest_type,
            exclude_provisional=exclude_provisional,
            season_contest_only=False,
            season_start_dt=None,
            season_end_dt=None,
        )
        rows = _build_points_rows_from_events(events, "overall", "global", None)
        return next(row for row in rows if row.aluno.id == self.student.id)

    def test_contest_type_filter_limits_points_to_atcoder_category_and_cf_division(self):
        self._create_contest(
            platform="AC",
            contest_id="abc900",
            title="AtCoder Beginner Contest 900",
            category="ABC",
        )
        self._create_contest(
            platform="AC",
            contest_id="arc900",
            title="AtCoder Regular Contest 900",
            category="ARC",
        )
        self._create_contest(
            platform="CF",
            contest_id="1900",
            title="Codeforces Round 1900 Div. 2",
            division="Div2",
        )
        self._create_contest(
            platform="CF",
            contest_id="1901",
            title="Codeforces Round 1901 Div. 1",
            division="Div1",
        )
        self._create_event(platform="AC", contest_id="abc900", index="A", points=1000)
        self._create_event(platform="AC", contest_id="arc900", index="B", points=2000)
        self._create_event(platform="CF", contest_id="1900", index="C", points=1500)
        self._create_event(platform="CF", contest_id="1901", index="D", points=2500)

        abc_row = self._points_for("ac_ABC")
        div2_row = self._points_for("cf_Div2")

        self.assertEqual(abc_row.points, 1000)
        self.assertEqual(abc_row.points_ac, 1000)
        self.assertEqual(abc_row.points_cf, 0)
        self.assertEqual(abc_row.activity_solves, 1)
        self.assertEqual(div2_row.points, 1500)
        self.assertEqual(div2_row.points_cf, 1500)
        self.assertEqual(div2_row.points_ac, 0)
        self.assertEqual(div2_row.activity_solves, 1)

    def test_exclude_provisional_removes_partial_rating_points(self):
        self._create_event(platform="CF", contest_id="2000", index="A", points=1200)
        self._create_event(
            platform="CF",
            contest_id="2001",
            index="B",
            points=700,
            provisional=True,
        )

        with_partial = self._points_for("all")
        without_partial = self._points_for("all", exclude_provisional=True)

        self.assertEqual(with_partial.points, 1900)
        self.assertEqual(with_partial.provisional_solves, 1)
        self.assertEqual(without_partial.points, 1200)
        self.assertEqual(without_partial.provisional_solves, 0)
        self.assertEqual(without_partial.activity_solves, 1)

    def test_contest_type_is_normalized_against_selected_source(self):
        self.assertEqual(_normalize_ranking_contest_type("ac_ABC", "cf"), "all")
        self.assertEqual(_normalize_ranking_contest_type("cf_Div2", "ac"), "all")
        self.assertEqual(_normalize_ranking_contest_type("cf_Div2", "overall"), "cf_Div2")
