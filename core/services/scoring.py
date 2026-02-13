from __future__ import annotations

from datetime import timedelta

from django.db.models import F, Q, Sum
from django.utils import timezone

from core.models import Contest, ContestProblem, ScoreEvent, Submissao, UserScoreAgg
from core.services.problem_urls import build_problem_url_from_fields, normalize_problem_url
from core.services.problem_ratings import get_or_schedule_problem_rating
from core.services.rating_stats import get_platform_percentile
from core.services.rating_conversion import convert_ac_to_cf


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
    # Regra atual: pontos = rating (escala CF-like).
    return int(round(max(0.0, float(raw_rating))))


def _resolve_contest_context(submission: Submissao, problem_url: str | None) -> tuple[Contest | None, bool]:
    contest = None
    if submission.contest_id:
        contest = Contest.objects.filter(
            platform=submission.plataforma,
            contest_id=submission.contest_id,
        ).first()
    if not contest and problem_url:
        contest_problem = ContestProblem.objects.select_related("contest").filter(
            problem_url=problem_url,
            contest__platform=submission.plataforma,
        ).first()
        if contest_problem:
            contest = contest_problem.contest

    if not contest or not contest.start_time or not contest.duration_seconds:
        return contest, False

    contest_end = contest.start_time + timedelta(seconds=contest.duration_seconds)
    solved_at = submission.submission_time
    in_contest = contest.start_time <= solved_at <= contest_end
    return contest, in_contest


def update_user_score_agg(aluno_id: int) -> None:
    totals = ScoreEvent.objects.filter(aluno_id=aluno_id).aggregate(
        points_cf=Sum("points_cf_raw"),
        points_ac=Sum("points_ac_raw"),
        points_general=Sum("points_general_norm"),
        points_general_cf=Sum("points_general_cf_equiv"),
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
            "points_total": totals.get("points_general_cf") or totals.get("points_general") or 0,
            "points_last_7d": points_last_7d or 0,
            "points_last_30d": points_last_30d or 0,
            "points_cf_raw_total": totals.get("points_cf") or 0,
            "points_ac_raw_total": totals.get("points_ac") or 0,
            "points_general_norm_total": totals.get("points_general") or 0,
            "points_general_cf_equiv_total": totals.get("points_general_cf") or 0,
        },
    )
    agg.save()


