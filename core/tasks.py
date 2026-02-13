import logging
import time
import json
import re

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, Q, Sum, Count
from django.utils import timezone
from datetime import timedelta
import redis

from .models import (
    Contest,
    ContestProblem,
    CodeforcesRatingChange,
    AtCoderRatingSnapshot,
    PerfilAluno,
    ProblemRatingCache,
    RatingFetchJob,
    ScoreEvent,
    Submissao,
    UserScoreAgg,
)
from .services.api_client import CodeforcesClient, AtCoderClient
from .services.clist_client import ClistClient
from .services.contest_catalog import (
    get_ac_contest_problems,
    get_ac_contests,
    get_cf_contest_problems,
    get_cf_contests,
)
from .services.contest_classification import (
    classify_atcoder_category,
    classify_codeforces_division,
)
from .services.problem_urls import build_problem_url_from_fields, normalize_problem_url
from .services.scoring import (
    process_submission_for_scoring,
    recalculate_points_for_platform,
    update_scores_for_problem_url,
)
from .services.rating_stats import compute_platform_stats
from .services.ranking import snapshot_rankings
from .services.rating_conversion import recompute_rating_conversion_ac_to_cf
from .services.season import get_active_season_range

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        _redis_client = redis.Redis.from_url(url)
    return _redis_client


def _acquire_lock(lock_key: str, ttl_seconds: int = 600) -> bool:
    try:
        client = _get_redis_client()
        return bool(client.set(lock_key, str(time.time()), nx=True, ex=ttl_seconds))
    except Exception:
        logger.exception("Lock failure for %s; continuing without lock.", lock_key)
        return True


def _set_task_health(task_name: str, payload: dict, ttl_seconds: int = 2 * 24 * 3600) -> None:
    data = {
        "task": task_name,
        "at": timezone.now().isoformat(),
        **(payload or {}),
    }
    try:
        _get_redis_client().set(
            f"task_health:{task_name}",
            json.dumps(data, default=str),
            ex=ttl_seconds,
        )
    except Exception:
        logger.exception("Failed to store task health for %s", task_name)


def _sync_backoff_minutes(attempts: int) -> int:
    return int(min(360, (2 ** max(0, attempts)) * 5))


def _is_cache_fresh(cache: ProblemRatingCache) -> bool:
    ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
    if not cache.rating_fetched_at:
        return False
    return cache.rating_fetched_at >= timezone.now() - timedelta(hours=ttl_hours)


def _mark_rating_fetch_attempt(platform: str, problem_url: str, attempt_at=None) -> int:
    """
    Count attempts only when we actually try a CLIST fetch.
    """
    now = attempt_at or timezone.now()
    return ContestProblem.objects.filter(
        platform=platform,
        problem_url=problem_url,
        rating_status__in=["MISSING", "TEMP_FAIL", "QUEUED"],
    ).update(
        rating_status="QUEUED",
        rating_last_requested_at=now,
        rating_attempts=F("rating_attempts") + 1,
    )


