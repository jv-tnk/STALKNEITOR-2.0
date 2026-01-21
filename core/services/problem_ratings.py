from datetime import timedelta

from celery import current_app
from django.conf import settings
from django.utils import timezone

from core.models import ProblemRatingCache


def get_or_schedule_problem_rating(
    platform: str,
    problem_url: str,
    problem_name: str | None = None,
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

    ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
    if cache.rating_fetched_at:
        if cache.rating_fetched_at >= timezone.now() - timedelta(hours=ttl_hours):
            return cache

    if created or cache.status in {"TEMP_FAIL", "NOT_FOUND"} or not cache.rating_fetched_at:
        current_app.send_task(
            "core.tasks.refresh_problem_rating_cache",
            args=[cache.id, problem_name],
        )

    return cache
