from __future__ import annotations

from datetime import timedelta

from django.db.models import F, Q, Sum
from django.utils import timezone

from core.models import ScoreEvent, Submissao, UserScoreAgg
from core.services.problem_urls import build_problem_url_from_fields, normalize_problem_url
from core.services.problem_ratings import get_or_schedule_problem_rating
from core.services.rating_stats import get_platform_percentile


ACCEPTED_VERDICTS = {
    "CF": {"OK"},
    "AC": {"AC"},
}


def build_problem_url(submission: Submissao) -> str | None:
    return build_problem_url_from_fields(
        submission.plataforma,
        submission.contest_id,
        submission.problem_index,
        submission.problem_name,
    )


def is_accepted(submission: Submissao) -> bool:
    verdicts = ACCEPTED_VERDICTS.get(submission.plataforma, set())
    return submission.verdict in verdicts


def calculate_points(raw_rating: float | None) -> int:
    if raw_rating is None:
        return 0
    t = (raw_rating - 800.0) / 100.0
    t = max(0.0, min(25.0, t))
    base_points = 12 + 7 * (t ** 1.35)
    return int(round(base_points))


def update_user_score_agg(aluno_id: int) -> None:
    totals = ScoreEvent.objects.filter(aluno_id=aluno_id).aggregate(
        points_cf=Sum("points_cf_raw"),
        points_ac=Sum("points_ac_raw"),
        points_general=Sum("points_general_norm"),
    )
    now = timezone.now()
    window_7d = now - timedelta(days=7)
    window_30d = now - timedelta(days=30)
    points_last_7d = ScoreEvent.objects.filter(
        aluno_id=aluno_id,
        solved_at__gte=window_7d,
    ).aggregate(total=Sum("points_general_norm"))["total"]
    points_last_30d = ScoreEvent.objects.filter(
        aluno_id=aluno_id,
        solved_at__gte=window_30d,
    ).aggregate(total=Sum("points_general_norm"))["total"]

    agg, _ = UserScoreAgg.objects.update_or_create(
        aluno_id=aluno_id,
        defaults={
            "points_cf_total": totals.get("points_cf") or 0,
            "points_ac_total": totals.get("points_ac") or 0,
            "points_total": totals.get("points_general") or 0,
            "points_last_7d": points_last_7d or 0,
            "points_last_30d": points_last_30d or 0,
            "points_cf_raw_total": totals.get("points_cf") or 0,
            "points_ac_raw_total": totals.get("points_ac") or 0,
            "points_general_norm_total": totals.get("points_general") or 0,
        },
    )
    agg.save()


def apply_score_delta(
    aluno_id: int,
    platform: str,
    delta_cf: int,
    delta_ac: int,
    delta_general: int,
) -> None:
    if not (delta_cf or delta_ac or delta_general):
        return

    agg, _ = UserScoreAgg.objects.get_or_create(aluno_id=aluno_id)
    updates = {}
    if delta_cf:
        updates["points_cf_total"] = F("points_cf_total") + delta_cf
        updates["points_cf_raw_total"] = F("points_cf_raw_total") + delta_cf
    if delta_ac:
        updates["points_ac_total"] = F("points_ac_total") + delta_ac
        updates["points_ac_raw_total"] = F("points_ac_raw_total") + delta_ac
    if delta_general:
        updates["points_total"] = F("points_total") + delta_general
        updates["points_general_norm_total"] = F("points_general_norm_total") + delta_general

    if updates:
        UserScoreAgg.objects.filter(pk=agg.pk).update(**updates)


def process_submission_for_scoring(submission: Submissao) -> ScoreEvent | None:
    if not is_accepted(submission):
        return None

    problem_url = build_problem_url(submission)
    if not problem_url:
        return None

    existing = ScoreEvent.objects.filter(
        aluno=submission.aluno,
        platform=submission.plataforma,
        problem_url=problem_url,
        reason="first_ac",
    ).first()
    if existing:
        return None

    cache = get_or_schedule_problem_rating(
        submission.plataforma,
        problem_url,
        problem_name=submission.problem_name,
    )

    raw_rating = cache.clist_rating if cache.status == "OK" else None
    base_points = calculate_points(raw_rating)
    percentile = get_platform_percentile(
        submission.plataforma,
        raw_rating,
        cache_token=timezone.now().date().isoformat(),
    )
    unified_rating = None
    points_general = 0
    if percentile is not None:
        unified_rating = 1000.0 + 2000.0 * percentile
        points_general = calculate_points(unified_rating)

    points_cf_raw = base_points if submission.plataforma == "CF" else 0
    points_ac_raw = base_points if submission.plataforma == "AC" else 0

    event = ScoreEvent.objects.create(
        aluno=submission.aluno,
        platform=submission.plataforma,
        submission=submission,
        problem_url=problem_url,
        solved_at=submission.submission_time,
        raw_rating=raw_rating,
        percentile=percentile,
        unified_rating=unified_rating,
        points_cf_raw=points_cf_raw,
        points_ac_raw=points_ac_raw,
        points_general_norm=points_general,
        points_awarded=points_general,
        reason="first_ac",
    )

    apply_score_delta(
        submission.aluno_id,
        submission.plataforma,
        points_cf_raw,
        points_ac_raw,
        points_general,
    )

    return event