def _parse_cf_problem_key(problem_url: str | None) -> str | None:
    if not problem_url:
        return None
    patterns = [
        r"^https?://codeforces\.com/contest/(?P<contest_id>\d+)/problem/(?P<index>[A-Za-z][A-Za-z0-9]*)/?$",
        r"^https?://codeforces\.com/problemset/problem/(?P<contest_id>\d+)/(?P<index>[A-Za-z][A-Za-z0-9]*)/?$",
        r"^https?://codeforces\.com/gym/(?P<contest_id>\d+)/problem/(?P<index>[A-Za-z][A-Za-z0-9]*)/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, problem_url, flags=re.IGNORECASE)
        if match:
            return f"{match.group('contest_id')}:{match.group('index').upper()}"
    return None


def _extract_cf_round_number(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(r"Round\s+(\d+)", title, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _find_cf_split_round_alias(problem_url: str) -> dict | None:
    """
    Some split rounds (Div.1/Div.2) share statements but CLIST keeps rating only on one URL.
    Try sibling contests with same start_time and same problem name as a safe alias.
    """
    cp = (
        ContestProblem.objects.select_related("contest")
        .filter(platform="CF", problem_url=problem_url)
        .first()
    )
    if not cp or not cp.name or not cp.contest or not cp.contest.start_time:
        return None

    round_number = _extract_cf_round_number(cp.contest.title)
    siblings = (
        ContestProblem.objects.select_related("contest")
        .filter(
            platform="CF",
            name=cp.name,
            contest__start_time=cp.contest.start_time,
        )
        .exclude(problem_url=problem_url)
    )

    if round_number:
        siblings = siblings.filter(contest__title__icontains=f"Round {round_number}")

    for sibling in siblings:
        sibling_cache = ProblemRatingCache.objects.filter(problem_url=sibling.problem_url).first()
        sibling_rating = None
        sibling_problem_id = None
        if sibling_cache and sibling_cache.effective_rating is not None:
            sibling_rating = int(sibling_cache.effective_rating)
            sibling_problem_id = sibling_cache.clist_problem_id
        elif sibling.cf_rating is not None:
            sibling_rating = int(sibling.cf_rating)

        if sibling_rating is not None:
            return {
                "rating": sibling_rating,
                "source_problem_url": sibling.problem_url,
                "source_problem_id": sibling_problem_id,
                "source_contest_id": sibling.contest.contest_id,
            }

    return None


def _find_cf_split_round_alias_via_clist_name(problem_url: str) -> dict | None:
    cp = (
        ContestProblem.objects.select_related("contest")
        .filter(platform="CF", problem_url=problem_url)
        .first()
    )
    if not cp or not cp.name or not cp.contest or not cp.contest.start_time:
        return None

    sibling_contests = Contest.objects.filter(
        platform="CF",
        start_time=cp.contest.start_time,
    )
    round_number = _extract_cf_round_number(cp.contest.title)
    if round_number:
        sibling_contests = sibling_contests.filter(title__icontains=f"Round {round_number}")
    allowed_contest_ids = {
        str(cid)
        for cid in sibling_contests.values_list("contest_id", flat=True)
    }
    if not allowed_contest_ids:
        allowed_contest_ids = {str(cp.contest.contest_id)}

    params = {
        "name": cp.name,
        "resource__regex": "codeforces",
        "format": "json",
    }
    params.update(ClistClient._auth_params())
    result = ClistClient._request(params)
    if result.get("status") == "TEMP_FAIL":
        return None

    objects = (result.get("payload") or {}).get("objects") or []
    for obj in objects:
        rating = obj.get("rating")
        parsed = _parse_cf_problem_key(obj.get("url") or "")
        if rating is None or not parsed or ":" not in parsed:
            continue
        contest_id, _index = parsed.split(":", 1)
        if contest_id not in allowed_contest_ids:
            continue
        return {
            "rating": int(rating),
            "source_problem_url": obj.get("url"),
            "source_problem_id": str(obj.get("id") or ""),
            "source_contest_id": contest_id,
        }

    return None


def _heal_cf_split_round_aliases(limit: int = 400) -> dict:
    now = timezone.now()
    candidates = list(
        ContestProblem.objects.select_related("contest")
        .filter(
            platform="CF",
            rating_status__in=["MISSING", "TEMP_FAIL", "NOT_FOUND"],
        )
        .exclude(name__isnull=True)
        .exclude(name="")
        .order_by("-contest__start_time")[:limit]
    )
    if not candidates:
        return {"healed": 0, "contests_touched": 0}

    healed = 0
    touched_contests: set[int] = set()
    for cp in candidates:
        alias = _find_cf_split_round_alias(cp.problem_url)
        if not alias or alias.get("rating") is None:
            continue
        cache, _ = ProblemRatingCache.objects.get_or_create(
            problem_url=cp.problem_url,
            defaults={"platform": "CF", "status": "TEMP_FAIL"},
        )
        cache.platform = "CF"
        cache.clist_problem_id = str(alias.get("source_problem_id") or "")
        cache.clist_rating = int(alias["rating"])
        cache.status = "OK"
        cache.rating_fetched_at = now
        _update_effective_rating(cache)
        cache.save(update_fields=[
            "platform",
            "clist_problem_id",
            "clist_rating",
            "status",
            "rating_fetched_at",
            "rating_source",
            "effective_rating",
        ])

        ContestProblem.objects.filter(id=cp.id).update(
            rating_status="OK",
            rating_last_ok_at=now,
            rating_attempts=0,
        )
        update_scores_for_problem_url("CF", cp.problem_url)
        touched_contests.add(cp.contest_id)
        healed += 1

    if touched_contests:
        _refresh_contest_rating_summary(list(touched_contests))
    return {"healed": healed, "contests_touched": len(touched_contests)}


def _heal_conflicting_cf_cache_entries(max_problem_ids: int = 50) -> dict:
    duplicate_problem_ids = list(
        ProblemRatingCache.objects.filter(
            platform="CF",
            status="OK",
        )
        .exclude(clist_problem_id__isnull=True)
        .exclude(clist_problem_id="")
        .values("clist_problem_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("clist_problem_id", flat=True)[:max_problem_ids]
    )
    if not duplicate_problem_ids:
        return {"conflicting_problem_ids": 0, "conflicting_urls": 0, "contests_touched": 0}

    conflicting_urls: set[str] = set()
    conflicting_ids: set[str] = set()
    for problem_id in duplicate_problem_ids:
        entries = list(
            ProblemRatingCache.objects.filter(
                platform="CF",
                clist_problem_id=problem_id,
            ).only("problem_url", "id")
        )
        entry_urls = [entry.problem_url for entry in entries if entry.problem_url]
        cp_rows = list(
            ContestProblem.objects.select_related("contest")
            .filter(platform="CF", problem_url__in=entry_urls)
        )
        safe_split_alias = False
        if cp_rows and len(cp_rows) == len(entry_urls):
            names = {(cp.name or "").strip().lower() for cp in cp_rows}
            start_times = {cp.contest.start_time for cp in cp_rows if cp.contest and cp.contest.start_time}
            # Valid mirrored split-round case: same statement, same start time, different contest URLs.
            if len(names) == 1 and len(start_times) == 1:
                safe_split_alias = True
        if safe_split_alias:
            continue

        keys = {_parse_cf_problem_key(entry.problem_url) for entry in entries}
        keys.discard(None)
        # If the same CLIST problem id points to multiple distinct CF problems,
        # previous fallback-by-name likely contaminated the cache.
        if len(keys) > 1:
            conflicting_ids.add(problem_id)
            conflicting_urls.update(entry.problem_url for entry in entries if entry.problem_url)

    if not conflicting_urls:
        return {"conflicting_problem_ids": 0, "conflicting_urls": 0, "contests_touched": 0}

    caches_to_fix = list(
        ProblemRatingCache.objects.filter(
            platform="CF",
            problem_url__in=conflicting_urls,
        )
    )
    for cache in caches_to_fix:
        cache.clist_problem_id = None
        cache.clist_rating = None
        cache.status = "TEMP_FAIL"
        cache.rating_fetched_at = None
        _update_effective_rating(cache)
    if caches_to_fix:
        ProblemRatingCache.objects.bulk_update(
            caches_to_fix,
            [
                "clist_problem_id",
                "clist_rating",
                "status",
                "rating_fetched_at",
                "effective_rating",
                "rating_source",
            ],
        )

    ContestProblem.objects.filter(problem_url__in=conflicting_urls).update(
        rating_status="MISSING",
        rating_attempts=0,
        rating_last_requested_at=None,
        rating_last_ok_at=None,
    )
    contest_ids = list(
        ContestProblem.objects.filter(problem_url__in=conflicting_urls)
        .values_list("contest_id", flat=True)
        .distinct()
    )
    _refresh_contest_rating_summary(contest_ids)
    logger.warning(
        "Healed conflicting CF CLIST matches: ids=%s urls=%s contests=%s",
        len(conflicting_ids),
        len(conflicting_urls),
        len(contest_ids),
    )
    return {
        "conflicting_problem_ids": len(conflicting_ids),
        "conflicting_urls": len(conflicting_urls),
        "contests_touched": len(contest_ids),
    }


def _catalog_years_for_platform(platform: str, now) -> list[int]:
    keep_recent = max(1, int(getattr(settings, "CONTEST_CATALOG_KEEP_RECENT_YEARS", 2)))
    recent_years = [now.year - i for i in range(keep_recent)]
    include_all_time = bool(getattr(settings, "CONTEST_CATALOG_INCLUDE_ALL_TIME", True))
    if not include_all_time:
        return sorted(set(recent_years), reverse=True)

    batch_size = max(0, int(getattr(settings, "CONTEST_CATALOG_HISTORY_BATCH_SIZE", 1)))
    if batch_size == 0:
        return sorted(set(recent_years), reverse=True)

    default_start = 2010 if platform == "CF" else 2014
    start_year = int(getattr(settings, f"CONTEST_CATALOG_START_YEAR_{platform}", default_start))
    history_end = min(recent_years) - 1
    if start_year > history_end:
        return sorted(set(recent_years), reverse=True)

    cursor_key = f"contests_catalog_cursor_year:{platform}"
    cursor = None
    try:
        raw = _get_redis_client().get(cursor_key)
        if raw is not None:
            cursor = int(raw)
    except Exception:
        cursor = None
    if cursor is None or cursor < start_year or cursor > history_end:
        cursor = history_end

    historical_years = []
    year = cursor
    for _ in range(batch_size):
        if year < start_year:
            year = history_end
        historical_years.append(year)
        year -= 1

    try:
        _get_redis_client().set(cursor_key, str(year))
    except Exception:
        pass
    return sorted(set(recent_years + historical_years), reverse=True)


def _refresh_contest_rating_summary(contest_ids: list[int]) -> None:
    if not contest_ids:
        return
    now = timezone.now()
    rows = (
        ContestProblem.objects.filter(contest_id__in=contest_ids)
        .values("contest_id")
        .annotate(
            total=Count("id"),
            ready=Count("id", filter=Q(rating_status="OK")),
        )
    )
    summary_by_id = {row["contest_id"]: row for row in rows}
    for contest in Contest.objects.filter(id__in=contest_ids):
        summary = summary_by_id.get(contest.id, {"total": 0, "ready": 0})
        total = summary["total"] or 0
        ready = summary["ready"] or 0
        if total == 0:
            status = "NONE"
        elif ready == total:
            status = "READY"
        elif ready == 0:
            status = "NONE"
        else:
            status = "PARTIAL"
        contest.ratings_total_count = total
        contest.ratings_ready_count = ready
        contest.ratings_summary_status = status
        contest.ratings_last_checked_at = now
        contest.save(update_fields=[
            "ratings_total_count",
            "ratings_ready_count",
            "ratings_summary_status",
            "ratings_last_checked_at",
        ])

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
            max_count = int(getattr(settings, "CF_SYNC_RECENT_MAX_COUNT", 1000))
            if not last_cf:
                # First sync: pull a deeper history window once.
                max_count = int(getattr(settings, "CF_SYNC_INITIAL_MAX_COUNT", 5000))
            cf_subs = CodeforcesClient.get_submissions(
                student.handle_codeforces,
                since=last_cf,
                max_count=max_count,
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


@shared_task
def sync_contest_submissions(platform: str, contest_id: str, max_count_cf: int = 1500) -> dict:
    """
    Sync de submissões para um contest específico.
    Evita depender de cursor incremental global do usuário para não perder participantes novos.
    """
    started = time.monotonic()
    platform = (platform or "").upper()
    contest_id = str(contest_id or "")
    if platform not in {"CF", "AC"} or not contest_id:
        return {"status": "error", "message": "invalid platform/contest_id"}

    lock_key = f"sync_contest_submissions:{platform}:{contest_id}"
    if not _acquire_lock(lock_key, ttl_seconds=10 * 60):
        return {"status": "locked", "message": "already running"}

    if platform == "CF":
        students_qs = PerfilAluno.objects.exclude(handle_codeforces__isnull=True).exclude(handle_codeforces="")
    else:
        students_qs = PerfilAluno.objects.exclude(handle_atcoder__isnull=True).exclude(handle_atcoder="")

    students_total = students_qs.count()
    fetched_total = 0
    created_total = 0
    updated_total = 0
    created_for_contest = 0
    updated_for_contest = 0
    errored_students = 0

    contest = Contest.objects.filter(platform=platform, contest_id=contest_id).only(
        "start_time"
    ).first()
    contest_since_hint = None
    if contest and contest.start_time:
        contest_since_hint = contest.start_time - timedelta(hours=6)

    for student in students_qs.iterator():
        try:
            if platform == "CF":
                subs = CodeforcesClient.get_contest_submissions(
                    student.handle_codeforces,
                    contest_id=contest_id,
                    max_count=max_count_cf or 10000,
                )
            else:
                all_subs = AtCoderClient.get_submissions(
                    student.handle_atcoder,
                    since=contest_since_hint,
                )
                subs = [
                    sub
                    for sub in all_subs
                    if str(sub.get("contest_id") or "") == contest_id
                ]

            fetched_total += len(subs)
            for sub in subs:
                submission, created = Submissao.objects.update_or_create(
                    plataforma=platform,
                    external_id=sub["external_id"],
                    defaults={
                        "aluno": student,
                        "plataforma": platform,
                        "contest_id": str(sub.get("contest_id") or ""),
                        "problem_index": sub.get("problem_index") or "",
                        "problem_name": sub.get("problem_name") or sub.get("problem_id") or "",
                        "tags": sub.get("tags", "") or "",
                        "verdict": sub.get("verdict") or "UNKNOWN",
                        "submission_time": sub.get("submission_time"),
                    },
                )
                if created:
                    created_total += 1
                    created_for_contest += 1
                else:
                    updated_total += 1
                    updated_for_contest += 1
                if submission.verdict in {"OK", "AC"}:
                    process_submission_for_scoring(submission)
        except Exception:
            errored_students += 1
            logger.exception(
                "sync_contest_submissions failed platform=%s contest_id=%s student_id=%s",
                platform,
                contest_id,
                student.id,
            )

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "sync_contest_submissions platform=%s contest_id=%s students=%s fetched=%s created=%s updated=%s created_for_contest=%s updated_for_contest=%s errors=%s duration_ms=%s",
        platform,
        contest_id,
        students_total,
        fetched_total,
        created_total,
        updated_total,
        created_for_contest,
        updated_for_contest,
        errored_students,
        duration_ms,
    )
    return {
        "status": "ok",
        "platform": platform,
        "contest_id": contest_id,
        "students": students_total,
        "fetched": fetched_total,
        "created": created_total,
        "updated": updated_total,
        "created_for_contest": created_for_contest,
        "updated_for_contest": updated_for_contest,
        "errors": errored_students,
        "duration_ms": duration_ms,
    }


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


@shared_task
def refresh_cf_rating_history(student_id: int, force: bool = False) -> str:
    try:
        student = PerfilAluno.objects.get(id=student_id)
    except PerfilAluno.DoesNotExist:
        return f"Student profile with ID {student_id} not found."

    if not student.handle_codeforces:
        return f"{student.user.username}: no CF handle."

    lock_key = f"refresh_cf_rating_history:{student_id}"
    if not force and not _acquire_lock(lock_key, ttl_seconds=6 * 60 * 60):
        return f"{student.user.username}: locked."

    changes = CodeforcesClient.get_rating_changes(student.handle_codeforces)
    if not changes:
        return f"{student.user.username}: no rating history data."

    now = timezone.now()
    objects = []
    for row in changes:
        contest_id = (row.get("contest_id") or "").strip()
        if not contest_id:
            continue
        rating_old = row.get("rating_old")
        rating_new = row.get("rating_new")
        if rating_old is None or rating_new is None:
            continue
        objects.append(
            CodeforcesRatingChange(
                aluno=student,
                contest_id=contest_id,
                contest_name=row.get("contest_name") or "",
                rating_old=int(rating_old),
                rating_new=int(rating_new),
                rating_update_time=row.get("rating_update_time") or now,
            )
        )

    if not objects:
        return f"{student.user.username}: no usable rating rows."

    CodeforcesRatingChange.objects.bulk_create(
        objects,
        update_conflicts=True,
        update_fields=["contest_name", "rating_old", "rating_new", "rating_update_time"],
        unique_fields=["aluno", "contest_id"],
    )

    return f"{student.user.username}: CF rating history upserted ({len(objects)} rows)."


@shared_task
def refresh_all_cf_rating_history() -> str:
    student_ids = list(
        PerfilAluno.objects.exclude(handle_codeforces__isnull=True)
        .exclude(handle_codeforces="")
        .values_list("id", flat=True)
    )
    for student_id in student_ids:
        refresh_cf_rating_history.delay(student_id)
    return f"Queued CF rating history refresh for {len(student_ids)} students."


@shared_task
def snapshot_atcoder_ratings() -> str:
    today = timezone.localdate()
    students = (
        PerfilAluno.objects.exclude(handle_atcoder__isnull=True)
        .exclude(handle_atcoder="")
        .exclude(ac_rating_current__isnull=True)
        .only("id", "ac_rating_current")
    )
    created = 0
    for student in students.iterator():
        rating = student.ac_rating_current
        if rating is None:
            continue
        _, was_created = AtCoderRatingSnapshot.objects.get_or_create(
            aluno=student,
            date=today,
            defaults={"rating": int(rating)},
        )
        if not was_created:
            # Keep "one snapshot per day"; update only if it was created today already.
            AtCoderRatingSnapshot.objects.filter(aluno=student, date=today).update(rating=int(rating))
        created += 1
    return f"AtCoder rating snapshot updated for {created} users."


@shared_task
def sync_contests(platform: str, year: int) -> dict:
    started = time.monotonic()
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"} or not year:
        return {"status": "invalid_args"}

    if platform == "CF":
        contests = get_cf_contests(year)
    else:
        contests = get_ac_contests(year)

    created = 0
    updated = 0
    now = timezone.now()

    with transaction.atomic():
        for contest in contests:
            title = contest.get("title") or ""
            start_time = contest.get("start_time")
            duration_seconds = contest.get("duration_seconds")
            year_value = start_time.year if start_time else year
            phase = contest.get("phase") or ""
            is_gym = bool(contest.get("is_gym")) if platform == "CF" else False
            category = None
            division = None
            if platform == "AC":
                category = classify_atcoder_category(contest.get("contest_id"), title)
            else:
                division = classify_codeforces_division(title)

            contest_id = str(contest.get("contest_id"))
            existing = Contest.objects.filter(
                platform=platform,
                contest_id=contest_id,
            ).first()

            if existing:
                changed = (
                    existing.title != title
                    or existing.start_time != start_time
                    or existing.duration_seconds != duration_seconds
                    or existing.year != year_value
                    or (platform == "CF" and existing.phase != phase)
                    or (platform == "CF" and existing.is_gym != is_gym)
                    or (category and existing.category != category)
                    or (division and existing.division != division)
                )
                existing.title = title
                existing.start_time = start_time
                existing.duration_seconds = duration_seconds
                existing.year = year_value
                if platform == "CF":
                    existing.phase = phase
                    existing.is_gym = is_gym
                    existing.last_sync_at = now
                if category:
                    existing.category = category
                if division:
                    existing.division = division
                if changed:
                    existing.problems_sync_status = "STALE"
                    existing.problems_next_sync_at = now
                existing.save()
                updated += 1
            else:
                contest_obj = Contest(
                    platform=platform,
                    contest_id=contest_id,
                    title=title,
                    start_time=start_time,
                    duration_seconds=duration_seconds,
                    year=year_value,
                    phase=phase if platform == "CF" else "",
                    is_gym=is_gym if platform == "CF" else False,
                    last_sync_at=now,
                    problems_sync_status="NEW",
                    problems_next_sync_at=now,
                )
                if category:
                    contest_obj.category = category
                if division:
                    contest_obj.division = division
                contest_obj.save()
                created += 1

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "sync_contests platform=%s year=%s created=%s updated=%s duration_ms=%s",
        platform,
        year,
        created,
        updated,
        duration_ms,
    )
    return {
        "status": "ok",
        "platform": platform,
        "year": year,
        "created": created,
        "updated": updated,
        "duration_ms": duration_ms,
    }


@shared_task
def sync_contest_problems(platform: str, contest_id: str) -> dict:
    started = time.monotonic()
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"} or not contest_id:
        return {"status": "invalid_args"}

    lock_key = f"sync_contest_problems:{platform}:{contest_id}"
    if not _acquire_lock(lock_key, ttl_seconds=600):
        return {"status": "locked", "platform": platform, "contest_id": contest_id}

    contest = Contest.objects.filter(
        platform=platform,
        contest_id=str(contest_id),
    ).first()
    if not contest:
        return {"status": "contest_not_found"}

    if platform == "CF":
        problems = get_cf_contest_problems(contest_id)
    else:
        problems = get_ac_contest_problems(contest_id)

    if not problems:
        contest.problems_sync_status = "FAILED"
        contest.problems_sync_attempts = contest.problems_sync_attempts + 1
        contest.problems_next_sync_at = timezone.now() + timedelta(
            minutes=_sync_backoff_minutes(contest.problems_sync_attempts)
        )
        contest.save(update_fields=[
            "problems_sync_status",
            "problems_sync_attempts",
            "problems_next_sync_at",
        ])
        return {"status": "no_problems", "contest_id": contest_id}

    created = 0
    updated = 0
    now = timezone.now()

    with transaction.atomic():
        objects_to_create = []
        seen_urls = set()
        for order, problem in enumerate(problems, start=1):
            index_label = str(problem.get("index") or "").strip()
            if not index_label:
                continue

            name = problem.get("name") or index_label
            tags_raw = problem.get("tags") or []
            tags_str = ""
            if platform == "CF" and tags_raw:
                tags_str = ",".join([str(t).strip() for t in tags_raw if str(t).strip()])
            cf_rating = None
            if platform == "CF":
                try:
                    cf_rating = int(problem.get("rating")) if problem.get("rating") is not None else None
                except Exception:
                    cf_rating = None
            problem_id = problem.get("problem_id")
            problem_url = build_problem_url_from_fields(
                platform,
                contest_id,
                index_label,
                problem_id,
            )
            problem_url = normalize_problem_url(problem_url)
            if not problem_url:
                continue
            if problem_url in seen_urls:
                continue
            seen_urls.add(problem_url)

            objects_to_create.append(
                ContestProblem(
                    contest=contest,
                    platform=platform,
                    order=order,
                    index_label=index_label,
                    problem_url=problem_url,
                    name=name,
                    tags=tags_str,
                    cf_rating=cf_rating,
                    last_sync_at=now,
                )
            )

        if objects_to_create:
            ContestProblem.objects.bulk_create(
                objects_to_create,
                update_conflicts=True,
                update_fields=["order", "index_label", "name", "platform", "tags", "cf_rating", "last_sync_at"],
                unique_fields=["contest", "problem_url"],
            )
            created = len(objects_to_create)

        contest.problems_last_synced_at = now
        contest.problems_sync_status = "SYNCED"
        contest.problems_sync_attempts = 0
        contest.problems_next_sync_at = None
        contest.save(update_fields=[
            "problems_last_synced_at",
            "problems_sync_status",
            "problems_sync_attempts",
            "problems_next_sync_at",
        ])

        contest_problem_qs = ContestProblem.objects.filter(contest=contest).only(
            "id",
            "problem_url",
            "rating_status",
            "rating_last_ok_at",
            "cf_rating",
            "platform",
        )
        problem_urls = [cp.problem_url for cp in contest_problem_qs]
        cache_map = {
            cache.problem_url: cache
            for cache in ProblemRatingCache.objects.filter(problem_url__in=problem_urls)
        }
        updates = []
        for cp in contest_problem_qs:
            cache = cache_map.get(cp.problem_url)
            if cache and cp.cf_rating is not None and cache.cf_rating != cp.cf_rating:
                cache.cf_rating = cp.cf_rating
                _update_effective_rating(cache)
                cache.save(update_fields=["cf_rating", "effective_rating", "rating_source"])

            if cache and cache.effective_rating is not None and _is_cache_fresh(cache):
                cp.rating_status = "OK"
                cp.rating_last_ok_at = cache.rating_fetched_at or now
            elif cache and cache.status == "NOT_FOUND":
                cp.rating_status = "NOT_FOUND"
            elif cache and cache.status == "TEMP_FAIL":
                cp.rating_status = "TEMP_FAIL"
            elif cp.platform == "CF" and cp.cf_rating is not None:
                cp.rating_status = "OK"
                cp.rating_last_ok_at = cp.rating_last_ok_at or now
            else:
                cp.rating_status = "MISSING"
            updates.append(cp)
        if updates:
            ContestProblem.objects.bulk_update(
                updates,
                ["rating_status", "rating_last_ok_at"],
            )

        _refresh_contest_rating_summary([contest.id])

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "sync_contest_problems platform=%s contest_id=%s created=%s updated=%s duration_ms=%s",
        platform,
        contest_id,
        created,
        updated,
        duration_ms,
    )
    return {
        "status": "ok",
        "platform": platform,
        "contest_id": contest_id,
        "created": created,
        "updated": updated,
        "duration_ms": duration_ms,
    }


@shared_task
def nightly_contests_sync() -> dict:
    started = time.monotonic()
    catalog_result = contests_catalog_refresh()
    scheduler_result = contests_problems_scheduler()

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "nightly_contests_sync catalog_runs=%s contests_enqueued=%s duration_ms=%s",
        int(catalog_result.get("runs") or 0),
        int(scheduler_result.get("enqueued") or 0),
        duration_ms,
    )
    return {
        "status": "ok",
        "catalog_runs": int(catalog_result.get("runs") or 0),
        "contests_enqueued": int(scheduler_result.get("enqueued") or 0),
        "duration_ms": duration_ms,
    }


@shared_task
def contests_catalog_refresh() -> dict:
    started = time.monotonic()
    now = timezone.now()
    years_by_platform = {
        "CF": _catalog_years_for_platform("CF", now),
        "AC": _catalog_years_for_platform("AC", now),
    }
    total_runs = 0
    for platform, years in years_by_platform.items():
        for year in years:
            sync_contests(platform, year)
            total_runs += 1
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "contests_catalog_refresh years_by_platform=%s runs=%s duration_ms=%s",
        years_by_platform,
        total_runs,
        duration_ms,
    )
    result = {
        "status": "ok",
        "years_by_platform": years_by_platform,
        "runs": total_runs,
        "duration_ms": duration_ms,
    }
    _set_task_health("contests_catalog_refresh", result)
    return result


