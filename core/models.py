from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

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
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.turma}"


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

class SolucaoCompartilhada(models.Model):
    aluno = models.ForeignKey(PerfilAluno, on_delete=models.CASCADE, related_name='solucoes')
    problema = models.ForeignKey(ProblemaReferencia, on_delete=models.CASCADE, related_name='solucoes_alunos')
    codigo = models.TextField(help_text="Cole seu código aqui")
    linguagem = models.CharField(max_length=50, default='C++', help_text="Linguagem utilizada")
    data_postagem = models.DateTimeField(default=timezone.now)
    votos_uteis = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['-data_postagem']
        verbose_name = "Solução Compartilhada"

    def __str__(self):
        return f"Solução de {self.aluno.user.username} para {self.problema}"


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
    ]

    platform = models.CharField(max_length=2, choices=PLATFORM_CHOICES)
    problem_url = models.URLField(max_length=500, unique=True)
    clist_problem_id = models.CharField(max_length=100, null=True, blank=True)
    clist_rating = models.IntegerField(null=True, blank=True)
    rating_fetched_at = models.DateTimeField(null=True, blank=True)
    rating_source = models.CharField(max_length=50, default='clist_api_v4')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OK')

    class Meta:
        indexes = [
            models.Index(fields=['platform', 'status']),
        ]
        verbose_name = "Problem Rating Cache"
        verbose_name_plural = "Problem Rating Cache"

    def __str__(self):
        return f"{self.platform} - {self.problem_url}"


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
    points_awarded = models.IntegerField(default=0)
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
    points_cf_7d = models.IntegerField(default=0)
    points_ac_7d = models.IntegerField(default=0)
    points_general_7d = models.IntegerField(default=0)
    points_cf_30d = models.IntegerField(default=0)
    points_ac_30d = models.IntegerField(default=0)
    points_general_30d = models.IntegerField(default=0)
    season_points_cf_raw = models.IntegerField(default=0)
    season_points_ac_raw = models.IntegerField(default=0)
    season_points_general_norm = models.IntegerField(default=0)
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "scope", "turma", "category", "window", "snapshot_date"],
                name="user_rank_snapshot_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "category", "window", "snapshot_date"]),
        ]
        verbose_name = "User Rank Snapshot"
        verbose_name_plural = "User Rank Snapshots"

    def __str__(self):
        return f"{self.aluno.user.username} #{self.rank} {self.category} {self.window}"