def update_scores_for_problem_url(platform: str, problem_url: str) -> None:
    normalized_url = normalize_problem_url(problem_url)
    if not normalized_url:
        return

    pending_events = ScoreEvent.objects.filter(
        platform=platform,
        problem_url=normalized_url,
        raw_rating__isnull=True,
    )
    if not pending_events.exists():
        return

    cache = get_or_schedule_problem_rating(platform, normalized_url)
    if cache.status != "OK" or cache.clist_rating is None:
        return

    base_points = calculate_points(cache.clist_rating)
    percentile = get_platform_percentile(
        platform,
        cache.clist_rating,
        cache_token=timezone.now().date().isoformat(),
    )
    unified_rating = None
    points_general = 0
    if percentile is not None:
        unified_rating = 1000.0 + 2000.0 * percentile
        points_general = calculate_points(unified_rating)

    for event in pending_events:
        previous_cf = event.points_cf_raw or 0
        previous_ac = event.points_ac_raw or 0
        previous_general = event.points_general_norm or 0
        event.raw_rating = cache.clist_rating
        event.percentile = percentile
        event.unified_rating = unified_rating
        event.points_cf_raw = base_points if platform == "CF" else 0
        event.points_ac_raw = base_points if platform == "AC" else 0
        event.points_general_norm = points_general
        event.points_awarded = points_general
        event.save(update_fields=[
            "raw_rating",
            "percentile",
            "unified_rating",
            "points_cf_raw",
            "points_ac_raw",
            "points_general_norm",
            "points_awarded",
        ])
        apply_score_delta(
            event.aluno_id,
            platform,
            event.points_cf_raw - previous_cf,
            event.points_ac_raw - previous_ac,
            event.points_general_norm - previous_general,
        )


def recalculate_points_for_platform(
    platform: str,
    aluno_id: int | None = None,
    cache_token: str | None = None,
) -> None:
    events = ScoreEvent.objects.filter(
        platform=platform,
        raw_rating__isnull=False,
    ).select_related("aluno")
    if aluno_id:
        events = events.filter(aluno_id=aluno_id)
    if not events.exists():
        return

    if cache_token is None:
        cache_token = timezone.now().date().isoformat()

    for event in events:
        previous_cf = event.points_cf_raw or 0
        previous_ac = event.points_ac_raw or 0
        previous_general = event.points_general_norm or 0

        base_points = calculate_points(event.raw_rating)
        percentile = get_platform_percentile(
            platform,
            event.raw_rating,
            cache_token=cache_token,
        )
        unified_rating = None
        points_general = 0
        if percentile is not None:
            unified_rating = 1000.0 + 2000.0 * percentile
            points_general = calculate_points(unified_rating)

        points_cf_raw = base_points if platform == "CF" else 0
        points_ac_raw = base_points if platform == "AC" else 0

        if (
            event.percentile != percentile
            or event.unified_rating != unified_rating
            or event.points_cf_raw != points_cf_raw
            or event.points_ac_raw != points_ac_raw
            or event.points_general_norm != points_general
        ):
            event.percentile = percentile
            event.unified_rating = unified_rating
            event.points_cf_raw = points_cf_raw
            event.points_ac_raw = points_ac_raw
            event.points_general_norm = points_general
            event.points_awarded = points_general
            event.save(update_fields=[
                "percentile",
                "unified_rating",
                "points_cf_raw",
                "points_ac_raw",
                "points_general_norm",
                "points_awarded",
            ])
            apply_score_delta(
                event.aluno_id,
                platform,
                points_cf_raw - previous_cf,
                points_ac_raw - previous_ac,
                points_general - previous_general,
            )