@shared_task
def contests_problems_scheduler(
    max_cf_per_run: int = 10,
    max_ac_per_run: int = 10,
    recent_days: int = 2,
    partial_stale_hours: int = 6,
) -> dict:
    started = time.monotonic()
    now = timezone.now()
    candidates: list[Contest] = []
    seen: set[int] = set()

    def add_candidates(qs):
        for contest in qs:
            if contest.id in seen:
                continue
            seen.add(contest.id)
            candidates.append(contest)

    add_candidates(
        Contest.objects.filter(problems_sync_status="NEW")
        .filter(Q(problems_next_sync_at__isnull=True) | Q(problems_next_sync_at__lte=now))
        .order_by("-start_time")
    )

    add_candidates(
        Contest.objects.filter(start_time__gte=now - timedelta(days=recent_days))
        .exclude(problems_sync_status="SYNCED")
        .order_by("-start_time")
    )

    add_candidates(
        Contest.objects.filter(ratings_summary_status="PARTIAL")
        .filter(Q(ratings_last_checked_at__isnull=True) | Q(ratings_last_checked_at__lte=now - timedelta(hours=partial_stale_hours)))
        .order_by("-start_time")
    )

    add_candidates(
        Contest.objects.filter(problems_sync_status="STALE")
        .filter(Q(problems_next_sync_at__isnull=True) | Q(problems_next_sync_at__lte=now))
        .order_by("-start_time")
    )

    counts = {"CF": 0, "AC": 0}
    enqueued = 0
    for contest in candidates:
        if contest.platform not in counts:
            continue
        if contest.platform == "CF" and counts["CF"] >= max_cf_per_run:
            continue
        if contest.platform == "AC" and counts["AC"] >= max_ac_per_run:
            continue
        sync_contest_problems.delay(contest.platform, contest.contest_id)
        counts[contest.platform] += 1
        enqueued += 1

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "contests_problems_scheduler enqueued=%s cf=%s ac=%s duration_ms=%s",
        enqueued,
        counts["CF"],
        counts["AC"],
        duration_ms,
    )
    result = {
        "status": "ok",
        "enqueued": enqueued,
        "cf": counts["CF"],
        "ac": counts["AC"],
        "duration_ms": duration_ms,
    }
    _set_task_health("contests_problems_scheduler", result)
    return result


