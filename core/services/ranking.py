from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, F, IntegerField, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.db.models.functions import TruncDate

from core.models import CompetitorGroup, PerfilAluno, ScoreEvent, UserRankSnapshot
from core.services.season import get_active_season_range


TIERS = [
    {"name": "Bronze", "min": 0, "max": 2000, "color": "from-amber-500 to-yellow-400"},
    {"name": "Prata", "min": 2000, "max": 6000, "color": "from-slate-300 to-slate-500"},
    {"name": "Ouro", "min": 6000, "max": 12000, "color": "from-yellow-400 to-amber-500"},
    {"name": "Platina", "min": 12000, "max": 20000, "color": "from-cyan-400 to-sky-500"},
]


@dataclass
class RankingRow:
    aluno: PerfilAluno
    points: int
    points_cf: int
    points_ac: int
    weekly_points: int
    rank: int
    delta: int
    tier_name: str
    tier_next: str | None
    tier_progress: int
    tier_range: str
    tier_color: str
    points_to_next: int
    points_to_above: int | None = None
    activity_days: int = 0
    activity_solves: int = 0
    rating_updated_at: datetime | None = None


def _tier_for_points(points: int) -> tuple[str, str | None, int, str, str, int]:
    for i, tier in enumerate(TIERS):
        if points < tier["max"] or i == len(TIERS) - 1:
            next_tier = TIERS[i + 1]["name"] if i + 1 < len(TIERS) else None
            next_min = TIERS[i + 1]["min"] if i + 1 < len(TIERS) else None
            if tier["max"] <= tier["min"]:
                progress = 100
            else:
                progress = int(
                    max(0, min(1, (points - tier["min"]) / (tier["max"] - tier["min"]))) * 100
                )
            tier_range = f"{tier['min']}–{tier['max']}"
            points_to_next = max(0, (next_min or 0) - points) if next_min else 0
            return tier["name"], next_tier, progress, tier_range, tier["color"], points_to_next
    last = TIERS[-1]
    return last["name"], None, 100, f"{last['min']}+", last["color"], 0


def tier_for_points(points: int) -> tuple[str, str | None, int, str, str, int]:
    return _tier_for_points(points)


def _base_queryset(scope: str, turma_id: int | None) -> PerfilAluno:
    villain_user_ids = list(
        CompetitorGroup.objects.filter(is_villain=True)
        .values_list("users__id", flat=True)
        .exclude(users__id__isnull=True)
        .distinct()
    )
    qs = (
        PerfilAluno.objects.select_related("user", "turma")
        .only(
            "id",
            "user_id",
            "turma_id",
            "handle_codeforces",
            "handle_atcoder",
            "cf_rating_current",
            "ac_rating_current",
            "cf_rating_updated_at",
            "ac_rating_updated_at",
            "user__id",
            "user__username",
            "turma__id",
            "turma__nome",
        )
    )
    if villain_user_ids:
        qs = qs.exclude(user_id__in=villain_user_ids)
    if scope == "turma" and turma_id:
        qs = qs.filter(turma_id=turma_id)
    return qs


def _get_points_annotation(category: str, window: str) -> tuple[Value, dict]:
    annotations = {}

    if window == "all":
        general_field = "points_general_cf_equiv_total"
        cf_field = "points_cf_raw_total"
        ac_field = "points_ac_raw_total"
    elif window == "7d":
        general_field = "points_general_cf_equiv_7d"
        cf_field = "points_cf_7d"
        ac_field = "points_ac_7d"
    elif window == "30d":
        general_field = "points_general_cf_equiv_30d"
        cf_field = "points_cf_30d"
        ac_field = "points_ac_30d"
    else:
        general_field = "season_points_general_cf_equiv"
        cf_field = "season_points_cf_raw"
        ac_field = "season_points_ac_raw"

    if category == "overall":
        points_field = general_field
    elif category == "cf":
        points_field = cf_field
    else:
        points_field = ac_field

    annotations["points"] = Coalesce(
        F(f"score_agg__{points_field}"), Value(0), output_field=IntegerField()
    )
    annotations["points_cf"] = Coalesce(
        F(f"score_agg__{cf_field}"), Value(0), output_field=IntegerField()
    )
    annotations["points_ac"] = Coalesce(
        F(f"score_agg__{ac_field}"), Value(0), output_field=IntegerField()
    )

    return annotations["points"], annotations


