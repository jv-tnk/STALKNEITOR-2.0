from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from core.models import Contest, ContestProblem, ProblemRatingCache


class Command(BaseCommand):
    help = "Rebuild contest sync state and rating summaries from existing data."

    def handle(self, *args, **options):
        now = timezone.now()
        contests = Contest.objects.all().order_by("id")
        updated_contests = 0
        updated_problems = 0

        for contest in contests.iterator():
            problems = list(ContestProblem.objects.filter(contest=contest))
            total = len(problems)
            if total == 0:
                contest.problems_sync_status = "NEW"
                contest.problems_next_sync_at = now
            else:
                contest.problems_sync_status = "SYNCED"
                contest.problems_next_sync_at = None

            if problems:
                urls = [p.problem_url for p in problems if p.problem_url]
                caches = {
                    cache.problem_url: cache
                    for cache in ProblemRatingCache.objects.filter(problem_url__in=urls)
                }
                to_update = []
                ready = 0
                ttl_hours = getattr(settings, "CLIST_CACHE_TTL_HOURS", 24)
                for problem in problems:
                    cache = caches.get(problem.problem_url)
                    cache_fresh = (
                        cache
                        and cache.rating_fetched_at
                        and cache.rating_fetched_at >= now - timedelta(hours=ttl_hours)
                    )
                    if cache and cache.effective_rating is not None and cache_fresh:
                        problem.rating_status = "OK"
                        problem.rating_last_ok_at = cache.rating_fetched_at
                        ready += 1
                    elif cache and cache.status == "NOT_FOUND":
                        problem.rating_status = "NOT_FOUND"
                    elif cache and cache.status == "TEMP_FAIL":
                        problem.rating_status = "TEMP_FAIL"
                    elif problem.platform == "CF" and problem.cf_rating is not None:
                        problem.rating_status = "OK"
                        problem.rating_last_ok_at = problem.rating_last_ok_at or now
                        ready += 1
                    else:
                        problem.rating_status = "MISSING"
                    problem.platform = contest.platform
                    to_update.append(problem)
                if to_update:
                    ContestProblem.objects.bulk_update(
                        to_update,
                        ["rating_status", "rating_last_ok_at", "platform"],
                    )
                    updated_problems += len(to_update)
            else:
                ready = 0

            contest.ratings_total_count = total
            contest.ratings_ready_count = ready
            if total == 0:
                contest.ratings_summary_status = "NONE"
            elif ready == total:
                contest.ratings_summary_status = "READY"
            elif ready == 0:
                contest.ratings_summary_status = "NONE"
            else:
                contest.ratings_summary_status = "PARTIAL"
            contest.ratings_last_checked_at = now
            contest.problems_last_synced_at = contest.problems_last_synced_at or now
            contest.save(update_fields=[
                "problems_sync_status",
                "problems_next_sync_at",
                "ratings_total_count",
                "ratings_ready_count",
                "ratings_summary_status",
                "ratings_last_checked_at",
                "problems_last_synced_at",
            ])
            updated_contests += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt contest state for {updated_contests} contests and {updated_problems} problems."
            )
        )