@shared_task
def ratings_backfill_scheduler(
    limit: int = 10,
    cooldown_minutes: int = 30,
    max_attempts: int = 6,
) -> dict:
    started = time.monotonic()
    now = timezone.now()
    cooldown = now - timedelta(minutes=cooldown_minutes)
    rating_priority_max = int(getattr(settings, "RATING_PRIORITY_MAX", 1800))
    reset_hours = int(getattr(settings, "RATING_ATTEMPT_RESET_HOURS", 12))
    reset_before = now - timedelta(hours=max(1, reset_hours))
    ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
    stale_before = now - timedelta(hours=ttl_hours)
    conflict_heal = _heal_conflicting_cf_cache_entries()
    alias_heal = _heal_cf_split_round_aliases()

    # Heal a bad state produced by older versions where cache.status="OK" but rating is null.
    # Those should be re-queued, otherwise contests can show "ready" while the UI has no rating.
    invalid_urls = list(
        ProblemRatingCache.objects.filter(status="OK", clist_rating__isnull=True)
        .values_list("problem_url", flat=True)[:2000]
    )
    if invalid_urls:
        ContestProblem.objects.filter(problem_url__in=invalid_urls, rating_status="OK").update(
            rating_status="MISSING"
        )
        affected_contests = list(
            ContestProblem.objects.filter(problem_url__in=invalid_urls)
            .values_list("contest_id", flat=True)
            .distinct()[:5000]
        )
        _refresh_contest_rating_summary(affected_contests)

    # Heal contests where ContestProblem says OK but cache is missing or lacks effective rating.
    ok_urls = list(
        ContestProblem.objects.filter(rating_status="OK")
        .values_list("problem_url", flat=True)[:2000]
    )
    if ok_urls:
        ok_cache_urls = set(
            ProblemRatingCache.objects.filter(problem_url__in=ok_urls, effective_rating__isnull=False)
            .values_list("problem_url", flat=True)
        )
        missing_urls = [url for url in ok_urls if url not in ok_cache_urls]
        if missing_urls:
            ContestProblem.objects.filter(problem_url__in=missing_urls, rating_status="OK").update(
                rating_status="MISSING"
            )
            affected_contests = list(
                ContestProblem.objects.filter(problem_url__in=missing_urls)
                .values_list("contest_id", flat=True)
                .distinct()[:5000]
            )
            _refresh_contest_rating_summary(affected_contests)

    stale_urls = list(
        ProblemRatingCache.objects.filter(status="OK", rating_fetched_at__lt=stale_before)
        .values_list("problem_url", flat=True)[:200]
    )
    if stale_urls:
        ContestProblem.objects.filter(problem_url__in=stale_urls, rating_status="OK").update(
            rating_status="MISSING"
        )

    # CF split rounds can temporarily be marked NOT_FOUND because CLIST keeps only one of the mirrored URLs.
    # Periodically retry NOT_FOUND for CF to allow alias healing and delayed CLIST updates.
    not_found_retry_before = now - timedelta(hours=12)
    ContestProblem.objects.filter(
        platform="CF",
        rating_status="NOT_FOUND",
    ).filter(
        Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=not_found_retry_before)
    ).update(
        rating_status="MISSING",
        rating_attempts=0,
    )

    # Unstick exhausted items after a longer cooldown window.
    reset_attempts_count = ContestProblem.objects.filter(
        rating_status__in=["MISSING", "TEMP_FAIL", "QUEUED"],
        rating_attempts__gte=max_attempts,
    ).filter(
        Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=reset_before)
    ).update(rating_attempts=0)

    base_qs = (
        ContestProblem.objects.filter(
            rating_status__in=["MISSING", "TEMP_FAIL", "QUEUED"],
            rating_attempts__lt=max_attempts,
        )
        .filter(Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=cooldown))
        .select_related("contest")
        .order_by("-contest__start_time", "rating_attempts")
    )

    priority_qs = base_qs.filter(
        Q(platform="AC")
        | (Q(platform="CF") & Q(cf_rating__isnull=False) & Q(cf_rating__lte=rating_priority_max))
    )
    priority_items = list(priority_qs[:limit])
    remaining = max(0, limit - len(priority_items))
    if remaining > 0:
        lazy_items = list(base_qs.exclude(id__in=[p.id for p in priority_items])[:remaining])
        items = priority_items + lazy_items
    else:
        items = priority_items

    enqueued = 0
    for problem in items:
        cache, _ = ProblemRatingCache.objects.get_or_create(
            problem_url=problem.problem_url,
            defaults={
                "platform": problem.platform,
                "status": "TEMP_FAIL",
            },
        )
        if cache.platform != problem.platform:
            cache.platform = problem.platform
            cache.save(update_fields=["platform"])

        problem.rating_status = "QUEUED"
        problem.rating_last_requested_at = now
        problem.save(update_fields=[
            "rating_status",
            "rating_last_requested_at",
        ])

        if problem.platform == "AC":
            priority = 0
        elif problem.cf_rating and problem.cf_rating <= rating_priority_max:
            priority = 0
        else:
            priority = 1
        RatingFetchJob.objects.update_or_create(
            platform=problem.platform,
            problem_url=problem.problem_url,
            defaults={"priority": priority, "status": "QUEUED"},
        )
        enqueued += 1

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "ratings_backfill_scheduler enqueued=%s reset_attempts=%s alias_healed=%s conflicts_ids=%s conflicts_urls=%s duration_ms=%s",
        enqueued,
        reset_attempts_count,
        alias_heal.get("healed", 0),
        conflict_heal.get("conflicting_problem_ids", 0),
        conflict_heal.get("conflicting_urls", 0),
        duration_ms,
    )
    result = {
        "status": "ok",
        "enqueued": enqueued,
        "reset_attempts": reset_attempts_count,
        "alias_healed": alias_heal.get("healed", 0),
        "alias_contests_touched": alias_heal.get("contests_touched", 0),
        "conflicting_problem_ids": conflict_heal.get("conflicting_problem_ids", 0),
        "conflicting_urls": conflict_heal.get("conflicting_urls", 0),
        "conflicts_contests_touched": conflict_heal.get("contests_touched", 0),
        "duration_ms": duration_ms,
    }
    _set_task_health("ratings_backfill_scheduler", result)
    return result


