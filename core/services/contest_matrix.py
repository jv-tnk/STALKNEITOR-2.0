"""Build a contest x user matrix.

Inspired by Stalkineitor v1: rows are students, columns are contests, cells
list the problem letters (e.g. "ABC") that student solved in that contest,
each clickable to its submission on the platform.
"""
from collections import defaultdict
from typing import Optional

from ..models import (
    CompetitorGroup,
    Contest,
    ContestProblem,
    PerfilAluno,
    ScoreEvent,
    Submissao,
)


ACCEPTED_VERDICTS_BY_PLATFORM = {"CF": {"OK"}, "AC": {"AC"}}


def _submission_url(platform: str, contest_id: str, external_id: Optional[str]) -> Optional[str]:
    if not external_id:
        return None
    if platform == "CF":
        return f"https://codeforces.com/contest/{contest_id}/submission/{external_id}"
    return f"https://atcoder.jp/contests/{contest_id}/submissions/{external_id}"


def _contest_external_url(platform: str, contest_id: str) -> str:
    if platform == "CF":
        return f"https://codeforces.com/contest/{contest_id}"
    return f"https://atcoder.jp/contests/{contest_id}"


def _hex_to_rgba(hex_color: str | None, alpha: float) -> str | None:
    if not hex_color or not isinstance(hex_color, str):
        return None
    value = hex_color.strip()
    if len(value) != 7 or not value.startswith("#"):
        return None
    try:
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
    except ValueError:
        return None
    alpha = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _villain_user_map() -> dict[int, dict]:
    rows = (
        CompetitorGroup.objects.filter(is_villain=True)
        .values("users__id", "name", "color", "priority")
        .order_by("priority", "id")
    )
    mapping: dict[int, dict] = {}
    for row in rows:
        user_id = row.get("users__id")
        if not user_id or user_id in mapping:
            continue
        color = row.get("color") or ""
        pill_bg = _hex_to_rgba(color, 0.18)
        pill_border = _hex_to_rgba(color, 0.55)
        mapping[int(user_id)] = {
            "name": row.get("name") or "Vilão",
            "color": color,
            "pill_style": (
                f"background-color:{pill_bg};border-color:{pill_border};color:#f8fafc;"
                if pill_bg and pill_border
                else ""
            ),
        }
    return mapping


def _villain_user_ids() -> set[int]:
    return set(_villain_user_map())


def _empty_matrix() -> dict:
    return {"contests": [], "rows": [], "villain_count": 0}