def _get_weekly_annotation(category: str) -> dict:
    annotations = {}
    if category == "overall":
        field = "points_general_cf_equiv_7d"
    else:
        field = "points_cf_7d" if category == "cf" else "points_ac_7d"
    annotations["weekly_points"] = Coalesce(
        F(f"score_agg__{field}"), Value(0), output_field=IntegerField()
    )
    return annotations


def build_ranking(
    category: str = "overall",
    window: str = "all",
    scope: str = "global",
    turma_id: int | None = None,
) -> list[RankingRow]:
    qs = _base_queryset(scope, turma_id)

    _, points_annotation = _get_points_annotation(category, window)
    weekly_annotation = _get_weekly_annotation(category)

    qs = qs.annotate(**points_annotation, **weekly_annotation)
    qs = qs.order_by("-points", "user__username")

    rows = []
    for idx, aluno in enumerate(qs, start=1):
        points = int(getattr(aluno, "points", 0) or 0)
        weekly_points = int(getattr(aluno, "weekly_points", 0) or 0)
        points_cf = int(getattr(aluno, "points_cf", 0) or 0)
        points_ac = int(getattr(aluno, "points_ac", 0) or 0)
        tier_name, next_tier, progress, tier_range, tier_color, points_to_next = _tier_for_points(points)
        rows.append(
            RankingRow(
                aluno=aluno,
                points=points,
                points_cf=points_cf,
                points_ac=points_ac,
                weekly_points=weekly_points,
                rank=idx,
                delta=0,
                tier_name=tier_name,
                tier_next=next_tier,
                tier_progress=progress,
                tier_range=tier_range,
                tier_color=tier_color,
                points_to_next=points_to_next,
            )
        )

    return rows


def _build_percentile_map(values: list[tuple[int, int]]) -> dict[int, float]:
    if not values:
        return {}
    values = sorted(values, key=lambda item: item[1])
    total = len(values)
    if total == 1:
        return {values[0][0]: 1.0}

    return {aluno_id: idx / float(total - 1) for idx, (aluno_id, _) in enumerate(values)}


def build_rating_ranking(
    category: str = "overall",
    scope: str = "global",
    turma_id: int | None = None,
) -> list[RankingRow]:
    qs = list(_base_queryset(scope, turma_id))
    if not qs:
        return []

    cf_values = [(aluno.id, aluno.cf_rating_current) for aluno in qs if aluno.cf_rating_current]
    ac_values = [(aluno.id, aluno.ac_rating_current) for aluno in qs if aluno.ac_rating_current]
    cf_percentiles = _build_percentile_map(cf_values)
    ac_percentiles = _build_percentile_map(ac_values)

    rows = []
    for aluno in qs:
        cf_rating = aluno.cf_rating_current or 0
        ac_rating = aluno.ac_rating_current or 0
        p_cf = cf_percentiles.get(aluno.id)
        p_ac = ac_percentiles.get(aluno.id)
        percentiles = [p for p in (p_cf, p_ac) if p is not None]

        if category == "overall":
            if percentiles:
                avg_percentile = sum(percentiles) / len(percentiles)
                points = int(round(1000 + 2000 * avg_percentile))
            else:
                points = 0
        elif category == "cf":
            points = cf_rating
        else:
            points = ac_rating

        updated_at = None
        if aluno.cf_rating_updated_at and aluno.ac_rating_updated_at:
            updated_at = max(aluno.cf_rating_updated_at, aluno.ac_rating_updated_at)
        else:
            updated_at = aluno.cf_rating_updated_at or aluno.ac_rating_updated_at

        tier_name, next_tier, progress, tier_range, tier_color, points_to_next = _tier_for_points(points)
        rows.append(
            RankingRow(
                aluno=aluno,
                points=points,
                points_cf=cf_rating,
                points_ac=ac_rating,
                weekly_points=0,
                rank=0,
                delta=0,
                tier_name=tier_name,
                tier_next=next_tier,
                tier_progress=progress,
                tier_range=tier_range,
                tier_color=tier_color,
                points_to_next=points_to_next,
                rating_updated_at=updated_at,
            )
        )

    def _rating_tiebreaker(row: RankingRow) -> int:
        if category == "cf":
            return row.points_cf
        if category == "ac":
            return row.points_ac
        return row.points_cf + row.points_ac

    rows.sort(
        key=lambda row: (-row.points, -_rating_tiebreaker(row), row.aluno.user.username),
    )
    for idx, row in enumerate(rows, start=1):
        row.rank = idx
    return rows