def _update_effective_rating(cache: ProblemRatingCache) -> None:
    effective = None
    source = "none"
    if cache.clist_rating is not None:
        effective = cache.clist_rating
        source = "clist"
    elif cache.cf_rating is not None:
        effective = cache.cf_rating
        source = "cf"
    cache.effective_rating = effective
    cache.rating_source = source


def _apply_clist_result(cache: ProblemRatingCache, result: dict) -> str:
    status = result.get("status")
    cache.rating_fetched_at = timezone.now()

    if status == "OK":
        cache.clist_problem_id = str(result.get("problem_id") or "")
        rating_value = result.get("rating")
        if rating_value is None:
            cache.clist_rating = None
            cache.status = "NOT_FOUND"
            _update_effective_rating(cache)
            cache.save(update_fields=[
                "clist_problem_id",
                "clist_rating",
                "rating_fetched_at",
                "rating_source",
                "effective_rating",
                "status",
            ])
            ContestProblem.objects.filter(problem_url=cache.problem_url).update(
                rating_status="NOT_FOUND",
            )
            contest_ids = list(
                ContestProblem.objects.filter(problem_url=cache.problem_url)
                .values_list("contest_id", flat=True)
                .distinct()
            )
            _refresh_contest_rating_summary(contest_ids)
            return "NOT_FOUND"

        cache.clist_rating = int(rating_value)
        cache.status = "OK"
        _update_effective_rating(cache)
        cache.save(update_fields=[
            "clist_problem_id",
            "clist_rating",
            "rating_fetched_at",
            "rating_source",
            "effective_rating",
            "status",
        ])
        ContestProblem.objects.filter(problem_url=cache.problem_url).update(
            rating_status="OK",
            rating_last_ok_at=cache.rating_fetched_at,
            rating_attempts=0,
        )
        contest_ids = list(
            ContestProblem.objects.filter(problem_url=cache.problem_url)
            .values_list("contest_id", flat=True)
            .distinct()
        )
        _refresh_contest_rating_summary(contest_ids)
        update_scores_for_problem_url(cache.platform, cache.problem_url)
        return "OK"

    if status == "NOT_FOUND":
        if cache.platform == "CF":
            alias = _find_cf_split_round_alias(cache.problem_url)
            if not alias:
                alias = _find_cf_split_round_alias_via_clist_name(cache.problem_url)
            if alias and alias.get("rating") is not None:
                cache.clist_problem_id = str(alias.get("source_problem_id") or "")
                cache.clist_rating = int(alias["rating"])
                cache.status = "OK"
                _update_effective_rating(cache)
                cache.save(update_fields=[
                    "clist_problem_id",
                    "clist_rating",
                    "status",
                    "rating_fetched_at",
                    "rating_source",
                    "effective_rating",
                ])
                ContestProblem.objects.filter(problem_url=cache.problem_url).update(
                    rating_status="OK",
                    rating_last_ok_at=cache.rating_fetched_at,
                    rating_attempts=0,
                )
                contest_ids = list(
                    ContestProblem.objects.filter(problem_url=cache.problem_url)
                    .values_list("contest_id", flat=True)
                    .distinct()
                )
                _refresh_contest_rating_summary(contest_ids)
                update_scores_for_problem_url(cache.platform, cache.problem_url)
                logger.info(
                    "CF alias rating resolved problem_url=%s source_url=%s source_contest=%s rating=%s",
                    cache.problem_url,
                    alias.get("source_problem_url"),
                    alias.get("source_contest_id"),
                    alias.get("rating"),
                )
                return "OK"

        cache.status = "NOT_FOUND"
        _update_effective_rating(cache)
        cache.save(update_fields=["status", "rating_fetched_at", "rating_source", "effective_rating"])
        ContestProblem.objects.filter(problem_url=cache.problem_url).update(
            rating_status="NOT_FOUND",
            rating_attempts=0,
        )
        contest_ids = list(
            ContestProblem.objects.filter(problem_url=cache.problem_url)
            .values_list("contest_id", flat=True)
            .distinct()
        )
        _refresh_contest_rating_summary(contest_ids)
        return "NOT_FOUND"

    cache.status = "TEMP_FAIL"
    _update_effective_rating(cache)
    cache.save(update_fields=["status", "rating_fetched_at", "rating_source", "effective_rating"])
    ContestProblem.objects.filter(problem_url=cache.problem_url).update(
        rating_status="TEMP_FAIL",
    )
    contest_ids = list(
        ContestProblem.objects.filter(problem_url=cache.problem_url)
        .values_list("contest_id", flat=True)
        .distinct()
    )
    _refresh_contest_rating_summary(contest_ids)
    return "TEMP_FAIL"


