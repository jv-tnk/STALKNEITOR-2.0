from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import (
    ContestProblem,
    PerfilAluno,
    ProblemRatingCache,
    ScoreEvent,
    Submissao,
    TrainingBlockedProblem,
    TrainingQueueItem,
    TrainingSessionItem,
)
from core.services.problem_urls import build_problem_url_from_fields
from core.services.problem_ratings import schedule_rating_job

CF_TRAIN_DIVISIONS = {"Div1", "Div2", "Div3", "Div4", "Educational", "Global"}
AC_TRAIN_CATEGORIES = {"ABC", "ARC", "AGC"}
RATING_PENDING_STATUSES = {"MISSING", "TEMP_FAIL", "QUEUED"}


@dataclass(frozen=True)
class TrainingZone:
    low: int
    high: int

    @property
    def label(self) -> str:
        return f"{self.low}–{self.high}"


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _normalize_tag(tag: str | None) -> str:
    if not tag:
        return ""
    return " ".join(str(tag).strip().lower().split())

def get_session_plan(mode: str, duration_minutes: int) -> dict[str, int | str]:
    """
    Returns how many problems to include in a session for CF/AC.

    Counts are "mandatory" (extras can be suggested on the dashboard separately).
    """
    mode = (mode or "evolution").lower()
    if duration_minutes not in {60, 90, 120}:
        duration_minutes = 90

    # (cf_count, ac_easy, ac_medium, ac_stretch)
    if duration_minutes == 60:
        if mode == "consistency":
            plan = (2, 2, 0, 0)
        elif mode == "challenge":
            plan = (1, 1, 0, 0)
        elif mode == "general":
            plan = (1, 1, 1, 0)
        else:  # evolution
            plan = (2, 1, 1, 0)
    elif duration_minutes == 120:
        if mode == "consistency":
            plan = (3, 3, 1, 0)
        elif mode == "challenge":
            plan = (2, 1, 1, 0)
        elif mode == "general":
            plan = (2, 2, 1, 0)
        else:  # evolution
            plan = (3, 1, 2, 0)
    else:  # 90
        if mode == "consistency":
            plan = (2, 2, 1, 0)
        elif mode == "challenge":
            plan = (2, 1, 1, 0)
        elif mode == "general":
            plan = (2, 1, 2, 0)
        else:  # evolution
            plan = (3, 1, 1, 0)

    cf_count, ac_easy, ac_medium, ac_stretch = plan
    ac_total = ac_easy + ac_medium + ac_stretch

    return {
        "cf_count": cf_count,
        "ac_easy": ac_easy,
        "ac_medium": ac_medium,
        "ac_stretch": ac_stretch,
        "objective": f"CF {cf_count} + AC {ac_total}",
    }


