from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import median

from django.conf import settings
from django.utils import timezone

from core.models import ContestProblem, ProblemRatingCache
from core.services.problem_urls import normalize_problem_url


REAL_RATING_SOURCES = {"clist", "cf"}
PROVISIONAL_RATING_SOURCE = "provisional"


@dataclass(frozen=True)
class ProvisionalRatingEstimate:
    rating: int
    confidence: float
    source: str


def provisional_ratings_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_PROVISIONAL_RATINGS", True))


def update_effective_rating(cache: ProblemRatingCache) -> None:
    effective = None
    source = "none"
    if cache.clist_rating is not None:
        effective = cache.clist_rating
        source = "clist"
    elif cache.cf_rating is not None:
        effective = cache.cf_rating
        source = "cf"
    elif cache.provisional_rating is not None:
        effective = cache.provisional_rating
        source = PROVISIONAL_RATING_SOURCE

    cache.effective_rating = effective
    cache.rating_source = source


def is_provisional_source(source: str | None) -> bool:
    return (source or "") == PROVISIONAL_RATING_SOURCE


def is_real_source(source: str | None) -> bool:
    return (source or "") in REAL_RATING_SOURCES


def estimate_provisional_rating(
    platform: str,
    problem_url: str,
    *,
    problem: ContestProblem | None = None,
) -> ProvisionalRatingEstimate | None:
    if not provisional_ratings_enabled():
        return None

    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        return None

    problem_url = normalize_problem_url(problem_url)
    if not problem_url:
        return None

    if problem is None:
        problem = (
            ContestProblem.objects.select_related("contest")
            .filter(platform=platform, problem_url=problem_url)
            .order_by("-contest__start_time", "order")
            .first()
        )
    if problem is None:
        return None

    baseline = _baseline_rating(problem)
    if baseline is None:
        return None

    candidates: list[tuple[float, float, str]] = [(float(baseline), 0.35, "index")]

    neighbor = _neighbor_estimate(problem)
    if neighbor is not None:
        candidates.append((float(neighbor.rating), neighbor.confidence, neighbor.source))

    historical = _historical_index_estimate(problem)
    if historical is not None:
        candidates.append((float(historical.rating), historical.confidence, historical.source))

    total_weight = sum(weight for _, weight, _ in candidates)
    if total_weight <= 0:
        return None

    weighted = sum(value * weight for value, weight, _ in candidates) / total_weight
    confidence = max(weight for _, weight, _ in candidates)
    confidence = max(0.30, min(0.85, confidence))

    # Shrink toward the stable index baseline so provisional points do not move
    # rankings as aggressively as a confirmed CLIST/official rating.
    rating = baseline + confidence * (weighted - baseline)
    rating = _round_rating(problem.platform, rating)
    rating = _clamp_rating(problem.platform, rating)

    sources = "+".join(dict.fromkeys(source for _, _, source in candidates))
    return ProvisionalRatingEstimate(
        rating=rating,
        confidence=round(confidence, 2),
        source=sources,
    )


def apply_provisional_rating(
    cache: ProblemRatingCache,
    *,
    problem: ContestProblem | None = None,
) -> bool:
    if not provisional_ratings_enabled():
        return False
    if cache.clist_rating is not None or cache.cf_rating is not None:
        return False
    if cache.rating_source and is_real_source(cache.rating_source):
        return False

    estimate = estimate_provisional_rating(cache.platform, cache.problem_url, problem=problem)
    if estimate is None:
        return False

    changed = (
        cache.provisional_rating != estimate.rating
        or cache.provisional_confidence != estimate.confidence
        or cache.provisional_source != estimate.source
        or cache.rating_source != PROVISIONAL_RATING_SOURCE
        or cache.effective_rating != estimate.rating
    )
    if not changed:
        return False

    cache.provisional_rating = estimate.rating
    cache.provisional_confidence = estimate.confidence
    cache.provisional_source = estimate.source
    cache.provisional_updated_at = timezone.now()
    update_effective_rating(cache)
    cache.save(update_fields=[
        "provisional_rating",
        "provisional_confidence",
        "provisional_source",
        "provisional_updated_at",
        "effective_rating",
        "rating_source",
    ])
    return True


def _index_rank(index_label: str | None) -> float | None:
    raw = (index_label or "").strip().upper()
    if not raw:
        return None
    if raw in {"EX", "EXTRA"}:
        return 8.5

    match = re.match(r"^([A-Z]+)(\d*)", raw)
    if not match:
        return None

    letters, suffix = match.groups()
    if len(letters) == 1:
        rank = ord(letters) - ord("A") + 1
    else:
        rank = float(sum(ord(ch) - ord("A") + 1 for ch in letters))
    if suffix:
        rank += min(0.8, int(suffix) / 10.0)
    return float(rank)


