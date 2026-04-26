from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import ProblemRatingCache, RatingFetchJob
from core.services.provisional_ratings import apply_provisional_rating, update_effective_rating


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
    if cache.effective_rating is None:
        apply_provisional_rating(cache)

    ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
    if cache.rating_fetched_at and cache.rating_fetched_at >= timezone.now() - timedelta(hours=ttl_hours):
        # Treat "OK but null rating" as stale/invalid so it can be retried.
        if not (cache.status == "OK" and cache.clist_rating is None):
            return cache

    if schedule and (created or cache.status in {"TEMP_FAIL", "NOT_FOUND", "RATE_LIMITED", "ERROR"} or not cache.rating_fetched_at):
        schedule_rating_job(platform, problem_url, priority=0)

    return cache


def _update_effective_rating(cache: ProblemRatingCache) -> None:
    update_effective_rating(cache)
    cache.save(update_fields=["effective_rating", "rating_source"])


def schedule_rating_job(platform: str, problem_url: str, priority: int = 1) -> None:
    if not problem_url:
        return
    RatingFetchJob.objects.update_or_create(
        platform=platform,
        problem_url=problem_url,
        defaults={"priority": int(priority), "status": "QUEUED"},
    )
