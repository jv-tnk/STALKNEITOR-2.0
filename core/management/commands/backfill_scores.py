from django.core.management.base import BaseCommand

from core.models import Submissao
from core.services.scoring import process_submission_for_scoring


class Command(BaseCommand):
    help = "Cria ScoreEvents para submissões antigas (primeiro AC por problema)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--aluno-id",
            dest="aluno_id",
            type=int,
            help="Filtra por um aluno especifico.",
        )
        parser.add_argument(
            "--platform",
            dest="platform",
            choices=["CF", "AC"],
            help="Filtra por plataforma.",
        )
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            help="Limita a quantidade de submissões processadas.",
        )

    def handle(self, *args, **options):
        aluno_id = options.get("aluno_id")
        platform = options.get("platform")
        limit = options.get("limit")

        qs = Submissao.objects.filter(verdict__in=["OK", "AC"]).exclude(
            contest_id__isnull=True,
            problem_index__isnull=True,
        )
        if aluno_id:
            qs = qs.filter(aluno_id=aluno_id)
        if platform:
            qs = qs.filter(plataforma=platform)

        qs = qs.order_by(
            "aluno_id",
            "plataforma",
            "contest_id",
            "problem_index",
            "submission_time",
            "id",
        ).distinct(
            "aluno_id",
            "plataforma",
            "contest_id",
            "problem_index",
        )

        total = qs.count()
        processed = 0
        created = 0

        for submission in qs.iterator(chunk_size=200):
            if limit and processed >= limit:
                break
            processed += 1
            event = process_submission_for_scoring(submission)
            if event:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill concluido: {created} ScoreEvents criados (de {processed}/{total} submissões)."
            )
        )