def build_activity_ranking(
    category: str = "overall",
    window: str = "season",
    scope: str = "global",
    turma_id: int | None = None,
) -> list[RankingRow]:
    qs = _base_queryset(scope, turma_id)

    now = timezone.now()
    window_start = None
    window_end = None
    if window == "7d":
        window_start = now - timedelta(days=7)
    elif window == "30d":
        window_start = now - timedelta(days=30)
    elif window == "season":
        _, season_start, season_end = get_active_season_range()
        if season_start and season_end:
            window_start = season_start
            window_end = season_end
        else:
            window_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if window_start.month == 12:
                window_end = window_start.replace(year=window_start.year + 1, month=1)
            else:
                window_end = window_start.replace(month=window_start.month + 1)

    event_filter = Q(score_events__isnull=False)
    if category == "cf":
        event_filter &= Q(score_events__platform="CF")
    elif category == "ac":
        event_filter &= Q(score_events__platform="AC")

    if window_start:
        event_filter &= Q(score_events__solved_at__gte=window_start)
    if window_end:
        event_filter &= Q(score_events__solved_at__lt=window_end)

    qs = qs.annotate(
        activity_days=Count(TruncDate("score_events__solved_at"), filter=event_filter, distinct=True),
        activity_solves=Count("score_events", filter=event_filter),
    )

    if category == "overall":
        cf_filter = Q(score_events__platform="CF")
        ac_filter = Q(score_events__platform="AC")
        if window_start:
            cf_filter &= Q(score_events__solved_at__gte=window_start)
            ac_filter &= Q(score_events__solved_at__gte=window_start)
        if window_end:
            cf_filter &= Q(score_events__solved_at__lt=window_end)
            ac_filter &= Q(score_events__solved_at__lt=window_end)
        qs = qs.annotate(
            solves_cf=Count("score_events", filter=cf_filter),
            solves_ac=Count("score_events", filter=ac_filter),
        )
    else:
        qs = qs.annotate(
            solves_cf=Value(0, output_field=IntegerField()),
            solves_ac=Value(0, output_field=IntegerField()),
        )

    qs = qs.order_by("-activity_days", "-activity_solves", "user__username")

    rows = []
    for idx, aluno in enumerate(qs, start=1):
        points = int(getattr(aluno, "activity_days", 0) or 0)
        solves = int(getattr(aluno, "activity_solves", 0) or 0)
        solves_cf = int(getattr(aluno, "solves_cf", 0) or 0)
        solves_ac = int(getattr(aluno, "solves_ac", 0) or 0)
        tier_name, next_tier, progress, tier_range, tier_color, points_to_next = _tier_for_points(points)
        rows.append(
            RankingRow(
                aluno=aluno,
                points=points,
                points_cf=solves_cf,
                points_ac=solves_ac,
                weekly_points=solves,
                rank=idx,
                delta=0,
                tier_name=tier_name,
                tier_next=next_tier,
                tier_progress=progress,
                tier_range=tier_range,
                tier_color=tier_color,
                points_to_next=points_to_next,
                activity_days=points,
                activity_solves=solves,
            )
        )

    return rows


