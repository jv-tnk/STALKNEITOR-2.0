from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    PerfilAluno,
    RatingConversionModel,
    RatingConversionPoint,
    RatingConversionSnapshot,
)


FALLBACK_AC_TO_CF_OFFSET = 200
AC_TO_CF_SLOPE = 0.763
AC_TO_CF_INTERCEPT = 760.0
AC_TO_CF_REFERENCE_URL = "https://silverfoxxxy.github.io/rating-correlation"
AC_TO_CF_FORMULA_LABEL = "CF ≈ 0.763 × AC + 760"


@dataclass
class ConversionStatus:
    model: RatingConversionModel | None
    points: list[RatingConversionPoint]
    pairs_used: int
    computed_at: datetime | None
    fallback_active: bool
    formula_label: str
    reference_url: str


def _get_active_model(direction: str = "AC_TO_CF") -> RatingConversionModel | None:
    return RatingConversionModel.objects.filter(direction=direction, is_active=True).order_by("-updated_at").first()


def _collect_pairs(model: RatingConversionModel | None) -> list[tuple[int, int]]:
    qs = PerfilAluno.objects.filter(
        cf_rating_current__isnull=False,
        ac_rating_current__isnull=False,
    )
    if model:
        rules = model.min_activity_rules_json or {}
        min_cf = rules.get("min_scoreevents_cf")
        min_ac = rules.get("min_scoreevents_ac")
        if min_cf or min_ac:
            qs = qs.annotate(
                score_cf=Count("score_events", filter=Q(score_events__platform="CF")),
                score_ac=Count("score_events", filter=Q(score_events__platform="AC")),
            )
            if min_cf:
                qs = qs.filter(score_cf__gte=min_cf)
            if min_ac:
                qs = qs.filter(score_ac__gte=min_ac)

    return list(qs.values_list("ac_rating_current", "cf_rating_current"))


def _bin_mean_monotone(pairs: list[tuple[int, int]], bin_size: int) -> list[tuple[int, int, int]]:
    if not pairs:
        return []
    pairs.sort(key=lambda item: item[0])
    if bin_size <= 0:
        bin_size = max(1, len(pairs))

    bins = []
    for start in range(0, len(pairs), bin_size):
        chunk = pairs[start:start + bin_size]
        if not chunk:
            continue
        xs = [p[0] for p in chunk]
        ys = [p[1] for p in chunk]
        x_mean = int(round(sum(xs) / len(xs)))
        y_mean = int(round(sum(ys) / len(ys)))
        bins.append([x_mean, y_mean, len(chunk)])

    # enforce monotonicity on y
    max_y = None
    for idx, item in enumerate(bins):
        if max_y is None:
            max_y = item[1]
        else:
            max_y = max(max_y, item[1])
        bins[idx][1] = max_y

    return [(x, y, n) for x, y, n in bins]


def recompute_rating_conversion_ac_to_cf() -> ConversionStatus:
    model = _get_active_model(direction="AC_TO_CF")
    if not model:
        model = RatingConversionModel.objects.create(
            direction="AC_TO_CF",
            method="bin_mean_monotone_v1",
            source_population="internal_users",
        )

    pairs = _collect_pairs(model)
    pairs_used = len(pairs)
    with transaction.atomic():
        model.method = "bin_mean_monotone_v1"
        model.source_population = "internal_users"
        model.is_active = True
        model.save(update_fields=["method", "source_population", "is_active", "updated_at"])
        snapshot = RatingConversionSnapshot.objects.create(
            model=model,
            computed_at=timezone.now(),
            pairs_used=pairs_used,
            notes="fixed_formula_ac_to_cf",
        )

    return ConversionStatus(
        model=model,
        points=[],
        pairs_used=pairs_used,
        computed_at=snapshot.computed_at,
        fallback_active=False,
        formula_label=AC_TO_CF_FORMULA_LABEL,
        reference_url=AC_TO_CF_REFERENCE_URL,
    )


def _load_points(model: RatingConversionModel | None) -> list[RatingConversionPoint]:
    if not model:
        return []
    return list(model.points.order_by("x_rating"))


def convert_ac_to_cf(ac_rating: int | None) -> int | None:
    if ac_rating is None:
        return None
    cf_equiv = AC_TO_CF_SLOPE * float(ac_rating) + AC_TO_CF_INTERCEPT
    return int(round(max(0.0, cf_equiv)))


def get_conversion_status() -> ConversionStatus:
    model = _get_active_model(direction="AC_TO_CF")
    snapshot = None
    pairs_used = 0
    if model:
        snapshot = model.snapshots.order_by("-computed_at").first()
        if snapshot:
            pairs_used = snapshot.pairs_used
    return ConversionStatus(
        model=model,
        points=[],
        pairs_used=pairs_used,
        computed_at=snapshot.computed_at if snapshot else None,
        fallback_active=False,
        formula_label=AC_TO_CF_FORMULA_LABEL,
        reference_url=AC_TO_CF_REFERENCE_URL,
    )
