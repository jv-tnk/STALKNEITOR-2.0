from django.conf import settings
from django.core.validators import MaxLengthValidator, RegexValidator
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from core.utils.languages import LANGUAGE_CHOICES

class Turma(models.Model):
    nome = models.CharField(max_length=100, help_text="Ex: Maratona Iniciante")
    semestre = models.CharField(max_length=20, help_text="Ex: 2024.1")

    class Meta:
        unique_together = ('nome', 'semestre')
        verbose_name = "Turma"
        verbose_name_plural = "Turmas"

    def __str__(self):
        return f"{self.nome} ({self.semestre})"


class PerfilAluno(models.Model):
    CREATED_VIA_CHOICES = [
        ("signup", "Cadastro"),
        ("admin", "Adicionado pelo ADM"),
        ("legacy", "Legado"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    turma = models.ForeignKey(Turma, on_delete=models.SET_NULL, null=True, blank=True, related_name='alunos')
    
    # Handles
    handle_codeforces = models.CharField(max_length=100, blank=True, null=True, unique=True)
    handle_atcoder = models.CharField(max_length=100, blank=True, null=True, unique=True)
    
    # Métricas de Cache (atualizadas via Celery/Background Tasks)
    rating_atual = models.IntegerField(default=0)
    total_solved = models.IntegerField(default=0)
    cf_rating_current = models.IntegerField(null=True, blank=True)
    cf_rating_max = models.IntegerField(null=True, blank=True)
    cf_rating_updated_at = models.DateTimeField(null=True, blank=True)
    ac_rating_current = models.IntegerField(null=True, blank=True)
    ac_rating_max = models.IntegerField(null=True, blank=True)
    ac_rating_updated_at = models.DateTimeField(null=True, blank=True)
    created_via = models.CharField(
        max_length=12,
        choices=CREATED_VIA_CHOICES,
        default="legacy",
        db_index=True,
    )
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.turma}"


class CompetitorGroup(models.Model):
    HEX_COLOR_VALIDATOR = RegexValidator(
        regex=r"^#[0-9A-Fa-f]{6}$",
        message="Use uma cor hexadecimal no formato #RRGGBB.",
    )

    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, validators=[HEX_COLOR_VALIDATOR], default="#22C55E")
    priority = models.PositiveIntegerField(default=100)
    is_villain = models.BooleanField(
        default=False,
        help_text='Se marcado, os membros deste grupo nao aparecem no ranking.',
    )
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="competitor_groups",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]
        verbose_name = "Grupo Competitivo"
        verbose_name_plural = "Grupos Competitivos"

    def __str__(self):
        return self.name


class CodeforcesRatingChange(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="cf_rating_changes")
    contest_id = models.CharField(max_length=50)
    contest_name = models.CharField(max_length=255, blank=True)
    rating_old = models.IntegerField()
    rating_new = models.IntegerField()
    rating_update_time = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "contest_id"],
                name="cf_rating_change_aluno_contest_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["aluno", "rating_update_time"]),
        ]
        verbose_name = "Codeforces Rating Change"
        verbose_name_plural = "Codeforces Rating Changes"

    def __str__(self):
        return f"{self.aluno.user.username} CF {self.rating_old}->{self.rating_new}"


class AtCoderRatingSnapshot(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="ac_rating_snapshots")
    date = models.DateField(db_index=True)
    rating = models.IntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "date"],
                name="ac_rating_snapshot_aluno_date_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["aluno", "date"]),
        ]
        verbose_name = "AtCoder Rating Snapshot"
        verbose_name_plural = "AtCoder Rating Snapshots"

    def __str__(self):
        return f"{self.aluno.user.username} AC {self.date}={self.rating}"


# --- Estrutura USACO ---