def load_previous_ranks(
    mode: str,
    source: str,
    window: str,
    scope: str,
) -> dict[int, int]:
    qs = UserRankSnapshot.objects.filter(
        mode=mode,
        source=source,
        window_key=window,
        scope_key=scope,
    )
    latest = qs.order_by("-snapshot_date").first()
    if not latest:
        return {}
    snapshots = qs.filter(snapshot_date=latest.snapshot_date)
    return {snap.aluno_id: snap.rank for snap in snapshots}


def build_ranking_with_delta(
    category: str = "overall",
    window: str = "all",
    scope: str = "global",
    turma_id: int | None = None,
) -> list[RankingRow]:
    rows = build_ranking(category, window, scope, turma_id)
    prev_ranks = load_previous_ranks("points", category, window, scope)

    for row in rows:
        prev_rank = prev_ranks.get(row.aluno.id)
        if prev_rank is not None:
            row.delta = prev_rank - row.rank

    return rows


def build_rating_ranking_with_delta(
    category: str = "overall",
    scope: str = "global",
    turma_id: int | None = None,
) -> list[RankingRow]:
    rows = build_rating_ranking(category, scope, turma_id)
    prev_ranks = load_previous_ranks("rating", category, "all", scope)
    for row in rows:
        prev_rank = prev_ranks.get(row.aluno.id)
        if prev_rank is not None:
            row.delta = prev_rank - row.rank
    return rows


def top_movers_last_7d(limit: int = 5) -> list[RankingRow]:
    rows = build_ranking_with_delta("overall", "7d", "global", None)
    since = timezone.now() - timedelta(days=7)
    activity = ScoreEvent.objects.filter(solved_at__gte=since).values("aluno_id").annotate(count=Count("id"))
    activity_map = {item["aluno_id"]: item["count"] for item in activity}
    filtered = [row for row in rows if activity_map.get(row.aluno.id, 0) >= 3 and row.delta > 0]
    filtered.sort(key=lambda r: (-r.delta, r.rank))
    return filtered[:limit]


def snapshot_rankings() -> None:
    today = timezone.localdate()
    scopes = ["global"]
    categories = ["overall", "cf", "ac"]
    windows = ["all", "7d", "30d", "season"]

    for scope in scopes:
        for category in categories:
            for window in windows:
                rows = build_ranking(category, window, scope, None)
                for row in rows:
                    UserRankSnapshot.objects.update_or_create(
                        aluno=row.aluno,
                        scope=_map_scope(scope),
                        turma=None,
                        category=_map_category(category),
                        window=_map_window(window),
                        snapshot_date=today,
                        defaults={
                            "rank": row.rank,
                            "points": row.points,
                            "mode": "points",
                            "source": category,
                            "window_key": window,
                            "scope_key": scope,
                            "value": row.points,
                        },
                    )

            rating_rows = build_rating_ranking(category, scope, None)
            for row in rating_rows:
                UserRankSnapshot.objects.update_or_create(
                    aluno=row.aluno,
                    scope=_map_scope(scope),
                    turma=None,
                    category=_map_category(category),
                    window=_map_window("all"),
                    snapshot_date=today,
                    defaults={
                        "rank": row.rank,
                        "points": row.points,
                        "mode": "rating",
                        "source": category,
                        "window_key": "all",
                        "scope_key": scope,
                        "value": row.points,
                    },
                )


def _map_category(category: str) -> str:
    return {"overall": "TOTAL", "cf": "CF", "ac": "AC"}.get(category, "TOTAL")


def _map_window(window: str) -> str:
    return {"all": "ALL", "7d": "7D", "30d": "30D", "season": "SEASON"}.get(window, "ALL")


def _map_scope(scope: str) -> str:
    return {"global": "GLOBAL", "turma": "TURMA"}.get(scope, "GLOBAL")
