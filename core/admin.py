from django.contrib import admin
from .models import (
    PerfilAluno,
    Turma,
    NivelUSACO,
    ModuloTeorico,
    ProblemaReferencia,
    SolucaoCompartilhada,
    ProgressoModulo,
    Submissao,
    ProblemRatingCache,
    ScoreEvent,
    UserScoreAgg,
    PlatformRatingStats,
    UserRankSnapshot,
)

@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'semestre')
    search_fields = ('nome', 'semestre')

@admin.register(PerfilAluno)
class PerfilAlunoAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'turma',
        'handle_codeforces',
        'handle_atcoder',
        'cf_rating_current',
        'ac_rating_current',
        'rating_atual',
        'total_solved',
    )
    search_fields = ('user__username', 'handle_codeforces', 'handle_atcoder')
    list_filter = ('turma',)

class ModuloTeoricoInline(admin.TabularInline):
    model = ModuloTeorico
    extra = 0

@admin.register(NivelUSACO)
class NivelUSACOAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ordem')
    ordering = ('ordem',)
    inlines = [ModuloTeoricoInline]

class ProblemaReferenciaInline(admin.TabularInline):
    model = ProblemaReferencia
    extra = 0

@admin.register(ModuloTeorico)
class ModuloTeoricoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'nivel', 'ordem')
    list_filter = ('nivel',)
    ordering = ('nivel__ordem', 'ordem')
    inlines = [ProblemaReferenciaInline]

@admin.register(ProblemaReferencia)
class ProblemaReferenciaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'problema_id', 'plataforma', 'modulo')
    list_filter = ('plataforma', 'modulo__nivel')
    search_fields = ('titulo', 'problema_id')

@admin.register(SolucaoCompartilhada)
class SolucaoCompartilhadaAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'problema', 'linguagem', 'data_postagem', 'votos_uteis')
    list_filter = ('linguagem', 'data_postagem')

@admin.register(ProgressoModulo)
class ProgressoModuloAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'modulo', 'teoria_lida', 'todos_problemas_resolvidos')
    list_filter = ('teoria_lida', 'todos_problemas_resolvidos')

@admin.register(Submissao)
class SubmissaoAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'plataforma', 'contest_id', 'problem_index', 'verdict', 'submission_time')
    list_filter = ('plataforma', 'verdict', 'submission_time')
    search_fields = ('aluno__user__username', 'contest_id', 'external_id')
    ordering = ('-submission_time',)


@admin.register(ProblemRatingCache)
class ProblemRatingCacheAdmin(admin.ModelAdmin):
    list_display = ('platform', 'problem_url', 'clist_rating', 'status', 'rating_fetched_at')
    list_filter = ('platform', 'status')
    search_fields = ('problem_url', 'clist_problem_id')
    ordering = ('-rating_fetched_at',)


@admin.register(ScoreEvent)
class ScoreEventAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'platform', 'points_awarded', 'raw_rating', 'normalized_rating', 'solved_at')
    list_filter = ('platform', 'reason')
    search_fields = ('aluno__user__username', 'problem_url')
    ordering = ('-solved_at',)


@admin.register(UserScoreAgg)
class UserScoreAggAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'points_cf_total', 'points_ac_total', 'points_total', 'updated_at')
    ordering = ('-points_total',)


@admin.register(PlatformRatingStats)
class PlatformRatingStatsAdmin(admin.ModelAdmin):
    list_display = ('platform', 'median', 'iqr', 'sample_size', 'updated_at')
    ordering = ('platform',)


@admin.register(UserRankSnapshot)
class UserRankSnapshotAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'scope', 'category', 'window', 'rank', 'points', 'snapshot_date')
    list_filter = ('scope', 'category', 'window', 'snapshot_date')
    search_fields = ('aluno__user__username',)