class NivelUSACO(models.Model):
    NIVEL_CHOICES = [
        ('Bronze', 'Bronze'),
        ('Silver', 'Silver'),
        ('Gold', 'Gold'),
        ('Platinum', 'Platinum'),
    ]
    nome = models.CharField(max_length=50, choices=NIVEL_CHOICES, unique=True)
    ordem = models.PositiveIntegerField(default=0, help_text="Ordem de dificuldade")

    class Meta:
        ordering = ['ordem']
        verbose_name = "Nível USACO"
        verbose_name_plural = "Níveis USACO"

    def __str__(self):
        return self.nome


class ModuloTeorico(models.Model):
    nivel = models.ForeignKey(NivelUSACO, on_delete=models.CASCADE, related_name='modulos')
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    link_aula = models.URLField(max_length=500, blank=True, help_text="Link para o USACO Guide ou aula")
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['nivel', 'ordem']
        verbose_name = "Módulo Teórico"
        verbose_name_plural = "Módulos Teóricos"

    def __str__(self):
        return f"[{self.nivel.nome}] {self.titulo}"


class ProblemaReferencia(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
        ('OJ', 'Outros'),
    ]
    modulo = models.ForeignKey(ModuloTeorico, on_delete=models.CASCADE, related_name='problemas')
    titulo = models.CharField(max_length=200)
    problema_id = models.CharField(max_length=100, help_text="Ex: 1980A, abc340_a")
    plataforma = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    link = models.URLField(max_length=500)

    def __str__(self):
        return f"{self.plataforma} - {self.problema_id} ({self.titulo})"


# --- Progresso e Social ---

class SubmissionProof(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='submission_proofs')
    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    problem_url = models.URLField(max_length=500, default="")
    contest_platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES, null=True, blank=True)
    contest_id = models.CharField(max_length=50, null=True, blank=True)
    submission_external_id = models.CharField(max_length=100, null=True, blank=True)
    submission_url = models.URLField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['aluno', 'platform', 'problem_url'],
                name='submissionproof_unique_per_problem',
            ),
        ]
        indexes = [
            models.Index(fields=['problem_url']),
            models.Index(fields=['aluno', 'problem_url']),
            models.Index(fields=['contest_id']),
        ]
        verbose_name = "Submission Proof"
        verbose_name_plural = "Submission Proofs"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.aluno.user.username} - {self.platform} proof"


class SolucaoCompartilhada(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]
    VISIBILITY_CHOICES = [
        ('private', 'Private'),
        ('class', 'Class'),
        ('public', 'Public'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('approved', 'Approved'),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='solucoes')
    platform_context = models.CharField(max_length=2, choices=PLATFORM_CHOICES, null=True, blank=True)
    problem_url = models.URLField(max_length=500, default="")
    contest_id = models.CharField(max_length=50, null=True, blank=True)
    language = models.CharField(max_length=30, choices=LANGUAGE_CHOICES, default="cpp")
    code_text = models.TextField(validators=[MaxLengthValidator(200000)], default="")
    idea_summary = models.TextField(null=True, blank=True)
    complexity = models.CharField(max_length=50, null=True, blank=True)
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default='class')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_solutions',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['aluno', 'problem_url'],
                name='solucao_unique_per_problem',
            ),
        ]
        indexes = [
            models.Index(fields=['problem_url']),
            models.Index(fields=['aluno', 'problem_url']),
            models.Index(fields=['contest_id']),
        ]
        ordering = ['-created_at']
        verbose_name = "Solução Compartilhada"
        verbose_name_plural = "Soluções Compartilhadas"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.status == "published":
            summary = (self.idea_summary or "").strip()
            if len(summary) < 80:
                errors["idea_summary"] = "Resumo precisa ter pelo menos 80 caracteres para publicar."
            code = (self.code_text or "").strip()
            if len(code) < 80:
                errors["code_text"] = "Codigo precisa ter pelo menos 80 caracteres para publicar."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Solução de {self.aluno.user.username}"