def _baseline_rating(problem: ContestProblem) -> int | None:
    rank = _index_rank(problem.index_label)
    if rank is None:
        return None

    platform = (problem.platform or "").upper()
    if platform == "CF":
        table = {
            1: 800,
            2: 1000,
            3: 1300,
            4: 1600,
            5: 1900,
            6: 2200,
            7: 2500,
            8: 2800,
        }
        base = table.get(int(math.floor(rank)), 2800 + int(max(0, rank - 8)) * 200)
        if rank % 1:
            base += int(round((rank % 1) * 200))
        return _clamp_rating(platform, base)

    if platform == "AC":
        contest_id = ((problem.contest.contest_id if problem.contest else problem.problem_url) or "").lower()
        title = ((problem.contest.title if problem.contest else "") or "").lower()
        if contest_id.startswith("abc") or "beginner" in title:
            table = [50, 200, 500, 900, 1400, 1900, 2300, 2700]
        elif contest_id.startswith("arc") or "regular" in title:
            table = [800, 1200, 1600, 2000, 2400, 2800]
        elif contest_id.startswith("agc") or "grand" in title:
            table = [1400, 1800, 2200, 2600, 3000, 3400]
        else:
            table = [600, 900, 1200, 1600, 2000, 2400, 2800]
        idx = int(math.floor(rank)) - 1
        if idx < len(table):
            base = table[max(0, idx)]
        else:
            base = table[-1] + (idx - len(table) + 1) * 300
        if rank % 1:
            base += int(round((rank % 1) * 250))
        return _clamp_rating(platform, base)

    return None


def _neighbor_estimate(problem: ContestProblem) -> ProvisionalRatingEstimate | None:
    if not problem.contest_id:
        return None
    target_rank = _index_rank(problem.index_label)
    if target_rank is None:
        return None

    siblings = list(
        ContestProblem.objects.filter(contest_id=problem.contest_id, platform=problem.platform)
        .exclude(id=problem.id)
        .only("id", "index_label", "problem_url", "cf_rating", "platform")
    )
    if not siblings:
        return None

    cache_by_url = {
        cache.problem_url: cache
        for cache in ProblemRatingCache.objects.filter(
            platform=problem.platform,
            problem_url__in=[sibling.problem_url for sibling in siblings],
        ).only("problem_url", "clist_rating", "cf_rating", "rating_source")
    }

    points: list[tuple[float, int]] = []
    for sibling in siblings:
        rank = _index_rank(sibling.index_label)
        if rank is None:
            continue
        cache = cache_by_url.get(sibling.problem_url)
        rating = None
        if cache and cache.clist_rating is not None:
            rating = int(cache.clist_rating)
        elif cache and cache.cf_rating is not None:
            rating = int(cache.cf_rating)
        elif sibling.platform == "CF" and sibling.cf_rating is not None:
            rating = int(sibling.cf_rating)
        if rating is not None:
            points.append((rank, rating))

    if not points:
        return None

    points.sort()
    lower = [item for item in points if item[0] <= target_rank]
    upper = [item for item in points if item[0] >= target_rank]
    step = 300 if problem.platform == "CF" else 400

    if lower and upper:
        left = lower[-1]
        right = upper[0]
        if left[0] == right[0]:
            rating = left[1]
        else:
            ratio = (target_rank - left[0]) / max(0.1, right[0] - left[0])
            rating = left[1] + ratio * (right[1] - left[1])
        confidence = 0.75
        source = "contest_neighbors"
    elif lower:
        left = lower[-1]
        rating = left[1] + (target_rank - left[0]) * step
        confidence = 0.60
        source = "contest_lower_neighbor"
    else:
        right = upper[0]
        rating = right[1] - (right[0] - target_rank) * step
        confidence = 0.60
        source = "contest_upper_neighbor"

    return ProvisionalRatingEstimate(
        rating=_clamp_rating(problem.platform, _round_rating(problem.platform, rating)),
        confidence=confidence,
        source=source,
    )


def _historical_index_estimate(problem: ContestProblem) -> ProvisionalRatingEstimate | None:
    urls = list(
        ContestProblem.objects.filter(platform=problem.platform, index_label=problem.index_label)
        .exclude(problem_url=problem.problem_url)
        .values_list("problem_url", flat=True)[:5000]
    )
    if not urls:
        return None

    ratings = list(
        ProblemRatingCache.objects.filter(
            platform=problem.platform,
            problem_url__in=urls,
            clist_rating__isnull=False,
        ).values_list("clist_rating", flat=True)[:1000]
    )
    if len(ratings) < 3:
        return None

    sample_size = len(ratings)
    if sample_size >= 30:
        confidence = 0.65
    elif sample_size >= 10:
        confidence = 0.55
    else:
        confidence = 0.45

    return ProvisionalRatingEstimate(
        rating=_clamp_rating(problem.platform, _round_rating(problem.platform, median(ratings))),
        confidence=confidence,
        source="historical_index",
    )


def _round_rating(platform: str, rating: float) -> int:
    step = 100
    if (platform or "").upper() == "AC" and rating < 400:
        step = 50
    return int(round(float(rating) / step) * step)


def _clamp_rating(platform: str, rating: float) -> int:
    platform = (platform or "").upper()
    if platform == "AC":
        return int(max(0, min(4000, rating)))
    return int(max(800, min(3500, rating)))