def get_baseline_cf(student: PerfilAluno) -> int:
    if student.cf_rating_current:
        return int(student.cf_rating_current)

    solved = list(
        ScoreEvent.objects.filter(aluno=student, platform="CF")
        .exclude(raw_rating__isnull=True)
        .values_list("raw_rating", flat=True)[:200]
    )
    if solved:
        solved_sorted = sorted(int(x) for x in solved if x is not None)
        return int(solved_sorted[len(solved_sorted) // 2])

    return 1200


def get_baseline_ac(student: PerfilAluno) -> int:
    if student.ac_rating_current is not None:
        return int(student.ac_rating_current)

    solved = list(
        ScoreEvent.objects.filter(aluno=student, platform="AC")
        .exclude(raw_rating__isnull=True)
        .values_list("raw_rating", flat=True)[:200]
    )
    if solved:
        solved_sorted = sorted(int(x) for x in solved if x is not None)
        return int(solved_sorted[len(solved_sorted) // 2])

    return 800


def get_cf_training_zone(mode: str, baseline: int, duration_minutes: int = 90) -> TrainingZone:
    mode = (mode or "evolution").lower()
    if duration_minutes not in {60, 90, 120}:
        duration_minutes = 90

    if mode == "consistency":
        low = baseline - 250
        high = baseline - 50
    elif mode == "challenge":
        low = baseline + 200
        high = baseline + 450
    elif mode == "general":
        low = baseline - 150
        high = baseline + 150
    else:  # evolution
        low = baseline - 100
        high = baseline + (150 if duration_minutes == 60 else 200)

    return TrainingZone(
        low=_clamp_int(low, 800, 3500),
        high=_clamp_int(high, 800, 3500),
    )


def get_ac_ladder_ranges(mode: str, baseline: int, duration_minutes: int = 90) -> dict[str, tuple[int, int]]:
    mode = (mode or "evolution").lower()
    if mode == "consistency":
        easy = (baseline - 400, baseline - 150)
        medium = (baseline - 50, baseline + 150)
        stretch = (baseline + 200, baseline + 350)
    elif mode == "challenge":
        easy = (baseline - 200, baseline + 0)
        medium = (baseline + 150, baseline + 350)
        stretch = (baseline + 400, baseline + 650)
    elif mode == "general":
        easy = (baseline - 250, baseline - 50)
        medium = (baseline + 0, baseline + 200)
        stretch = (baseline + 250, baseline + 450)
    else:
        easy = (baseline - 300, baseline - 50)
        medium = (baseline + 0, baseline + 200)
        stretch = (baseline + 250, baseline + 450)

    def clamp_pair(pair: tuple[int, int]) -> tuple[int, int]:
        lo, hi = pair
        return (_clamp_int(lo, 0, 4000), _clamp_int(hi, 0, 4000))

    return {
        "easy": clamp_pair(easy),
        "medium": clamp_pair(medium),
        "stretch": clamp_pair(stretch),
    }


def _parse_tags(tags_str: str | None) -> list[str]:
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


def _training_solved_urls(student: PerfilAluno, platform: str) -> set[str]:
    """
    Problems marked as SOLVED/EDITORIAL inside training sessions.
    These should not be suggested in future sessions even if there is no ScoreEvent yet.
    """
    return set(
        TrainingSessionItem.objects.filter(
            session__aluno=student,
            platform=platform,
            result__in=["SOLVED", "EDITORIAL"],
        ).values_list("problem_url", flat=True)
    )


def _build_problem_pool(
    platform: str,
    *,
    contest_categories: set[str] | None = None,
    contest_divisions: set[str] | None = None,
) -> list[dict]:
    platform = (platform or "").upper()
    qs = ContestProblem.objects.filter(platform=platform)
    if contest_categories:
        qs = qs.filter(contest__category__in=contest_categories)
    if contest_divisions:
        qs = qs.filter(contest__division__in=contest_divisions)

    problems = list(
        qs
        .select_related("contest")
        .only(
            "id",
            "problem_url",
            "name",
            "tags",
            "index_label",
            "cf_rating",
            "contest__contest_id",
            "contest__category",
            "contest__division",
            "contest__start_time",
        )
    )
    urls = [p.problem_url for p in problems if p.problem_url]
    caches = {
        c.problem_url: c
        for c in ProblemRatingCache.objects.filter(
            problem_url__in=urls,
            status__in=["OK", "NOT_FOUND", "TEMP_FAIL", "RATE_LIMITED", "ERROR"],
        )
    }

    pool_map: dict[str, dict] = {}
    for p in problems:
        if not p.problem_url:
            continue
        cache = caches.get(p.problem_url)
        rating = None
        rating_source = "none"
        has_clist = False
        cf_rating = p.cf_rating
        rating_rank = 0

        if cache and cache.effective_rating is not None:
            rating = int(cache.effective_rating)
            rating_source = cache.rating_source or "none"
            has_clist = cache.clist_rating is not None
            rating_rank = 3 if rating_source == "clist" else 2
        elif cf_rating is not None:
            rating = int(cf_rating)
            rating_source = "cf"
            rating_rank = 2
        elif cache and cache.clist_rating is not None:
            rating = int(cache.clist_rating)
            rating_source = "clist"
            has_clist = True
            rating_rank = 3

        entry = pool_map.get(p.problem_url)
        if not entry:
            pool_map[p.problem_url] = {
                "contest_id": p.contest.contest_id if p.contest_id else None,
                "contest_category": getattr(p.contest, "category", None),
                "index_label": p.index_label,
                "title": p.name or p.index_label,
                "problem_url": p.problem_url,
                "rating": rating,
                "rating_source": rating_source,
                "has_clist": has_clist,
                "cf_rating": cf_rating,
                "tags": _parse_tags(p.tags),
                "_rating_rank": rating_rank,
            }
            continue

        # Merge tags
        existing_tags = set(entry.get("tags") or [])
        existing_tags.update(_parse_tags(p.tags))
        entry["tags"] = list(existing_tags)

        # Prefer higher-quality rating sources (clist > cf > none)
        if rating is not None:
            existing_rank = entry.get("_rating_rank", 0)
            if rating_rank > existing_rank:
                entry["rating"] = rating
                entry["rating_source"] = rating_source
                entry["has_clist"] = entry.get("has_clist") or has_clist
                entry["_rating_rank"] = rating_rank
            elif rating_rank == existing_rank and entry.get("rating") is not None:
                # In rare conflicts, keep the higher rating for stability.
                entry["rating"] = max(int(entry["rating"]), int(rating))
        if entry.get("cf_rating") is None and cf_rating is not None:
            entry["cf_rating"] = cf_rating
        if has_clist:
            entry["has_clist"] = True

    pool = list(pool_map.values())
    for entry in pool:
        entry.pop("_rating_rank", None)
    return pool


def compute_cf_tag_focus(student: PerfilAluno, candidate_tag_counts: Counter[str]) -> tuple[list[str], list[str]]:
    """
    Returns (weak_tags, growth_tags).
    Based on the student's CF submissions + solved set.
    """
    now = timezone.now()
    start_dt = now - timedelta(days=180)

    solved_keys = set(
        ScoreEvent.objects.filter(aluno=student, platform="CF", solved_at__gte=start_dt)
        .values_list("submission__contest_id", "submission__problem_index")
    )

    subs_qs = (
        Submissao.objects.filter(aluno=student, plataforma="CF", submission_time__gte=start_dt)
        .exclude(tags__isnull=True)
        .exclude(tags="")
    )

    problems: dict[tuple[str, str], dict] = {}
    for row in subs_qs.values("contest_id", "problem_index", "tags").iterator():
        contest_id = row.get("contest_id")
        idx = row.get("problem_index")
        if not contest_id or not idx:
            continue
        key = (contest_id, idx)
        meta = problems.setdefault(key, {"tags": set(), "submissions": 0})
        meta["tags"].update({_normalize_tag(t) for t in (row.get("tags") or "").split(",") if _normalize_tag(t)})
        meta["submissions"] += 1

    urls = []
    key_to_url = {}
    for contest_id, idx in problems.keys():
        url = build_problem_url_from_fields("CF", contest_id, idx, None)
        if url:
            key_to_url[(contest_id, idx)] = url
            urls.append(url)

    rating_map = {
        r.problem_url: (r.effective_rating if r.effective_rating is not None else r.clist_rating)
        for r in ProblemRatingCache.objects.filter(problem_url__in=urls, status__in=["OK", "NOT_FOUND", "TEMP_FAIL", "RATE_LIMITED", "ERROR"])
        if (r.effective_rating is not None or r.clist_rating is not None)
    }

    tag_stats: dict[str, dict] = {}
    for key, meta in problems.items():
        is_solved = key in solved_keys
        url = key_to_url.get(key)
        rating = rating_map.get(url) if url else None
        submissions_n = int(meta.get("submissions") or 0)
        for tag in meta["tags"]:
            st = tag_stats.setdefault(tag, {
                "attempted": 0,
                "solved": 0,
                "submissions": 0,
                "rating_sum": 0,
                "rating_n": 0,
            })
            st["attempted"] += 1
            if is_solved:
                st["solved"] += 1
            st["submissions"] += submissions_n
            if rating is not None:
                st["rating_sum"] += int(rating)
                st["rating_n"] += 1

    rows = []
    for tag, st in tag_stats.items():
        attempted = st["attempted"]
        solved = st["solved"]
        if attempted <= 0:
            continue
        solve_rate = solved / attempted
        avg_rating = (st["rating_sum"] / st["rating_n"]) if st["rating_n"] else 1000.0
        avg_subs = (st["submissions"] / attempted) if attempted else 0.0
        # Pain rises with unsolved volume + low conversion + higher difficulty + more attempts.
        unsolved = attempted - solved
        difficulty_factor = max(0.75, min(float(avg_rating) / 1200.0, 2.0))
        pain_score = unsolved * (0.6 + (1.0 - solve_rate)) * difficulty_factor * (1.0 + max(0.0, avg_subs - 1.0) * 0.25)
        rows.append({
            "tag": tag,
            "attempted": attempted,
            "solved": solved,
            "solve_rate": solve_rate,
            "pain_score": pain_score,
        })

    weak = [r for r in rows if r["attempted"] >= 5 and r["solve_rate"] <= 0.60 and (r["attempted"] - r["solved"]) >= 3]
    weak_sorted = sorted(weak, key=lambda r: (r["pain_score"], r["attempted"]), reverse=True)
    weak_tags = [r["tag"] for r in weak_sorted[:3]]

    attempted_by_tag = {r["tag"]: r["attempted"] for r in rows}
    growth_candidates: list[tuple[float, int, str]] = []
    seen = set()
    for tag, count in candidate_tag_counts.most_common(120):
        normalized_tag = _normalize_tag(tag)
        if not normalized_tag or normalized_tag in seen:
            continue
        seen.add(normalized_tag)
        if normalized_tag in weak_tags:
            continue
        attempted = int(attempted_by_tag.get(normalized_tag, 0))
        # Growth means low historical exposure with high usefulness in the pool.
        if attempted > 4:
            continue
        if count < 8:
            continue
        scarcity_score = max(0, 4 - attempted) * 3.0
        support_score = min(int(count), 300) / 80.0
        growth_score = scarcity_score + support_score
        growth_candidates.append((growth_score, int(count), normalized_tag))

    growth_candidates.sort(key=lambda r: (r[0], r[1]), reverse=True)
    growth_tags = [r[2] for r in growth_candidates[:3]]

    return weak_tags, growth_tags


def _score_cf_problem(problem: dict, zone: TrainingZone, focus_tags: set[str], growth_tags: set[str]) -> float:
    rating = problem.get("rating")
    if rating is None:
        return -1e9

    tags = set(problem.get("_norm_tags") or [])
    focus_hits = len(tags & focus_tags)
    center = (zone.low + zone.high) / 2.0
    rating_score = max(0.0, 25.0 - abs(float(rating) - center) / 40.0)
    score = focus_hits * 120.0 + rating_score
    growth_hits = len(tags & growth_tags) if growth_tags else 0
    if growth_hits:
        score += min(growth_hits, 2) * 25.0
    return score


def estimate_expected_minutes(platform: str, rating: int | None, baseline: int) -> int:
    if rating is None:
        return 15
    diff = int(rating) - int(baseline)
    base = 18 if platform == "CF" else 15
    if diff >= 250:
        base += 12
    elif diff >= 100:
        base += 6
    elif diff <= -150:
        base -= 4
    return _clamp_int(base, 8, 45)


def pick_cf_problems_by_tags(
    tags: list[str],
    *,
    low: int,
    high: int,
    limit: int,
    excluded: set[str],
) -> list[dict]:
    if not tags:
        return []
    tag_set = {t.lower() for t in tags if t}
    pool = _build_problem_pool("CF", contest_divisions=CF_TRAIN_DIVISIONS)
    picked = []
    for p in pool:
        url = p.get("problem_url")
        rating = p.get("rating")
        if not url or url in excluded or rating is None:
            continue
        if rating < low or rating > high:
            continue
        p_tags = {t.lower() for t in (p.get("tags") or [])}
        if not (p_tags & tag_set):
            continue
        picked.append(p)
    picked.sort(key=lambda r: (r.get("rating") or 0, r.get("title") or ""))
    return picked[:limit]


def build_cf_suggestions(
    student: PerfilAluno,
    mode: str,
    count: int = 10,
    duration_minutes: int = 90,
    allowed_urls: set[str] | None = None,
    rating_low: int | None = None,
    rating_high: int | None = None,
) -> tuple[TrainingZone, list[dict], dict]:
    baseline = get_baseline_cf(student)
    if rating_low is not None and rating_high is not None:
        zone = TrainingZone(
            low=_clamp_int(min(int(rating_low), int(rating_high)), 800, 3500),
            high=_clamp_int(max(int(rating_low), int(rating_high)), 800, 3500),
        )
    else:
        zone = get_cf_training_zone(mode, baseline, duration_minutes=duration_minutes)

    pool = _build_problem_pool("CF", contest_divisions=CF_TRAIN_DIVISIONS)
    tag_counts = Counter()
    for p in pool:
        for t in p.get("tags") or []:
            normalized_tag = _normalize_tag(t)
            if normalized_tag:
                tag_counts[normalized_tag] += 1

    weak_tags, growth_tags = compute_cf_tag_focus(student, tag_counts)
    growth_set = set(growth_tags)
    focus = set(weak_tags)
    focus.update(growth_set)

    solved_urls = set(
        ScoreEvent.objects.filter(aluno=student, platform="CF").values_list("problem_url", flat=True)
    )
    training_solved_urls = _training_solved_urls(student, "CF")
    blocked_urls = set(
        TrainingBlockedProblem.objects.filter(aluno=student, platform="CF").values_list("problem_url", flat=True)
    )
    queued_urls = set(
        TrainingQueueItem.objects.filter(aluno=student, platform="CF", status="QUEUED").values_list("problem_url", flat=True)
    )
    recent_seen_urls = set(
        TrainingSessionItem.objects.filter(
            session__aluno=student,
            platform="CF",
            created_at__gte=timezone.now() - timedelta(days=30),
        ).values_list("problem_url", flat=True)
    )
    excluded = solved_urls | training_solved_urls | blocked_urls | queued_urls

    base_tags = {
        "implementation",
        "math",
        "greedy",
        "binary search",
        "two pointers",
        "strings",
        "data structures",
        "sortings",
    }

    allow = set(allowed_urls or [])
    candidates = []
    for p in pool:
        url = p.get("problem_url")
        rating = p.get("rating")
        if not url:
            continue
        if allow and url not in allow:
            continue
        if url in excluded:
            continue
        if rating is None:
            continue
        rating_int = int(rating)
        if rating_int < 800 or rating_int > 3500:
            continue
        tags = set(p.get("tags") or [])
        norm_tags = {_normalize_tag(t) for t in tags if _normalize_tag(t)}
        if not norm_tags:
            continue
        p["_norm_tags"] = norm_tags
        p["_rating_int"] = rating_int
        p["_score"] = _score_cf_problem(p, zone, focus, growth_set)
        p["_seen"] = url in recent_seen_urls
        p["_focus_hit"] = bool(norm_tags & focus)
        p["_base_hit"] = bool(norm_tags & base_tags)
        candidates.append(p)

    def pick(n: int, filt, *, exclude_urls: set[str] | None = None) -> list[dict]:
        picked: list[dict] = []
        picked_urls: set[str] = set()
        blocked_urls = exclude_urls or set()
        tag_use = Counter()
        sorted_candidates = sorted(
            [c for c in candidates if filt(c)],
            key=lambda c: (c["_score"], -int(c.get("rating") or 0)),
            reverse=True,
        )
        for c in sorted_candidates:
            if len(picked) >= n:
                break
            url = c.get("problem_url")
            if not url or url in picked_urls or url in blocked_urls:
                continue
            tags = set(c.get("_norm_tags") or [])
            # Light diversity: don't repeat the same tag too much in the same batch.
            if any(tag_use[t] >= 2 for t in tags & focus):
                continue
            for t in tags & focus:
                tag_use[t] += 1
            picked_urls.add(url)
            picked.append(c)
        return picked

    selected: list[dict] = []
    selected_urls: set[str] = set()

    def add_selected(batch: list[dict]) -> None:
        for row in batch:
            url = row.get("problem_url")
            if not url or url in selected_urls:
                continue
            selected.append(row)
            selected_urls.add(url)

    # Always keep a mixed batch: focus + fundamentals + open-zone.
    n_focus = max(0, int(round(count * 0.55))) if focus else 0
    n_base = max(1, int(round(count * 0.25)))
    n_open = max(0, count - n_focus - n_base)
    if count >= 4 and n_open == 0:
        n_open = 1
        if n_focus > 1:
            n_focus -= 1

    if n_focus > 0:
        add_selected(
            pick(
                n_focus,
                lambda c: c["_focus_hit"] and not c["_seen"] and zone.low <= c["_rating_int"] <= zone.high,
                exclude_urls=selected_urls,
            )
        )
        remaining_focus = max(0, n_focus - len(selected))
        if remaining_focus > 0:
            add_selected(
                pick(
                    remaining_focus,
                    lambda c: c["_focus_hit"] and zone.low <= c["_rating_int"] <= zone.high,
                    exclude_urls=selected_urls,
                )
            )

    add_selected(
        pick(
            n_base,
            lambda c: c["_base_hit"] and zone.low <= c["_rating_int"] <= zone.high,
            exclude_urls=selected_urls,
        )
    )

    if n_open > 0:
        add_selected(
            pick(
                n_open,
                lambda c: zone.low <= c["_rating_int"] <= zone.high,
                exclude_urls=selected_urls,
            )
        )

    remaining = count - len(selected)
    if remaining > 0:
        add_selected(
            pick(
                remaining,
                lambda c: zone.low <= c["_rating_int"] <= zone.high,
                exclude_urls=selected_urls,
            )
        )

    # Hard guard: never suggest CF problems outside the active session zone.
    selected = [p for p in selected if zone.low <= int(p["_rating_int"]) <= zone.high]

    # Fallback only when no focus-based recommendation exists.
    if not selected:
        add_selected(
            pick(
                count,
                lambda c: c["_base_hit"] and zone.low <= c["_rating_int"] <= zone.high,
                exclude_urls=selected_urls,
            )
        )
        remaining = count - len(selected)
        if remaining > 0:
            add_selected(
                pick(
                    remaining,
                    lambda c: zone.low <= c["_rating_int"] <= zone.high,
                    exclude_urls=selected_urls,
                )
            )

    def why_line(p: dict) -> str:
        tags = set(p.get("_norm_tags") or [])
        focus_hit = list(tags & focus)
        if focus_hit:
            tag = sorted(focus_hit)[0]
            if tag in growth_set:
                return f"Crescimento: ampliar repertorio em {tag}."
            return f"Foco: baixa conversão em {tag}."
        if tags & base_tags:
            return "Base: fundamentos recorrentes."
        return f"Zona {zone.label}."

    suggestions = []
    for p in selected[:count]:
        display_tags = sorted(
            list(p.get("_norm_tags") or []),
            key=lambda t: (0 if t in focus else 1, t),
        )
        suggestions.append({
            "platform": "CF",
            "problem_url": p["problem_url"],
            "title": p["title"],
            "contest_id": p.get("contest_id"),
            "index_label": p.get("index_label"),
            "rating": p.get("rating"),
            "tags": display_tags,
            "why": why_line(p),
            "focus_tags": weak_tags,
            "growth_tags": growth_tags,
            "growth_tag": growth_tags[0] if growth_tags else None,
        })

    # On-demand rating fetch for suggested problems lacking CLIST rating
    for p in selected[:count]:
        if not p.get("problem_url"):
            continue
        if p.get("has_clist"):
            continue
        rating_priority_max = int(getattr(settings, "RATING_PRIORITY_MAX", 1800))
        priority = 0 if (p.get("cf_rating") and int(p.get("cf_rating")) <= rating_priority_max) else 1
        schedule_rating_job("CF", p["problem_url"], priority=priority)

    active_focus_tags = sorted(
        {
            tag
            for p in selected[:count]
            for tag in (p.get("_norm_tags") or set())
            if tag in focus
        }
    )

    meta = {
        "baseline": baseline,
        "weak_tags": [t for t in weak_tags if t in active_focus_tags],
        "growth_tags": [t for t in growth_tags if t in active_focus_tags],
        "growth_tag": (next((t for t in growth_tags if t in active_focus_tags), None)),
    }

    return zone, suggestions, meta


def build_ac_suggestions(
    student: PerfilAluno,
    mode: str,
    count_easy: int = 2,
    count_medium: int = 2,
    count_stretch: int = 1,
    duration_minutes: int = 90,
    rating_low: int | None = None,
    rating_high: int | None = None,
    custom_count: int | None = None,
) -> tuple[dict, list[dict], dict]:
    baseline = get_baseline_ac(student)
    custom_mode = (
        rating_low is not None
        and rating_high is not None
        and custom_count is not None
    )
    if custom_mode:
        custom_low = _clamp_int(min(int(rating_low), int(rating_high)), 0, 4000)
        custom_high = _clamp_int(max(int(rating_low), int(rating_high)), 0, 4000)
        ranges = {
            "easy": (custom_low, custom_high),
            "medium": (custom_low, custom_high),
            "stretch": (custom_low, custom_high),
        }
    else:
        ranges = get_ac_ladder_ranges(mode, baseline, duration_minutes=duration_minutes)

    pool = _build_problem_pool("AC", contest_categories=AC_TRAIN_CATEGORIES)
    solved_urls = set(
        ScoreEvent.objects.filter(aluno=student, platform="AC").values_list("problem_url", flat=True)
    )
    training_solved_urls = _training_solved_urls(student, "AC")
    blocked_urls = set(
        TrainingBlockedProblem.objects.filter(aluno=student, platform="AC").values_list("problem_url", flat=True)
    )
    queued_urls = set(
        TrainingQueueItem.objects.filter(aluno=student, platform="AC", status="QUEUED").values_list("problem_url", flat=True)
    )
    excluded = solved_urls | training_solved_urls | blocked_urls | queued_urls

    def in_range(p: dict, lo: int, hi: int) -> bool:
        r = p.get("rating")
        if r is None:
            return False
        return lo <= int(r) <= hi

    candidates = [p for p in pool if p.get("problem_url") and p["problem_url"] not in excluded and p.get("rating") is not None]

    def pick(lo: int, hi: int, n: int) -> list[dict]:
        ranked = [p for p in candidates if in_range(p, lo, hi)]
        # Prefer closer to the center of the band.
        center = (lo + hi) / 2.0
        ranked.sort(key=lambda p: abs(float(p["rating"]) - center))
        return ranked[:n]

    selected = []
    easy: list[dict] = []
    medium: list[dict] = []
    stretch: list[dict] = []
    if custom_mode:
        desired_total = max(0, int(custom_count or 0))
        medium = pick(*ranges["medium"], desired_total)
        selected += medium
    else:
        easy = pick(*ranges["easy"], count_easy)
        selected += easy
        selected_urls = {p["problem_url"] for p in selected}
        medium = [p for p in pick(*ranges["medium"], count_medium + 4) if p["problem_url"] not in selected_urls][:count_medium]
        selected += medium
        selected_urls = {p["problem_url"] for p in selected}
        stretch = [p for p in pick(*ranges["stretch"], count_stretch + 4) if p["problem_url"] not in selected_urls][:count_stretch]
        selected += stretch

    # Fallback: if we couldn't fill the desired amount due to missing ratings,
    # use unrated problems from the same pool and estimate their rating near the medium band.
    desired_total = int(custom_count or 0) if custom_mode else (count_easy + count_medium + count_stretch)
    if len(selected) < desired_total:
        fallback_candidates = [
            p for p in pool
            if p.get("problem_url")
            and p.get("rating") is None
            and p["problem_url"] not in excluded
        ]
        fallback_needed = desired_total - len(selected)
        fallback_center = int((ranges["medium"][0] + ranges["medium"][1]) / 2)
        for p in fallback_candidates[:fallback_needed]:
            p["rating"] = fallback_center
            p["estimated"] = True
            selected.append(p)

    def duration_label(rating: int) -> str:
        delta = rating - baseline
        if delta <= -150:
            return "curto"
        if delta <= 200:
            return "médio"
        return "longo"

    suggestions = []
    for p in selected:
        rating = int(p["rating"])
        delta = rating - baseline
        rel = f"{'+' if delta >= 0 else ''}{delta}"
        if custom_mode:
            why = f"Faixa personalizada: {rel} vs baseline."
            tier = "custom"
        elif p in easy:
            why = f"Aquecimento: {rel} vs baseline."
            tier = "easy"
        elif p in stretch:
            why = f"Desafio opcional: {rel} vs baseline."
            tier = "stretch"
        else:
            why = f"Zona de evolução: {rel} vs baseline."
            tier = "medium"

        suggestions.append({
            "platform": "AC",
            "problem_url": p["problem_url"],
            "title": p["title"],
            "contest_id": p.get("contest_id"),
            "index_label": p.get("index_label"),
            "rating": rating,
            "relative": rel,
            "duration": duration_label(rating),
            "why": why,
            "tier": tier,
        })

    # On-demand rating fetch for suggested AtCoder problems lacking CLIST rating
    for p in selected:
        if not p.get("problem_url"):
            continue
        if p.get("has_clist"):
            continue
        schedule_rating_job("AC", p["problem_url"], priority=0)

    meta = {"baseline": baseline}
    return ranges, suggestions, meta


def build_training_inventory(
    student: PerfilAluno,
    mode: str,
    duration_minutes: int = 90,
    *,
    max_attempts: int = 6,
) -> dict:
    """
    Inventory counters for the training page:
    - user-facing: how many problems are available in the current training bands.
    - admin-facing: global sync backlog for the training pools.
    """
    now = timezone.now()
    cf_zone = get_cf_training_zone(mode, get_baseline_cf(student), duration_minutes=duration_minutes)
    ac_ranges = get_ac_ladder_ranges(mode, get_baseline_ac(student), duration_minutes=duration_minutes)
    ac_low = min(ac_ranges["easy"][0], ac_ranges["medium"][0], ac_ranges["stretch"][0])
    ac_high = max(ac_ranges["easy"][1], ac_ranges["medium"][1], ac_ranges["stretch"][1])

    cf_rows = list(
        ContestProblem.objects.filter(
            platform="CF",
            contest__start_time__lte=now,
            contest__division__in=CF_TRAIN_DIVISIONS,
        ).values("problem_url", "cf_rating", "rating_status", "rating_attempts", "tags")
    )
    ac_rows = list(
        ContestProblem.objects.filter(
            platform="AC",
            contest__start_time__lte=now,
            contest__category__in=AC_TRAIN_CATEGORIES,
        ).values("problem_url", "cf_rating", "rating_status", "rating_attempts", "tags")
    )

    def index_rows(rows: list[dict]) -> dict[str, dict]:
        indexed: dict[str, dict] = {}
        for row in rows:
            url = row.get("problem_url")
            if not url:
                continue
            entry = indexed.setdefault(
                url,
                {
                    "cf_rating": None,
                    "pending": False,
                    "not_found": False,
                    "attempts": 0,
                    "has_tags": False,
                },
            )
            cf_rating = row.get("cf_rating")
            if cf_rating is not None:
                cf_rating = int(cf_rating)
                if entry["cf_rating"] is None or cf_rating > int(entry["cf_rating"]):
                    entry["cf_rating"] = cf_rating
            status = str(row.get("rating_status") or "")
            if status in RATING_PENDING_STATUSES:
                entry["pending"] = True
            if status == "NOT_FOUND":
                entry["not_found"] = True
            attempts = int(row.get("rating_attempts") or 0)
            if attempts > entry["attempts"]:
                entry["attempts"] = attempts
            if row.get("tags") and str(row.get("tags")).strip():
                entry["has_tags"] = True
        return indexed

    cf_index = index_rows(cf_rows)
    ac_index = index_rows(ac_rows)

    all_urls = list({*cf_index.keys(), *ac_index.keys()})
    cache_map = {
        row["problem_url"]: row
        for row in ProblemRatingCache.objects.filter(problem_url__in=all_urls).values(
            "problem_url",
            "effective_rating",
            "clist_rating",
        )
    }

    def rating_for(url: str, meta: dict) -> int | None:
        cache = cache_map.get(url)
        if cache and cache.get("effective_rating") is not None:
            return int(cache["effective_rating"])
        if meta.get("cf_rating") is not None:
            return int(meta["cf_rating"])
        if cache and cache.get("clist_rating") is not None:
            return int(cache["clist_rating"])
        return None

    def has_clist(url: str) -> bool:
        cache = cache_map.get(url)
        return bool(cache and cache.get("clist_rating") is not None)

    excluded_cf = set(
        ScoreEvent.objects.filter(aluno=student, platform="CF").values_list("problem_url", flat=True)
    ) | set(
        TrainingSessionItem.objects.filter(
            session__aluno=student,
            platform="CF",
            result__in=["SOLVED", "EDITORIAL"],
        ).values_list("problem_url", flat=True)
    ) | set(
        TrainingBlockedProblem.objects.filter(aluno=student, platform="CF").values_list("problem_url", flat=True)
    ) | set(
        TrainingQueueItem.objects.filter(aluno=student, platform="CF", status="QUEUED").values_list("problem_url", flat=True)
    )
    excluded_ac = set(
        ScoreEvent.objects.filter(aluno=student, platform="AC").values_list("problem_url", flat=True)
    ) | set(
        TrainingSessionItem.objects.filter(
            session__aluno=student,
            platform="AC",
            result__in=["SOLVED", "EDITORIAL"],
        ).values_list("problem_url", flat=True)
    ) | set(
        TrainingBlockedProblem.objects.filter(aluno=student, platform="AC").values_list("problem_url", flat=True)
    ) | set(
        TrainingQueueItem.objects.filter(aluno=student, platform="AC", status="QUEUED").values_list("problem_url", flat=True)
    )

    user_available_cf = 0
    user_pending_cf = 0
    for url, meta in cf_index.items():
        if url in excluded_cf or not meta.get("has_tags"):
            continue
        rating = rating_for(url, meta)
        if rating is None:
            continue
        if cf_zone.low <= rating <= cf_zone.high:
            user_available_cf += 1
            if meta.get("pending") and not has_clist(url):
                user_pending_cf += 1

    user_available_ac = 0
    user_pending_ac = 0
    for url, meta in ac_index.items():
        if url in excluded_ac:
            continue
        rating = rating_for(url, meta)
        if rating is None:
            continue
        if ac_low <= rating <= ac_high:
            user_available_ac += 1
            if meta.get("pending") and not has_clist(url):
                user_pending_ac += 1

    def summarize(indexed: dict[str, dict]) -> dict:
        total = len(indexed)
        ready = 0
        pending = 0
        missing = 0
        blocked = 0
        not_found = 0
        for url, meta in indexed.items():
            if rating_for(url, meta) is not None:
                ready += 1
            else:
                missing += 1
            if meta.get("pending") and not has_clist(url):
                pending += 1
            if meta.get("pending") and int(meta.get("attempts") or 0) >= max_attempts:
                blocked += 1
            if meta.get("not_found"):
                not_found += 1
        return {
            "total": total,
            "ready": ready,
            "pending": pending,
            "missing": missing,
            "blocked": blocked,
            "not_found": not_found,
        }

    cf_global = summarize(cf_index)
    ac_global = summarize(ac_index)
    global_total = {
        key: int(cf_global.get(key, 0)) + int(ac_global.get(key, 0))
        for key in ["total", "ready", "pending", "missing", "blocked", "not_found"]
    }

    return {
        "user": {
            "available_total": user_available_cf + user_available_ac,
            "available_cf": user_available_cf,
            "available_ac": user_available_ac,
            "pending_sync_total": user_pending_cf + user_pending_ac,
            "pending_sync_cf": user_pending_cf,
            "pending_sync_ac": user_pending_ac,
            "cf_zone_label": cf_zone.label,
            "ac_zone_label": f"{ac_low}–{ac_high}",
        },
        "admin": {
            "cf": cf_global,
            "ac": ac_global,
            "total": global_total,
            "max_attempts": int(max_attempts),
        },
    }