class ProgressoModulo(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='progresso_modulos')
    modulo = models.ForeignKey(ModuloTeorico, on_delete=models.CASCADE, related_name='progresso_alunos')
    
    # Status
    teoria_lida = models.BooleanField(default=False)
    todos_problemas_resolvidos = models.BooleanField(default=False)
    
    # Metadata
    data_conclusao = models.DateTimeField(null=True, blank=True)
    ultimo_acesso = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('aluno', 'modulo')
        verbose_name = "Progresso no Módulo"

    def __str__(self):
        status = "Concluído" if self.todos_problemas_resolvidos else "Em andamento"
        return f"{self.aluno.user.username} - {self.modulo.titulo}: {status}"


class Submissao(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='submissoes')
    plataforma = models.CharField(max_length=10, choices=[('CF', 'Codeforces'), ('AC', 'AtCoder')])
    contest_id = models.CharField(max_length=50)  # Ex: '1980', 'abc340'
    problem_index = models.CharField(max_length=10)  # Ex: 'A', 'B'
    verdict = models.CharField(max_length=50)  # Ex: 'OK', 'AC', 'WA'
    submission_time = models.DateTimeField()
    
    # Metadata for UI
    problem_name = models.CharField(max_length=200, null=True, blank=True)
    tags = models.CharField(max_length=500, null=True, blank=True)
    
    # Identificador único na plataforma para evitar duplicatas (ex: id da submissão no CF)
    external_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        ordering = ['-submission_time']
        indexes = [
            models.Index(fields=['contest_id', 'problem_index']),
            models.Index(
                fields=['plataforma', 'contest_id', 'problem_index'],
                name='core_submis_platafo_76be9d_idx',
            ),
            models.Index(fields=['submission_time']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['plataforma', 'external_id'],
                condition=models.Q(external_id__isnull=False),
                name='core_submissao_platform_external_id_uniq',
            ),
        ]
        verbose_name = "Submissão"
        verbose_name_plural = "Submissões"

    def __str__(self):
        return f"{self.aluno.user.username} - {self.plataforma} {self.contest_id}{self.problem_index} ({self.verdict})"


class ProblemRatingCache(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]
    STATUS_CHOICES = [
        ('OK', 'OK'),
        ('NOT_FOUND', 'Not Found'),
        ('TEMP_FAIL', 'Temporary Failure'),
        ('RATE_LIMITED', 'Rate Limited'),
        ('ERROR', 'Error'),
    ]

    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    problem_url = models.URLField(max_length=500, unique=True)
    clist_problem_id = models.CharField(max_length=100, null=True, blank=True)
    clist_rating = models.IntegerField(null=True, blank=True)
    cf_rating = models.IntegerField(null=True, blank=True)
    effective_rating = models.IntegerField(null=True, blank=True)
    rating_fetched_at = models.DateTimeField(null=True, blank=True)
    rating_source = models.CharField(max_length=20, default='none')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OK')
    attempts_count = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['platform', 'status']),
        ]
        verbose_name = "Problem Rating Cache"
        verbose_name_plural = "Problem Rating Cache"

    def __str__(self):
        return f"{self.platform} - {self.problem_url}"


class RatingFetchJob(models.Model):
    PRIORITY_CHOICES = [
        (0, "High"),
        (1, "Low"),
    ]
    STATUS_CHOICES = [
        ("QUEUED", "Queued"),
        ("RUNNING", "Running"),
        ("DONE", "Done"),
        ("FAILED", "Failed"),
    ]

    platform = models.CharField(
        max_length=2,
        choices=[('CF', 'Codeforces'), ('AC', 'AtCoder')],
        db_index=True,
    )
    problem_url = models.URLField(max_length=500)
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=1, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="QUEUED", db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "problem_url"],
                name="rating_fetch_job_unique_problem",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
        ]
        verbose_name = "Rating Fetch Job"
        verbose_name_plural = "Rating Fetch Jobs"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        super().save(*args, **kwargs)


