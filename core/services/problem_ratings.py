from datetime import timedelta

from celery import current_app
from django.conf import settings
from django.utils import timezone

from core.models import ProblemRatingCache, RatingFetchJob


def get_or_schedule_problem_rating(
    platform: str,
    problem_url: str,
    problem_name: str | None = None,
    schedule: bool = False,
) -> ProblemRatingCache:
    cache, created = ProblemRatingCache.objects.get_or_create(
        problem_url=problem_url,
        defaults={
            "platform": platform,
            "status": "TEMP_FAIL",
        },
    )

    if cache.platform != platform:
        cache.platform = platform
        cache.save(update_fields=["platform"])

    # compute effective rating based on available sources
    _update_effective_rating(cache)

    ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
    if cache.rating_fetched_at and cache.rating_fetched_at >= timezone.now() - timedelta(hours=ttl_hours):
        # Treat "OK but null rating" as stale/invalid so it can be retried.
        if not (cache.status == "OK" and cache.clist_rating is None):
            return cache

    if schedule and (created or cache.status in {"TEMP_FAIL", "NOT_FOUND", "RATE_LIMITED", "ERROR"} or not cache.rating_fetched_at):
        schedule_rating_job(platform, problem_url, priority=0)

    return cache


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
    cache.save(update_fields=["effective_rating", "rating_source"])


def schedule_rating_job(platform: str, problem_url: str, priority: int = 1) -> None:
    if not problem_url:
        return
    RatingFetchJob.objects.update_or_create(
        platform=platform,
        problem_url=problem_url,
        defaults={"priority": int(priority), "status": "QUEUED"},
    )