@shared_task
def process_rating_fetch_jobs(limit: int = 5) -> dict:
    started = time.monotonic()
    now = timezone.now()
    qs = (
        RatingFetchJob.objects.filter(status="QUEUED")
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .order_by("priority", "created_at")
    )[:limit]

    processed = 0
    for job in qs:
        job.status = "RUNNING"
        job.locked_at = now
        job.attempts = (job.attempts or 0) + 1
        job.save(update_fields=["status", "locked_at", "attempts"])

        cache, _ = ProblemRatingCache.objects.get_or_create(
            problem_url=job.problem_url,
            defaults={"platform": job.platform, "status": "TEMP_FAIL"},
        )
        if cache.platform != job.platform:
            cache.platform = job.platform
            cache.save(update_fields=["platform"])

        _mark_rating_fetch_attempt(job.platform, job.problem_url, attempt_at=now)
        result = ClistClient.fetch_problem_rating(
            cache.platform,
            cache.problem_url,
            problem_name=None,
        )
        status = _apply_clist_result(cache, result)

        if status in {"OK", "NOT_FOUND"}:
            job.status = "DONE"
            job.next_retry_at = None
        else:
            backoff = min(3600, 60 * (2 ** min(job.attempts, 5)))
            job.status = "FAILED" if job.attempts >= 8 else "QUEUED"
            job.next_retry_at = now + timedelta(seconds=backoff)
        job.save(update_fields=["status", "next_retry_at"])
        processed += 1

    duration_ms = int((time.monotonic() - started) * 1000)
    result = {"status": "ok", "processed": processed, "duration_ms": duration_ms}
    _set_task_health("process_rating_fetch_jobs", result)
    return result