class Contest(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]
    CATEGORY_CHOICES = [
        ('ABC', 'ABC'),
        ('ARC', 'ARC'),
        ('AGC', 'AGC'),
        ('AHC', 'AHC'),
        ('Other', 'Other'),
    ]
    DIVISION_CHOICES = [
        ('Div1', 'Div1'),
        ('Div2', 'Div2'),
        ('Div3', 'Div3'),
        ('Div4', 'Div4'),
        ('Educational', 'Educational'),
        ('Global', 'Global'),
        ('Other', 'Other'),
    ]
    PROBLEM_SYNC_STATUS_CHOICES = [
        ('NEW', 'NEW'),
        ('SYNCED', 'SYNCED'),
        ('STALE', 'STALE'),
        ('FAILED', 'FAILED'),
    ]
    RATING_SUMMARY_STATUS_CHOICES = [
        ('NONE', 'NONE'),
        ('PARTIAL', 'PARTIAL'),
        ('READY', 'READY'),
    ]

    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    contest_id = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    start_time = models.DateTimeField(db_index=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    year = models.IntegerField(db_index=True)
    phase = models.CharField(max_length=50, blank=True, default="")
    is_gym = models.BooleanField(default=False, db_index=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default='Other', db_index=True)
    division = models.CharField(max_length=20, choices=DIVISION_CHOICES, default='Other', db_index=True)
    problems_sync_status = models.CharField(
        max_length=10,
        choices=PROBLEM_SYNC_STATUS_CHOICES,
        default='NEW',
        db_index=True,
    )
    problems_last_synced_at = models.DateTimeField(null=True, blank=True)
    problems_next_sync_at = models.DateTimeField(null=True, blank=True)
    problems_sync_attempts = models.IntegerField(default=0)
    ratings_summary_status = models.CharField(
        max_length=10,
        choices=RATING_SUMMARY_STATUS_CHOICES,
        default='NONE',
        db_index=True,
    )
    ratings_ready_count = models.IntegerField(default=0)
    ratings_total_count = models.IntegerField(default=0)
    ratings_last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['platform', 'contest_id'],
                name='contest_platform_contest_id_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['platform', 'start_time']),
            models.Index(fields=['platform', 'year']),
            models.Index(fields=['problems_sync_status', 'problems_next_sync_at']),
        ]
        verbose_name = "Contest"
        verbose_name_plural = "Contests"

    def save(self, *args, **kwargs):
        if self.start_time:
            self.year = self.start_time.year
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.platform} {self.contest_id} - {self.title}"


class ContestProblem(models.Model):
    RATING_STATUS_CHOICES = [
        ('MISSING', 'Missing'),
        ('QUEUED', 'Queued'),
        ('OK', 'OK'),
        ('NOT_FOUND', 'Not Found'),
        ('TEMP_FAIL', 'Temporary Failure'),
    ]

    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='problems')
    platform = models.CharField(
        max_length=2,
        choices=Contest.PLATFORM_CHOICES,
        default='CF',
        db_index=True,
    )
    order = models.IntegerField(db_index=True)
    index_label = models.CharField(max_length=20)
    problem_url = models.URLField(max_length=500, db_index=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    tags = models.CharField(max_length=500, null=True, blank=True)
    cf_rating = models.IntegerField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    rating_status = models.CharField(
        max_length=12,
        choices=RATING_STATUS_CHOICES,
        default='MISSING',
        db_index=True,
    )
    rating_last_requested_at = models.DateTimeField(null=True, blank=True)
    rating_last_ok_at = models.DateTimeField(null=True, blank=True)
    rating_attempts = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['contest', 'problem_url'],
                name='contest_problem_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['contest', 'order']),
            models.Index(fields=['contest', 'rating_status']),
            models.Index(fields=['platform', 'rating_status']),
        ]
        verbose_name = "Contest Problem"
        verbose_name_plural = "Contest Problems"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        if self.contest and not self.platform:
            self.platform = self.contest.platform
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.contest} {self.index_label}"


