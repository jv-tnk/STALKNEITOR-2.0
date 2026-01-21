from celery import shared_task
from django.db.models import Max, Q, Sum
from django.utils import timezone
from datetime import timedelta

from .models import PerfilAluno, ProblemRatingCache, ScoreEvent, Submissao, UserScoreAgg
from .services.api_client import CodeforcesClient, AtCoderClient
from .services.clist_client import ClistClient
from .services.scoring import (
    process_submission_for_scoring,
    recalculate_points_for_platform,
    update_scores_for_problem_url,
)
from .services.rating_stats import compute_platform_stats
from .services.ranking import snapshot_rankings

@shared_task
def fetch_student_data(student_id):
    try:
        student = PerfilAluno.objects.get(id=student_id)

        # Codeforces
        cf_subs = []
        if student.handle_codeforces:
            last_cf = Submissao.objects.filter(
                aluno=student,
                plataforma='CF',
            ).aggregate(Max('submission_time'))['submission_time__max']
            cf_subs = CodeforcesClient.get_submissions(
                student.handle_codeforces,
                since=last_cf,
                max_count=1000,
            )
        count_cf = 0
        for sub in cf_subs:
            submission, created = Submissao.objects.update_or_create(
                plataforma='CF',
                external_id=sub['external_id'],
                defaults={
                    'aluno': student,
                    'plataforma': 'CF',
                    'contest_id': sub['contest_id'],
                    'problem_index': sub['problem_index'],
                    'problem_name': sub.get('problem_name', ''),
                    'tags': sub.get('tags', ''),
                    'verdict': sub['verdict'],
                    'submission_time': sub['submission_time']
                }
            )
            if created:
                process_submission_for_scoring(submission)
            count_cf += 1

        # AtCoder
        ac_subs = []
        if student.handle_atcoder:
            last_ac = Submissao.objects.filter(
                aluno=student,
                plataforma='AC',
            ).aggregate(Max('submission_time'))['submission_time__max']
            ac_subs = AtCoderClient.get_submissions(student.handle_atcoder, since=last_ac)
        count_ac = 0
        for sub in ac_subs:
            submission, created = Submissao.objects.update_or_create(
                plataforma='AC',
                external_id=sub['external_id'],
                defaults={
                    'aluno': student,
                    'plataforma': 'AC',
                    'contest_id': sub['contest_id'],
                    'problem_index': sub['problem_index'],
                    'problem_name': sub.get('problem_name') or sub.get('problem_id', ''),
                    'tags': sub.get('tags', ''),
                    'verdict': sub['verdict'],
                    'submission_time': sub['submission_time']
                }
            )
            if created:
                process_submission_for_scoring(submission)
            count_ac += 1
        
        # Update cache metrics
        total = Submissao.objects.filter(aluno=student, verdict__in=['OK', 'AC']).count()
        student.total_solved = total
        _refresh_student_ratings(student)
        student.save(update_fields=['total_solved'])
        
        return f"Updated {student.user.username}: {count_cf} CF, {count_ac} AC submissions synced. Total solved: {total}"
        
    except PerfilAluno.DoesNotExist:
        return f"Student profile with ID {student_id} not found."
    except Exception as e:
        return f"Error updating student {student_id}: {str(e)}"


@shared_task
def sync_all_students():
    student_ids = list(PerfilAluno.objects.values_list('id', flat=True))
    for student_id in student_ids:
        fetch_student_data.delay(student_id)

    return f"Queued sync for {len(student_ids)} students."


def _refresh_student_ratings(student, min_interval_hours=12):
    now = timezone.now()
    refresh_delta = timedelta(hours=min_interval_hours)
    updated_fields = []

    if student.handle_codeforces:
        if not student.cf_rating_updated_at or now - student.cf_rating_updated_at > refresh_delta:
            info = CodeforcesClient.get_user_info(student.handle_codeforces)
            if info:
                student.cf_rating_current = info.get("rating")
                student.cf_rating_max = info.get("max_rating")
                student.cf_rating_updated_at = now
                updated_fields.extend([
                    "cf_rating_current",
                    "cf_rating_max",
                    "cf_rating_updated_at",
                ])

    if student.handle_atcoder:
        if not student.ac_rating_updated_at or now - student.ac_rating_updated_at > refresh_delta:
            info = AtCoderClient.get_user_info(student.handle_atcoder)
            if info:
                student.ac_rating_current = info.get("rating")
                student.ac_rating_max = info.get("max_rating")
                student.ac_rating_updated_at = now
                updated_fields.extend([
                    "ac_rating_current",
                    "ac_rating_max",
                    "ac_rating_updated_at",
                ])

    if updated_fields:
        student.save(update_fields=updated_fields)

    return updated_fields