def apply_score_delta(
    aluno_id: int,
    platform: str,
    delta_cf: int,
    delta_ac: int,
    delta_general: int,
    delta_general_cf: int = 0,
) -> None:
    if not (delta_cf or delta_ac or delta_general or delta_general_cf):
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
        updates["points_general_norm_total"] = F("points_general_norm_total") + delta_general
    if delta_general_cf:
        updates["points_general_cf_equiv_total"] = F("points_general_cf_equiv_total") + delta_general_cf
        updates["points_total"] = F("points_total") + delta_general_cf

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

    if cache.cf_rating is None and submission.plataforma == "CF":
        cp = ContestProblem.objects.filter(problem_url=problem_url).only("cf_rating").first()
        if cp and cp.cf_rating is not None:
            cache.cf_rating = cp.cf_rating
            cache.effective_rating = cache.effective_rating or cp.cf_rating
            cache.rating_source = "cf" if cache.effective_rating == cp.cf_rating else cache.rating_source
            cache.save(update_fields=["cf_rating", "effective_rating", "rating_source"])

    raw_rating = cache.effective_rating if cache.effective_rating is not None else None
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

    rating_used_cf_equiv = None
    if raw_rating is not None:
        rating_used_cf_equiv = raw_rating if submission.plataforma == "CF" else convert_ac_to_cf(int(raw_rating))
    points_cf_raw = base_points if submission.plataforma == "CF" else 0
    points_ac_raw = (
        calculate_points(rating_used_cf_equiv)
        if submission.plataforma == "AC" and rating_used_cf_equiv is not None
        else 0
    )
    points_general_cf = calculate_points(rating_used_cf_equiv) if rating_used_cf_equiv is not None else 0

    contest, in_contest = _resolve_contest_context(submission, problem_url)
    bonus_multiplier = 1.10 if in_contest else 1.0
    if bonus_multiplier != 1.0:
        points_cf_raw = int(round(points_cf_raw * bonus_multiplier))
        points_ac_raw = int(round(points_ac_raw * bonus_multiplier))
        points_general_cf = int(round(points_general_cf * bonus_multiplier))

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
        points_general_cf_equiv=points_general_cf,
        rating_used_cf_equiv=rating_used_cf_equiv,
        points_awarded=points_general_cf or points_general,
        in_contest=in_contest,
        contest_platform=contest.platform if contest else submission.plataforma,
        contest_id=contest.contest_id if contest else submission.contest_id,
        bonus_multiplier=bonus_multiplier,
        reason="first_ac",
    )

    apply_score_delta(
        submission.aluno_id,
        submission.plataforma,
        points_cf_raw,
        points_ac_raw,
        points_general,
        points_general_cf,
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
    if cache.effective_rating is None:
        return

    base_points = calculate_points(cache.effective_rating)
    percentile = get_platform_percentile(
        platform,
        cache.effective_rating,
        cache_token=timezone.now().date().isoformat(),
    )
    unified_rating = None
    points_general = 0
    if percentile is not None:
        unified_rating = 1000.0 + 2000.0 * percentile
        points_general = calculate_points(unified_rating)

    rating_used_cf_equiv = cache.effective_rating if platform == "CF" else convert_ac_to_cf(int(cache.effective_rating))
    points_general_cf_base = calculate_points(rating_used_cf_equiv) if rating_used_cf_equiv is not None else 0

    for event in pending_events:
        previous_cf = event.points_cf_raw or 0
        previous_ac = event.points_ac_raw or 0
        previous_general = event.points_general_norm or 0
        previous_general_cf = event.points_general_cf_equiv or 0
        event.raw_rating = cache.effective_rating
        event.percentile = percentile
        event.unified_rating = unified_rating
        bonus_multiplier = event.bonus_multiplier or (1.10 if event.in_contest else 1.0)
        points_cf_raw = base_points if platform == "CF" else 0
        points_ac_raw = (
            calculate_points(rating_used_cf_equiv)
            if platform == "AC" and rating_used_cf_equiv is not None
            else 0
        )
        if bonus_multiplier != 1.0:
            points_cf_raw = int(round(points_cf_raw * bonus_multiplier))
            points_ac_raw = int(round(points_ac_raw * bonus_multiplier))
        points_general_cf = int(round(points_general_cf_base * bonus_multiplier))
        event.points_cf_raw = points_cf_raw
        event.points_ac_raw = points_ac_raw
        event.points_general_norm = points_general
        event.points_general_cf_equiv = points_general_cf
        event.rating_used_cf_equiv = rating_used_cf_equiv
        event.points_awarded = points_general_cf or points_general
        event.save(update_fields=[
            "raw_rating",
            "percentile",
            "unified_rating",
            "points_cf_raw",
            "points_ac_raw",
            "points_general_norm",
            "points_general_cf_equiv",
            "rating_used_cf_equiv",
            "points_awarded",
        ])
        apply_score_delta(
            event.aluno_id,
            platform,
            event.points_cf_raw - previous_cf,
            event.points_ac_raw - previous_ac,
            event.points_general_norm - previous_general,
            event.points_general_cf_equiv - previous_general_cf,
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
        previous_general_cf = event.points_general_cf_equiv or 0

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

        rating_used_cf_equiv = event.raw_rating if platform == "CF" else convert_ac_to_cf(int(event.raw_rating))
        points_cf_raw = base_points if platform == "CF" else 0
        points_ac_raw = (
            calculate_points(rating_used_cf_equiv)
            if platform == "AC" and rating_used_cf_equiv is not None
            else 0
        )
        points_general_cf = calculate_points(rating_used_cf_equiv) if rating_used_cf_equiv is not None else 0
        bonus_multiplier = event.bonus_multiplier or (1.10 if event.in_contest else 1.0)
        if bonus_multiplier != 1.0:
            points_cf_raw = int(round(points_cf_raw * bonus_multiplier))
            points_ac_raw = int(round(points_ac_raw * bonus_multiplier))
            points_general_cf = int(round(points_general_cf * bonus_multiplier))

        if (
            event.percentile != percentile
            or event.unified_rating != unified_rating
            or event.points_cf_raw != points_cf_raw
            or event.points_ac_raw != points_ac_raw
            or event.points_general_norm != points_general
            or event.points_general_cf_equiv != points_general_cf
        ):
            event.percentile = percentile
            event.unified_rating = unified_rating
            event.points_cf_raw = points_cf_raw
            event.points_ac_raw = points_ac_raw
            event.points_general_norm = points_general
            event.points_general_cf_equiv = points_general_cf
            event.rating_used_cf_equiv = rating_used_cf_equiv
            event.points_awarded = points_general_cf or points_general
            event.save(update_fields=[
                "percentile",
                "unified_rating",
                "points_cf_raw",
                "points_ac_raw",
                "points_general_norm",
                "points_general_cf_equiv",
                "rating_used_cf_equiv",
                "points_awarded",
            ])
            apply_score_delta(
                event.aluno_id,
                platform,
                points_cf_raw - previous_cf,
                points_ac_raw - previous_ac,
                points_general - previous_general,
                points_general_cf - previous_general_cf,
            )