class TrainingSession(models.Model):
    MODE_CHOICES = [
        ("consistency", "Consistência"),
        ("general", "Geral"),
        ("evolution", "Evolução"),
        ("challenge", "Desafio"),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="training_sessions")
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default="evolution", db_index=True)
    target_minutes = models.PositiveIntegerField(default=90)
    objective = models.CharField(max_length=200, blank=True, default="2 CF + 2 AC")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["aluno", "is_active", "started_at"]),
        ]
        verbose_name = "Training Session"
        verbose_name_plural = "Training Sessions"

    def __str__(self):
        return f"{self.aluno.user.username} {self.mode} {self.started_at:%Y-%m-%d}"


class TrainingSessionItem(models.Model):
    RESULT_CHOICES = [
        ("TODO", "Todo"),
        ("SOLVED", "Resolvi"),
        ("EDITORIAL", "Usei editorial"),
        ("STUCK", "Travei"),
        ("SKIPPED", "Adiei"),
        ("BLOCKED", "Bloqueei"),
    ]
    STUCK_REASON_CHOICES = [
        ("idea", "Ideia"),
        ("implementation", "Implementação"),
        ("math", "Matemática"),
        ("reading", "Leitura"),
        ("edge_cases", "Edge cases"),
    ]

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name="items")
    platform = models.CharField(max_length=2, choices=Contest.PLATFORM_CHOICES, db_index=True)
    order = models.PositiveIntegerField(default=0, db_index=True)
    problem_url = models.URLField(max_length=500)
    contest_id = models.CharField(max_length=50, null=True, blank=True)
    index_label = models.CharField(max_length=20, null=True, blank=True)
    title = models.CharField(max_length=255, blank=True, default="")
    rating = models.IntegerField(null=True, blank=True)
    tags = models.CharField(max_length=500, blank=True, default="")
    expected_minutes = models.PositiveIntegerField(default=0)
    is_optional = models.BooleanField(default=False)
    origin = models.CharField(max_length=30, blank=True, default="normal_suggestion")
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default="TODO", db_index=True)
    stuck_reason = models.CharField(max_length=20, choices=STUCK_REASON_CHOICES, null=True, blank=True)
    time_spent_seconds = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "platform", "problem_url"],
                name="training_session_item_unique_problem",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "order"]),
            models.Index(fields=["session", "result"]),
        ]
        verbose_name = "Training Session Item"
        verbose_name_plural = "Training Session Items"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.session_id} {self.platform} {self.problem_url}"


class TrainingQueueItem(models.Model):
    STATUS_CHOICES = [
        ("QUEUED", "Na fila"),
        ("DONE", "Concluído"),
        ("BLOCKED", "Bloqueado"),
    ]
    SOURCE_CHOICES = [
        ("cf_suggest", "Sugestão CF"),
        ("ac_suggest", "Sugestão AC"),
        ("manual", "Manual"),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="training_queue")
    platform = models.CharField(max_length=2, choices=Contest.PLATFORM_CHOICES, db_index=True)
    problem_url = models.URLField(max_length=500)
    title = models.CharField(max_length=255, blank=True, default="")
    rating = models.IntegerField(null=True, blank=True)
    tags = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="QUEUED", db_index=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual", db_index=True)
    priority = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "platform", "problem_url"],
                name="training_queue_unique_problem",
            ),
        ]
        indexes = [
            models.Index(fields=["aluno", "status", "priority"]),
        ]
        verbose_name = "Training Queue Item"
        verbose_name_plural = "Training Queue Items"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.aluno.user.username} {self.platform} {self.problem_url}"


