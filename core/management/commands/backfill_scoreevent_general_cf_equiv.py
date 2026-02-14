from datetime import timedelta

from django.core.management.base import BaseCommand

from core.models import Contest, ContestProblem, ScoreEvent
from core.services.rating_conversion import convert_ac_to_cf
from core.services.scoring import calculate_points


class Command(BaseCommand):
    help = "Backfill points_general_cf_equiv and rating_used_cf_equiv for ScoreEvents."

    def handle(self, *args, **options):
        updated = 0
        events = ScoreEvent.objects.select_related("submission").filter(raw_rating__isnull=False)
        for event in events:
            rating_used = None
            if event.raw_rating is not None:
                rating_used = event.raw_rating if event.platform == "CF" else convert_ac_to_cf(int(event.raw_rating))

            if rating_used is None:
                continue

            in_contest = event.in_contest
            contest = None
            if not in_contest:
                if event.contest_id and event.contest_platform:
                    contest = Contest.objects.filter(
                        platform=event.contest_platform,
                        contest_id=event.contest_id,
                    ).first()
                if not contest:
                    cp = ContestProblem.objects.select_related("contest").filter(
                        problem_url=event.problem_url,
                        contest__platform=event.platform,
                    ).first()
                    if cp:
                        contest = cp.contest
                if contest and contest.start_time and contest.duration_seconds:
                    contest_end = contest.start_time + timedelta(seconds=contest.duration_seconds)
                    if contest.start_time <= event.solved_at <= contest_end:
                        in_contest = True
                        event.contest_platform = contest.platform
                        event.contest_id = contest.contest_id

            bonus_multiplier = event.bonus_multiplier or (1.10 if in_contest else 1.0)
            points_general_cf = calculate_points(rating_used)
            if bonus_multiplier != 1.0:
                points_general_cf = int(round(points_general_cf * bonus_multiplier))

            if (
                event.rating_used_cf_equiv != rating_used
                or event.points_general_cf_equiv != points_general_cf
                or event.in_contest != in_contest
            ):
                event.rating_used_cf_equiv = rating_used
                event.points_general_cf_equiv = points_general_cf
                event.in_contest = in_contest
                event.bonus_multiplier = bonus_multiplier
                event.points_awarded = points_general_cf or event.points_awarded
                event.save(update_fields=[
                    "rating_used_cf_equiv",
                    "points_general_cf_equiv",
                    "in_contest",
                    "bonus_multiplier",
                    "points_awarded",
                    "contest_platform",
                    "contest_id",
                ])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Backfill completo: {updated} ScoreEvents atualizados."))
