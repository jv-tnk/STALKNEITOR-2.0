from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    CompetitorGroup,
    Contest,
    ContestProblem,
    PerfilAluno,
    ScoreEvent,
    Submissao,
)
from core.services.contest_matrix import build_contest_matrix


class ContestMatrixTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.eve = User.objects.create_user(username="eve", password="x")
        self.alice_p = PerfilAluno.objects.create(
            user=self.alice, handle_atcoder="alice_ac"
        )
        self.bob_p = PerfilAluno.objects.create(
            user=self.bob, handle_atcoder="bob_ac"
        )
        self.eve_p = PerfilAluno.objects.create(
            user=self.eve, handle_atcoder="eve_ac"
        )

        self.abc454 = Contest.objects.create(
            platform="AC",
            contest_id="abc454",
            title="AtCoder Beginner Contest 454",
            start_time=now - timedelta(days=7),
            year=now.year,
            category="ABC",
        )
        self.abc455 = Contest.objects.create(
            platform="AC",
            contest_id="abc455",
            title="AtCoder Beginner Contest 455",
            start_time=now - timedelta(days=1),
            year=now.year,
            category="ABC",
        )
        self.arc190 = Contest.objects.create(
            platform="AC",
            contest_id="arc190",
            title="AtCoder Regular Contest 190",
            start_time=now - timedelta(days=3),
            year=now.year,
            category="ARC",
        )

        for label, order in [("A", 0), ("B", 1), ("C", 2), ("D", 3)]:
            ContestProblem.objects.create(
                contest=self.abc454,
                platform="AC",
                order=order,
                index_label=label,
                problem_url=f"https://atcoder.jp/contests/abc454/tasks/abc454_{label.lower()}",
            )
            ContestProblem.objects.create(
                contest=self.abc455,
                platform="AC",
                order=order,
                index_label=label,
                problem_url=f"https://atcoder.jp/contests/abc455/tasks/abc455_{label.lower()}",
            )

        # Alice solves A(100),B(200),C(400) in abc454 and A(100),B(200) in abc455 → 5 solves, 1000 pts
        alice_specs = [
            ("abc454", "A", "1", 100),
            ("abc454", "B", "2", 200),
            ("abc454", "C", "3", 400),
            ("abc455", "A", "10", 100),
            ("abc455", "B", "11", 200),
        ]
        for cid, idx, ext, points in alice_specs:
            sub = Submissao.objects.create(
                aluno=self.alice_p,
                plataforma="AC",
                contest_id=cid,
                problem_index=idx,
                verdict="AC",
                submission_time=timezone.now(),
                external_id=f"alice-{cid}-{ext}",
            )
            ScoreEvent.objects.create(
                aluno=self.alice_p,
                platform="AC",
                submission=sub,
                problem_url=f"https://atcoder.jp/contests/{cid}/tasks/{cid}_{idx.lower()}",
                solved_at=sub.submission_time,
                points_general_cf_equiv=points,
                contest_id=cid,
                contest_platform="AC",
            )

        # Bob solves only A(100) in abc454 (1 total, 100 pts)
        bob_sub = Submissao.objects.create(
            aluno=self.bob_p,
            plataforma="AC",
            contest_id="abc454",
            problem_index="A",
            verdict="AC",
            submission_time=timezone.now(),
            external_id="bob-454-1",
        )
        ScoreEvent.objects.create(
            aluno=self.bob_p,
            platform="AC",
            submission=bob_sub,
            problem_url="https://atcoder.jp/contests/abc454/tasks/abc454_a",
            solved_at=bob_sub.submission_time,
            points_general_cf_equiv=100,
            contest_id="abc454",
            contest_platform="AC",
        )

        # Eve is a villain — solved A,B,C,D in abc455
        villain_group = CompetitorGroup.objects.create(
            name="Villains", color="#ff0000", is_villain=True, priority=0
        )
        villain_group.users.add(self.eve)
        for idx, ext in [("A", "20"), ("B", "21"), ("C", "22"), ("D", "23")]:
            Submissao.objects.create(
                aluno=self.eve_p,
                plataforma="AC",
                contest_id="abc455",
                problem_index=idx,
                verdict="AC",
                submission_time=timezone.now(),
                external_id=f"eve-455-{ext}",
            )

    def test_returns_contests_filtered_by_category(self):
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        ids = [c["contest_id"] for c in result["contests"]]
        self.assertIn("abc454", ids)
        self.assertIn("abc455", ids)
        self.assertNotIn("arc190", ids)

    def test_orders_contests_oldest_first_newest_on_right(self):
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        ids = [c["contest_id"] for c in result["contests"]]
        self.assertEqual(ids, ["abc454", "abc455"])

    def test_excludes_villains(self):
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        usernames = [r["username"] for r in result["rows"]]
        self.assertIn("alice", usernames)
        self.assertIn("bob", usernames)
        self.assertNotIn("eve", usernames)
        self.assertEqual(result["villain_count"], 0)

    def test_can_include_villains_with_metadata(self):
        result = build_contest_matrix(
            platform="AC",
            category="ABC",
            limit_contests=10,
            include_villains=True,
        )
        usernames = [r["username"] for r in result["rows"]]
        self.assertIn("eve", usernames)
        eve = next(r for r in result["rows"] if r["username"] == "eve")
        self.assertTrue(eve["is_villain"])
        self.assertEqual(eve["villain_group_name"], "Villains")
        self.assertEqual(eve["villain_group_color"], "#ff0000")
        self.assertTrue(eve["villain_pill_style"])
        self.assertEqual(result["villain_count"], 1)
        self.assertEqual([item["label"] for item in eve["cells"][1]["items"]], ["A", "B", "C", "D"])

    def test_can_include_registered_villain_without_submissions_in_displayed_contests(self):
        zero = User.objects.create_user(username="zero_villain", password="x")
        PerfilAluno.objects.create(user=zero, handle_atcoder="zero_villain_ac")
        CompetitorGroup.objects.get(name="Villains").users.add(zero)

        result = build_contest_matrix(
            platform="AC",
            category="ABC",
            limit_contests=10,
            include_villains=True,
        )

        zero_row = next(r for r in result["rows"] if r["username"] == "zero_villain")
        self.assertTrue(zero_row["is_villain"])
        self.assertEqual(zero_row["total_solved"], 0)
        self.assertEqual(zero_row["total_points"], 0)
        self.assertTrue(all(not cell["items"] for cell in zero_row["cells"]))
        self.assertEqual(result["villain_count"], 2)

    def test_orders_users_by_total_points_then_solves(self):
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        usernames = [r["username"] for r in result["rows"]]
        self.assertEqual(usernames, ["alice", "bob"])
        self.assertEqual([r["position"] for r in result["rows"]], [1, 2])
        self.assertEqual(result["rows"][0]["total_solved"], 5)
        self.assertEqual(result["rows"][0]["total_points"], 1000)
        self.assertEqual(result["rows"][1]["total_solved"], 1)
        self.assertEqual(result["rows"][1]["total_points"], 100)

    def test_orders_visible_villains_by_points_with_regular_users(self):
        for sub in Submissao.objects.filter(aluno=self.eve_p, contest_id="abc455"):
            ScoreEvent.objects.create(
                aluno=self.eve_p,
                platform="AC",
                submission=sub,
                problem_url=f"https://atcoder.jp/contests/{sub.contest_id}/tasks/{sub.contest_id}_{sub.problem_index.lower()}",
                solved_at=sub.submission_time,
                points_general_cf_equiv=450,
                contest_id=sub.contest_id,
                contest_platform="AC",
            )

        result = build_contest_matrix(
            platform="AC",
            category="ABC",
            limit_contests=10,
            include_villains=True,
        )

        usernames = [r["username"] for r in result["rows"]]
        self.assertEqual(usernames[:3], ["eve", "alice", "bob"])
        self.assertEqual([r["position"] for r in result["rows"][:3]], [1, 2, 3])
        self.assertEqual(result["rows"][0]["total_points"], 1800)
        self.assertTrue(result["rows"][0]["is_villain"])

    def test_cell_contains_letters_points_and_submission_urls(self):
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        alice = next(r for r in result["rows"] if r["username"] == "alice")
        # cells follow displayed contest order: abc454 (older) then abc455 (newer)
        labels_454 = [item["label"] for item in alice["cells"][0]["items"]]
        labels_455 = [item["label"] for item in alice["cells"][1]["items"]]
        self.assertEqual(labels_454, ["A", "B", "C"])
        self.assertEqual(labels_455, ["A", "B"])
        self.assertEqual(alice["cells"][0]["points"], 700)  # 100+200+400
        self.assertEqual(alice["cells"][1]["points"], 300)  # 100+200
        self.assertEqual(
            [item["points"] for item in alice["cells"][0]["items"]],
            [100, 200, 400],
        )
        self.assertTrue(
            alice["cells"][0]["items"][0]["url"].startswith(
                "https://atcoder.jp/contests/abc454/submissions/"
            )
        )

    def test_cell_points_default_to_zero_when_no_score_event(self):
        # Add a Submissao without a corresponding ScoreEvent
        Submissao.objects.create(
            aluno=self.bob_p,
            plataforma="AC",
            contest_id="abc455",
            problem_index="C",
            verdict="AC",
            submission_time=timezone.now(),
            external_id="bob-455-c-noscore",
        )
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        bob = next(r for r in result["rows"] if r["username"] == "bob")
        cell_455 = bob["cells"][1]
        self.assertEqual([i["label"] for i in cell_455["items"]], ["C"])
        self.assertEqual(cell_455["items"][0]["points"], 0)
        self.assertEqual(cell_455["points"], 0)
        self.assertEqual(bob["total_solved"], 2)
        self.assertEqual(bob["total_points"], 100)

    def test_unknown_platform_returns_empty(self):
        result = build_contest_matrix(platform="UNKNOWN", category="ABC")
        self.assertEqual(result, {"contests": [], "rows": [], "villain_count": 0})

    def test_no_matching_contests_returns_empty_rows(self):
        result = build_contest_matrix(
            platform="AC", category="AGC", limit_contests=10
        )
        self.assertEqual(result["contests"], [])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["villain_count"], 0)

    def test_dedupes_repeated_accepted_submissions(self):
        # Add a second AC for alice on abc454/A — must not double-count
        Submissao.objects.create(
            aluno=self.alice_p,
            plataforma="AC",
            contest_id="abc454",
            problem_index="A",
            verdict="AC",
            submission_time=timezone.now(),
            external_id="alice-454-1-dup",
        )
        result = build_contest_matrix(platform="AC", category="ABC", limit_contests=10)
        alice = next(r for r in result["rows"] if r["username"] == "alice")
        # abc454 is index 0 now (oldest first)
        labels_454 = [item["label"] for item in alice["cells"][0]["items"]]
        self.assertEqual(labels_454, ["A", "B", "C"])
        self.assertEqual(alice["total_solved"], 5)
        self.assertEqual(alice["total_points"], 1000)

    def test_view_show_villains_filter_is_off_by_default_and_opt_in(self):
        self.client.force_login(self.alice)

        default_response = self.client.get(reverse("contests_matrix"))
        show_response = self.client.get(
            reverse("contests_matrix"),
            {"platform": "AC", "category": "ABC", "show_villains": "1"},
        )

        self.assertEqual(default_response.status_code, 200)
        self.assertFalse(default_response.context["show_villains"])
        default_usernames = [r["username"] for r in default_response.context["matrix"]["rows"]]
        self.assertNotIn("eve", default_usernames)
        self.assertEqual(show_response.status_code, 200)
        self.assertTrue(show_response.context["show_villains"])
        self.assertContains(show_response, "eve")
        self.assertContains(show_response, "Villains")