class TrainingBlockedProblem(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="training_blocked")
    platform = models.CharField(max_length=2, choices=Contest.PLATFORM_CHOICES, db_index=True)
    problem_url = models.URLField(max_length=500)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "platform", "problem_url"],
                name="training_block_unique_problem",
            ),
        ]
        indexes = [
            models.Index(fields=["aluno", "platform"]),
            models.Index(fields=["problem_url"]),
        ]
        verbose_name = "Training Blocked Problem"
        verbose_name_plural = "Training Blocked Problems"

    def save(self, *args, **kwargs):
        from core.services.problem_urls import normalize_problem_url

        self.problem_url = normalize_problem_url(self.problem_url)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.aluno.user.username} blocked {self.problem_url}"


class SeasonConfig(models.Model):
    name = models.CharField(max_length=100, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Season Config"
        verbose_name_plural = "Season Configs"

    def __str__(self):
        label = self.name or f"{self.start_date} → {self.end_date}"
        return f"{label} ({'active' if self.is_active else 'inactive'})"


class ScoreEvent(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='score_events')
    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    submission = models.OneToOneField(Submissao, on_delete=models.CASCADE, related_name='score_event')
    problem_url = models.URLField(max_length=500)
    solved_at = models.DateTimeField()
    raw_rating = models.IntegerField(null=True, blank=True)
    normalized_rating = models.FloatField(null=True, blank=True)
    percentile = models.FloatField(null=True, blank=True)
    unified_rating = models.FloatField(null=True, blank=True)
    points_cf_raw = models.IntegerField(default=0)
    points_ac_raw = models.IntegerField(default=0)
    points_general_norm = models.IntegerField(default=0)
    points_general_cf_equiv = models.IntegerField(null=True, blank=True)
    rating_used_cf_equiv = models.IntegerField(null=True, blank=True)
    points_awarded = models.IntegerField(default=0)
    in_contest = models.BooleanField(default=False)
    contest_platform = models.CharField(max_length=2, null=True, blank=True)
    contest_id = models.CharField(max_length=50, null=True, blank=True)
    bonus_multiplier = models.FloatField(default=1.0)
    reason = models.CharField(max_length=50, default='first_ac')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['aluno', 'platform', 'problem_url'],
                name='scoreevent_first_solve_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['aluno', 'platform']),
            models.Index(fields=['solved_at']),
            models.Index(fields=['problem_url']),
        ]
        verbose_name = "Score Event"
        verbose_name_plural = "Score Events"

    def __str__(self):
        return f"{self.aluno.user.username} - {self.platform} {self.points_awarded} pts"


class UserScoreAgg(models.Model):
    aluno = models.OneToOneField(PerfilAluno, on_delete=models.CASCADE, related_name='score_agg')
    points_cf_total = models.IntegerField(default=0)
    points_ac_total = models.IntegerField(default=0)
    points_total = models.IntegerField(default=0)
    points_last_7d = models.IntegerField(default=0)
    points_last_30d = models.IntegerField(default=0)
    points_cf_raw_total = models.IntegerField(default=0)
    points_ac_raw_total = models.IntegerField(default=0)
    points_general_norm_total = models.IntegerField(default=0)
    points_general_cf_equiv_total = models.IntegerField(default=0)
    points_cf_7d = models.IntegerField(default=0)
    points_ac_7d = models.IntegerField(default=0)
    points_general_7d = models.IntegerField(default=0)
    points_general_cf_equiv_7d = models.IntegerField(default=0)
    points_cf_30d = models.IntegerField(default=0)
    points_ac_30d = models.IntegerField(default=0)
    points_general_30d = models.IntegerField(default=0)
    points_general_cf_equiv_30d = models.IntegerField(default=0)
    season_points_cf_raw = models.IntegerField(default=0)
    season_points_ac_raw = models.IntegerField(default=0)
    season_points_general_norm = models.IntegerField(default=0)
    season_points_general_cf_equiv = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Score Aggregate"
        verbose_name_plural = "User Score Aggregates"

    def __str__(self):
        return f"{self.aluno.user.username} - {self.points_total} pts"


