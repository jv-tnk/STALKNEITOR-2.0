from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import PerfilAluno
from core.services.scoring import recalculate_points_for_platform, update_user_score_agg
from core.tasks import recompute_score_windows


class Command(BaseCommand):
    help = "Recalcula pontos e agregados do ranking com base nos ratings atuais."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            choices=["CF", "AC"],
            help="Limita a plataforma recalculada.",
        )
        parser.add_argument(
            "--aluno-id",
            type=int,
            help="Limita ao aluno informado.",
        )
        parser.add_argument(
            "--skip-windows",
            action="store_true",
            help="Nao recalcula janelas 7d/30d/temporada.",
        )
        parser.add_argument(
            "--cache-token",
            help="Token para invalidar o cache de distribuicao.",
        )

    def handle(self, *args, **options):
        platform = options.get("platform")
        aluno_id = options.get("aluno_id")
        skip_windows = options.get("skip_windows")
        cache_token = options.get("cache_token") or timezone.now().date().isoformat()

        platforms = [platform] if platform else ["CF", "AC"]
        for current in platforms:
            recalculate_points_for_platform(
                current,
                aluno_id=aluno_id,
                cache_token=cache_token,
            )

        qs = PerfilAluno.objects.all()
        if aluno_id:
            qs = qs.filter(id=aluno_id)

        total = 0
        for aluno in qs.iterator(chunk_size=200):
            update_user_score_agg(aluno.id)
            total += 1

        if aluno_id and not skip_windows:
            self.stdout.write(
                self.style.WARNING(
                    "Aviso: recompute_score_windows atualiza todas as agregacoes."
                )
            )

        if not skip_windows:
            recompute_score_windows()

        self.stdout.write(
            self.style.SUCCESS(
                f"Recalculo concluido para {total} alunos."
            )
        )
