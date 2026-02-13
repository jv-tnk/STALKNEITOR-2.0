from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import (
    CompetitorGroup,
    Contest,
    ContestProblem,
    PerfilAluno,
    SeasonConfig,
    ProblemRatingCache,
    PlatformRatingStats,
    ProgressoModulo,
    ProblemaReferencia,
    RatingConversionModel,
    RatingConversionPoint,
    RatingConversionSnapshot,
    ScoreEvent,
    SolucaoCompartilhada,
    SubmissionProof,
    Submissao,
    Turma,
    UserScoreAgg,
    ModuloTeorico,
    NivelUSACO,
    UserRankSnapshot,
)

User = get_user_model()

admin.site.site_header = "Stalkineitor Administracao"
admin.site.site_title = "Stalkineitor Admin"
admin.site.index_title = "Painel de administracao"


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


class SuperuserOnlyAdmin(admin.ModelAdmin):
    """
    Esconde modelos técnicos para staff comum e mantém visível apenas para superuser.
    """

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('username', 'email', 'is_staff', 'is_superuser', 'is_active', 'last_login')
    list_filter = ('is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email')
    ordering = ('username',)
    actions = ['make_superuser', 'remove_superuser']

    def make_superuser(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "Somente superusers podem promover.", level=messages.ERROR)
            return
        updated = queryset.update(is_superuser=True, is_staff=True)
        self.message_user(request, f"{updated} usuario(s) promovido(s) a superuser.", level=messages.SUCCESS)

    def remove_superuser(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, "Somente superusers podem rebaixar.", level=messages.ERROR)
            return
        updated = queryset.update(is_superuser=False)
        self.message_user(request, f"{updated} usuario(s) rebaixado(s) de superuser.", level=messages.SUCCESS)

    make_superuser.short_description = "Promover a superuser"
    remove_superuser.short_description = "Rebaixar de superuser"


@admin.register(CompetitorGroup)
class CompetitorGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_villain", "color_swatch", "priority", "members_count")
    list_filter = ("is_villain",)
    search_fields = ("name", "users__username", "users__email")
    filter_horizontal = ("users",)
    ordering = ("priority", "name")

    @admin.display(description="Cor")
    def color_swatch(self, obj: CompetitorGroup):
        return f"{obj.color}"

    @admin.display(description="Membros")
    def members_count(self, obj: CompetitorGroup):
        return obj.users.count()


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
    list_display = ('aluno', 'problem_url', 'language', 'status', 'visibility', 'created_at')
    list_filter = ('language', 'status', 'visibility')
    search_fields = ('aluno__user__username', 'problem_url')
    ordering = ('-created_at',)

@admin.register(ProgressoModulo)
class ProgressoModuloAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'modulo', 'teoria_lida', 'todos_problemas_resolvidos')
    list_filter = ('teoria_lida', 'todos_problemas_resolvidos')


@admin.register(SeasonConfig)
class SeasonConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("-start_date",)


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = (
        "platform",
        "contest_id",
        "title",
        "start_time",
        "year",
        "problems_sync_status",
        "ratings_summary_status",
    )
    list_filter = ("platform", "year", "problems_sync_status", "ratings_summary_status", "division", "category")
    search_fields = ("contest_id", "title")
    ordering = ("-start_time",)


@admin.register(ContestProblem)
class ContestProblemAdmin(admin.ModelAdmin):
    list_display = ("contest", "index_label", "name", "platform", "cf_rating", "rating_status", "rating_attempts")
    list_filter = ("platform", "rating_status")
    search_fields = ("contest__contest_id", "name", "problem_url")
    ordering = ("contest", "order")


@admin.register(SubmissionProof)
class SubmissionProofAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'platform', 'problem_url', 'contest_id', 'created_at')
    list_filter = ('platform',)
    search_fields = ('aluno__user__username', 'problem_url', 'contest_id')
    ordering = ('-created_at',)

@admin.register(Submissao)
class SubmissaoAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'plataforma', 'contest_id', 'problem_index', 'verdict', 'submission_time')
    list_filter = ('plataforma', 'verdict', 'submission_time')
    search_fields = ('aluno__user__username', 'contest_id', 'external_id')
    ordering = ('-submission_time',)


@admin.register(ProblemRatingCache)
class ProblemRatingCacheAdmin(SuperuserOnlyAdmin):
    list_display = ('platform', 'problem_url', 'clist_rating', 'status', 'rating_fetched_at')
    list_filter = ('platform', 'status')
    search_fields = ('problem_url', 'clist_problem_id')
    ordering = ('-rating_fetched_at',)


@admin.register(ScoreEvent)
class ScoreEventAdmin(SuperuserOnlyAdmin):
    list_display = ('aluno', 'platform', 'points_awarded', 'raw_rating', 'normalized_rating', 'solved_at')
    list_filter = ('platform', 'reason')
    search_fields = ('aluno__user__username', 'problem_url')
    ordering = ('-solved_at',)


@admin.register(UserScoreAgg)
class UserScoreAggAdmin(SuperuserOnlyAdmin):
    list_display = ('aluno', 'points_cf_total', 'points_ac_total', 'points_total', 'updated_at')
    ordering = ('-points_total',)


@admin.register(PlatformRatingStats)
class PlatformRatingStatsAdmin(SuperuserOnlyAdmin):
    list_display = ('platform', 'median', 'iqr', 'sample_size', 'updated_at')
    ordering = ('platform',)


@admin.register(RatingConversionModel)
class RatingConversionModelAdmin(SuperuserOnlyAdmin):
    list_display = ('direction', 'method', 'source_population', 'min_pair_count', 'is_active', 'updated_at')
    list_filter = ('direction', 'is_active')


@admin.register(RatingConversionPoint)
class RatingConversionPointAdmin(SuperuserOnlyAdmin):
    list_display = ('model', 'x_rating', 'y_rating', 'sample_n', 'created_at')
    list_filter = ('model',)
    ordering = ('model', 'x_rating')


@admin.register(RatingConversionSnapshot)
class RatingConversionSnapshotAdmin(SuperuserOnlyAdmin):
    list_display = ('model', 'computed_at', 'pairs_used', 'mae')
    list_filter = ('model',)


@admin.register(UserRankSnapshot)
class UserRankSnapshotAdmin(SuperuserOnlyAdmin):
    list_display = ('aluno', 'scope', 'category', 'window', 'rank', 'points', 'snapshot_date')
    list_filter = ('scope', 'category', 'window', 'snapshot_date')
    search_fields = ('aluno__user__username',)