def build_contest_matrix(
    platform: str,
    *,
    category: Optional[str] = None,
    division: Optional[str] = None,
    year: Optional[int] = None,
    limit_contests: int = 10,
    include_empty_users: bool = False,
    include_villains: bool = False,
) -> dict:
    """Return matrix data for a given platform/category/year filter set.

    Output:
        {
          "contests": [
              {id, contest_id, platform, title, start_time, url,
               problems: [{index, url, name}, ...]},
              ...
          ],
          "rows": [
              {username, cells: [{contest_id,
                                  items: [{label, url, points}, ...],
                                  solved_count, points}, ...],
               total_solved, total_points},
              ...
          ],
        }

    Contests are ordered oldest → newest (most recent on the right).
    Cells are sorted by problem order. Rows are sorted by total_points desc,
    then total_solved desc, then username. Villains (CompetitorGroup.is_villain)
    are excluded unless ``include_villains`` is true. `points` per cell item comes from
    ``ScoreEvent.points_general_cf_equiv`` (the ranking metric).
    """
    accepted = ACCEPTED_VERDICTS_BY_PLATFORM.get(platform, set())
    if not accepted:
        return _empty_matrix()

    qs = Contest.objects.filter(platform=platform, start_time__isnull=False)
    if year:
        qs = qs.filter(year=year)
    if platform == "AC" and category and category != "all":
        qs = qs.filter(category=category)
    if platform == "CF" and division and division != "all":
        qs = qs.filter(division=division)

    contests = list(
        qs.only(
            "id",
            "platform",
            "contest_id",
            "title",
            "start_time",
            "category",
            "division",
        ).order_by("-start_time")[:limit_contests]
    )
    if not contests:
        return _empty_matrix()

    # Display order: oldest on the left, most recent on the right.
    contests.reverse()
    contest_ids = [c.contest_id for c in contests]

    problem_rows = (
        ContestProblem.objects.filter(contest__in=contests)
        .values(
            "contest__contest_id",
            "index_label",
            "problem_url",
            "name",
            "order",
        )
        .order_by("order")
    )
    problems_by_contest: dict[str, list[dict]] = defaultdict(list)
    for row in problem_rows:
        problems_by_contest[row["contest__contest_id"]].append(
            {
                "index": row["index_label"],
                "url": row["problem_url"],
                "name": row["name"] or row["index_label"],
            }
        )

    villain_map = _villain_user_map()
    villain_user_ids = set(villain_map)

    sub_rows = (
        Submissao.objects.filter(
            plataforma=platform,
            contest_id__in=contest_ids,
            verdict__in=accepted,
        )
        .values(
            "aluno_id",
            "aluno__user_id",
            "aluno__user__username",
            "contest_id",
            "problem_index",
            "external_id",
            "submission_time",
        )
        .order_by("submission_time")
    )

    # Ranking points per first-AC, keyed by (aluno, contest, problem_index).
    points_by_key: dict[tuple[int, str, str], int] = {}
    score_rows = ScoreEvent.objects.filter(
        platform=platform,
        submission__contest_id__in=contest_ids,
    ).values(
        "submission__aluno_id",
        "submission__contest_id",
        "submission__problem_index",
        "points_general_cf_equiv",
    )
    for row in score_rows.iterator():
        idx = row["submission__problem_index"]
        if not idx:
            continue
        key = (
            row["submission__aluno_id"],
            row["submission__contest_id"],
            idx,
        )
        points_by_key[key] = row["points_general_cf_equiv"] or 0

    seen_keys: set[tuple[int, str, str]] = set()
    cells_by_user_contest: dict[tuple[int, str], list[dict]] = defaultdict(list)
    user_meta: dict[int, dict] = {}
    user_solved_count: dict[int, int] = defaultdict(int)
    user_total_points: dict[int, int] = defaultdict(int)

    for row in sub_rows.iterator():
        aluno_id = row["aluno_id"]
        contest_id = row["contest_id"]
        idx = row["problem_index"]
        if not idx:
            continue
        key = (aluno_id, contest_id, idx)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        points = points_by_key.get(key, 0)
        cells_by_user_contest[(aluno_id, contest_id)].append(
            {
                "label": idx,
                "url": _submission_url(platform, contest_id, row["external_id"]),
                "points": points,
            }
        )
        user_solved_count[aluno_id] += 1
        user_total_points[aluno_id] += points
        if aluno_id not in user_meta:
            user_meta[aluno_id] = {
                "aluno_id": aluno_id,
                "user_id": row["aluno__user_id"],
                "username": row["aluno__user__username"] or "unknown",
            }

    if include_villains and villain_user_ids:
        for profile in (
            PerfilAluno.objects.select_related("user")
            .filter(user_id__in=villain_user_ids)
            .only("id", "user_id", "user__username")
        ):
            user_meta.setdefault(
                profile.id,
                {
                    "aluno_id": profile.id,
                    "user_id": profile.user_id,
                    "username": profile.user.username or "unknown",
                },
            )

    rows: list[dict] = []
    for aluno_id, meta in user_meta.items():
        user_id = meta["user_id"]
        is_villain = user_id in villain_user_ids
        if is_villain and not include_villains:
            continue
        villain_meta = villain_map.get(user_id, {})
        cells = []
        for contest in contests:
            items = cells_by_user_contest.get((aluno_id, contest.contest_id), [])
            order_map = {
                p["index"]: i
                for i, p in enumerate(problems_by_contest.get(contest.contest_id, []))
            }
            items_sorted = sorted(
                items,
                key=lambda x: order_map.get(x["label"], 999),
            )
            cell_points = sum(item["points"] for item in items_sorted)
            cells.append(
                {
                    "contest_id": contest.contest_id,
                    "items": items_sorted,
                    "solved_count": len(items_sorted),
                    "points": cell_points,
                }
            )
        rows.append(
            {
                "username": meta["username"],
                "cells": cells,
                "total_solved": user_solved_count[aluno_id],
                "total_points": user_total_points[aluno_id],
                "is_villain": is_villain,
                "villain_group_name": villain_meta.get("name", ""),
                "villain_group_color": villain_meta.get("color", ""),
                "villain_pill_style": villain_meta.get("pill_style", ""),
            }
        )

    rows.sort(
        key=lambda r: (
            -r["total_points"],
            -r["total_solved"],
            r["username"].lower(),
        )
    )
    for position, row in enumerate(rows, start=1):
        row["position"] = position

    contest_dicts = [
        {
            "id": c.id,
            "contest_id": c.contest_id,
            "platform": c.platform,
            "title": c.title,
            "start_time": c.start_time,
            "category": c.category,
            "division": c.division,
            "url": _contest_external_url(c.platform, c.contest_id),
            "problems": problems_by_contest.get(c.contest_id, []),
            "problem_count": len(problems_by_contest.get(c.contest_id, [])),
        }
        for c in contests
    ]

    return {
        "contests": contest_dicts,
        "rows": rows,
        "villain_count": sum(1 for row in rows if row.get("is_villain")),
    }