@shared_task
def refresh_student_ratings(student_id):
    try:
        student = PerfilAluno.objects.get(id=student_id)
    except PerfilAluno.DoesNotExist:
        return f"Student profile with ID {student_id} not found."

    updated_fields = _refresh_student_ratings(student)
    if updated_fields:
        return f"Updated ratings for {student.user.username}."
    return f"No rating refresh needed for {student.user.username}."


@shared_task
def refresh_all_ratings():
    student_ids = list(PerfilAluno.objects.values_list('id', flat=True))
    for student_id in student_ids:
        refresh_student_ratings.delay(student_id)

    return f"Queued rating refresh for {len(student_ids)} students."


@shared_task(bind=True, max_retries=5)
def refresh_problem_rating_cache(self, cache_id, problem_name=None):
    try:
        cache = ProblemRatingCache.objects.get(id=cache_id)
    except ProblemRatingCache.DoesNotExist:
        return "Cache entry not found."

    result = ClistClient.fetch_problem_rating(
        cache.platform,
        cache.problem_url,
        problem_name=problem_name,
    )
    status = result.get("status")

    cache.rating_source = "clist_api_v4"
    cache.rating_fetched_at = timezone.now()

    if status == "OK":
        cache.clist_problem_id = str(result.get("problem_id") or "")
        cache.clist_rating = result.get("rating")
        cache.status = "OK"
        cache.save(update_fields=[
            "clist_problem_id",
            "clist_rating",
            "rating_fetched_at",
            "rating_source",
            "status",
        ])
        update_scores_for_problem_url(cache.platform, cache.problem_url)
        return "OK"

    if status == "NOT_FOUND":
        cache.status = "NOT_FOUND"
        cache.save(update_fields=["status", "rating_fetched_at", "rating_source"])
        return "NOT_FOUND"

    cache.status = "TEMP_FAIL"
    cache.save(update_fields=["status", "rating_fetched_at", "rating_source"])

    countdown = min(300, 2 ** self.request.retries)
    raise self.retry(countdown=countdown)


@shared_task
def recompute_rating_stats():
    for platform in ("CF", "AC"):
        stats = compute_platform_stats(platform)
        if stats:
            recalculate_points_for_platform(platform)

    return "Rating stats updated."


@shared_task
def recompute_score_windows():
    now = timezone.now()
    window_7d = now - timedelta(days=7)
    window_30d = now - timedelta(days=30)
    season_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if season_start.month == 12:
        season_end = season_start.replace(year=season_start.year + 1, month=1)
    else:
        season_end = season_start.replace(month=season_start.month + 1)

    UserScoreAgg.objects.update(
        points_last_7d=0,
        points_last_30d=0,
        points_cf_7d=0,
        points_ac_7d=0,
        points_general_7d=0,
        points_cf_30d=0,
        points_ac_30d=0,
        points_general_30d=0,
        season_points_cf_raw=0,
        season_points_ac_raw=0,
        season_points_general_norm=0,
    )

    rows = ScoreEvent.objects.filter(
        solved_at__gte=window_30d
    ).values("aluno_id").annotate(
        points_cf_30=Sum("points_cf_raw"),
        points_ac_30=Sum("points_ac_raw"),
        points_general_30=Sum("points_general_norm"),
        points_cf_7=Sum("points_cf_raw", filter=Q(solved_at__gte=window_7d)),
        points_ac_7=Sum("points_ac_raw", filter=Q(solved_at__gte=window_7d)),
        points_general_7=Sum("points_general_norm", filter=Q(solved_at__gte=window_7d)),
    )

    for row in rows:
        UserScoreAgg.objects.update_or_create(
            aluno_id=row["aluno_id"],
            defaults={
                "points_last_7d": row["points_general_7"] or 0,
                "points_last_30d": row["points_general_30"] or 0,
                "points_cf_7d": row["points_cf_7"] or 0,
                "points_ac_7d": row["points_ac_7"] or 0,
                "points_general_7d": row["points_general_7"] or 0,
                "points_cf_30d": row["points_cf_30"] or 0,
                "points_ac_30d": row["points_ac_30"] or 0,
                "points_general_30d": row["points_general_30"] or 0,
            },
        )

    season_rows = ScoreEvent.objects.filter(
        solved_at__gte=season_start,
        solved_at__lt=season_end,
    ).values("aluno_id").annotate(
        points_cf=Sum("points_cf_raw"),
        points_ac=Sum("points_ac_raw"),
        points_general=Sum("points_general_norm"),
    )

    for row in season_rows:
        UserScoreAgg.objects.update_or_create(
            aluno_id=row["aluno_id"],
            defaults={
                "season_points_cf_raw": row["points_cf"] or 0,
                "season_points_ac_raw": row["points_ac"] or 0,
                "season_points_general_norm": row["points_general"] or 0,
            },
        )

    return "Score windows updated."


@shared_task
def snapshot_rankings_task():
    snapshot_rankings()
    return "Ranking snapshots created."
