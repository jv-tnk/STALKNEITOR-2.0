import math
from bisect import bisect_right
from functools import lru_cache

from core.models import PlatformRatingStats, ProblemRatingCache, ProblemaReferencia, ScoreEvent, Submissao
from core.services.problem_urls import build_problem_url_from_fields, normalize_problem_url


def get_platform_stats(platform: str) -> PlatformRatingStats | None:
    return PlatformRatingStats.objects.filter(platform=platform).first()


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])

    k = (len(values) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(values[int(k)])
    d0 = values[int(f)] * (c - k)
    d1 = values[int(c)] * (k - f)
    return float(d0 + d1)


@lru_cache(maxsize=8)
def get_platform_distribution(
    platform: str,
    buckets: int = 200,
    cache_token: str | None = None,
) -> dict[str, object]:
    if buckets < 2:
        return {}

    urls = _collect_problem_urls(platform)
    if not urls:
        return {}

    ratings = list(
        ProblemRatingCache.objects.filter(
            platform=platform,
            problem_url__in=urls,
            status="OK",
            clist_rating__isnull=False,
        ).values_list("clist_rating", flat=True)
    )
    if not ratings:
        return {}

    ratings.sort()
    quantiles = []
    for i in range(1, buckets):
        percentile = 100.0 * i / buckets
        quantiles.append(_percentile(ratings, percentile))

    return {
        "min": float(ratings[0]),
        "max": float(ratings[-1]),
        "quantiles": quantiles,
        "buckets": buckets,
    }


def get_platform_percentile(
    platform: str,
    rating: int | None,
    buckets: int = 200,
    cache_token: str | None = None,
) -> float | None:
    if rating is None:
        return None

    dist = get_platform_distribution(platform, buckets=buckets, cache_token=cache_token)
    if not dist:
        return None

    min_rating = dist["min"]
    max_rating = dist["max"]
    quantiles = dist["quantiles"]
    bucket_count = dist["buckets"]

    rating = float(rating)
    if rating <= min_rating:
        return 0.0
    if rating >= max_rating:
        return 1.0

    idx = bisect_right(quantiles, rating)
    lower = min_rating if idx == 0 else quantiles[idx - 1]
    upper = max_rating if idx >= len(quantiles) else quantiles[idx]
    span = max(1.0, float(upper) - float(lower))
    frac = (rating - float(lower)) / span
    return (idx + frac) / float(bucket_count)




def _collect_problem_urls(platform: str) -> set[str]:
    urls: set[str] = set()

    for link in ProblemaReferencia.objects.filter(plataforma=platform).values_list("link", flat=True):
        normalized = normalize_problem_url(link)
        if normalized:
            urls.add(normalized)

    for row in Submissao.objects.filter(plataforma=platform).values(
        "contest_id",
        "problem_index",
        "problem_name",
    ):
        problem_url = build_problem_url_from_fields(
            platform,
            row.get("contest_id"),
            row.get("problem_index"),
            row.get("problem_name"),
        )
        if problem_url:
            urls.add(problem_url)

    for problem_url in ScoreEvent.objects.filter(platform=platform).values_list("problem_url", flat=True):
        normalized = normalize_problem_url(problem_url)
        if normalized:
            urls.add(normalized)

    return urls


def compute_platform_stats(platform: str) -> PlatformRatingStats | None:
    urls = _collect_problem_urls(platform)
    if not urls:
        return None

    ratings = list(
        ProblemRatingCache.objects.filter(
            platform=platform,
            problem_url__in=urls,
            status="OK",
            clist_rating__isnull=False,
        ).values_list("clist_rating", flat=True)
    )

    if not ratings:
        return None

    ratings.sort()
    median = _percentile(ratings, 50)
    q1 = _percentile(ratings, 25)
    q3 = _percentile(ratings, 75)
    iqr = q3 - q1
    if iqr <= 0:
        iqr = 1.0

    stats, _ = PlatformRatingStats.objects.update_or_create(
        platform=platform,
        defaults={
            "median": float(median),
            "iqr": float(iqr),
            "sample_size": len(ratings),
        },
    )

    return stats