@shared_task(bind=True, max_retries=5)
def refresh_problem_rating_cache(self, cache_id, problem_name=None):
    try:
        cache = ProblemRatingCache.objects.get(id=cache_id)
    except ProblemRatingCache.DoesNotExist:
        return "Cache entry not found."

    _mark_rating_fetch_attempt(cache.platform, cache.problem_url)
    result = ClistClient.fetch_problem_rating(
        cache.platform,
        cache.problem_url,
        problem_name=problem_name,
    )
    status = _apply_clist_result(cache, result)

    if status in {"OK", "NOT_FOUND"}:
        return status

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
def recompute_rating_conversion():
    status = recompute_rating_conversion_ac_to_cf()
    return f"AC→CF fixed formula active ({status.formula_label}) (pairs={status.pairs_used})"


@shared_task
def recompute_score_windows():
    now = timezone.now()
    window_7d = now - timedelta(days=7)
    window_30d = now - timedelta(days=30)
    season, season_start, season_end = get_active_season_range()
    if not season_start or not season_end:
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
        points_general_cf_equiv_7d=0,
        points_cf_30d=0,
        points_ac_30d=0,
        points_general_30d=0,
        points_general_cf_equiv_30d=0,
        season_points_cf_raw=0,
        season_points_ac_raw=0,
        season_points_general_norm=0,
        season_points_general_cf_equiv=0,
    )

    rows = ScoreEvent.objects.filter(
        solved_at__gte=window_30d
    ).values("aluno_id").annotate(
        points_cf_30=Sum("points_cf_raw"),
        points_ac_30=Sum("points_ac_raw"),
        points_general_30=Sum("points_general_norm"),
        points_general_cf_30=Sum("points_general_cf_equiv"),
        points_cf_7=Sum("points_cf_raw", filter=Q(solved_at__gte=window_7d)),
        points_ac_7=Sum("points_ac_raw", filter=Q(solved_at__gte=window_7d)),
        points_general_7=Sum("points_general_norm", filter=Q(solved_at__gte=window_7d)),
        points_general_cf_7=Sum("points_general_cf_equiv", filter=Q(solved_at__gte=window_7d)),
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
                "points_general_cf_equiv_7d": row["points_general_cf_7"] or 0,
                "points_cf_30d": row["points_cf_30"] or 0,
                "points_ac_30d": row["points_ac_30"] or 0,
                "points_general_30d": row["points_general_30"] or 0,
                "points_general_cf_equiv_30d": row["points_general_cf_30"] or 0,
            },
        )

    season_rows = ScoreEvent.objects.filter(
        solved_at__gte=season_start,
        solved_at__lt=season_end,
    ).values("aluno_id").annotate(
        points_cf=Sum("points_cf_raw"),
        points_ac=Sum("points_ac_raw"),
        points_general=Sum("points_general_norm"),
        points_general_cf=Sum("points_general_cf_equiv"),
    )

    for row in season_rows:
        UserScoreAgg.objects.update_or_create(
            aluno_id=row["aluno_id"],
            defaults={
                "season_points_cf_raw": row["points_cf"] or 0,
                "season_points_ac_raw": row["points_ac"] or 0,
                "season_points_general_norm": row["points_general"] or 0,
                "season_points_general_cf_equiv": row["points_general_cf"] or 0,
            },
        )

    return "Score windows updated."


@shared_task
def snapshot_rankings_task():
    snapshot_rankings()
    return "Ranking snapshots created."