class PlatformRatingStats(models.Model):
    PLATFORM_CHOICES = [
        ('CF', 'Codeforces'),
        ('AC', 'AtCoder'),
    ]

    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES, unique=True)
    median = models.FloatField()
    iqr = models.FloatField()
    sample_size = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Rating Stats"
        verbose_name_plural = "Platform Rating Stats"

    def __str__(self):
        return f"{self.platform} median={self.median} iqr={self.iqr}"


class RatingConversionModel(models.Model):
    DIRECTION_CHOICES = [
        ("AC_TO_CF", "AC_TO_CF"),
        ("CF_TO_AC", "CF_TO_AC"),
    ]
    METHOD_CHOICES = [
        ("bin_mean_monotone_v1", "bin_mean_monotone_v1"),
    ]
    SOURCE_CHOICES = [
        ("internal_users", "internal_users"),
    ]

    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    method = models.CharField(max_length=30, choices=METHOD_CHOICES)
    source_population = models.CharField(max_length=30, choices=SOURCE_CHOICES)
    min_pair_count = models.IntegerField(default=200)
    min_activity_rules_json = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rating Conversion Model"
        verbose_name_plural = "Rating Conversion Models"

    def __str__(self):
        return f"{self.direction} {self.method}"


class RatingConversionPoint(models.Model):
    model = models.ForeignKey(RatingConversionModel, on_delete=models.CASCADE, related_name="points")
    x_rating = models.IntegerField()
    y_rating = models.IntegerField()
    sample_n = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["model", "x_rating"], name="rating_conversion_point_unique"),
        ]
        indexes = [
            models.Index(fields=["model", "x_rating"]),
        ]
        verbose_name = "Rating Conversion Point"
        verbose_name_plural = "Rating Conversion Points"

    def __str__(self):
        return f"{self.model} {self.x_rating}->{self.y_rating}"


class RatingConversionSnapshot(models.Model):
    model = models.ForeignKey(RatingConversionModel, on_delete=models.CASCADE, related_name="snapshots")
    computed_at = models.DateTimeField()
    pairs_used = models.IntegerField()
    mae = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Rating Conversion Snapshot"
        verbose_name_plural = "Rating Conversion Snapshots"

    def __str__(self):
        return f"{self.model} {self.computed_at.date()}"


class UserRankSnapshot(models.Model):
    SCOPE_CHOICES = [
        ("GLOBAL", "Global"),
        ("TURMA", "Turma"),
    ]
    CATEGORY_CHOICES = [
        ("TOTAL", "Geral"),
        ("CF", "Codeforces"),
        ("AC", "AtCoder"),
    ]
    WINDOW_CHOICES = [
        ("ALL", "All-time"),
        ("7D", "Last 7 days"),
        ("30D", "Last 30 days"),
        ("SEASON", "Season"),
    ]

    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name="rank_snapshots")
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES)
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, null=True, blank=True)
    category = models.CharField(max_length=5, choices=CATEGORY_CHOICES)
    window = models.CharField(max_length=10, choices=WINDOW_CHOICES)
    rank = models.PositiveIntegerField()
    points = models.IntegerField(default=0)
    snapshot_date = models.DateField()
    mode = models.CharField(max_length=10, null=True, blank=True)
    source = models.CharField(max_length=10, null=True, blank=True)
    window_key = models.CharField(max_length=10, null=True, blank=True)
    scope_key = models.CharField(max_length=20, null=True, blank=True)
    value = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "scope", "turma", "category", "window", "snapshot_date"],
                name="user_rank_snapshot_unique",
            ),
            models.UniqueConstraint(
                fields=["mode", "source", "window_key", "scope_key", "snapshot_date", "aluno"],
                name="user_rank_snapshot_unique_v2",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "category", "window", "snapshot_date"]),
            models.Index(fields=["mode", "source", "window_key", "scope_key", "snapshot_date"]),
        ]
        verbose_name = "User Rank Snapshot"
        verbose_name_plural = "User Rank Snapshots"

    def __str__(self):
        return f"{self.aluno.user.username} #{self.rank} {self.category} {self.window}"
