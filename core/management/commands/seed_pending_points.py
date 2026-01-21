from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import ScoreEvent
from core.services.scoring import apply_score_delta, calculate_points


class Command(BaseCommand):
    help = "Define pontos base para ScoreEvents pendentes (sem rating do CLIST)."

    def handle(self, *args, **options):
        min_rating = float(getattr(settings, "RATING_NORMALIZE_MIN", 800))
        base_points = calculate_points(min_rating)

        pending = ScoreEvent.objects.filter(raw_rating__isnull=True)
        updated = 0

        for event in pending.iterator(chunk_size=200):
            points_cf_raw = base_points if event.platform == "CF" else 0
            points_ac_raw = base_points if event.platform == "AC" else 0
            if (
                event.points_awarded == base_points
                and event.points_general_norm == base_points
                and event.points_cf_raw == points_cf_raw
                and event.points_ac_raw == points_ac_raw
                and event.normalized_rating == min_rating
            ):
                continue

            previous_cf = event.points_cf_raw or 0
            previous_ac = event.points_ac_raw or 0
            previous_general = event.points_general_norm or 0

            event.normalized_rating = min_rating
            event.points_awarded = base_points
            event.points_cf_raw = points_cf_raw
            event.points_ac_raw = points_ac_raw
            event.points_general_norm = base_points
            event.save(update_fields=[
                "normalized_rating",
                "points_awarded",
                "points_cf_raw",
                "points_ac_raw",
                "points_general_norm",
            ])

            apply_score_delta(
                event.aluno_id,
                event.platform,
                event.points_cf_raw - previous_cf,
                event.points_ac_raw - previous_ac,
                event.points_general_norm - previous_general,
            )
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Pontos base aplicados em {updated} ScoreEvents pendentes."
            )
        )
