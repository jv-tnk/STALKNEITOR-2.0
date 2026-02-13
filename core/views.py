import json
import time
import requests
from django.shortcuts import render, get_object_or_404, redirect
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Max, Sum, Q, Count, Exists, OuterRef
from django.db.models.functions import TruncDate
from datetime import datetime, timedelta
from collections import Counter
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import models
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.cache import cache
from django.utils import timezone
from urllib.parse import urlsplit, urlencode
from .models import (
    CompetitorGroup,
    Contest,
    ContestProblem,
    AtCoderRatingSnapshot,
    PerfilAluno,
    ProblemaReferencia,
    ProblemRatingCache,
    ScoreEvent,
    SolucaoCompartilhada,
    Submissao,
    TrainingBlockedProblem,
    TrainingQueueItem,
    TrainingSession,
    TrainingSessionItem,
    Turma,
    UserScoreAgg,
    SeasonConfig,
)
from .services.api_client import CodeforcesClient, AtCoderClient
from .services.ranking import (
    build_ranking_with_delta,
    build_rating_ranking_with_delta,
    top_movers_last_7d,
    tier_for_points,
)
from .services.scoring import calculate_points
from .services.problem_urls import build_problem_url_from_fields
from .services.rating_conversion import convert_ac_to_cf, get_conversion_status
from .services.season import get_active_season_range
from .services.training import (
    build_ac_suggestions,
    build_cf_suggestions,
    build_training_inventory,
    get_baseline_ac,
    get_baseline_cf,
    get_cf_training_zone,
    get_session_plan,
    estimate_expected_minutes,
)


def _build_admin_api_catalog() -> list[dict]:
    return [
        {
            "name": "Codeforces API",
            "base_url": "https://codeforces.com/api",
            "usage": "Buscar dados de usuario, contests e submissões no Codeforces.",
            "internal": "core/services/api_client.py (CodeforcesClient), core/services/contest_catalog.py",
            "notes": "Pode limitar chamadas em horario de pico.",
        },
        {
            "name": "AtCoder API (Kenkoooo v3)",
            "base_url": "https://kenkoooo.com/atcoder/atcoder-api/v3",
            "usage": "Buscar submissões e rating do AtCoder.",
            "internal": "core/services/api_client.py (AtCoderClient)",
            "notes": "Se cair, o sistema usa o site oficial como plano B.",
        },
        {
            "name": "AtCoder Resources (S3 fallback)",
            "base_url": "https://s3.ap-northeast-1.amazonaws.com/kenkoooo.com/resources",
            "usage": "Plano B para lista de contests e problemas do AtCoder.",
            "internal": "core/services/contest_catalog.py",
            "notes": "Fornece arquivos estaticos quando a API principal falha.",
        },
        {
            "name": "AtCoder Oficial (history fallback)",
            "base_url": "https://atcoder.jp/users/<handle>/history/json",
            "usage": "Plano C para obter historico de rating direto do AtCoder.",
            "internal": "core/services/api_client.py (AtCoderClient._get_user_info_from_official)",
            "notes": "Pode vir vazio para usuario sem contest valendo rating.",
        },
        {
            "name": "CLIST API v4",
            "base_url": "https://clist.by/api/v4/problem/",
            "usage": "Buscar dificuldade dos problemas para pontuação e ranking.",
            "internal": "core/services/clist_client.py, core/tasks.py",
            "notes": "Tem limite de chamadas por minuto.",
        },
    ]


def _run_admin_api_checks(force_refresh: bool = False) -> tuple[list[dict], datetime | None]:
    cache_key = "admin_api_checks_v1"
    ttl_seconds = 180

    cached = None if force_refresh else cache.get(cache_key)
    if cached:
        checked_at = None
        raw_checked_at = cached.get("checked_at")
        if raw_checked_at:
            try:
                checked_at = datetime.fromisoformat(raw_checked_at)
                if timezone.is_naive(checked_at):
                    checked_at = timezone.make_aware(checked_at, timezone.get_current_timezone())
            except Exception:
                checked_at = None
        return cached.get("checks", []), checked_at

    def _probe(
        *,
        name: str,
        endpoint: str,
        purpose: str,
        params: dict | None = None,
        timeout: int = 8,
        parser=None,
    ) -> dict:
        started = time.monotonic()
        result = {
            "name": name,
            "endpoint": endpoint,
            "purpose": purpose,
            "ok": False,
            "status_code": None,
            "latency_ms": None,
            "detail": "Sem resposta.",
        }
        try:
            response = requests.get(endpoint, params=params, timeout=timeout)
            result["status_code"] = response.status_code
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            if parser:
                ok, detail = parser(response)
            else:
                ok = response.status_code == 200
                detail = f"HTTP {response.status_code}"
            result["ok"] = bool(ok)
            result["detail"] = detail
            return result
        except Exception as exc:
            result["latency_ms"] = int((time.monotonic() - started) * 1000)
            result["detail"] = f"Erro: {exc}"
            return result

    sample_cf_handle = (
        PerfilAluno.objects.exclude(handle_codeforces__isnull=True)
        .exclude(handle_codeforces="")
        .values_list("handle_codeforces", flat=True)
        .first()
        or "tourist"
    )
    sample_ac_handle = (
        PerfilAluno.objects.exclude(handle_atcoder__isnull=True)
        .exclude(handle_atcoder="")
        .values_list("handle_atcoder", flat=True)
        .first()
        or "chokudai"
    )
    sample_problem_url = (
        ContestProblem.objects.filter(platform="CF")
        .exclude(problem_url__isnull=True)
        .exclude(problem_url="")
        .values_list("problem_url", flat=True)
        .first()
        or "https://codeforces.com/contest/1/problem/A"
    )

    checks = []
    checks.append(
        _probe(
            name="Codeforces API",
            endpoint="https://codeforces.com/api/user.info",
            params={"handles": sample_cf_handle},
            purpose=f"Conferir se conseguimos ler dados publicos do Codeforces (exemplo: {sample_cf_handle}).",
            parser=lambda response: (
                response.status_code == 200
                and (response.json().get("status") == "OK"),
                (
                    "OK"
                    if response.status_code == 200 and response.json().get("status") == "OK"
                    else f"HTTP {response.status_code} • {response.json().get('comment') if response.status_code == 200 else 'falha'}"
                ),
            ),
        )
    )

    checks.append(
        _probe(
            name="AtCoder API (Kenkoooo)",
            endpoint="https://kenkoooo.com/atcoder/atcoder-api/v3/user/info",
            params={"user": sample_ac_handle},
            purpose=f"Conferir se conseguimos ler dados principais do AtCoder (exemplo: {sample_ac_handle}).",
            parser=lambda response: (
                response.status_code == 200,
                "OK" if response.status_code == 200 else f"HTTP {response.status_code}",
            ),
        )
    )

    checks.append(
        _probe(
            name="AtCoder Resources (S3 fallback)",
            endpoint="https://s3.ap-northeast-1.amazonaws.com/kenkoooo.com/resources/contests.json",
            purpose="Conferir o plano B de lista de contests/problemas do AtCoder.",
            parser=lambda response: (
                response.status_code == 200,
                "OK" if response.status_code == 200 else f"HTTP {response.status_code}",
            ),
        )
    )

    checks.append(
        _probe(
            name="AtCoder Oficial (fallback)",
            endpoint=f"https://atcoder.jp/users/{sample_ac_handle}/history/json",
            purpose=f"Conferir o plano C de historico de rating no site oficial (exemplo: {sample_ac_handle}).",
            parser=lambda response: (
                response.status_code == 200,
                "OK" if response.status_code == 200 else f"HTTP {response.status_code}",
            ),
        )
    )

    clist_username = getattr(settings, "CLIST_USERNAME", "")
    clist_api_key = getattr(settings, "CLIST_API_KEY", "")
    clist_base = getattr(settings, "CLIST_API_URL", "https://clist.by/api/v4").rstrip("/")
    clist_params = {"format": "json", "url": sample_problem_url}
    if clist_username and clist_api_key:
        clist_params.update({"username": clist_username, "api_key": clist_api_key})

    checks.append(
        _probe(
            name="CLIST API v4",
            endpoint=f"{clist_base}/problem/",
            params=clist_params,
            purpose="Conferir a API que informa dificuldade dos problemas.",
            parser=lambda response: (
                response.status_code == 200,
                (
                    "OK"
                    if response.status_code == 200
                    else (
                        "Rate limit (429) - aguarde cooldown"
                        if response.status_code == 429
                        else f"HTTP {response.status_code}"
                    )
                ),
            ),
        )
    )

    checked_at = timezone.now()
    cache.set(
        cache_key,
        {"checked_at": checked_at.isoformat(), "checks": checks},
        timeout=ttl_seconds,
    )
    return checks, checked_at


def _rating_badge(rating, status, platform):
    if rating is None:
        label = "Sem rating" if status == "NOT_FOUND" else "Pendente"
        return label, "#64748b", 0, "#e2e8f0"

    platform = (platform or "").upper()
    if platform == "AC":
        tiers = [
            {"min": 0, "max": 399, "color": "#808080"},
            {"min": 400, "max": 799, "color": "#7A4A12"},
            {"min": 800, "max": 1199, "color": "#00A900"},
            {"min": 1200, "max": 1599, "color": "#03A89E"},
            {"min": 1600, "max": 1999, "color": "#1E88E5"},
            {"min": 2000, "max": 2399, "color": "#FFD700"},
            {"min": 2400, "max": 2799, "color": "#FF8C00"},
            {"min": 2800, "max": 3199, "color": "#FF0000"},
            {"min": 3200, "max": None, "color": "#7F0000"},
        ]
    else:
        tiers = [
            {"min": 0, "max": 1199, "color": "#BFBFBF"},
            {"min": 1200, "max": 1399, "color": "#00A900"},
            {"min": 1400, "max": 1599, "color": "#03A89E"},
            {"min": 1600, "max": 1899, "color": "#1E88E5"},
            {"min": 1900, "max": 2099, "color": "#AA00AA"},
            {"min": 2100, "max": 2299, "color": "#FF8C00"},
            {"min": 2300, "max": 2399, "color": "#FF8C00"},
            {"min": 2400, "max": 2599, "color": "#FF0000"},
            {"min": 2600, "max": 2999, "color": "#FF0000"},
            {"min": 3000, "max": None, "color": "#FF0000"},
        ]

    color = "#64748b"
    tier_min = 0.0
    tier_max = None
    for tier in tiers:
        if tier["max"] is None or rating <= tier["max"]:
            color = tier["color"]
            tier_min = float(tier["min"])
            tier_max = float(tier["max"]) if tier["max"] is not None else None
            break

    if tier_max is None:
        fill_percent = 100
    else:
        clamped = max(tier_min, min(float(rating), tier_max))
        span = max(1.0, tier_max - tier_min)
        ratio = (clamped - tier_min) / span
        fill_percent = int(round(ratio * 100))

    if fill_percent < 5:
        fill_percent = 2
    if fill_percent > 95:
        fill_percent = 100

    text_color = "#ffffff"

    return f"{int(rating)}", color, fill_percent, text_color


def _resolve_effective_rating(problem: ContestProblem | None, cache: ProblemRatingCache | None) -> tuple[int | None, str]:
    """
    Returns (rating, status) using effective_rating (clist -> cf fallback).
    Keeps status aligned to avoid "OK with no rating" states.
    """
    rating = None
    status = "TEMP_FAIL"
    if cache:
        if cache.effective_rating is not None:
            rating = int(cache.effective_rating)
            status = "OK"
        else:
            status = cache.status or "TEMP_FAIL"
            if status == "OK":
                status = "TEMP_FAIL"
    if rating is None and problem is not None:
        if getattr(problem, "cf_rating", None) is not None:
            rating = int(problem.cf_rating)
            status = "OK"
        elif getattr(problem, "rating_status", "") == "NOT_FOUND":
            status = "NOT_FOUND"
    return rating, status


def _season_label(season: SeasonConfig) -> str:
    if season.name:
        return season.name
    return f"{season.start_date:%d/%m/%Y} → {season.end_date:%d/%m/%Y}"


def _season_name(season: SeasonConfig) -> str:
    if season.name:
        return season.name
    return f"Temporada #{season.id}"


def _resolve_season_selection(season_id: str | None) -> tuple[list[SeasonConfig], SeasonConfig | None, datetime | None, datetime | None]:
    seasons = list(SeasonConfig.objects.order_by("-start_date"))
    active_candidates = [season for season in seasons if season.is_active]
    selected = max(active_candidates, key=lambda season: season.updated_at) if active_candidates else None
    if season_id and str(season_id).isdigit():
        selected_by_id = next((season for season in seasons if season.id == int(season_id)), None)
        selected = selected_by_id or selected
    if selected:
        start_dt = timezone.make_aware(datetime.combine(selected.start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(selected.end_date, datetime.max.time()))
    else:
        start_dt = None
        end_dt = None
    return seasons, selected, start_dt, end_dt


def _villain_user_ids() -> set[int]:
    return set(
        CompetitorGroup.objects.filter(is_villain=True).values_list("users__id", flat=True)
    )


def _build_user_sync_status(user: User) -> dict:
    profile = getattr(user, "perfil", None)
    if not profile:
        return {
            "overall_percent": 0,
            "components": [
                {"label": "Perfil", "percent": 0, "detail": "Nao encontrado"},
            ],
        }

    now = timezone.now()
    freshness_limit = now - timedelta(hours=24)

    handles_total = 0
    handles_synced = 0
    if profile.handle_codeforces:
        handles_total += 1
        if profile.cf_rating_updated_at and profile.cf_rating_updated_at >= freshness_limit:
            handles_synced += 1
    if profile.handle_atcoder:
        handles_total += 1
        if profile.ac_rating_updated_at and profile.ac_rating_updated_at >= freshness_limit:
            handles_synced += 1
    handles_percent = int(round((handles_synced / handles_total) * 100)) if handles_total else 100

    accepted_unique = (
        Submissao.objects.filter(aluno=profile)
        .filter(Q(plataforma="CF", verdict="OK") | Q(plataforma="AC", verdict="AC"))
        .values("plataforma", "contest_id", "problem_index")
        .distinct()
        .count()
    )
    score_stats = ScoreEvent.objects.filter(aluno=profile).aggregate(
        total=Count("id"),
        rated=Count("id", filter=Q(raw_rating__isnull=False)),
    )
    score_events_total = int(score_stats.get("total") or 0)
    scoring_percent = (
        int(round((min(score_events_total, accepted_unique) / accepted_unique) * 100))
        if accepted_unique
        else 100
    )

    rated_events = int(score_stats.get("rated") or 0)
    ratings_percent = (
        int(round((rated_events / score_events_total) * 100))
        if score_events_total
        else 100
    )

    components = [
        {
            "label": "Plataformas atualizadas (24h)",
            "percent": handles_percent,
            "detail": f"{handles_synced}/{handles_total}",
        },
        {
            "label": "Submissoes processadas",
            "percent": scoring_percent,
            "detail": f"{min(score_events_total, accepted_unique)}/{accepted_unique}",
        },
        {
            "label": "Eventos com rating",
            "percent": ratings_percent,
            "detail": f"{rated_events}/{score_events_total}",
        },
    ]
    overall_percent = int(
        round(sum(item["percent"] for item in components) / len(components))
    )
    return {
        "overall_percent": overall_percent,
        "components": components,
        "updated_at": now,
    }


def _contest_external_url(platform: str, contest_id: str) -> str:
    if platform == "CF":
        return f"https://codeforces.com/contest/{contest_id}"
    return f"https://atcoder.jp/contests/{contest_id}"


def _contest_row_fallback(contest: Contest) -> dict:
    return {
        "contest": contest,
        "detail_url": reverse(
            "contest_detail",
            kwargs={
                "platform": contest.platform.lower(),
                "contest_id": contest.contest_id,
            },
        ),
        "url": _contest_external_url(contest.platform, contest.contest_id),
        "problems": [],
        "average_color": "#64748b",
        "average_fill": 0,
        "average_text": "#e2e8f0",
        "average_display": "Media pendente",
        "ratings_ready": 0,
        "ratings_total": 0,
        "ratings_status": "NONE",
    }


def _get_dashboard_recent_submissions(villain_ids: set[int], limit: int = 20) -> list[Submissao]:
    recent_subs_qs = (
        Submissao.objects.select_related("aluno__user")
        .only(
            "id",
            "plataforma",
            "contest_id",
            "external_id",
            "problem_index",
            "submission_time",
            "verdict",
            "aluno_id",
            "aluno__user_id",
            "aluno__user__username",
        )
        .order_by("-submission_time")
    )
    if villain_ids:
        recent_subs_qs = recent_subs_qs.exclude(aluno__user_id__in=villain_ids)
    recent_subs = list(recent_subs_qs[:limit])
    for sub in recent_subs:
        sub.submission_url = _submission_url(sub.plataforma, sub.contest_id, sub.external_id)
    return recent_subs


def _build_points_rows_from_events(events, category: str, scope: str, turma_id: int | None):
    agg = events.values("aluno_id").annotate(
        points_cf=Sum("points_cf_raw"),
        points_ac=Sum("points_ac_raw"),
        points_general=Sum("points_general_cf_equiv"),
        solves=Count("id"),
    )
    agg_map = {row["aluno_id"]: row for row in agg}
    alunos = (
        PerfilAluno.objects.select_related("user")
        .only(
            "id",
            "user_id",
            "turma_id",
            "handle_codeforces",
            "handle_atcoder",
            "user__id",
            "user__username",
        )
    )
    villain_ids = _villain_user_ids()
    if villain_ids:
        alunos = alunos.exclude(user_id__in=villain_ids)
    if scope == "turma" and turma_id:
        alunos = alunos.filter(turma_id=turma_id)

    rows = []
    for aluno in alunos:
        data = agg_map.get(aluno.id, {})
        points_cf = int(data.get("points_cf") or 0)
        points_ac = int(data.get("points_ac") or 0)
        points_general = int(data.get("points_general") or 0)
        points = points_general if category == "overall" else (points_cf if category == "cf" else points_ac)
        row = type("Row", (), {})()
        tier_name, next_tier, progress, tier_range, tier_color, points_to_next = tier_for_points(points)
        row.aluno = aluno
        row.points = points
        row.points_cf = points_cf
        row.points_ac = points_ac
        row.weekly_points = 0
        row.rank = 0
        row.delta = 0
        row.tier_name = tier_name
        row.tier_next = next_tier
        row.tier_progress = progress
        row.tier_range = tier_range
        row.tier_color = tier_color
        row.points_to_next = points_to_next
        row.activity_solves = int(data.get("solves") or 0)
        rows.append(row)
    rows.sort(key=lambda r: (-r.points, r.aluno.user.username))
    for idx, row in enumerate(rows, start=1):
        row.rank = idx
        if idx == 1:
            row.points_to_above = None
        else:
            row.points_to_above = max(0, int(rows[idx - 2].points) - int(row.points))
    return rows


def _is_truthy_param(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def _filter_events_to_season_contests(events, season_start_dt: datetime | None, season_end_dt: datetime | None):
    if not season_start_dt or not season_end_dt:
        return events.none()

    contest_match_by_platform = Contest.objects.filter(
        platform=OuterRef("platform"),
        contest_id=OuterRef("contest_id"),
        start_time__gte=season_start_dt,
        start_time__lte=season_end_dt,
    )
    contest_match_by_contest_platform = Contest.objects.filter(
        platform=OuterRef("contest_platform"),
        contest_id=OuterRef("contest_id"),
        start_time__gte=season_start_dt,
        start_time__lte=season_end_dt,
    )

    return (
        events.exclude(contest_id__isnull=True)
        .exclude(contest_id="")
        .annotate(
            _contest_in_season_platform=Exists(contest_match_by_platform),
            _contest_in_season_contest_platform=Exists(contest_match_by_contest_platform),
        )
        .filter(
            Q(_contest_in_season_platform=True)
            | Q(_contest_in_season_contest_platform=True)
        )
    )


def _compute_season_badges(student: PerfilAluno) -> list[dict]:
    badges: list[dict] = []
    seasons = list(SeasonConfig.objects.order_by("-start_date"))
    villain_ids = _villain_user_ids()
    for season in seasons:
        start_dt = timezone.make_aware(datetime.combine(season.start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(season.end_date, datetime.max.time()))
        events = ScoreEvent.objects.filter(solved_at__gte=start_dt, solved_at__lte=end_dt)
        if villain_ids:
            events = events.exclude(aluno__user_id__in=villain_ids)
        agg = events.values("aluno_id").annotate(points=Sum("points_general_cf_equiv")).order_by("-points", "aluno_id")[:3]
        for idx, row in enumerate(agg, start=1):
            if row["aluno_id"] == student.id and (row.get("points") or 0) > 0:
                badges.append({
                    "season": season,
                    "season_label": _season_label(season),
                    "rank": idx,
                    "points": int(row.get("points") or 0),
                })
                break
    return badges

def _sanitize_handle(handle: str | None) -> str | None:
    if handle is None:
        return None
    cleaned = handle.strip()
    return cleaned or None


def _default_profile_origin_for_user(user: User) -> str:
    return "admin" if (user.is_staff or user.is_superuser) else "signup"


def _profile_origin_ui(created_via: str | None) -> dict:
    code = (created_via or "legacy").strip().lower()
    if code == "admin":
        return {
            "code": "admin",
            "label": "ADM",
            "title": "Adicionado pelo ADM",
            "pill_class": "border-amber-500/40 bg-amber-500/15 text-amber-100",
        }
    if code == "signup":
        return {
            "code": "signup",
            "label": "Cadastro",
            "title": "Cadastrado pelo próprio usuário",
            "pill_class": "border-emerald-500/40 bg-emerald-500/15 text-emerald-100",
        }
    return {
        "code": "legacy",
        "label": "Legado",
        "title": "Origem não registrada",
        "pill_class": "border-slate-700 bg-slate-800/60 text-slate-200",
    }


WELCOME_TOUR_SESSION_KEY = "welcome_tour_step"


def _welcome_tour_steps_for_user(user: User) -> list[dict]:
    username = getattr(user, "username", "")
    profile_url = reverse("user_profile", kwargs={"username": username}) + "?tab=settings"
    return [
        {
            "key": "dashboard",
            "title": "Dashboard",
            "description": "Visao geral da plataforma: atividade, progresso e atalhos principais.",
            "url": reverse("dashboard"),
        },
        {
            "key": "ranking",
            "title": "Ranking",
            "description": "Comparativo geral de desempenho por pontos, rating e filtros de periodo.",
            "url": reverse("ranking"),
        },
        {
            "key": "train",
            "title": "Treino",
            "description": "Area para montar sessoes de estudo e praticar problemas no nivel certo.",
            "url": reverse("train"),
        },
        {
            "key": "contests",
            "title": "Contests",
            "description": "Historico de contests com problemas, filtros e detalhes de cada evento.",
            "url": reverse("contests_overview"),
        },
        {
            "key": "profile",
            "title": "Minha Conta",
            "description": "Seu perfil, configuracoes pessoais, handles e ajustes da conta.",
            "url": profile_url,
        },
    ]


def _get_welcome_tour_state(request) -> tuple[int, list[dict]] | None:
    # Backward compatibility for the previous one-page tour flag.
    if request.session.pop("show_welcome_tour", False):
        request.session[WELCOME_TOUR_SESSION_KEY] = 0

    steps = _welcome_tour_steps_for_user(request.user)
    raw_step = request.session.get(WELCOME_TOUR_SESSION_KEY)
    if raw_step is None:
        return None

    try:
        step = int(raw_step)
    except (TypeError, ValueError):
        request.session.pop(WELCOME_TOUR_SESSION_KEY, None)
        return None

    if step < 0 or step >= len(steps):
        request.session.pop(WELCOME_TOUR_SESSION_KEY, None)
        return None

    return step, steps


def _build_welcome_tour_context(request, page_key: str, *, enabled: bool = True) -> dict:
    if not enabled:
        return {"welcome_tour": None}

    state = _get_welcome_tour_state(request)
    if not state:
        return {"welcome_tour": None}

    step, steps = state
    current = steps[step]
    if current["key"] != page_key:
        return {"welcome_tour": None}

    next_step = steps[step + 1] if (step + 1) < len(steps) else None
    skip_query = urlencode({"next": request.get_full_path()})
    return {
        "welcome_tour": {
            "step_number": step + 1,
            "total_steps": len(steps),
            "title": current["title"],
            "description": current["description"],
            "next_page_title": next_step["title"] if next_step else "",
            "next_url": reverse("welcome_tour_next"),
            "skip_url": f"{reverse('welcome_tour_skip')}?{skip_query}",
            "next_label": "Proxima pagina" if next_step else "Concluir tour",
            "is_last": next_step is None,
        }
    }


@login_required
def welcome_tour_next(request):
    state = _get_welcome_tour_state(request)
    if not state:
        return redirect("dashboard")

    step, steps = state
    next_step = step + 1
    if next_step >= len(steps):
        request.session.pop(WELCOME_TOUR_SESSION_KEY, None)
        return redirect("dashboard")

    request.session[WELCOME_TOUR_SESSION_KEY] = next_step
    return redirect(steps[next_step]["url"])


@login_required
def welcome_tour_skip(request):
    request.session.pop(WELCOME_TOUR_SESSION_KEY, None)
    request.session.pop("show_welcome_tour", None)

    next_url = (request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("dashboard")


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    errors = {}
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password1 = request.POST.get("password1") or ""
        password2 = request.POST.get("password2") or ""
        cf_handle = _sanitize_handle(request.POST.get("cf_handle"))
        ac_handle = _sanitize_handle(request.POST.get("ac_handle"))

        if not username:
            errors["username"] = "Informe um username."
        elif User.objects.filter(username__iexact=username).exists():
            errors["username"] = "Este username já está em uso."

        if not password1 or not password2:
            errors["password"] = "Informe e confirme a senha."
        elif password1 != password2:
            errors["password"] = "As senhas não conferem."
        else:
            try:
                validate_password(password1, user=User(username=username))
            except Exception as exc:
                errors["password"] = " ".join([str(e) for e in exc.error_list])

        if cf_handle and PerfilAluno.objects.filter(handle_codeforces__iexact=cf_handle).exists():
            errors["cf_handle"] = "Este handle do Codeforces já está em uso."
        if ac_handle and PerfilAluno.objects.filter(handle_atcoder__iexact=ac_handle).exists():
            errors["ac_handle"] = "Este handle do AtCoder já está em uso."

        if not errors:
            user = User.objects.create_user(username=username, password=password1)
            PerfilAluno.objects.create(
                user=user,
                handle_codeforces=cf_handle,
                handle_atcoder=ac_handle,
                created_via="signup",
            )
            auth_login(request, user)
            request.session[WELCOME_TOUR_SESSION_KEY] = 0
            request.session.pop("show_welcome_tour", None)
            return redirect('dashboard')

    return render(request, 'core/signup.html', {
        'errors': errors,
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ""

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user:
            auth_login(request, user)
            if next_url:
                return redirect(next_url)
            return redirect('dashboard')
        error = "Usuário ou senha inválidos."

    return render(request, 'core/login.html', {
        'error': error,
        'next': next_url,
    })


def logout_view(request):
    auth_logout(request)
    return redirect('login')


@login_required
def me_view(request):
    profile = getattr(request, 'profile', None)
    if not profile:
        profile = PerfilAluno.objects.create(
            user=request.user,
            created_via=_default_profile_origin_for_user(request.user),
        )

    errors = {}
    success = False
    if request.method == "POST":
        cf_handle = _sanitize_handle(request.POST.get("cf_handle"))
        ac_handle = _sanitize_handle(request.POST.get("ac_handle"))

        if cf_handle:
            exists = PerfilAluno.objects.filter(handle_codeforces__iexact=cf_handle).exclude(id=profile.id).exists()
            if exists:
                errors["cf_handle"] = "Este handle do Codeforces já está em uso."
        if ac_handle:
            exists = PerfilAluno.objects.filter(handle_atcoder__iexact=ac_handle).exclude(id=profile.id).exists()
            if exists:
                errors["ac_handle"] = "Este handle do AtCoder já está em uso."

        if not errors:
            profile.handle_codeforces = cf_handle
            profile.handle_atcoder = ac_handle
            profile.save()
            try:
                from core.tasks import _refresh_student_ratings
                _refresh_student_ratings(profile, min_interval_hours=0)
                profile.refresh_from_db(fields=[
                    "cf_rating_current",
                    "cf_rating_max",
                    "cf_rating_updated_at",
                    "ac_rating_current",
                    "ac_rating_max",
                    "ac_rating_updated_at",
                ])
            except Exception:
                # Avoid breaking profile update if rating fetch fails
                pass
            success = True

    return render(request, 'core/me.html', {
        'profile': profile,
        'errors': errors,
        'success': success,
    })


@login_required
def _refresh_profile_ratings_response(request, profile: PerfilAluno):
    if request.method != "POST":
        return HttpResponseBadRequest("Método inválido.")

    now = timezone.now()
    min_minutes = int(getattr(settings, "MANUAL_RATING_REFRESH_MINUTES", 60))
    min_delta = timedelta(minutes=min_minutes)

    statuses = []
    updated_fields: list[str] = []

    def _format_cooldown(until_dt):
        remaining = max(0, int((until_dt - now).total_seconds() // 60))
        return max(1, remaining)

    def _check_platform(platform: str):
        nonlocal updated_fields
        platform = platform.upper()
        if platform == "CF":
            handle = profile.handle_codeforces
            updated_at = profile.cf_rating_updated_at
            cooldown_key = f"ratings_refresh:{profile.id}:CF"
            info_fn = CodeforcesClient.get_user_info_detailed
            set_fields = ("cf_rating_current", "cf_rating_max", "cf_rating_updated_at")
        else:
            handle = profile.handle_atcoder
            updated_at = profile.ac_rating_updated_at
            cooldown_key = f"ratings_refresh:{profile.id}:AC"
            info_fn = AtCoderClient.get_user_info_detailed
            set_fields = ("ac_rating_current", "ac_rating_max", "ac_rating_updated_at")

        if not handle:
            statuses.append({
                "platform": platform,
                "state": "muted",
                "message": "Sem handle configurado.",
            })
            return

        cooldown_until = cache.get(cooldown_key)
        if cooldown_until and now < cooldown_until:
            statuses.append({
                "platform": platform,
                "state": "cooldown",
                "message": f"Em cooldown ({_format_cooldown(cooldown_until)} min).",
            })
            return

        if updated_at and now - updated_at < min_delta:
            until_dt = updated_at + min_delta
            cache.set(cooldown_key, until_dt, timeout=min_delta.total_seconds())
            statuses.append({
                "platform": platform,
                "state": "cooldown",
                "message": f"Em cooldown ({_format_cooldown(until_dt)} min).",
            })
            return

        info, error = info_fn(handle)
        cache.set(cooldown_key, now + min_delta, timeout=min_delta.total_seconds())

        if error:
            statuses.append({
                "platform": platform,
                "state": "error",
                "message": error,
            })
            return

        rating = info.get("rating") if info else None
        max_rating = info.get("max_rating") if info else None

        if rating is None:
            statuses.append({
                "platform": platform,
                "state": "error",
                "message": "Usuário sem rating registrado (ou ainda não rated).",
            })
            return

        if platform == "CF":
            profile.cf_rating_current = rating
            profile.cf_rating_max = max_rating or rating
            profile.cf_rating_updated_at = now
        else:
            profile.ac_rating_current = rating
            profile.ac_rating_max = max_rating or rating
            profile.ac_rating_updated_at = now

        updated_fields.extend(list(set_fields))
        statuses.append({
            "platform": platform,
            "state": "ok",
            "message": f"Atualizado: {rating} (máx {max_rating or rating}).",
        })

    _check_platform("CF")
    _check_platform("AC")

    if updated_fields:
        profile.save(update_fields=updated_fields)

    return render(request, "core/partials/profile_rating_refresh_status.html", {
        "statuses": statuses,
        "min_minutes": min_minutes,
    })


@login_required
def refresh_my_ratings(request):
    profile = getattr(request, 'profile', None)
    if not profile:
        profile = PerfilAluno.objects.create(
            user=request.user,
            created_via=_default_profile_origin_for_user(request.user),
        )
    return _refresh_profile_ratings_response(request, profile)


@login_required
def refresh_user_ratings(request, username: str):
    profile = get_object_or_404(PerfilAluno, user__username=username)
    if request.user.id != profile.user_id and not request.user.is_staff:
        return HttpResponseForbidden("Sem permissão para atualizar este perfil.")
    return _refresh_profile_ratings_response(request, profile)


@login_required
def change_password_view(request):
    if request.method == "POST":
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            return redirect('me')
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'core/change_password.html', {
        'form': form,
    })


def _build_add_student_context(
    *,
    error: str | None = None,
    success: str | None = None,
    form_data: dict | None = None,
) -> dict:
    merged_form_data = {
        "username": "",
        "cf_handle": "",
        "ac_handle": "",
        "mark_as_villain": False,
        "villain_group_id": "",
    }
    if form_data:
        merged_form_data.update(form_data)

    villain_groups_qs = (
        CompetitorGroup.objects.filter(is_villain=True)
        .order_by("priority", "name")
        .prefetch_related(
            models.Prefetch(
                "users",
                queryset=User.objects.order_by("username"),
            )
        )
    )

    member_user_ids = {
        int(user_id)
        for user_id in villain_groups_qs.values_list("users__id", flat=True)
        if user_id
    }
    member_origins = {
        int(user_id): created_via
        for user_id, created_via in PerfilAluno.objects.filter(
            user_id__in=member_user_ids
        ).values_list("user_id", "created_via")
    }

    villain_groups = []
    villain_members_total = 0
    for group in villain_groups_qs:
        members = []
        for member in group.users.all():
            origin = _profile_origin_ui(member_origins.get(member.id))
            members.append(
                {
                    "id": member.id,
                    "username": member.username,
                    "origin_code": origin["code"],
                    "origin_label": origin["label"],
                    "origin_title": origin["title"],
                    "origin_pill_class": origin["pill_class"],
                }
            )
        villain_members_total += len(members)
        villain_groups.append(
            {
                "group": group,
                "members": members,
            }
        )

    return {
        "error": error,
        "success": success,
        "form_data": merged_form_data,
        "villain_groups": villain_groups,
        "villain_group_total": len(villain_groups),
        "villain_members_total": villain_members_total,
    }


VILLAIN_GROUP_FIXED_PRIORITY = 100


def _normalize_villain_group_priorities() -> None:
    CompetitorGroup.objects.filter(is_villain=True).exclude(
        priority=VILLAIN_GROUP_FIXED_PRIORITY
    ).update(priority=VILLAIN_GROUP_FIXED_PRIORITY)


@login_required
def add_student(request):
    error = None
    success = None
    form_data = None
    _normalize_villain_group_priorities()

    if request.method == 'POST':
        action = (request.POST.get("action") or "create_student").strip()

        if action == "create_student":
            username = (request.POST.get('username') or '').strip()
            cf_handle = (request.POST.get('cf_handle') or '').strip() or None
            ac_handle = (request.POST.get('ac_handle') or '').strip() or None
            mark_as_villain = (request.POST.get("mark_as_villain") or "").strip().lower() in {
                "1",
                "true",
                "on",
                "yes",
                "sim",
            }
            villain_group_id_raw = (request.POST.get("villain_group_id") or "").strip()
            villain_group_id = None
            selected_villain_group = None
            if villain_group_id_raw:
                try:
                    villain_group_id = int(villain_group_id_raw)
                except ValueError:
                    villain_group_id = None

            form_data = {
                "username": username,
                "cf_handle": cf_handle or "",
                "ac_handle": ac_handle or "",
                "mark_as_villain": mark_as_villain,
                "villain_group_id": villain_group_id_raw,
            }

            if not username:
                error = "Username é obrigatório."
            elif User.objects.filter(username=username).exists():
                error = "Username já existe."
            elif cf_handle and PerfilAluno.objects.filter(handle_codeforces=cf_handle).exists():
                error = "Handle Codeforces já está em uso."
            elif ac_handle and PerfilAluno.objects.filter(handle_atcoder=ac_handle).exists():
                error = "Handle AtCoder já está em uso."
            elif mark_as_villain and villain_group_id_raw and villain_group_id is None:
                error = 'Seleção inválida para grupo de "Vilões".'
            elif mark_as_villain and villain_group_id:
                selected_villain_group = CompetitorGroup.objects.filter(
                    id=villain_group_id,
                    is_villain=True,
                ).first()
                if selected_villain_group is None:
                    error = 'Grupo de "Vilões" selecionado não encontrado.'

            if not error:
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            username=username,
                            password='password123',
                        )  # Default password for now
                        perfil = PerfilAluno.objects.create(
                            user=user,
                            handle_codeforces=cf_handle,
                            handle_atcoder=ac_handle,
                            created_via="admin",
                        )

                        villain_group = None
                        if mark_as_villain:
                            if selected_villain_group is not None:
                                villain_group = selected_villain_group
                                if villain_group.priority != VILLAIN_GROUP_FIXED_PRIORITY:
                                    villain_group.priority = VILLAIN_GROUP_FIXED_PRIORITY
                                    villain_group.save(update_fields=["priority", "updated_at"])
                            else:
                                target_group_name = "Grupo de Vilões"
                                villain_group = CompetitorGroup.objects.filter(
                                    name__iexact=target_group_name
                                ).first()
                                if villain_group is None:
                                    villain_group = CompetitorGroup.objects.create(
                                        name=target_group_name,
                                        color="#DC2626",
                                        priority=VILLAIN_GROUP_FIXED_PRIORITY,
                                        is_villain=True,
                                    )
                                elif not villain_group.is_villain:
                                    villain_group.is_villain = True
                                    villain_group.priority = VILLAIN_GROUP_FIXED_PRIORITY
                                    villain_group.save(update_fields=["is_villain", "priority", "updated_at"])
                                elif villain_group.priority != VILLAIN_GROUP_FIXED_PRIORITY:
                                    villain_group.priority = VILLAIN_GROUP_FIXED_PRIORITY
                                    villain_group.save(update_fields=["priority", "updated_at"])
                            villain_group.users.add(user)
                except Exception as exc:
                    error = f"Falha ao cadastrar aluno: {exc}"
                else:
                    if hasattr(request, 'htmx') and request.htmx:
                        return render(request, 'core/partials/student_row.html', {'student': perfil})
                    if mark_as_villain:
                        success = (
                            f"Aluno {username} cadastrado e marcado como vilão "
                            f'no grupo "{villain_group.name}".'
                        )
                    else:
                        success = f"Aluno {username} cadastrado com sucesso."
                    form_data = None

        elif action == "create_villain_group":
            group_name = (request.POST.get("group_name") or "").strip()
            group_color = (request.POST.get("group_color") or "#DC2626").strip() or "#DC2626"

            if not group_name:
                error = 'Informe o nome do grupo de "Vilões".'
            else:
                try:
                    group = CompetitorGroup(
                        name=group_name,
                        color=group_color,
                        priority=VILLAIN_GROUP_FIXED_PRIORITY,
                        is_villain=True,
                    )
                    group.full_clean()
                    group.save()
                except ValidationError as exc:
                    error = "; ".join(exc.messages)
                except Exception as exc:
                    error = f'Falha ao criar grupo de "Vilões": {exc}'
                else:
                    success = f'Grupo de "Vilões" criado: {group.name}.'

        elif action == "update_villain_group":
            group_id = request.POST.get("group_id")
            group = CompetitorGroup.objects.filter(id=group_id, is_villain=True).first()
            if not group:
                error = 'Grupo de "Vilões" não encontrado.'
            else:
                group_name = (request.POST.get("group_name") or "").strip()
                group_color = (request.POST.get("group_color") or "").strip() or group.color

                if not group_name:
                    error = 'Informe o nome do grupo de "Vilões".'
                else:
                    try:
                        group.name = group_name
                        group.color = group_color
                        group.priority = VILLAIN_GROUP_FIXED_PRIORITY
                        group.is_villain = True
                        group.full_clean()
                        group.save()
                    except ValidationError as exc:
                        error = "; ".join(exc.messages)
                    except Exception as exc:
                        error = f'Falha ao atualizar grupo de "Vilões": {exc}'
                    else:
                        success = f'Grupo de "Vilões" atualizado: {group.name}.'

        elif action == "delete_villain_group":
            group_id = request.POST.get("group_id")
            group = CompetitorGroup.objects.filter(id=group_id, is_villain=True).first()
            if not group:
                error = 'Grupo de "Vilões" não encontrado.'
            else:
                group_name = group.name
                group.delete()
                success = f'Grupo de "Vilões" removido: {group_name}.'

        elif action == "add_user_to_villain_group":
            group_id = request.POST.get("group_id")
            target_username = (request.POST.get("target_username") or "").strip()
            group = CompetitorGroup.objects.filter(id=group_id, is_villain=True).first()
            if not group:
                error = 'Grupo de "Vilões" não encontrado.'
            elif not target_username:
                error = 'Informe o username para adicionar ao grupo de "Vilões".'
            else:
                target_user = User.objects.filter(username=target_username).first()
                if not target_user:
                    error = f"Usuário {target_username} não encontrado."
                else:
                    if group.users.filter(id=target_user.id).exists():
                        success = f"Usuário {target_user.username} já está no grupo {group.name}."
                    else:
                        group.users.add(target_user)
                        success = f"Usuário {target_user.username} adicionado ao grupo {group.name}."

        elif action == "remove_user_from_villain_group":
            group_id = request.POST.get("group_id")
            user_id = request.POST.get("user_id")
            group = CompetitorGroup.objects.filter(id=group_id, is_villain=True).first()
            if not group:
                error = 'Grupo de "Vilões" não encontrado.'
            else:
                member = User.objects.filter(id=user_id).first()
                if not member:
                    error = "Usuário não encontrado."
                elif not group.users.filter(id=member.id).exists():
                    error = f"Usuário {member.username} não pertence ao grupo {group.name}."
                else:
                    group.users.remove(member)
                    success = f"Usuário {member.username} removido do grupo {group.name}."

        else:
            error = "Ação inválida."

    return render(
        request,
        'core/add_student.html',
        _build_add_student_context(
            error=error,
            success=success,
            form_data=form_data,
        ),
    )

@login_required
def dashboard(request):
    """
    Dashboard macro: visão geral de atividade, contests e ranking rápido.
    """
    now = timezone.now()
    window_7d = now - timedelta(days=7)
    window_30d = now - timedelta(days=30)
    active_season, season_start, season_end = get_active_season_range()

    villain_ids = _villain_user_ids()

    students_qs = PerfilAluno.objects.all()
    if villain_ids:
        students_qs = students_qs.exclude(user_id__in=villain_ids)
    total_students = students_qs.count()

    events_qs = ScoreEvent.objects.all()
    if villain_ids:
        events_qs = events_qs.exclude(aluno__user_id__in=villain_ids)

    season_filter = Q(id__isnull=True)
    if season_start and season_end:
        season_filter = Q(solved_at__gte=season_start, solved_at__lte=season_end)

    events_stats = events_qs.aggregate(
        active_students_7d=Count("aluno_id", filter=Q(solved_at__gte=window_7d), distinct=True),
        solves_7d=Count("id", filter=Q(solved_at__gte=window_7d)),
        solves_30d=Count("id", filter=Q(solved_at__gte=window_30d)),
        solves_season=Count("id", filter=season_filter),
    )
    active_students_7d = int(events_stats.get("active_students_7d") or 0)
    solves_7d = int(events_stats.get("solves_7d") or 0)
    solves_30d = int(events_stats.get("solves_30d") or 0)
    solves_season = int(events_stats.get("solves_season") or 0)

    contests = list(
        Contest.objects.filter(start_time__isnull=False, start_time__lte=now)
        .only(
            "id",
            "platform",
            "contest_id",
            "title",
            "start_time",
            "ratings_total_count",
            "ratings_ready_count",
            "ratings_summary_status",
        )
        .order_by("-start_time")
        .annotate(problem_count=Count("problems"))
        [:6]
    )
    contest_rows = []
    for contest in contests:
        total = contest.ratings_total_count or contest.problem_count or 0
        ready = contest.ratings_ready_count or 0
        percent = int((ready / total) * 100) if total else 0
        contest_rows.append({
            "contest": contest,
            "detail_url": reverse(
                "contest_detail",
                kwargs={"platform": contest.platform.lower(), "contest_id": contest.contest_id},
            ),
            "url": (
                f"https://codeforces.com/contest/{contest.contest_id}"
                if contest.platform == "CF"
                else f"https://atcoder.jp/contests/{contest.contest_id}"
            ),
            "problems_total": total,
            "ratings_ready": ready,
            "ratings_percent": percent,
            "ratings_status": contest.ratings_summary_status,
        })

    top_students = list(
        UserScoreAgg.objects.select_related("aluno__user")
        .only("season_points_general_cf_equiv", "aluno__user__username", "aluno_id")
        .exclude(aluno__user_id__in=villain_ids if villain_ids else [])
        .order_by("-season_points_general_cf_equiv")
        [:5]
    )

    recent_subs = _get_dashboard_recent_submissions(villain_ids, limit=20)

    profile = getattr(request.user, "perfil", None)
    my_agg = None
    if profile:
        my_agg = UserScoreAgg.objects.filter(aluno=profile).first()
    user_sync = _build_user_sync_status(request.user)
    context = {
        "total_students": total_students,
        "active_students_7d": active_students_7d,
        "solves_7d": solves_7d,
        "solves_30d": solves_30d,
        "solves_season": solves_season,
        "contest_rows": contest_rows,
        "top_students": top_students,
        "recent_subs": recent_subs,
        "my_agg": my_agg,
        "active_season": active_season,
        "season_start": season_start,
        "season_end": season_end,
        "now": now,
        "user_sync": user_sync,
    }
    context.update(_build_welcome_tour_context(request, "dashboard"))
    return render(request, "core/dashboard.html", context)


@login_required
def dashboard_activity(request):
    villain_ids = _villain_user_ids()
    recent_subs = _get_dashboard_recent_submissions(villain_ids, limit=20)
    return render(request, "core/partials/dashboard_activity.html", {
        "recent_subs": recent_subs,
    })


@login_required
def perfil_aluno(request, username):
    # Back-compat route. The profile lives at /u/<username>/ now.
    return redirect("user_profile", username=username)


def _profile_problem_id_from_submission(sub: dict) -> str | None:
    platform = sub.get("plataforma")
    contest_id = (sub.get("contest_id") or "").strip()
    problem_index = (sub.get("problem_index") or "").strip()
    problem_name = (sub.get("problem_name") or "").strip()

    if platform == "CF":
        if not contest_id or not problem_index:
            return None
        return f"{contest_id}{problem_index}"

    if platform == "AC":
        if problem_name and "_" in problem_name:
            return problem_name
        if contest_id and problem_index:
            return f"{contest_id}_{problem_index.lower()}"
        return None

    return None


def _profile_collect_solved_problem_ids(student: PerfilAluno) -> set[str]:
    rows = (
        Submissao.objects.filter(
            aluno=student,
        )
        .filter(Q(plataforma="CF", verdict="OK") | Q(plataforma="AC", verdict="AC"))
        .values("plataforma", "contest_id", "problem_index", "problem_name")
    )
    solved = set()
    for row in rows.iterator():
        pid = _profile_problem_id_from_submission(row)
        if pid:
            solved.add(pid)
    return solved


def _profile_build_heatmap_weeks(
    counts_by_day: dict,
    start_date,
    end_date,
    metric: str,
):
    # Align to full weeks (Mon..Sun) for a stable grid.
    start_monday = start_date - timedelta(days=start_date.weekday())
    end_sunday = end_date + timedelta(days=(6 - end_date.weekday()))

    values = [v for d, v in counts_by_day.items() if start_date <= d <= end_date and v is not None]
    max_value = max(values) if values else 0

    def level_for(value: int | None) -> int:
        if value is None:
            return -1
        if value <= 0:
            return 0
        if metric == "points" and max_value > 0:
            # Dynamic mapping for points (keeps it readable).
            ratio = value / float(max_value)
            if ratio <= 0.25:
                return 1
            if ratio <= 0.5:
                return 2
            if ratio <= 0.75:
                return 3
            return 4
        if value <= 1:
            return 1
        if value <= 3:
            return 2
        if value <= 6:
            return 3
        return 4

    weeks = []
    cur = start_monday
    while cur <= end_sunday:
        week = []
        for i in range(7):
            day = cur + timedelta(days=i)
            in_range = start_date <= day <= end_date
            value = counts_by_day.get(day, 0) if in_range else None
            week.append({
                "date": day,
                "in_range": in_range,
                "value": value,
                "level": level_for(value if in_range else None),
            })
        weeks.append(week)
        cur += timedelta(days=7)

    return weeks, max_value


def _score_event_points_display(event: ScoreEvent) -> tuple[int, bool]:
    """
    Return the best available points value for profile UI and whether it's pending.
    Pending means the solve still has no rating, so points may appear as zero temporarily.
    """
    candidates = [
        event.points_general_cf_equiv,
        event.points_awarded,
        event.points_cf_raw,
        event.points_ac_raw,
        event.points_general_norm,
    ]
    for value in candidates:
        if value is not None and value > 0:
            return int(value), False
    pending = event.raw_rating is None
    return 0, pending


@login_required
def user_profile(request, username):
    student = PerfilAluno.objects.filter(user__username=username).first()
    if not student:
        # Some staff/superusers might not have a profile yet. Create it when they
        # try to access their own profile.
        if request.user.is_authenticated and request.user.username == username:
            student = PerfilAluno.objects.create(
                user=request.user,
                created_via=_default_profile_origin_for_user(request.user),
            )
        else:
            raise Http404("Aluno não encontrado.")
    agg = getattr(student, "score_agg", None)
    active_season, season_start, season_end = get_active_season_range()

    total_solves = ScoreEvent.objects.filter(aluno=student).count()
    last_solve = (
        ScoreEvent.objects.filter(aluno=student)
        .aggregate(last=Max("solved_at"))
        .get("last")
    )

    is_owner = request.user.is_authenticated and request.user.id == student.user_id
    season_badges = _compute_season_badges(student)
    initial_tab = (request.GET.get("tab") or "overview").lower()
    allowed_tabs = {"overview", "activity", "tags", "visualizer_cf", "visualizer_ac"}
    if is_owner:
        allowed_tabs.add("settings")
    if initial_tab not in allowed_tabs:
        initial_tab = "overview"

    initial_tab_url = reverse("user_profile_tab", args=[student.user.username, initial_tab])
    if initial_tab == "tags":
        initial_tab_url += "?period=all"

    context = {
        "student": student,
        "agg": agg,
        "active_season": active_season,
        "season_start": season_start,
        "season_end": season_end,
        "total_solves": total_solves,
        "last_solve": last_solve,
        "is_owner": is_owner,
        "initial_tab": initial_tab,
        "initial_tab_url": initial_tab_url,
        "season_badges": season_badges,
    }
    context.update(_build_welcome_tour_context(request, "profile", enabled=is_owner))
    return render(request, "core/user_profile.html", context)


@login_required
def user_profile_tab(request, username, tab):
    student = get_object_or_404(PerfilAluno, user__username=username)
    tab = (tab or "overview").lower()

    if tab not in {"overview", "activity", "tags", "visualizer_cf", "visualizer_ac", "settings"}:
        return HttpResponseBadRequest("Tab invalida.")

    if tab == "overview":
        is_owner = request.user.is_authenticated and request.user.id == student.user_id
        recent_solves = list(
            ScoreEvent.objects.select_related("submission")
            .filter(aluno=student)
            .order_by("-solved_at")[:20]
        )
        for ev in recent_solves:
            ev.submission_url = _submission_url(ev.platform, ev.contest_id or ev.submission.contest_id, ev.submission.external_id)
            ev.points_display, ev.points_pending = _score_event_points_display(ev)

        solved_cf = ScoreEvent.objects.filter(aluno=student, platform="CF").count()
        solved_ac = ScoreEvent.objects.filter(aluno=student, platform="AC").count()

        return render(request, "core/partials/profile_overview.html", {
            "student": student,
            "agg": getattr(student, "score_agg", None),
            "recent_solves": recent_solves,
            "solved_cf": solved_cf,
            "solved_ac": solved_ac,
            "is_owner": is_owner,
        })

    if tab == "activity":
        return render(request, "core/partials/profile_activity.html", {
            "student": student,
        })

    if tab == "settings":
        is_owner = request.user.is_authenticated and request.user.id == student.user_id
        if not is_owner:
            return HttpResponseForbidden("Acesso restrito.")

        errors: dict[str, str] = {}
        success = False

        if request.method == "POST":
            cf_handle = _sanitize_handle(request.POST.get("cf_handle"))
            ac_handle = _sanitize_handle(request.POST.get("ac_handle"))

            if cf_handle:
                exists = (
                    PerfilAluno.objects.filter(handle_codeforces__iexact=cf_handle)
                    .exclude(id=student.id)
                    .exists()
                )
                if exists:
                    errors["cf_handle"] = "Este handle do Codeforces já está em uso."

            if ac_handle:
                exists = (
                    PerfilAluno.objects.filter(handle_atcoder__iexact=ac_handle)
                    .exclude(id=student.id)
                    .exists()
                )
                if exists:
                    errors["ac_handle"] = "Este handle do AtCoder já está em uso."

            if not errors:
                student.handle_codeforces = cf_handle
                student.handle_atcoder = ac_handle
                student.save(update_fields=["handle_codeforces", "handle_atcoder", "updated_at"])
                try:
                    from core.tasks import _refresh_student_ratings
                    _refresh_student_ratings(student, min_interval_hours=0)
                    student.refresh_from_db(fields=[
                        "cf_rating_current",
                        "cf_rating_max",
                        "cf_rating_updated_at",
                        "ac_rating_current",
                        "ac_rating_max",
                        "ac_rating_updated_at",
                    ])
                except Exception:
                    pass
                success = True

        return render(request, "core/partials/profile_settings.html", {
            "student": student,
            "errors": errors,
            "success": success,
        })

    if tab == "tags":
        # CF tags are the only reliable source today.
        period = (request.GET.get("period") or "all").lower()
        _active_season, season_start, season_end = get_active_season_range()
        now = timezone.now()

        start_dt = None
        end_dt = None
        if period == "season":
            if season_start and season_end:
                start_dt = timezone.make_aware(datetime.combine(timezone.localdate(season_start), datetime.min.time()))
                end_dt = timezone.make_aware(datetime.combine(timezone.localdate(season_end), datetime.max.time()))
            else:
                period = "all"
        elif period == "30d":
            start_dt = now - timedelta(days=30)
            end_dt = now

        solved_qs = ScoreEvent.objects.filter(aluno=student, platform="CF")
        if start_dt:
            solved_qs = solved_qs.filter(solved_at__gte=start_dt)
        if end_dt:
            solved_qs = solved_qs.filter(solved_at__lte=end_dt)
        solved_urls = set(solved_qs.values_list("problem_url", flat=True))

        subs_qs = (
            Submissao.objects.filter(aluno=student, plataforma="CF")
            .exclude(tags__isnull=True)
            .exclude(tags="")
        )
        if start_dt:
            subs_qs = subs_qs.filter(submission_time__gte=start_dt)
        if end_dt:
            subs_qs = subs_qs.filter(submission_time__lte=end_dt)

        # Build per-problem metadata (unique problem URL) + submission counts.
        problems: dict[str, dict] = {}
        for row in subs_qs.values("contest_id", "problem_index", "tags").iterator():
            contest_id = (row.get("contest_id") or "").strip()
            idx = (row.get("problem_index") or "").strip()
            if not contest_id or not idx:
                continue
            url = build_problem_url_from_fields("CF", contest_id, idx, None)
            if not url:
                continue
            tags = {t.strip() for t in (row.get("tags") or "").split(",") if t.strip()}
            if not tags:
                continue
            meta = problems.setdefault(url, {"tags": set(), "submissions": 0})
            meta["tags"].update(tags)
            meta["submissions"] += 1

        urls = list(problems.keys())
        rating_map = {}
        if urls:
            for cached in ProblemRatingCache.objects.filter(problem_url__in=urls, status="OK").iterator():
                rating = cached.effective_rating or cached.clist_rating or cached.cf_rating
                if rating is not None:
                    rating_map[cached.problem_url] = int(rating)

        tag_stats: dict[str, dict] = {}
        for url, meta in problems.items():
            is_solved = url in solved_urls
            rating = rating_map.get(url)
            submissions_n = int(meta.get("submissions") or 0)
            for tag in meta.get("tags", set()):
                st = tag_stats.setdefault(tag, {
                    "problems_attempted": 0,
                    "problems_solved": 0,
                    "submissions": 0,
                    "rating_attempted_sum": 0,
                    "rating_attempted_n": 0,
                    "rating_solved_sum": 0,
                    "rating_solved_n": 0,
                    "rating_unsolved_sum": 0,
                    "rating_unsolved_n": 0,
                })
                st["problems_attempted"] += 1
                if is_solved:
                    st["problems_solved"] += 1
                st["submissions"] += submissions_n
                if rating is not None:
                    st["rating_attempted_sum"] += rating
                    st["rating_attempted_n"] += 1
                    if is_solved:
                        st["rating_solved_sum"] += rating
                        st["rating_solved_n"] += 1
                    else:
                        st["rating_unsolved_sum"] += rating
                        st["rating_unsolved_n"] += 1

        def clamp01(value: float) -> float:
            return max(0.0, min(1.0, float(value)))

        baseline_cf = int(student.cf_rating_current or 1200)
        prior_n = 4.0
        prior_mean = 0.50

        rows = []
        for tag, st in tag_stats.items():
            attempted = int(st["problems_attempted"] or 0)
            solved = int(st["problems_solved"] or 0)
            if attempted <= 0:
                continue
            unsolved = max(0, attempted - solved)
            solve_rate = solved / float(attempted)
            solve_rate_bayes = (solved + prior_n * prior_mean) / (attempted + prior_n)

            avg_attempted_rating = (st["rating_attempted_sum"] / st["rating_attempted_n"]) if st["rating_attempted_n"] else None
            avg_solved_rating = (st["rating_solved_sum"] / st["rating_solved_n"]) if st["rating_solved_n"] else None
            avg_unsolved_rating = (st["rating_unsolved_sum"] / st["rating_unsolved_n"]) if st["rating_unsolved_n"] else None
            avg_submissions = (st["submissions"] / attempted) if attempted else 0.0

            confidence = clamp01(attempted / 10.0)
            confidence_pct = int(round(confidence * 100.0))
            reliability = 0.55 + 0.45 * confidence
            volume_score = clamp01(attempted / 15.0)

            efficiency_penalty = clamp01((float(avg_submissions) - 1.2) / 2.8)
            efficiency_score = 1.0 - efficiency_penalty
            struggle_score = clamp01((float(avg_submissions) - 1.3) / 2.2)

            solved_diff_ref = avg_solved_rating or avg_attempted_rating or baseline_cf
            unsolved_diff_ref = avg_unsolved_rating or avg_attempted_rating or baseline_cf
            difficulty_push = clamp01((float(solved_diff_ref) - max(700, baseline_cf - 300)) / 1100.0)
            challenge_gap = clamp01((float(unsolved_diff_ref) - max(700, baseline_cf - 200)) / 1100.0)

            strength_score = 100.0 * reliability * (
                0.45 * solve_rate_bayes
                + 0.20 * efficiency_score
                + 0.20 * difficulty_push
                + 0.15 * volume_score
            )
            weakness_score = 100.0 * reliability * (
                0.45 * (1.0 - solve_rate_bayes)
                + 0.25 * struggle_score
                + 0.20 * challenge_gap
                + 0.10 * volume_score
            )
            growth_score = 100.0 * reliability * (
                0.35 * (1.0 - abs(solve_rate_bayes - 0.55))
                + 0.30 * challenge_gap
                + 0.20 * confidence
                + 0.15 * volume_score
            )

            rows.append({
                "tag": tag,
                "attempted": attempted,
                "solved": solved,
                "unsolved": unsolved,
                "solve_rate": solve_rate,
                "solve_rate_bayes": solve_rate_bayes,
                "avg_attempted_rating": avg_attempted_rating,
                "avg_solved_rating": avg_solved_rating,
                "avg_unsolved_rating": avg_unsolved_rating,
                "avg_submissions": avg_submissions,
                "strength_score": strength_score,
                "weakness_score": weakness_score,
                "growth_score": growth_score,
                "confidence": confidence,
                "confidence_pct": confidence_pct,
                "confidence_label": "Alta" if confidence_pct >= 70 else ("Média" if confidence_pct >= 40 else "Baixa"),
            })

        ranked = sorted(
            rows,
            key=lambda r: (
                r["strength_score"],
                r["solve_rate_bayes"],
                r["solved"],
                (r["avg_solved_rating"] or 0),
                r["attempted"],
            ),
            reverse=True,
        )

        strengths = [
            r for r in ranked
            if r["attempted"] >= 3 and r["solved"] >= 2 and r["solve_rate_bayes"] >= 0.55
        ][:10]
        if not strengths:
            strengths = [r for r in ranked if r["attempted"] >= 2][:6]
        strength_tags = {r["tag"] for r in strengths}

        weak_pool = [
            r for r in rows
            if (
                r["tag"] not in strength_tags
                and r["attempted"] >= 4
                and r["unsolved"] >= 2
                and r["solve_rate_bayes"] <= 0.52
            )
        ]
        difficulties = sorted(
            weak_pool,
            key=lambda r: (
                r["weakness_score"],
                r["unsolved"],
                (r["avg_unsolved_rating"] or r["avg_attempted_rating"] or 0),
                r["attempted"],
            ),
            reverse=True,
        )[:10]
        if not difficulties:
            difficulties = [
                r for r in sorted(rows, key=lambda x: (x["weakness_score"], x["unsolved"]), reverse=True)
                if r["tag"] not in strength_tags and r["unsolved"] > 0
            ][:6]
        difficulty_tags = {r["tag"] for r in difficulties}

        growth_tags = [
            r for r in sorted(
                rows,
                key=lambda r: (r["growth_score"], r["attempted"], (r["avg_attempted_rating"] or 0)),
                reverse=True,
            )
            if (
                r["tag"] not in strength_tags
                and r["tag"] not in difficulty_tags
                and r["attempted"] >= 2
                and 0.35 <= r["solve_rate_bayes"] <= 0.75
            )
        ][:10]
        if not growth_tags:
            growth_tags = [
                r for r in ranked
                if r["tag"] not in strength_tags and r["tag"] not in difficulty_tags
            ][:6]

        attempted_total = sum(r["attempted"] for r in rows)
        solved_total = sum(r["solved"] for r in rows)
        weighted_rate = (solved_total / attempted_total) if attempted_total else 0.0

        return render(request, "core/partials/profile_tags.html", {
            "student": student,
            "period": period,
            "tag_ranking": ranked[:30],
            "tags_total": len(rows),
            "strengths": strengths,
            "difficulties": difficulties,
            "growth_tags": growth_tags,
            "has_tags": bool(rows),
            "baseline_cf": baseline_cf,
            "summary_attempted": attempted_total,
            "summary_solved": solved_total,
            "summary_rate": weighted_rate,
            "model_summary": "Modelo: taxa bayesiana + confiança da amostra + eficiência + dificuldade relativa.",
        })

    if tab == "visualizer_cf":
        if student.handle_codeforces:
            from core.tasks import refresh_cf_rating_history
            if not student.cf_rating_changes.exists():
                refresh_cf_rating_history.delay(student.id)

        changes = list(
            student.cf_rating_changes.order_by("rating_update_time")[:2000]
        )
        contest_ids = {
            str(c.contest_id).strip()
            for c in changes
            if c.contest_id is not None and str(c.contest_id).strip()
        }
        available_contest_ids = set(
            Contest.objects.filter(platform="CF", contest_id__in=contest_ids).values_list("contest_id", flat=True)
        )
        contest_detail_url_map = {
            contest_id: reverse(
                "contest_detail",
                kwargs={"platform": "cf", "contest_id": contest_id},
            )
            for contest_id in available_contest_ids
        }

        ratings = [int(c.rating_new) for c in changes if c.rating_new is not None]
        points = []
        y_ticks = []
        x_markers = []
        area_path = ""
        width = 760
        height = 280
        chart_left = 56
        chart_right = 18
        chart_top = 16
        chart_bottom = height - 36
        chart_height = chart_bottom - chart_top
        chart_width = width - chart_left - chart_right
        axis_min = None
        axis_max = None
        axis_span = None

        if ratings:
            min_r = min(ratings)
            max_r = max(ratings)
            raw_span = max_r - min_r
            margin = max(80, int(round(raw_span * 0.18)))
            axis_min = max(0, min_r - margin)
            axis_max = max_r + margin
            axis_span = max(1, axis_max - axis_min)
            n = len(ratings)
            width = max(760, min(2800, chart_left + chart_right + max(1, n - 1) * 28))
            chart_width = width - chart_left - chart_right
            chart_height = chart_bottom - chart_top

            for i, c in enumerate(changes):
                rating_new = int(c.rating_new)
                rating_old = int(c.rating_old) if c.rating_old is not None else rating_new
                x = chart_left + int(round((i / max(1, n - 1)) * chart_width))
                y = chart_top + int(round((1.0 - ((rating_new - axis_min) / axis_span)) * chart_height))
                delta = rating_new - rating_old
                contest_id = str(c.contest_id).strip() if c.contest_id is not None else ""
                detail_url = contest_detail_url_map.get(contest_id, "")
                points.append({
                    "x": x,
                    "y": y,
                    "rating": rating_new,
                    "contest": c.contest_name,
                    "date": c.rating_update_time,
                    "delta": delta,
                    "detail_url": detail_url,
                    "tooltip": (
                        f"{c.rating_update_time:%d/%m/%Y} • "
                        f"{(c.contest_name or 'Contest').strip() or 'Contest'} • "
                        f"{rating_new} (Δ {'+' if delta >= 0 else ''}{delta})"
                        + (" • clique para abrir detalhes" if detail_url else "")
                    ),
                })

            tick_count = 5
            for i in range(tick_count):
                ratio = i / max(1, tick_count - 1)
                y = chart_top + int(round(ratio * chart_height))
                value = int(round(axis_max - (ratio * axis_span)))
                y_ticks.append({"y": y, "value": value})

            marker_indexes = sorted({0, max(0, n // 2), n - 1})
            for idx in marker_indexes:
                marker = changes[idx]
                x = points[idx]["x"]
                anchor = "middle"
                if idx == 0:
                    anchor = "start"
                elif idx == n - 1:
                    anchor = "end"
                x_markers.append({
                    "x": x,
                    "label": marker.rating_update_time.strftime("%m/%Y"),
                    "anchor": anchor,
                })

            if points:
                line_path = " ".join([f"L {p['x']} {p['y']}" for p in points[1:]])
                area_path = (
                    f"M {points[0]['x']} {chart_bottom} "
                    f"L {points[0]['x']} {points[0]['y']} "
                    f"{line_path} "
                    f"L {points[-1]['x']} {chart_bottom} Z"
                )

        polyline = " ".join([f"{p['x']},{p['y']}" for p in points])

        cf_solved_ratings = list(
            ScoreEvent.objects.filter(aluno=student, platform="CF")
            .exclude(raw_rating__isnull=True)
            .values_list("raw_rating", flat=True)
        )
        bins = []
        top_bins = []
        hist_max_count = 0
        if cf_solved_ratings:
            step = 200
            start = 800
            end = 3600
            bucket_count = ((end - start) // step) + 1
            counts = [0] * bucket_count
            total_solved = len(cf_solved_ratings)
            for r in cf_solved_ratings:
                rr = int(r)
                if rr < start:
                    idx = 0
                elif rr >= end:
                    idx = bucket_count - 1
                else:
                    idx = (rr - start) // step
                counts[idx] += 1
            hist_max_count = max(counts) if counts else 0
            for i, c in enumerate(counts):
                lo = start + i * step
                hi = lo + step - 1
                if i == bucket_count - 1:
                    label = f"{lo}+"
                else:
                    label = f"{lo}-{hi}"
                height_px = 0
                if hist_max_count and c > 0:
                    height_px = 10 + int(round((c / hist_max_count) * 126))
                percent = (c / total_solved) * 100.0 if total_solved else 0.0
                bins.append({
                    "label": label,
                    "count": c,
                    "height_px": height_px,
                    "percent": percent,
                    "is_peak": bool(c > 0 and c == hist_max_count),
                })
            top_bins = sorted(
                [b for b in bins if b["count"] > 0],
                key=lambda row: (row["count"], row["percent"]),
                reverse=True,
            )[:5]

        hardest = max(cf_solved_ratings) if cf_solved_ratings else None
        avg = (sum(cf_solved_ratings) / len(cf_solved_ratings)) if cf_solved_ratings else None
        latest_changes = changes[-8:][::-1]
        for c in latest_changes:
            rating_old = int(c.rating_old) if c.rating_old is not None else int(c.rating_new)
            c.delta = int(c.rating_new) - rating_old
            contest_id = str(c.contest_id).strip() if c.contest_id is not None else ""
            c.contest_detail_url = contest_detail_url_map.get(contest_id, "")

        latest_rating = ratings[-1] if ratings else None
        delta_last = None
        delta_window = None
        if len(ratings) >= 2:
            delta_last = ratings[-1] - ratings[-2]
            anchor_idx = max(0, len(ratings) - 6)
            delta_window = ratings[-1] - ratings[anchor_idx]

        return render(request, "core/partials/profile_visualizer_cf.html", {
            "student": student,
            "changes": latest_changes,
            "svg_width": width,
            "svg_height": height,
            "chart_left": chart_left,
            "chart_right": chart_right,
            "chart_top": chart_top,
            "chart_bottom": chart_bottom,
            "chart_height": chart_height,
            "polyline": polyline,
            "area_path": area_path,
            "points": points,
            "y_ticks": y_ticks,
            "x_markers": x_markers,
            "axis_min": axis_min,
            "axis_max": axis_max,
            "bins": bins,
            "hist_peak_count": hist_max_count,
            "top_bins": top_bins,
            "hardest": hardest,
            "avg": avg,
            "solved_n": len(cf_solved_ratings),
            "latest_rating": latest_rating,
            "peak_rating": max(ratings) if ratings else None,
            "contests_n": len(ratings),
            "delta_last": delta_last,
            "delta_window": delta_window,
            "first_date": changes[0].rating_update_time if changes else None,
            "last_date": changes[-1].rating_update_time if changes else None,
        })

    if tab == "visualizer_ac":
        visualizer_error = None
        if student.handle_atcoder:
            snapshots_qs = student.ac_rating_snapshots.order_by("date")
            snapshot_count = snapshots_qs.count()
            last_snapshot = snapshots_qs.last()
            need_history_refresh = (
                snapshot_count < 10
                or not last_snapshot
                or last_snapshot.date < timezone.localdate()
            )

            if need_history_refresh:
                history_url = f"https://atcoder.jp/users/{student.handle_atcoder}/history/json"
                try:
                    response = requests.get(history_url, timeout=10)
                    if response.status_code == 404:
                        visualizer_error = "Usuário não encontrado no AtCoder."
                    else:
                        response.raise_for_status()
                        history = response.json() or []
                        if history:
                            upserted = 0
                            latest_rating = None
                            max_rating = None
                            for row in history:
                                new_rating = row.get("NewRating")
                                end_time = (row.get("EndTime") or "").strip()
                                if new_rating is None or not end_time:
                                    continue
                                try:
                                    snap_date = datetime.fromisoformat(
                                        end_time.replace("Z", "+00:00")
                                    ).date()
                                except ValueError:
                                    try:
                                        snap_date = datetime.strptime(
                                            end_time[:19], "%Y-%m-%dT%H:%M:%S"
                                        ).date()
                                    except ValueError:
                                        continue

                                AtCoderRatingSnapshot.objects.update_or_create(
                                    aluno=student,
                                    date=snap_date,
                                    defaults={"rating": int(new_rating)},
                                )
                                upserted += 1
                                latest_rating = int(new_rating)
                                max_rating = (
                                    int(new_rating)
                                    if max_rating is None
                                    else max(max_rating, int(new_rating))
                                )

                            if upserted > 0:
                                update_fields = []
                                if latest_rating is not None:
                                    student.ac_rating_current = latest_rating
                                    update_fields.append("ac_rating_current")
                                if max_rating is not None:
                                    student.ac_rating_max = max_rating
                                    update_fields.append("ac_rating_max")
                                student.ac_rating_updated_at = timezone.now()
                                update_fields.append("ac_rating_updated_at")
                                student.save(update_fields=update_fields + ["updated_at"])
                        elif student.ac_rating_current is not None:
                            AtCoderRatingSnapshot.objects.update_or_create(
                                aluno=student,
                                date=timezone.localdate(),
                                defaults={"rating": int(student.ac_rating_current)},
                            )
                except requests.RequestException as exc:
                    visualizer_error = f"Falha ao buscar histórico no AtCoder: {exc}"
                except Exception as exc:
                    visualizer_error = f"Erro ao processar histórico no AtCoder: {exc}"

        snapshots = list(
            student.ac_rating_snapshots.order_by("date")[:400]
        )
        ratings = [int(s.rating) for s in snapshots if s.rating is not None]
        points = []
        y_ticks = []
        x_markers = []
        area_path = ""
        width = 760
        height = 280
        chart_left = 56
        chart_right = 18
        chart_top = 16
        chart_bottom = height - 36
        chart_height = chart_bottom - chart_top
        chart_width = width - chart_left - chart_right
        axis_min = None
        axis_max = None
        axis_span = None

        if ratings:
            min_r = min(ratings)
            max_r = max(ratings)
            raw_span = max_r - min_r
            margin = max(80, int(round(raw_span * 0.18)))
            axis_min = max(0, min_r - margin)
            axis_max = max_r + margin
            axis_span = max(1, axis_max - axis_min)
            n = len(ratings)
            width = max(760, min(2600, chart_left + chart_right + max(1, n - 1) * 24))
            chart_width = width - chart_left - chart_right
            chart_height = chart_bottom - chart_top

            for i, s in enumerate(snapshots):
                rating = int(s.rating)
                x = chart_left + int(round((i / max(1, n - 1)) * chart_width))
                y = chart_top + int(round((1.0 - ((rating - axis_min) / axis_span)) * chart_height))
                points.append({
                    "x": x,
                    "y": y,
                    "rating": rating,
                    "date": s.date,
                    "tooltip": f"{s.date:%d/%m/%Y} • {rating}",
                })

            tick_count = 5
            for i in range(tick_count):
                ratio = i / max(1, tick_count - 1)
                y = chart_top + int(round(ratio * chart_height))
                value = int(round(axis_max - (ratio * axis_span)))
                y_ticks.append({"y": y, "value": value})

            marker_indexes = sorted({0, max(0, n // 2), n - 1})
            for idx in marker_indexes:
                marker = snapshots[idx]
                x = points[idx]["x"]
                anchor = "middle"
                if idx == 0:
                    anchor = "start"
                elif idx == n - 1:
                    anchor = "end"
                x_markers.append({
                    "x": x,
                    "label": marker.date.strftime("%m/%Y"),
                    "anchor": anchor,
                })

            if points:
                line_path = " ".join([f"L {p['x']} {p['y']}" for p in points[1:]])
                area_path = (
                    f"M {points[0]['x']} {chart_bottom} "
                    f"L {points[0]['x']} {points[0]['y']} "
                    f"{line_path} "
                    f"L {points[-1]['x']} {chart_bottom} Z"
                )

        polyline = " ".join([f"{p['x']},{p['y']}" for p in points])

        ac_solved_ratings = list(
            ScoreEvent.objects.filter(aluno=student, platform="AC")
            .exclude(raw_rating__isnull=True)
            .values_list("raw_rating", flat=True)
        )
        bins = []
        top_bins = []
        hist_max_count = 0
        if ac_solved_ratings:
            step = 200
            start = 0
            end = 3200
            bucket_count = ((end - start) // step) + 1
            counts = [0] * bucket_count
            total_solved = len(ac_solved_ratings)
            for r in ac_solved_ratings:
                rr = int(r)
                if rr < start:
                    idx = 0
                elif rr >= end:
                    idx = bucket_count - 1
                else:
                    idx = (rr - start) // step
                counts[idx] += 1
            hist_max_count = max(counts) if counts else 0
            for i, c in enumerate(counts):
                lo = start + i * step
                hi = lo + step - 1
                if i == bucket_count - 1:
                    label = f"{lo}+"
                else:
                    label = f"{lo}-{hi}"
                height_px = 0
                if hist_max_count and c > 0:
                    height_px = 10 + int(round((c / hist_max_count) * 126))
                percent = (c / total_solved) * 100.0 if total_solved else 0.0
                bins.append({
                    "label": label,
                    "count": c,
                    "height_px": height_px,
                    "percent": percent,
                    "is_peak": bool(c > 0 and c == hist_max_count),
                })
            top_bins = sorted(
                [b for b in bins if b["count"] > 0],
                key=lambda row: (row["count"], row["percent"]),
                reverse=True,
            )[:5]

        hardest = max(ac_solved_ratings) if ac_solved_ratings else None
        avg = (sum(ac_solved_ratings) / len(ac_solved_ratings)) if ac_solved_ratings else None
        latest_rating = ratings[-1] if ratings else None
        delta_last = None
        delta_window = None
        if len(ratings) >= 2:
            delta_last = ratings[-1] - ratings[-2]
            anchor_idx = max(0, len(ratings) - 6)
            delta_window = ratings[-1] - ratings[anchor_idx]

        return render(request, "core/partials/profile_visualizer_ac.html", {
            "student": student,
            "snapshots": snapshots[-8:][::-1],
            "svg_width": width,
            "svg_height": height,
            "chart_left": chart_left,
            "chart_right": chart_right,
            "chart_top": chart_top,
            "chart_bottom": chart_bottom,
            "chart_height": chart_height,
            "polyline": polyline,
            "area_path": area_path,
            "points": points,
            "y_ticks": y_ticks,
            "x_markers": x_markers,
            "axis_min": axis_min,
            "axis_max": axis_max,
            "bins": bins,
            "hist_peak_count": hist_max_count,
            "top_bins": top_bins,
            "hardest": hardest,
            "avg": avg,
            "solved_n": len(ac_solved_ratings),
            "latest_rating": latest_rating,
            "peak_rating": max(ratings) if ratings else None,
            "snapshots_n": len(ratings),
            "delta_last": delta_last,
            "delta_window": delta_window,
            "first_date": snapshots[0].date if snapshots else None,
            "last_date": snapshots[-1].date if snapshots else None,
            "visualizer_error": visualizer_error,
        })

    return HttpResponseBadRequest("Tab invalida.")


@login_required
def user_profile_heatmap(request, username):
    student = get_object_or_404(PerfilAluno, user__username=username)
    metric = (request.GET.get("metric") or "first_ac").lower()
    platform = (request.GET.get("platform") or "all").upper()
    range_key = (request.GET.get("range") or "180d").lower()

    if metric not in {"first_ac", "submissions", "points"}:
        metric = "first_ac"
    if platform not in {"ALL", "CF", "AC"}:
        platform = "ALL"

    end_date = timezone.localdate()
    _active_season, season_start, season_end = get_active_season_range()
    if range_key == "season":
        if season_start and season_end:
            start_date = timezone.localdate(season_start)
            season_end_date = timezone.localdate(season_end)
            end_date = min(season_end_date, end_date)
        else:
            # Fallback when there is no active season configured.
            range_key = "180d"
            start_date = end_date - timedelta(days=179)
    elif range_key == "365d":
        start_date = end_date - timedelta(days=364)
    elif range_key == "90d":
        start_date = end_date - timedelta(days=89)
    else:
        start_date = end_date - timedelta(days=179)
    if start_date > end_date:
        start_date = end_date

    score_events_qs = ScoreEvent.objects.filter(
        aluno=student,
        solved_at__date__gte=start_date,
        solved_at__date__lte=end_date,
    )
    submissions_qs = Submissao.objects.filter(
        aluno=student,
        submission_time__date__gte=start_date,
        submission_time__date__lte=end_date,
    )
    if platform in {"CF", "AC"}:
        score_events_qs = score_events_qs.filter(platform=platform)
        submissions_qs = submissions_qs.filter(plataforma=platform)

    problems_rows = (
        score_events_qs.annotate(day=TruncDate("solved_at"))
        .values("day")
        .annotate(value=Count("id"))
    )
    problems_by_day = {row["day"]: int(row["value"] or 0) for row in problems_rows}

    submissions_rows = (
        submissions_qs.annotate(day=TruncDate("submission_time"))
        .values("day")
        .annotate(value=Count("id"))
    )
    submissions_by_day = {row["day"]: int(row["value"] or 0) for row in submissions_rows}

    points_rows = (
        score_events_qs.annotate(day=TruncDate("solved_at"))
        .values("day")
        .annotate(
            points_general_cf=Sum("points_general_cf_equiv"),
            points_legacy=Sum("points_awarded"),
            points_cf_raw=Sum("points_cf_raw"),
            points_ac_raw=Sum("points_ac_raw"),
        )
    )
    points_by_day = {}
    for row in points_rows:
        primary = int(row.get("points_general_cf") or 0)
        legacy = int(row.get("points_legacy") or 0)
        raw_total = int(row.get("points_cf_raw") or 0) + int(row.get("points_ac_raw") or 0)
        points_by_day[row["day"]] = primary if primary > 0 else (legacy if legacy > 0 else raw_total)

    if metric == "submissions":
        counts_by_day = submissions_by_day
    elif metric == "points":
        counts_by_day = points_by_day
    else:
        counts_by_day = problems_by_day

    weeks, max_value = _profile_build_heatmap_weeks(counts_by_day, start_date, end_date, metric)
    for week in weeks:
        for cell in week:
            if not cell.get("in_range"):
                continue
            day = cell.get("date")
            cell["problems"] = int(problems_by_day.get(day, 0) or 0)
            cell["submissions"] = int(submissions_by_day.get(day, 0) or 0)
            cell["points"] = int(points_by_day.get(day, 0) or 0)

    total_value = sum(v for d, v in counts_by_day.items() if start_date <= d <= end_date)
    active_days = sum(1 for d, v in counts_by_day.items() if start_date <= d <= end_date and v > 0)

    # Current streak (days ending today with activity)
    streak = 0
    cur = end_date
    while cur >= start_date:
        if counts_by_day.get(cur, 0) <= 0:
            break
        streak += 1
        cur -= timedelta(days=1)

    return render(request, "core/partials/profile_heatmap.html", {
        "student": student,
        "metric": metric,
        "platform": platform,
        "range_key": range_key,
        "weeks": weeks,
        "max_value": max_value,
        "total_value": total_value,
        "active_days": active_days,
        "streak": streak,
        "start_date": start_date,
        "end_date": end_date,
        "metric_label": {
            "first_ac": "Primeiro AC",
            "submissions": "Submissões",
            "points": "Pontos",
        }.get(metric, "Primeiro AC"),
    })

@login_required
def solution_modal(request, submission_id):
    from core.services.problem_urls import build_problem_url_from_fields, normalize_problem_url
    from core.utils.languages import get_hljs_class

    submission = get_object_or_404(
        Submissao.objects.select_related('aluno__user'),
        id=submission_id,
    )
    student = submission.aluno

    problem_url = build_problem_url_from_fields(
        submission.plataforma,
        submission.contest_id,
        submission.problem_index,
        submission.problem_name,
    )
    problem_url = normalize_problem_url(problem_url) if problem_url else None

    problem_id = None
    if submission.plataforma == 'CF':
        problem_id = f"{submission.contest_id}{submission.problem_index}"
    elif submission.plataforma == 'AC' and submission.problem_index:
        problem_id = f"{submission.contest_id}_{submission.problem_index.lower()}"

    problem = None
    solution = None
    if problem_id:
        problem = ProblemaReferencia.objects.filter(
            plataforma=submission.plataforma,
            problema_id=problem_id,
        ).first()
    if not problem and problem_url:
        problem = ProblemaReferencia.objects.filter(link=problem_url).first()
    if problem_url:
        solution = SolucaoCompartilhada.objects.filter(
            aluno=student,
            problem_url=problem_url,
        ).order_by('-created_at').first()

    problem_title = problem.titulo if problem else (submission.problem_name or problem_id or "Problema")
    hljs_class = get_hljs_class(solution.language) if solution else "language-plaintext"

    return render(request, 'core/partials/solution_modal_content.html', {
        'student': student,
        'submission': submission,
        'problem': problem,
        'problem_title': problem_title,
        'solution': solution,
        'hljs_class': hljs_class,
    })


def _infer_platform_from_url(problem_url: str | None) -> str | None:
    if not problem_url:
        return None
    netloc = urlsplit(problem_url).netloc.lower()
    if "codeforces.com" in netloc:
        return "CF"
    if "atcoder.jp" in netloc:
        return "AC"
    return None


def _resolve_problem_title(problem_url: str | None) -> str | None:
    if not problem_url:
        return None
    contest_problem = ContestProblem.objects.filter(problem_url=problem_url).first()
    if contest_problem and contest_problem.name:
        return contest_problem.name
    ref = ProblemaReferencia.objects.filter(link=problem_url).first()
    if ref:
        return ref.titulo
    return None


@login_required
def solutions_modal(request):
    from core.services.problem_urls import normalize_problem_url
    from core.utils.languages import get_language_options, get_hljs_class

    problem_url = normalize_problem_url(request.GET.get("problem_url", ""))
    contest_id = request.GET.get("contest_id") or None
    platform = request.GET.get("platform") or None
    origin_id = request.GET.get("origin_id") or None

    if not problem_url:
        return HttpResponseBadRequest("problem_url is required")

    student = getattr(request, 'profile', None)
    if not student:
        return HttpResponseForbidden("Aluno não encontrado")

    solution = SolucaoCompartilhada.objects.filter(
        aluno=student,
        problem_url=problem_url,
    ).first()

    if not solution:
        solution = SolucaoCompartilhada(
            aluno=student,
            problem_url=problem_url,
            contest_id=contest_id,
            platform_context=platform or _infer_platform_from_url(problem_url),
        )
    elif contest_id and not solution.contest_id:
        solution.contest_id = contest_id

    problem_title = _resolve_problem_title(problem_url) or "Problema"

    return render(request, 'core/solutions_modal.html', {
        'solution': solution,
        'problem_url': problem_url,
        'contest_id': contest_id,
        'platform': platform or solution.platform_context or _infer_platform_from_url(problem_url),
        'problem_title': problem_title,
        'language_options': get_language_options(),
        'hljs_class': get_hljs_class(solution.language),
        'origin_id': origin_id,
    })


@login_required
def solutions_save(request):
    import json
    from core.services.problem_urls import normalize_problem_url
    from core.utils.languages import get_language_options, get_hljs_class
    from django.core.exceptions import ValidationError

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    student = getattr(request, 'profile', None)
    if not student:
        return HttpResponseForbidden("Aluno não encontrado")

    problem_url = normalize_problem_url(request.POST.get("problem_url", ""))
    contest_id = request.POST.get("contest_id") or None
    platform = request.POST.get("platform") or None
    origin_id = request.POST.get("origin_id") or None

    if not problem_url:
        return HttpResponseBadRequest("problem_url is required")

    action = request.POST.get("action", "draft")
    language = request.POST.get("language", "cpp")
    code_text = request.POST.get("code_text", "")
    idea_summary = request.POST.get("idea_summary", "")
    visibility = request.POST.get("visibility", "class")

    solution, _ = SolucaoCompartilhada.objects.get_or_create(
        aluno=student,
        problem_url=problem_url,
        defaults={
            "contest_id": contest_id,
            "platform_context": platform or _infer_platform_from_url(problem_url),
            "language": language,
            "code_text": code_text,
            "idea_summary": idea_summary,
            "visibility": visibility,
        },
    )

    previous_status = solution.status
    solution.contest_id = contest_id or solution.contest_id
    solution.platform_context = platform or solution.platform_context or _infer_platform_from_url(problem_url)
    solution.language = language
    solution.code_text = code_text
    solution.idea_summary = idea_summary
    solution.visibility = visibility
    solution.status = "published" if action == "publish" else "draft"
    if solution.status != "approved":
        solution.approved_by = None
        solution.approved_at = None

    errors = None
    try:
        solution.save()
    except ValidationError as exc:
        errors = exc.message_dict
        solution.status = previous_status

    problem_title = _resolve_problem_title(problem_url) or "Problema"
    context = {
        'solution': solution,
        'problem_url': problem_url,
        'contest_id': contest_id,
        'platform': solution.platform_context,
        'problem_title': problem_title,
        'language_options': get_language_options(),
        'hljs_class': get_hljs_class(solution.language),
        'origin_id': origin_id,
        'errors': errors,
        'saved_action': action if not errors else None,
    }
    response = render(request, 'core/solutions_modal.html', context)
    if not errors:
        response["HX-Trigger"] = json.dumps({
            "solution-updated": {
                "problem_url": problem_url,
                "status": solution.status,
                "solution_id": solution.id,
            }
        })
    return response


@login_required
def solutions_format(request):
    import shutil
    import subprocess
    from django.conf import settings
    from core.services.problem_urls import normalize_problem_url
    from core.utils.languages import get_language_options, get_hljs_class

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    student = getattr(request, 'profile', None)
    if not student:
        return HttpResponseForbidden("Aluno não encontrado")

    problem_url = normalize_problem_url(request.POST.get("problem_url", ""))
    contest_id = request.POST.get("contest_id") or None
    platform = request.POST.get("platform") or None
    origin_id = request.POST.get("origin_id") or None
    language = request.POST.get("language", "cpp")
    code_text = request.POST.get("code_text", "")
    idea_summary = request.POST.get("idea_summary", "")
    visibility = request.POST.get("visibility", "class")

    if not problem_url:
        return HttpResponseBadRequest("problem_url is required")

    formatted_code = code_text
    format_message = None
    max_size = 200000

    if not getattr(settings, "ENABLE_CODE_FORMATTER", False):
        format_message = "formatter unavailable"
    elif len(code_text) > max_size:
        format_message = "formatter unavailable"
    else:
        if language == "py" and shutil.which("black"):
            try:
                result = subprocess.run(
                    ["black", "-q", "-"],
                    input=code_text,
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0:
                    formatted_code = result.stdout
                    if not formatted_code and code_text.strip():
                        format_message = "formatter unavailable"
                else:
                    format_message = "formatter unavailable"
            except (subprocess.TimeoutExpired, OSError):
                format_message = "formatter unavailable"
        elif language == "cpp" and shutil.which("clang-format"):
            try:
                result = subprocess.run(
                    ["clang-format", "-assume-filename=main.cpp"],
                    input=code_text,
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0:
                    formatted_code = result.stdout
                    if not formatted_code and code_text.strip():
                        format_message = "formatter unavailable"
                else:
                    format_message = "formatter unavailable"
            except (subprocess.TimeoutExpired, OSError):
                format_message = "formatter unavailable"
        else:
            format_message = "formatter unavailable"

    solution = SolucaoCompartilhada.objects.filter(
        aluno=student,
        problem_url=problem_url,
    ).first()
    if not solution:
        solution = SolucaoCompartilhada(
            aluno=student,
            problem_url=problem_url,
            contest_id=contest_id,
            platform_context=platform or _infer_platform_from_url(problem_url),
        )

    solution.contest_id = contest_id or solution.contest_id
    solution.platform_context = platform or solution.platform_context or _infer_platform_from_url(problem_url)
    solution.language = language
    solution.code_text = formatted_code
    solution.idea_summary = idea_summary
    solution.visibility = visibility

    problem_title = _resolve_problem_title(problem_url) or "Problema"

    return render(request, 'core/solutions_modal.html', {
        'solution': solution,
        'problem_url': problem_url,
        'contest_id': contest_id,
        'platform': solution.platform_context,
        'problem_title': problem_title,
        'language_options': get_language_options(),
        'hljs_class': get_hljs_class(solution.language),
        'origin_id': origin_id,
        'format_message': format_message,
    })


@login_required
def solutions_view(request):
    from core.utils.languages import get_hljs_class

    solution_id = request.GET.get("solution_id")
    if not solution_id or not solution_id.isdigit():
        return HttpResponseBadRequest("solution_id inválido")

    solution = get_object_or_404(
        SolucaoCompartilhada.objects.select_related('aluno__user'),
        id=int(solution_id),
    )

    can_edit = solution.aluno.user_id == request.user.id or request.user.is_staff
    problem_title = _resolve_problem_title(solution.problem_url) or "Problema"

    return render(request, 'core/solutions_view.html', {
        'solution': solution,
        'problem_title': problem_title,
        'hljs_class': get_hljs_class(solution.language),
        'can_edit': can_edit,
    })


@login_required
def solutions_approve(request):
    from django.utils import timezone as dj_timezone
    from core.utils.languages import get_hljs_class

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    if not request.user.is_staff:
        return HttpResponseForbidden("Apenas staff pode aprovar")

    solution_id = request.POST.get("solution_id")
    if not solution_id or not solution_id.isdigit():
        return HttpResponseBadRequest("solution_id inválido")

    solution = get_object_or_404(SolucaoCompartilhada, id=int(solution_id))
    solution.status = "approved"
    solution.approved_by = request.user
    solution.approved_at = dj_timezone.now()
    solution.save()

    return render(request, 'core/solutions_view.html', {
        'solution': solution,
        'problem_title': _resolve_problem_title(solution.problem_url) or "Problema",
        'hljs_class': get_hljs_class(solution.language),
        'can_edit': True,
        'approved': True,
    })


@login_required
def ranking(request):
    mode = request.GET.get("mode", "points")
    if mode not in {"points", "rating", "how"}:
        mode = "points"
    category = request.GET.get("source") or request.GET.get("category", "overall")
    if category not in {"overall", "cf", "ac"}:
        category = "overall"
    window = request.GET.get("window", "season")
    if window == "alltime":
        window = "all"
    if window not in {"season", "7d", "30d", "all", "custom"}:
        window = "season"
    scope = "global"
    turma_id = None
    turmas = []
    season_contest_only = _is_truthy_param(request.GET.get("season_contest_only"))
    season_id = request.GET.get("season_id")
    seasons, selected_season, season_start_dt, season_end_dt = _resolve_season_selection(season_id if window == "season" else None)

    mode_label = {"points": "Pontos", "rating": "Rating", "how": "Como funciona"}[mode]
    source_label = {"overall": "Geral", "cf": "Codeforces", "ac": "AtCoder"}[category]
    window_label = {
        "season": "Temporada",
        "7d": "7 dias",
        "30d": "30 dias",
        "all": "All-time",
        "custom": "Período custom",
    }.get(window, "Temporada")
    if window == "season" and selected_season:
        window_label = f"Temporada • {_season_name(selected_season)}"

    subtitle = f"{mode_label} • {source_label} • {window_label}"
    if mode == "points" and season_contest_only:
        subtitle += " • Apenas contests da temporada"

    context = {
        "mode": mode,
        "category": category,
        "window": window,
        "scope": scope,
        "turma_id": turma_id,
        "turmas": turmas,
        "mode_unavailable": False,
        "subtitle": subtitle,
        "seasons": seasons,
        "selected_season": selected_season,
        "season_start_dt": season_start_dt,
        "season_end_dt": season_end_dt,
        "season_id": selected_season.id if selected_season else "",
        "season_contest_only": season_contest_only,
    }
    context.update(_build_welcome_tour_context(request, "ranking"))

    return render(request, "core/ranking.html", context)


def _ranking_examples_payload() -> dict:
    conversion_status = get_conversion_status()
    examples_ac = []
    for ac in (800, 1200, 1600, 2000):
        cf_equiv = convert_ac_to_cf(ac)
        examples_ac.append({
            "ac": ac,
            "cf_equiv": cf_equiv,
            "points": calculate_points(cf_equiv),
        })

    examples_cf = []
    for cf in (800, 1200, 1600, 2000):
        examples_cf.append({
            "cf": cf,
            "points": calculate_points(cf),
        })

    return {
        "conversion_status": conversion_status,
        "examples_ac": examples_ac,
        "examples_cf": examples_cf,
    }


@login_required
def ranking_list(request):
    mode = request.GET.get("mode", "points")
    if mode not in {"points", "rating", "how"}:
        mode = "points"
    category = request.GET.get("source") or request.GET.get("category", "overall")
    if category not in {"overall", "cf", "ac"}:
        category = "overall"
    window = request.GET.get("window", "season")
    if window == "alltime":
        window = "all"
    if window not in {"season", "7d", "30d", "all", "custom"}:
        window = "season"
    scope = "global"
    turma_id = None
    season_id = request.GET.get("season_id")
    season_contest_only = _is_truthy_param(request.GET.get("season_contest_only"))
    seasons, selected_season, season_start_dt, season_end_dt = _resolve_season_selection(
        season_id if window == "season" else None
    )
    q = (request.GET.get("q") or "").strip()
    q_lower = q.lower()
    min_solves = request.GET.get("min_solves")
    min_solves = int(min_solves) if min_solves and min_solves.isdigit() else None
    movers_only = request.GET.get("movers_only") == "1"
    only_with_rating = request.GET.get("only_with_rating") == "1"
    order_by = request.GET.get("order_by") or "score"
    start_date = request.GET.get("start_date") or ""
    end_date = request.GET.get("end_date") or ""

    if mode == "how":
        mode_label = "Como funciona"
        source_label = {"overall": "Geral", "cf": "Codeforces", "ac": "AtCoder"}[category]
        window_label = {
            "season": "Temporada",
            "7d": "7d",
            "30d": "30d",
            "all": "All-time",
            "custom": "Custom",
        }.get(window, "Temporada")
        if window == "season" and selected_season:
            window_label = f"Temporada • {_season_name(selected_season)}"

        subtitle = f"{mode_label} • {source_label} • {window_label}"
        payload = {
            "rows": [],
            "mode": mode,
            "category": category,
            "window": window,
            "scope": scope,
            "turma_id": turma_id,
            "current_user_rank": None,
            "mode_unavailable": False,
            "top_movers": [],
            "q": q,
            "start_date": start_date,
            "end_date": end_date,
            "min_solves": min_solves,
            "movers_only": movers_only,
            "only_with_rating": only_with_rating,
            "order_by": order_by,
            "page": 1,
            "per_page": 25,
            "total_pages": 1,
            "has_prev": False,
            "has_next": False,
            "prev_page": 1,
            "next_page": 1,
            "chips": [],
            "filters_count": 0,
            "next_goal": None,
            "subtitle": subtitle,
            "season_info": None,
            "season_id": selected_season.id if selected_season else "",
            "season_contest_only": season_contest_only,
        }
        payload.update(_ranking_examples_payload())
        return render(request, "core/partials/ranking_list.html", payload)

    rows = []
    rows_have_activity_solves = False
    custom_start = None
    custom_end = None
    if mode != "points" and window == "custom":
        window = "season"

    if window == "custom" and start_date and end_date and mode == "points":
        try:
            custom_start = datetime.fromisoformat(start_date).date()
            custom_end = datetime.fromisoformat(end_date).date()
        except ValueError:
            custom_start = None
            custom_end = None
        if custom_start and custom_end and custom_end < custom_start:
            custom_start = None
            custom_end = None
        if custom_start and custom_end:
            start_dt = timezone.make_aware(datetime.combine(custom_start, datetime.min.time()))
            end_dt = timezone.make_aware(datetime.combine(custom_end, datetime.max.time()))
            events = ScoreEvent.objects.filter(solved_at__gte=start_dt, solved_at__lte=end_dt)
            if category == "cf":
                events = events.filter(platform="CF")
            elif category == "ac":
                events = events.filter(platform="AC")
            if season_contest_only:
                events = _filter_events_to_season_contests(events, season_start_dt, season_end_dt)
            rows = _build_points_rows_from_events(events, category, scope, turma_id)
            rows_have_activity_solves = True
        else:
            window = "season"

    if not rows:
        if mode == "points":
            if season_contest_only:
                events = ScoreEvent.objects.all()
                now = timezone.now()
                if window == "7d":
                    events = events.filter(solved_at__gte=now - timedelta(days=7), solved_at__lte=now)
                elif window == "30d":
                    events = events.filter(solved_at__gte=now - timedelta(days=30), solved_at__lte=now)
                elif window == "season":
                    if season_start_dt and season_end_dt:
                        events = events.filter(solved_at__gte=season_start_dt, solved_at__lte=season_end_dt)
                    else:
                        events = events.none()
                elif window == "custom":
                    if start_date and end_date:
                        try:
                            custom_start = datetime.fromisoformat(start_date).date()
                            custom_end = datetime.fromisoformat(end_date).date()
                        except ValueError:
                            custom_start = None
                            custom_end = None
                        if custom_start and custom_end and custom_end >= custom_start:
                            start_dt = timezone.make_aware(datetime.combine(custom_start, datetime.min.time()))
                            end_dt = timezone.make_aware(datetime.combine(custom_end, datetime.max.time()))
                            events = events.filter(solved_at__gte=start_dt, solved_at__lte=end_dt)
                        else:
                            window = "season"
                            if season_start_dt and season_end_dt:
                                events = events.filter(solved_at__gte=season_start_dt, solved_at__lte=season_end_dt)
                            else:
                                events = events.none()
                    else:
                        window = "season"
                        if season_start_dt and season_end_dt:
                            events = events.filter(solved_at__gte=season_start_dt, solved_at__lte=season_end_dt)
                        else:
                            events = events.none()

                if category == "cf":
                    events = events.filter(platform="CF")
                elif category == "ac":
                    events = events.filter(platform="AC")
                events = _filter_events_to_season_contests(events, season_start_dt, season_end_dt)
                rows = _build_points_rows_from_events(events, category, scope, turma_id)
                rows_have_activity_solves = True
            elif window == "season" and selected_season and not selected_season.is_active:
                events = ScoreEvent.objects.filter(solved_at__gte=season_start_dt, solved_at__lte=season_end_dt)
                if category == "cf":
                    events = events.filter(platform="CF")
                elif category == "ac":
                    events = events.filter(platform="AC")
                rows = _build_points_rows_from_events(events, category, scope, turma_id)
                rows_have_activity_solves = True
            else:
                rows = build_ranking_with_delta(category, window, scope, turma_id)
        elif mode == "rating":
            rows = build_rating_ranking_with_delta(category, scope, turma_id)
        else:
            rows = []
    current_user_rank = None
    aluno = getattr(request, 'profile', None)

    # attach solves count when missing
    if rows and mode in {"points", "rating"} and window != "custom" and (mode == "rating" or not rows_have_activity_solves):
        now = timezone.now()
        if window == "7d":
            start_dt = now - timedelta(days=7)
            end_dt = now
        elif window == "30d":
            start_dt = now - timedelta(days=30)
            end_dt = now
        elif window == "season":
            if season_start_dt and season_end_dt:
                start_dt = season_start_dt
                end_dt = season_end_dt
            else:
                start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if start_dt.month == 12:
                    end_dt = start_dt.replace(year=start_dt.year + 1, month=1)
                else:
                    end_dt = start_dt.replace(month=start_dt.month + 1)
        else:
            start_dt = None
            end_dt = None

        events = ScoreEvent.objects.all()
        if start_dt:
            events = events.filter(solved_at__gte=start_dt)
        if end_dt:
            events = events.filter(solved_at__lt=end_dt)
        if category == "cf":
            events = events.filter(platform="CF")
        elif category == "ac":
            events = events.filter(platform="AC")
        if mode == "points" and season_contest_only:
            events = _filter_events_to_season_contests(events, season_start_dt, season_end_dt)
        solves = events.values("aluno_id").annotate(count=Count("id"))
        solves_map = {row["aluno_id"]: row["count"] for row in solves}
        for row in rows:
            row.activity_solves = solves_map.get(row.aluno.id, 0)

    if q:
        rows = [
            row for row in rows
            if q_lower in row.aluno.user.username.lower()
            or q_lower in (row.aluno.handle_codeforces or "").lower()
            or q_lower in (row.aluno.handle_atcoder or "").lower()
        ]

    if mode == "rating" and only_with_rating:
        if category == "cf":
            rows = [row for row in rows if row.points_cf > 0]
        elif category == "ac":
            rows = [row for row in rows if row.points_ac > 0]
        else:
            rows = [row for row in rows if row.points > 0]

    if min_solves is not None:
        rows = [row for row in rows if getattr(row, "activity_solves", 0) >= min_solves]

    if movers_only:
        rows = [row for row in rows if row.delta > 0]

    if order_by == "delta":
        rows.sort(key=lambda r: (-r.delta, -r.points, r.aluno.user.username))
    elif order_by == "solves":
        rows.sort(key=lambda r: (-getattr(r, "activity_solves", 0), -r.points, r.aluno.user.username))

    for idx, row in enumerate(rows, start=1):
        row.rank = idx
        if idx == 1:
            row.points_to_above = None
        else:
            row.points_to_above = max(0, int(rows[idx - 2].points) - int(row.points))

    if aluno:
        for row in rows:
            if row.aluno.id == aluno.id:
                current_user_rank = row
                break

    page = request.GET.get("page") or "1"
    per_page = request.GET.get("per_page") or "25"
    try:
        page = max(1, int(page))
    except ValueError:
        page = 1
    try:
        per_page = max(10, min(100, int(per_page)))
    except ValueError:
        per_page = 25
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    end = start + per_page
    all_rows = rows
    rows = rows[start:end]
    has_prev = page > 1
    has_next = page < total_pages

    # chips + filters count
    mode_label = {"points": "Pontos", "rating": "Rating", "how": "Como funciona"}[mode]
    source_label = {"overall": "Geral", "cf": "Codeforces", "ac": "AtCoder"}[category]
    window_label = {
        "season": "Temporada",
        "7d": "7d",
        "30d": "30d",
        "all": "All-time",
        "custom": "Custom",
    }.get(window, "Temporada")
    if window == "season" and selected_season:
        window_label = f"Temporada • {_season_name(selected_season)}"

    chips = [
        {"label": f"Modo: {mode_label}", "key": "mode", "value": "points"},
        {"label": f"Fonte: {source_label}", "key": "source", "value": "overall"},
        {"label": f"Período: {window_label}", "key": "window", "value": "season"},
    ]
    if window == "custom" and start_date and end_date:
        chips[-1]["label"] = f"Período: {start_date} → {end_date}"
    if q:
        chips.append({"label": f"Busca: {q}", "key": "q", "value": ""})
    if mode == "points" and season_contest_only:
        chips.append({
            "label": "Somente contests da temporada",
            "key": "season_contest_only",
            "value": "0",
        })
    if movers_only:
        chips.append({"label": "Só quem subiu", "key": "movers_only", "value": "0"})
    if only_with_rating:
        chips.append({"label": "Somente rating", "key": "only_with_rating", "value": "0"})
    if min_solves is not None:
        chips.append({"label": f"Min solves: {min_solves}", "key": "min_solves", "value": ""})
    if order_by and order_by != "score":
        chips.append({"label": f"Ordenar: {order_by}", "key": "order_by", "value": "score"})

    filters_count = 0
    if mode != "points":
        filters_count += 1
    if category != "overall":
        filters_count += 1
    if window != "season":
        filters_count += 1
    if q:
        filters_count += 1
    if mode == "points" and season_contest_only:
        filters_count += 1
    if movers_only:
        filters_count += 1
    if only_with_rating:
        filters_count += 1
    if min_solves is not None:
        filters_count += 1
    if order_by and order_by != "score":
        filters_count += 1

    subtitle = f"{mode_label} • {source_label} • {window_label}"
    if mode == "points" and season_contest_only:
        subtitle += " • Apenas contests da temporada"

    next_goal = None
    if current_user_rank and all_rows:
        if len(all_rows) >= 10 and current_user_rank.rank > 10:
            target_points = all_rows[9].points
            next_goal = max(0, target_points - current_user_rank.points + 1)

    season_info = None
    if window == "season" and selected_season:
        season_info = {
            "id": selected_season.id,
            "name": selected_season.name or "",
            "label": _season_name(selected_season),
            "start": selected_season.start_date,
            "end": selected_season.end_date,
            "is_active": selected_season.is_active,
        }

    return render(request, "core/partials/ranking_list.html", {
        "rows": rows,
        "mode": mode,
        "category": category,
        "window": window,
        "scope": scope,
        "turma_id": turma_id,
        "current_user_rank": current_user_rank,
        "mode_unavailable": False,
        "top_movers": top_movers_last_7d(),
        "conversion_status": None,
        "examples_ac": [],
        "examples_cf": [],
        "q": q,
        "start_date": start_date,
        "end_date": end_date,
        "min_solves": min_solves,
        "movers_only": movers_only,
        "only_with_rating": only_with_rating,
        "order_by": order_by,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_page": page - 1,
        "next_page": page + 1,
        "chips": chips,
        "filters_count": filters_count,
        "next_goal": next_goal,
        "subtitle": subtitle,
        "season_info": season_info,
        "season_id": selected_season.id if selected_season else "",
        "season_contest_only": season_contest_only,
    })


@login_required
def contests_overview(request):
    platform_filter = (request.GET.get("platform") or "all").lower()
    if platform_filter not in {"all", "cf", "ac"}:
        platform_filter = "all"

    ac_category = request.GET.get("ac_category") or "all"
    cf_division = request.GET.get("cf_division") or "all"
    valid_ac = {choice[0] for choice in Contest.CATEGORY_CHOICES}
    valid_cf = {choice[0] for choice in Contest.DIVISION_CHOICES}
    if ac_category != "all" and ac_category not in valid_ac:
        ac_category = "all"
    if cf_division != "all" and cf_division not in valid_cf:
        cf_division = "all"
    if platform_filter == "cf":
        ac_category = "all"
    if platform_filter == "ac":
        cf_division = "all"

    now = timezone.now()
    current_year = now.year
    year_param = request.GET.get("year")
    selected_year = current_year
    if year_param and year_param.isdigit():
        selected_year = int(year_param)

    available_years = list(
        Contest.objects.values_list("year", flat=True)
        .distinct()
        .order_by("-year")
    )
    if not available_years:
        available_years = [current_year]

    qs = Contest.objects.filter(
        year=selected_year,
        start_time__isnull=False,
        start_time__lte=now,
    )
    if platform_filter == "cf":
        qs = qs.filter(platform="CF")
    elif platform_filter == "ac":
        qs = qs.filter(platform="AC")

    if ac_category != "all":
        qs = qs.filter(platform="AC", category=ac_category)
    if cf_division != "all":
        qs = qs.filter(platform="CF", division=cf_division)

    qs = qs.only(
        "id",
        "platform",
        "contest_id",
        "title",
        "start_time",
        "category",
        "division",
    ).order_by("-start_time")
    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    contests_page = list(page_obj.object_list)
    contests, total_problems, total_solves = _build_contest_rows(contests_page)

    context = {
        "contests": contests,
        "current_year": current_year,
        "selected_year": selected_year,
        "platform_filter": platform_filter,
        "available_years": available_years,
        "total_contests": paginator.count,
        "total_problems": total_problems,
        "total_solves": total_solves,
        "page_obj": page_obj,
        "ac_category": ac_category,
        "cf_division": cf_division,
        "ac_categories": [choice[0] for choice in Contest.CATEGORY_CHOICES],
        "cf_divisions": [choice[0] for choice in Contest.DIVISION_CHOICES],
    }
    context.update(_build_welcome_tour_context(request, "contests"))
    return render(request, "core/contests.html", context)


def _build_contest_rows(contests_page: list[Contest]) -> tuple[list[dict], int, int]:
    if not contests_page:
        return [], 0, 0

    problems_by_contest: dict[int, list[ContestProblem]] = {}
    problem_urls: set[str] = set()
    for problem in (
        ContestProblem.objects.filter(contest__in=contests_page)
        .only(
            "id",
            "contest_id",
            "index_label",
            "problem_url",
            "name",
            "tags",
            "cf_rating",
            "rating_status",
            "order",
        )
        .order_by("contest_id", "order")
    ):
        problems_by_contest.setdefault(problem.contest_id, []).append(problem)
        if problem.problem_url:
            problem_urls.add(problem.problem_url)

    cache_by_url = {
        cache.problem_url: cache
        for cache in ProblemRatingCache.objects.filter(problem_url__in=problem_urls).only(
            "problem_url",
            "effective_rating",
            "status",
        )
    }

    tags_map: dict[tuple[str, str, str], set[str]] = {}
    cf_ids = [contest.contest_id for contest in contests_page if contest.platform == "CF"]
    ac_ids = [contest.contest_id for contest in contests_page if contest.platform == "AC"]
    tags_filter = Q(id__isnull=True)
    if cf_ids:
        tags_filter |= Q(plataforma="CF", contest_id__in=cf_ids)
    if ac_ids:
        tags_filter |= Q(plataforma="AC", contest_id__in=ac_ids)
    if cf_ids or ac_ids:
        tag_rows = (
            Submissao.objects.filter(tags_filter)
            .exclude(tags__isnull=True)
            .exclude(tags="")
            .values("plataforma", "contest_id", "problem_index", "tags")
        )
        for row in tag_rows.iterator():
            key = (row.get("plataforma"), row.get("contest_id"), row.get("problem_index"))
            tags_map.setdefault(key, set()).update(
                {t.strip() for t in (row.get("tags") or "").split(",") if t.strip()}
            )

    solvers_map: dict[tuple[str, str, str], list[dict]] = {}
    seen_map: dict[tuple[str, str, str], set[int]] = {}
    solver_user_ids: set[int] = set()
    solver_filter = Q(id__isnull=True)
    if cf_ids:
        solver_filter |= Q(plataforma="CF", contest_id__in=cf_ids, verdict="OK")
    if ac_ids:
        solver_filter |= Q(plataforma="AC", contest_id__in=ac_ids, verdict="AC")
    if cf_ids or ac_ids:
        solver_rows = (
            Submissao.objects.filter(solver_filter)
            .values(
                "plataforma",
                "contest_id",
                "problem_index",
                "aluno_id",
                "aluno__user_id",
                "aluno__user__username",
                "submission_time",
            )
            .order_by("submission_time")
        )
        for row in solver_rows.iterator():
            key = (row.get("plataforma"), row.get("contest_id"), row.get("problem_index"))
            seen = seen_map.setdefault(key, set())
            aluno_id = row.get("aluno_id")
            if aluno_id in seen:
                continue
            seen.add(aluno_id)
            user_id = row.get("aluno__user_id")
            if user_id is not None:
                solver_user_ids.add(int(user_id))
            solvers_map.setdefault(key, []).append(
                {
                    "username": row.get("aluno__user__username") or "unknown",
                    "user_id": user_id,
                }
            )

    solver_group_map = _solver_group_map(solver_user_ids)
    for solvers in solvers_map.values():
        for solver in solvers:
            group_meta = solver_group_map.get(solver.get("user_id"), {})
            is_villain_group = bool(group_meta.get("is_villain"))
            solver["is_villain_group"] = is_villain_group
            solver["group_name"] = group_meta.get("name", "") if is_villain_group else ""
            solver["group_color"] = group_meta.get("color", "") if is_villain_group else ""
            solver["pill_style"] = group_meta.get("style", "") if is_villain_group else ""
            solver.pop("user_id", None)

    contests = []
    total_problems = 0
    total_solves = 0
    for contest in contests_page:
        problems = []
        ratings = []
        ready_count = 0
        for problem in problems_by_contest.get(contest.id, []):
            cache = cache_by_url.get(problem.problem_url)
            rating, status = _resolve_effective_rating(problem, cache)
            if rating is not None:
                ready_count += 1
            rating_label, rating_color, rating_fill, rating_text = _rating_badge(
                rating,
                status,
                contest.platform,
            )
            if rating is not None:
                ratings.append(rating)

            key = (contest.platform, contest.contest_id, problem.index_label)
            tags_set = set(tags_map.get(key, set()))
            if problem.tags:
                tags_set.update({t.strip() for t in (problem.tags or "").split(",") if t.strip()})
            tags = sorted(tags_set)
            solvers = solvers_map.get(key, [])
            total_solves += len(solvers)

            problems.append(
                {
                    "index_label": problem.index_label,
                    "name": problem.name or problem.index_label,
                    "url": problem.problem_url,
                    "solvers": solvers,
                    "solve_count": len(solvers),
                    "tags": tags,
                    "rating_label": rating_label,
                    "rating_color": rating_color,
                    "rating_fill": rating_fill,
                    "rating_text": rating_text,
                }
            )

        total_problems += len(problems)
        total_count = len(problems)
        avg_rating = None
        if ratings:
            avg_rating = int(round(sum(ratings) / len(ratings)))
        avg_label, avg_color, avg_fill, avg_text = _rating_badge(
            avg_rating,
            "OK" if avg_rating is not None else "TEMP_FAIL",
            contest.platform,
        )
        if total_count == 0:
            avg_display = "Media pendente"
            ratings_status = "NONE"
        elif ready_count == 0:
            avg_display = f"Media pendente ({ready_count}/{total_count})"
            ratings_status = "NONE"
        elif ready_count < total_count:
            avg_display = f"Media parcial ({ready_count}/{total_count})"
            ratings_status = "PARTIAL"
        else:
            avg_display = f"Media {avg_label}"
            ratings_status = "READY"

        contests.append(
            {
                "contest": contest,
                "detail_url": reverse(
                    "contest_detail",
                    kwargs={
                        "platform": contest.platform.lower(),
                        "contest_id": contest.contest_id,
                    },
                ),
                "url": _contest_external_url(contest.platform, contest.contest_id),
                "problems": problems,
                "average_rating": avg_rating,
                "average_label": avg_label,
                "average_color": avg_color,
                "average_fill": avg_fill,
                "average_text": avg_text,
                "average_display": avg_display,
                "ratings_ready": ready_count,
                "ratings_total": total_count,
                "ratings_status": ratings_status,
            }
        )

    return contests, total_problems, total_solves


@login_required
def admin_panel(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Acesso restrito.")

    error = None
    success = None
    force_error = None
    force_feedback = None
    maintenance_error = None
    maintenance_feedback = None
    force_refresh_api_checks = False
    active_season, season_start, season_end = get_active_season_range()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_season":
            start_date = request.POST.get("season_start")
            end_date = request.POST.get("season_end")
            name = (request.POST.get("season_name") or "").strip()
            try:
                start_dt = datetime.fromisoformat(start_date).date()
                end_dt = datetime.fromisoformat(end_date).date()
                if end_dt < start_dt:
                    raise ValueError("Data final menor que a inicial.")
                SeasonConfig.objects.update(is_active=False)
                active_season = SeasonConfig.objects.create(
                    name=name,
                    start_date=start_dt,
                    end_date=end_dt,
                    is_active=True,
                )
                try:
                    from core.tasks import recompute_score_windows
                    recompute_score_windows()
                except Exception:
                    pass
                success = "Temporada atual atualizada."
                active_season, season_start, season_end = get_active_season_range()
            except Exception as exc:
                error = f"Erro ao salvar temporada: {exc}"

        if action == "delete_season":
            season_id_raw = (request.POST.get("season_id") or "").strip()
            if not season_id_raw.isdigit():
                error = "Temporada invalida."
            else:
                season = SeasonConfig.objects.filter(id=int(season_id_raw)).first()
                if not season:
                    error = "Temporada nao encontrada."
                else:
                    deleted_label = _season_label(season)
                    deleting_active = bool(season.is_active)
                    season.delete()

                    if deleting_active:
                        replacement = SeasonConfig.objects.order_by("-start_date", "-id").first()
                        if replacement:
                            SeasonConfig.objects.update(is_active=False)
                            replacement.is_active = True
                            replacement.save(update_fields=["is_active", "updated_at"])
                            active_season, season_start, season_end = get_active_season_range()
                            success = (
                                f"Temporada {deleted_label} apagada. "
                                f"{_season_label(replacement)} virou a temporada ativa."
                            )
                        else:
                            active_season = None
                            season_start = None
                            season_end = None
                            success = (
                                f"Temporada {deleted_label} apagada. "
                                "Agora nao ha temporada ativa."
                            )
                    else:
                        success = f"Temporada {deleted_label} apagada."

                    try:
                        from core.tasks import recompute_score_windows
                        recompute_score_windows()
                    except Exception:
                        pass

        if action == "force_ratings":
            limit_raw = request.POST.get("limit") or "8"
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 8
            limit = max(1, min(limit, 10))

            lock_key = "admin_force_ratings_lock"
            if cache.get(lock_key):
                force_error = "Atualizacao ja executada recentemente. Aguarde ~1 minuto para respeitar o limite do CLIST."
            else:
                cache.set(lock_key, True, timeout=60)

                cooldown_minutes = 30
                max_attempts = 6
                now = timezone.now()
                cooldown = now - timedelta(minutes=cooldown_minutes)

                pending_qs = ContestProblem.objects.filter(
                    rating_status__in=["MISSING", "TEMP_FAIL", "QUEUED"]
                )
                eligible_qs = pending_qs.filter(
                    rating_attempts__lt=max_attempts
                ).filter(
                    Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=cooldown)
                )

                total_pending = pending_qs.count()
                eligible_count = eligible_qs.count()
                attempts_blocked = pending_qs.filter(rating_attempts__gte=max_attempts).count()
                cooldown_blocked = pending_qs.filter(
                    rating_attempts__lt=max_attempts,
                    rating_last_requested_at__gt=cooldown,
                ).count()
                not_found = ContestProblem.objects.filter(rating_status="NOT_FOUND").count()

                try:
                    from core.tasks import ratings_backfill_scheduler
                    result = ratings_backfill_scheduler(
                        limit=limit,
                        cooldown_minutes=cooldown_minutes,
                        max_attempts=max_attempts,
                    )
                except Exception as exc:
                    force_error = f"Falha ao enfileirar atualizacoes: {exc}"
                    result = {}

                enqueued = int(result.get("enqueued") or 0)
                reset_attempts = int(result.get("reset_attempts") or 0)
                remaining = max(0, eligible_count - enqueued)
                force_feedback = {
                    "enqueued": enqueued,
                    "limit": limit,
                    "eligible": eligible_count,
                    "remaining": remaining,
                    "pending": total_pending,
                    "cooldown_blocked": cooldown_blocked,
                    "attempts_blocked": attempts_blocked,
                    "reset_attempts": reset_attempts,
                    "not_found": not_found,
                    "cooldown_minutes": cooldown_minutes,
                    "max_attempts": max_attempts,
                    "rate_limited": enqueued >= limit and remaining > 0,
                    "cooldown_warning": cooldown_blocked > 0,
                }

        if action == "refresh_api_checks":
            force_refresh_api_checks = True
            success = "Checks de APIs atualizados."

        if action == "run_maintenance_cycle":
            try:
                from core.tasks import (
                    contests_catalog_refresh,
                    contests_problems_scheduler,
                    ratings_backfill_scheduler,
                    process_rating_fetch_jobs,
                )
                try:
                    scheduler_limit = int(request.POST.get("scheduler_limit") or 12)
                except ValueError:
                    scheduler_limit = 12
                scheduler_limit = max(2, min(40, scheduler_limit))

                try:
                    backfill_limit = int(request.POST.get("backfill_limit") or 10)
                except ValueError:
                    backfill_limit = 10
                backfill_limit = max(1, min(10, backfill_limit))

                try:
                    fetch_jobs_limit = int(request.POST.get("fetch_jobs_limit") or 10)
                except ValueError:
                    fetch_jobs_limit = 10
                fetch_jobs_limit = max(1, min(20, fetch_jobs_limit))

                catalog_result = contests_catalog_refresh()
                scheduler_result = contests_problems_scheduler(
                    max_cf_per_run=scheduler_limit,
                    max_ac_per_run=scheduler_limit,
                )
                backfill_result = ratings_backfill_scheduler(limit=backfill_limit)
                jobs_result = process_rating_fetch_jobs(limit=fetch_jobs_limit)

                maintenance_feedback = {
                    "type": "cycle",
                    "catalog": catalog_result,
                    "scheduler": scheduler_result,
                    "backfill": backfill_result,
                    "jobs": jobs_result,
                    "scheduler_limit": scheduler_limit,
                    "backfill_limit": backfill_limit,
                    "fetch_jobs_limit": fetch_jobs_limit,
                }
                success = "Ciclo de manutencao executado."
            except Exception as exc:
                maintenance_error = f"Falha no ciclo de manutencao: {exc}"

        if action == "reset_failed_contests":
            try:
                now = timezone.now()
                reset_count = Contest.objects.filter(problems_sync_status="FAILED").update(
                    problems_sync_status="STALE",
                    problems_next_sync_at=now,
                )
                maintenance_feedback = {
                    "type": "reset_failed_contests",
                    "reset_count": reset_count,
                }
                success = f"{reset_count} contests em falha movidos para STALE."
            except Exception as exc:
                maintenance_error = f"Falha ao reativar contests em falha: {exc}"

        if action == "reset_rating_attempts":
            try:
                try:
                    threshold = int(request.POST.get("attempt_threshold") or 6)
                except ValueError:
                    threshold = 6
                threshold = max(1, min(20, threshold))
                reset_count = ContestProblem.objects.filter(
                    rating_status__in=["MISSING", "TEMP_FAIL", "QUEUED"],
                    rating_attempts__gte=threshold,
                ).update(
                    rating_attempts=0,
                    rating_last_requested_at=None,
                )
                maintenance_feedback = {
                    "type": "reset_rating_attempts",
                    "reset_count": reset_count,
                    "threshold": threshold,
                }
                success = f"{reset_count} problemas tiveram rating_attempts resetado."
            except Exception as exc:
                maintenance_error = f"Falha ao resetar rating_attempts: {exc}"

        if action == "clear_sync_locks":
            try:
                from core.tasks import _get_redis_client

                redis_client = _get_redis_client()
                lock_patterns = [
                    "sync_contest_problems:*",
                    "sync_contest_submissions:*",
                    "force_contest_sync:*",
                    "admin_force_ratings_lock",
                    "contests_catalog_cursor_year:*",
                ]
                deleted = 0
                per_pattern = {}
                for pattern in lock_patterns:
                    keys = list(redis_client.scan_iter(match=pattern, count=1000))
                    if keys:
                        removed = int(redis_client.delete(*keys))
                    else:
                        removed = 0
                    deleted += removed
                    per_pattern[pattern] = removed

                maintenance_feedback = {
                    "type": "clear_sync_locks",
                    "deleted": deleted,
                    "per_pattern": per_pattern,
                }
                success = f"{deleted} locks/chaves de sync removidas no Redis."
            except Exception as exc:
                maintenance_error = f"Falha ao limpar locks de sync: {exc}"

    villain_ids = _villain_user_ids()
    students_qs = PerfilAluno.objects.all()
    if villain_ids:
        students_qs = students_qs.exclude(user_id__in=villain_ids)

    user_total = User.objects.count()
    super_total = User.objects.filter(is_superuser=True).count()
    student_total = students_qs.count()
    contest_total = Contest.objects.count()

    api_checks, api_checks_checked_at = _run_admin_api_checks(
        force_refresh=force_refresh_api_checks
    )
    api_checks_ok = sum(1 for row in api_checks if row.get("ok"))
    api_checks_total = len(api_checks)
    if api_checks_checked_at:
        api_checks_age_seconds = int(
            max(0, (timezone.now() - api_checks_checked_at).total_seconds())
        )
    else:
        api_checks_age_seconds = None

    api_catalog = _build_admin_api_catalog()
    now = timezone.now()

    # Health score: visão rápida de sincronização dos principais blocos.
    contest_synced = Contest.objects.filter(problems_sync_status="SYNCED").count()
    contest_sync_percent = int(round((contest_synced / contest_total) * 100)) if contest_total else 100

    problems_total = ContestProblem.objects.count()
    problems_resolved = ContestProblem.objects.filter(
        rating_status__in=["OK", "NOT_FOUND"]
    ).count()
    ratings_sync_percent = int(round((problems_resolved / problems_total) * 100)) if problems_total else 100

    students_with_handles = students_qs.filter(
        (Q(handle_codeforces__isnull=False) & ~Q(handle_codeforces=""))
        | (Q(handle_atcoder__isnull=False) & ~Q(handle_atcoder=""))
    )
    students_handles_total = students_with_handles.count()
    students_synced_recent = students_with_handles.filter(
        Q(cf_rating_updated_at__gte=now - timedelta(hours=24))
        | Q(ac_rating_updated_at__gte=now - timedelta(hours=24))
    ).count()
    students_sync_percent = (
        int(round((students_synced_recent / students_handles_total) * 100))
        if students_handles_total
        else 100
    )

    api_sync_percent = int(round((api_checks_ok / api_checks_total) * 100)) if api_checks_total else 100

    sync_components = [
        {
            "label": "Alunos (sync 24h)",
            "percent": students_sync_percent,
            "detail": f"{students_synced_recent}/{students_handles_total}",
        },
        {
            "label": "Contests",
            "percent": contest_sync_percent,
            "detail": f"{contest_synced}/{contest_total}",
        },
        {
            "label": "Ratings de problemas",
            "percent": ratings_sync_percent,
            "detail": f"{problems_resolved}/{problems_total}",
        },
        {
            "label": "APIs externas",
            "percent": api_sync_percent,
            "detail": f"{api_checks_ok}/{api_checks_total}",
        },
    ]
    overall_sync_percent = int(
        round(sum(item["percent"] for item in sync_components) / len(sync_components))
    )

    sync_health = {
        "overall_percent": overall_sync_percent,
        "components": sync_components,
        "updated_at": now,
    }

    season_history_rows = []
    ranking_base_url = reverse("ranking")
    for season in SeasonConfig.objects.order_by("-start_date"):
        start_dt = timezone.make_aware(
            datetime.combine(season.start_date, datetime.min.time())
        )
        end_dt = timezone.make_aware(
            datetime.combine(season.end_date, datetime.max.time())
        )

        season_events = ScoreEvent.objects.filter(
            solved_at__gte=start_dt,
            solved_at__lte=end_dt,
        )
        if villain_ids:
            season_events = season_events.exclude(aluno__user_id__in=villain_ids)

        summary = season_events.aggregate(
            solves_total=Count("id"),
            active_students=Count("aluno_id", distinct=True),
            points_total=Sum("points_general_cf_equiv"),
            cf_solves=Count("id", filter=Q(platform="CF")),
            ac_solves=Count("id", filter=Q(platform="AC")),
        )
        top_student = (
            season_events.values("aluno__user__username")
            .annotate(points=Sum("points_general_cf_equiv"), solves=Count("id"))
            .order_by("-points", "-solves", "aluno__user__username")
            .first()
        )
        contests_total = Contest.objects.filter(
            start_time__gte=start_dt,
            start_time__lte=end_dt,
        ).count()

        season_history_rows.append(
            {
                "id": season.id,
                "name": _season_label(season),
                "is_active": bool(season.is_active),
                "start_date": season.start_date,
                "end_date": season.end_date,
                "duration_days": (season.end_date - season.start_date).days + 1,
                "solves_total": int(summary.get("solves_total") or 0),
                "active_students": int(summary.get("active_students") or 0),
                "points_total": int(summary.get("points_total") or 0),
                "cf_solves": int(summary.get("cf_solves") or 0),
                "ac_solves": int(summary.get("ac_solves") or 0),
                "contests_total": int(contests_total or 0),
                "top_student_name": (top_student or {}).get("aluno__user__username"),
                "top_student_points": int((top_student or {}).get("points") or 0),
                "ranking_url": f"{ranking_base_url}?window=season&season_id={season.id}",
            }
        )

    return render(request, "core/admin_panel.html", {
        "error": error,
        "success": success,
        "force_error": force_error,
        "maintenance_error": maintenance_error,
        "active_season": active_season,
        "season_start": season_start,
        "season_end": season_end,
        "user_total": user_total,
        "super_total": super_total,
        "student_total": student_total,
        "contest_total": contest_total,
        "force_feedback": force_feedback,
        "maintenance_feedback": maintenance_feedback,
        "api_checks": api_checks,
        "api_checks_ok": api_checks_ok,
        "api_checks_total": api_checks_total,
        "api_checks_checked_at": api_checks_checked_at,
        "api_checks_age_seconds": api_checks_age_seconds,
        "api_catalog": api_catalog,
        "sync_health": sync_health,
        "season_history_rows": season_history_rows,
    })


@login_required
def admin_users(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Acesso restrito.")

    q = (request.GET.get("q") or "").strip()
    page_number = request.GET.get("page") or "1"

    qs = User.objects.all().order_by("username")
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(perfil__handle_codeforces__icontains=q)
            | Q(perfil__handle_atcoder__icontains=q)
        )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(page_number)
    users_page = list(page_obj.object_list)
    profiles = {
        p.user_id: p
        for p in PerfilAluno.objects.filter(user_id__in=[u.id for u in users_page])
    }
    for u in users_page:
        u.profile_obj = profiles.get(u.id)
        origin = _profile_origin_ui(getattr(u.profile_obj, "created_via", None))
        u.origin_code = origin["code"]
        u.origin_label = origin["label"]
        u.origin_title = origin["title"]
        u.origin_pill_class = origin["pill_class"]

    source_stats = PerfilAluno.objects.aggregate(
        count_signup=Count("id", filter=Q(created_via="signup")),
        count_admin=Count("id", filter=Q(created_via="admin")),
        count_legacy=Count("id", filter=Q(created_via="legacy")),
    )

    success = None
    error = None
    success_action = (request.GET.get("success") or "").strip().lower()
    target_username = request.GET.get("user") or ""
    if success_action == "deleted":
        success = f"Usuário removido: {target_username}" if target_username else "Usuário removido."
    elif success_action == "promoted":
        success = (
            f"Usuário promovido a superuser: {target_username}"
            if target_username
            else "Usuário promovido a superuser."
        )
    if request.GET.get("error"):
        error = request.GET.get("error")

    return render(request, "core/admin_users.html", {
        "q": q,
        "page_obj": page_obj,
        "users_page": users_page,
        "total_users": paginator.count,
        "source_signup": int(source_stats.get("count_signup") or 0),
        "source_admin": int(source_stats.get("count_admin") or 0),
        "source_legacy": int(source_stats.get("count_legacy") or 0),
        "success": success,
        "error": error,
    })


@login_required
def admin_user_promote(request, user_id: int):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Acesso restrito.")
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    target = get_object_or_404(User, id=user_id)
    if target.is_superuser:
        params = urlencode({"error": f"Usuário {target.username} já é superuser."})
        return redirect(f"{reverse('admin_users')}?{params}")

    username = target.username
    try:
        target.is_superuser = True
        # Keep Django admin access consistent with superuser role.
        target.is_staff = True
        target.save(update_fields=["is_superuser", "is_staff"])
    except Exception as exc:
        params = urlencode({"error": f"Falha ao promover usuário: {exc}"})
        return redirect(f"{reverse('admin_users')}?{params}")

    params = urlencode({"success": "promoted", "user": username})
    return redirect(f"{reverse('admin_users')}?{params}")


@login_required
def admin_user_delete(request, user_id: int):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Acesso restrito.")
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    target = get_object_or_404(User, id=user_id)
    if target.id == request.user.id:
        params = urlencode({"error": "Você não pode remover sua própria conta."})
        return redirect(f"{reverse('admin_users')}?{params}")

    username = target.username
    try:
        target.delete()
    except Exception as exc:
        params = urlencode({"error": f"Falha ao remover usuário: {exc}"})
        return redirect(f"{reverse('admin_users')}?{params}")

    params = urlencode({"success": "deleted", "user": username})
    return redirect(f"{reverse('admin_users')}?{params}")


@login_required
def force_contest_sync(request, platform, contest_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    contest = get_object_or_404(Contest, platform=platform, contest_id=str(contest_id))
    detail_mode = (request.GET.get("detail") == "1") or (request.POST.get("detail") == "1")
    is_privileged_user = bool(request.user.is_staff or request.user.is_superuser)

    def _render_card(status: str, message: str, details: dict | None = None, *, auto_refresh: bool = False):
        base_details = {
            "ratings_enqueued": 0,
            "eligible_remaining": 0,
            "cooldown_blocked": 0,
            "attempts_blocked": 0,
            "not_found": 0,
            "pending_total": 0,
            "cooldown_minutes": 30,
            "max_attempts": 6,
            "rate_limited": False,
            "submissions_platform": platform,
            "submissions_students": 0,
            "submissions_queued": False,
            "submissions_mode": "none",
            "submissions_fetched": 0,
            "submissions_created_for_contest": 0,
            "submissions_updated_for_contest": 0,
            "submissions_sync_errors": 0,
            "submissions_error": None,
        }
        if details:
            base_details.update(details)
        rows, _, _ = _build_contest_rows([contest])
        row = rows[0] if rows else _contest_row_fallback(contest)
        refresh_key = f"contest_card_autorefresh:{platform}:{contest_id}"
        status_key = f"contest_force_status:{platform}:{contest_id}"
        payload = {
            "status": status,
            "message": message,
            "details": base_details,
            "auto_refresh": auto_refresh,
        }
        if detail_mode:
            detail_refresh_until = None
            if auto_refresh:
                detail_refresh_until = int((timezone.now() + timedelta(seconds=120)).timestamp() * 1000)
                cache.set(
                    f"contest_detail_autorefresh:{platform}:{contest_id}",
                    detail_refresh_until,
                    timeout=120,
                )
            response = render(request, "core/partials/contest_force_status.html", payload)
            if auto_refresh and detail_refresh_until:
                refresh_url = (
                    reverse(
                        "contest_solvers_chunk",
                        kwargs={"platform": platform.lower(), "contest_id": contest_id},
                    )
                    + "?offset=0&limit=500&scope=global"
                )
                response["HX-Trigger"] = json.dumps(
                    {
                        "contest-force-refresh": {
                            "url": refresh_url,
                            "until": detail_refresh_until,
                        }
                    }
                )
            return response
        if auto_refresh:
            cache.set(refresh_key, True, timeout=120)
            cache.set(status_key, payload, timeout=120)

        return render(request, "core/partials/contest_card.html", {
            "row": row,
            "force_status": payload,
            "auto_refresh": auto_refresh,
        })

    global_lock_seconds = max(
        20,
        int(getattr(settings, "FORCE_CONTEST_SYNC_GLOBAL_LOCK_SECONDS", 60)),
    )
    user_lock_seconds = max(
        global_lock_seconds,
        int(getattr(settings, "FORCE_CONTEST_SYNC_USER_LOCK_SECONDS", 180)),
    )
    global_lock_key = f"force_contest_sync:{platform}:{contest_id}"
    if cache.get(global_lock_key):
        global_wait_minutes = max(1, int((global_lock_seconds + 59) // 60))
        return _render_card(
            "warning",
            f"Atualizacao ja solicitada recentemente. Aguarde ~{global_wait_minutes} min para respeitar o limite.",
            {},
        )

    if not is_privileged_user:
        user_lock_key = f"force_contest_sync:user:{request.user.id}:{platform}:{contest_id}"
        if cache.get(user_lock_key):
            wait_minutes = max(1, int((user_lock_seconds + 59) // 60))
            return _render_card(
                "warning",
                f"Limite por usuario ativo. Aguarde ~{wait_minutes} min antes de tentar novamente.",
                {},
            )
        cache.set(user_lock_key, True, timeout=user_lock_seconds)

    cache.set(global_lock_key, True, timeout=global_lock_seconds)

    now = timezone.now()
    if platform == "CF" and contest.start_time and contest.start_time > now:
        return _render_card(
            "warning",
            "Contest ainda nao iniciou. O Codeforces so expõe problemas apos o inicio.",
            {"future_start": contest.start_time},
        )

    sync_error = None
    sync_contest_problems_task = None
    refresh_problem_rating_cache_task = None
    try:
        from core.tasks import (
            sync_contest_problems as sync_contest_problems_task,
            refresh_problem_rating_cache as refresh_problem_rating_cache_task,
        )
    except Exception as exc:
        sync_error = f"Falha ao carregar tarefas de sincronização: {exc}"

    problem_count = ContestProblem.objects.filter(contest=contest).count()
    if problem_count == 0:
        problems_sync_queued = False
        if sync_contest_problems_task is not None:
            try:
                sync_contest_problems_task.delay(platform, contest_id)
                problems_sync_queued = True
            except Exception as exc:
                sync_error = f"Falha ao enfileirar sync de problemas: {exc}"

        if problems_sync_queued:
            message = (
                "Problemas do contest ainda não estavam salvos. "
                "Forçar update enfileirou a sincronização de problemas; aguarde alguns segundos."
            )
            status = "success"
        elif sync_error:
            message = sync_error
            status = "error"
        elif platform == "AC":
            message = "Catálogo do AtCoder ainda não listou os problemas. Tente novamente mais tarde."
            status = "warning"
        else:
            message = "Nenhum problema associado a este contest ainda."
            status = "warning"

        return _render_card(
            status,
            message,
            {
                "problem_count": problem_count,
                "problems_sync_queued": problems_sync_queued,
            },
            auto_refresh=problems_sync_queued,
        )

    if sync_contest_problems_task is not None:
        try:
            sync_contest_problems_task.delay(platform, contest_id)
        except Exception as exc:
            sync_error = f"Falha ao enfileirar sync: {exc}"

    submissions_students = 0
    sync_sub_error = None
    submissions_mode = "none"
    submissions_result = None
    try:
        if platform == "CF":
            submissions_students = PerfilAluno.objects.exclude(handle_codeforces__isnull=True).exclude(handle_codeforces="").count()
        else:
            submissions_students = PerfilAluno.objects.exclude(handle_atcoder__isnull=True).exclude(handle_atcoder="").count()

        submissions_enabled_for_users = bool(
            getattr(settings, "FORCE_CONTEST_SYNC_ALLOW_SUBMISSIONS_FOR_USERS", False)
        )
        submissions_enabled = is_privileged_user or submissions_enabled_for_users
        if not submissions_enabled:
            submissions_mode = "disabled"
        else:
            from core.tasks import sync_contest_submissions

            if submissions_students > 0:
                if is_privileged_user:
                    inline_max_students = int(
                        getattr(
                            settings,
                            "FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_ADMIN",
                            getattr(settings, "FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS", 25),
                        )
                    )
                else:
                    inline_max_students = int(
                        getattr(settings, "FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_USER", 0)
                    )
                inline_max_students = max(0, min(50, inline_max_students))
                if submissions_students <= inline_max_students:
                    submissions_mode = "inline"
                    submissions_result = sync_contest_submissions(platform, contest_id)
                    if submissions_result.get("status") != "ok":
                        sync_sub_error = submissions_result.get("message") or "Falha ao sincronizar submissões."
                else:
                    submissions_mode = "queued"
                    sync_contest_submissions.delay(platform, contest_id)
    except Exception as exc:
        sync_sub_error = f"Falha ao enfileirar sync de submissões: {exc}"

    if is_privileged_user:
        limit = int(getattr(settings, "FORCE_CONTEST_SYNC_RATINGS_LIMIT_ADMIN", 10))
        cooldown_minutes = int(getattr(settings, "FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_ADMIN", 30))
        max_attempts = int(getattr(settings, "FORCE_CONTEST_SYNC_MAX_ATTEMPTS_ADMIN", 6))
    else:
        limit = int(getattr(settings, "FORCE_CONTEST_SYNC_RATINGS_LIMIT_USER", 3))
        cooldown_minutes = int(getattr(settings, "FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_USER", 45))
        max_attempts = int(getattr(settings, "FORCE_CONTEST_SYNC_MAX_ATTEMPTS_USER", 4))
    limit = max(1, min(20, limit))
    cooldown_minutes = max(5, min(120, cooldown_minutes))
    max_attempts = max(1, min(12, max_attempts))
    cooldown = now - timedelta(minutes=cooldown_minutes)
    reset_hours = int(getattr(settings, "RATING_ATTEMPT_RESET_HOURS", 12))
    reset_before = now - timedelta(hours=max(1, reset_hours))

    pending_statuses = ["MISSING", "TEMP_FAIL", "QUEUED"]
    if platform == "CF":
        # For split rounds in CF, allow manual recheck of NOT_FOUND as well.
        pending_statuses.append("NOT_FOUND")

    pending_qs = ContestProblem.objects.filter(
        contest=contest,
        rating_status__in=pending_statuses,
    )
    reset_attempts = pending_qs.filter(
        rating_attempts__gte=max_attempts
    ).filter(
        Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=reset_before)
    ).update(rating_attempts=0)
    pending_qs = ContestProblem.objects.filter(
        contest=contest,
        rating_status__in=pending_statuses,
    )
    eligible_qs = pending_qs.filter(
        rating_attempts__lt=max_attempts
    ).filter(
        Q(rating_last_requested_at__isnull=True) | Q(rating_last_requested_at__lte=cooldown)
    )

    total_pending = pending_qs.count()
    eligible_count = eligible_qs.count()
    attempts_blocked = pending_qs.filter(rating_attempts__gte=max_attempts).count()
    cooldown_blocked = pending_qs.filter(
        rating_attempts__lt=max_attempts,
        rating_last_requested_at__gt=cooldown,
    ).count()
    not_found = ContestProblem.objects.filter(contest=contest, rating_status="NOT_FOUND").count()
    ratings_ok = ContestProblem.objects.filter(contest=contest, rating_status="OK").count()
    # Safety: compute ready ratings from the cache as well (status OK + non-null rating),
    # since older data could have inconsistent statuses.
    problem_urls = list(
        ContestProblem.objects.filter(contest=contest)
        .exclude(problem_url__isnull=True)
        .exclude(problem_url="")
        .values_list("problem_url", flat=True)
    )
    cache_ready = ProblemRatingCache.objects.filter(
        problem_url__in=problem_urls,
        effective_rating__isnull=False,
    ).count()

    enqueued = 0
    if eligible_count > 0:
        for problem in eligible_qs.order_by("rating_attempts")[:limit]:
            cache_entry, _ = ProblemRatingCache.objects.get_or_create(
                problem_url=problem.problem_url,
                defaults={
                    "platform": problem.platform,
                    "status": "TEMP_FAIL",
                },
            )
            if cache_entry.platform != problem.platform:
                cache_entry.platform = problem.platform
                cache_entry.save(update_fields=["platform"])

            problem.rating_status = "QUEUED"
            problem.rating_last_requested_at = now
            problem.save(update_fields=[
                "rating_status",
                "rating_last_requested_at",
            ])

            if refresh_problem_rating_cache_task is not None:
                refresh_problem_rating_cache_task.delay(cache_entry.id, problem.name)
                enqueued += 1
            elif not sync_error:
                sync_error = "Falha ao carregar tarefa de atualização de ratings."

    remaining = max(0, eligible_count - enqueued)
    rate_limited = enqueued >= limit and remaining > 0

    status = "success" if not sync_error else "error"
    message = "Atualizacao do contest enfileirada com sucesso." if not sync_error else sync_error
    if enqueued == 0 and total_pending == 0:
        if cache_ready == problem_count:
            message = f"Ratings ja estao prontos para este contest ({cache_ready}/{problem_count})."
            status = "success" if not sync_error else "warning"
        elif cache_ready == 0 and not_found == problem_count:
            message = f"Nenhum rating disponivel no CLIST para este contest ({not_found}/{problem_count})."
            status = "warning" if not sync_error else "warning"
        else:
            message = f"Sem pendencias: {cache_ready}/{problem_count} ratings disponiveis, {not_found} sem rating no CLIST."
            status = "warning" if not sync_error else "warning"

    details = {
        "sync_queued": sync_error is None,
        "ratings_enqueued": enqueued,
        "eligible_remaining": remaining,
        "cooldown_blocked": cooldown_blocked,
        "attempts_blocked": attempts_blocked,
        "reset_attempts": reset_attempts,
        "not_found": not_found,
        "pending_total": total_pending,
        "problem_count": problem_count,
        "ratings_ok": ratings_ok,
        "cooldown_minutes": cooldown_minutes,
        "max_attempts": max_attempts,
        "rate_limited": rate_limited,
        "submissions_platform": platform,
        "submissions_students": submissions_students,
        "submissions_queued": sync_sub_error is None and submissions_mode == "queued",
        "submissions_mode": submissions_mode,
        "submissions_fetched": (submissions_result or {}).get("fetched", 0),
        "submissions_created_for_contest": (submissions_result or {}).get("created_for_contest", 0),
        "submissions_updated_for_contest": (submissions_result or {}).get("updated_for_contest", 0),
        "submissions_sync_errors": (submissions_result or {}).get("errors", 0),
        "submissions_error": sync_sub_error,
    }

    submissions_changed = (
        int((submissions_result or {}).get("created_for_contest", 0))
        + int((submissions_result or {}).get("updated_for_contest", 0))
    ) > 0
    auto_refresh = (sync_error is None) and (
        (enqueued > 0)
        or (sync_sub_error is None and submissions_mode == "queued")
        or submissions_changed
    )
    if sync_sub_error and status != "error":
        status = "warning"
        message = f"{message} (Submissões: falha ao enfileirar.)"
    elif submissions_mode == "inline" and submissions_result:
        message = (
            f"{message} "
            f"Submissões do contest sincronizadas agora: "
            f"{details['submissions_created_for_contest']} novas, "
            f"{details['submissions_updated_for_contest']} atualizadas."
        )
    return _render_card(status, message, details, auto_refresh=auto_refresh)


@login_required
def contest_card_snippet(request, platform, contest_id):
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    contest = get_object_or_404(Contest, platform=platform, contest_id=str(contest_id))
    rows, _, _ = _build_contest_rows([contest])
    row = rows[0] if rows else _contest_row_fallback(contest)

    refresh_key = f"contest_card_autorefresh:{platform}:{contest_id}"
    status_key = f"contest_force_status:{platform}:{contest_id}"
    auto_refresh = bool(cache.get(refresh_key))
    force_status = cache.get(status_key) if auto_refresh else None

    return render(request, "core/partials/contest_card.html", {
        "row": row,
        "auto_refresh": auto_refresh,
        "force_status": force_status,
    })


@login_required
def contest_problems_snippet(request, platform, contest_id):
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    contest = get_object_or_404(
        Contest,
        platform=platform,
        contest_id=str(contest_id),
    )

    items = _build_contest_problem_cards(contest, platform)

    return render(
        request,
        "core/partials/contest_problems_snippet.html",
        {
            "contest": contest,
            "problems": items,
        },
    )


@login_required
def contest_detail(request, platform, contest_id):
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    contest = get_object_or_404(
        Contest,
        platform=platform,
        contest_id=str(contest_id),
    )

    problem_cards = _build_contest_problem_cards(contest, platform)

    chunk_size = 4
    chunk_offsets = list(range(0, len(problem_cards), chunk_size))
    refresh_key = f"contest_detail_autorefresh:{platform}:{contest_id}"
    detail_refresh_until = cache.get(refresh_key)
    try:
        detail_refresh_until = int(detail_refresh_until)
    except Exception:
        detail_refresh_until = None
    now_ms = int(timezone.now().timestamp() * 1000)
    if detail_refresh_until and detail_refresh_until <= now_ms:
        detail_refresh_until = None
    refresh_chunk_url = (
        reverse(
            "contest_solvers_chunk",
            kwargs={"platform": platform.lower(), "contest_id": contest_id},
        )
        + "?offset=0&limit=500&scope=global"
    )

    contest_url = (
        f"https://codeforces.com/contest/{contest_id}"
        if platform == "CF"
        else f"https://atcoder.jp/contests/{contest_id}"
    )
    return render(
        request,
        "core/contest_detail.html",
        {
            "contest": contest,
            "platform": platform,
            "contest_id": contest_id,
            "problems": problem_cards,
            "contest_url": contest_url,
            "chunk_offsets": chunk_offsets,
            "chunk_size": chunk_size,
            "detail_refresh_until": detail_refresh_until,
            "refresh_chunk_url": refresh_chunk_url,
        },
    )


def _submission_url(platform: str, contest_id: str, external_id: str | None) -> str | None:
    if not external_id:
        return None
    if platform == "CF":
        return f"https://codeforces.com/contest/{contest_id}/submission/{external_id}"
    return f"https://atcoder.jp/contests/{contest_id}/submissions/{external_id}"


def _hex_to_rgba(hex_color: str | None, alpha: float) -> str | None:
    if not hex_color or not isinstance(hex_color, str):
        return None
    value = hex_color.strip()
    if len(value) != 7 or not value.startswith("#"):
        return None
    try:
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
    except ValueError:
        return None
    alpha = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _solver_group_map(user_ids: set[int]) -> dict[int, dict]:
    if not user_ids:
        return {}
    rows = (
        CompetitorGroup.objects.filter(users__id__in=user_ids)
        .values("users__id", "name", "color", "is_villain", "priority")
        .order_by("-is_villain", "priority", "id")
    )
    mapping: dict[int, dict] = {}
    for row in rows:
        user_id = row.get("users__id")
        if not user_id or user_id in mapping:
            continue
        color = row.get("color")
        bg = _hex_to_rgba(color, 0.18) if color else None
        border = _hex_to_rgba(color, 0.48) if color else None
        style = ""
        if bg and border:
            style = f"background-color:{bg};border-color:{border};color:#f8fafc;"
        mapping[user_id] = {
            "name": row.get("name") or "",
            "color": color or "",
            "is_villain": bool(row.get("is_villain")),
            "style": style,
        }
    return mapping


def _build_contest_problem_cards(contest: Contest, platform: str) -> list[dict]:
    problems = list(
        ContestProblem.objects.filter(contest=contest)
        .only("id", "index_label", "name", "problem_url", "tags", "cf_rating", "rating_status", "order")
        .order_by("order")
    )

    tags_by_index: dict[str, set[str]] = {}
    for problem in problems:
        if problem.index_label and problem.tags:
            tags_by_index.setdefault(problem.index_label, set()).update(
                {t.strip() for t in (problem.tags or "").split(",") if t.strip()}
            )

    tags_rows = Submissao.objects.filter(
        plataforma=platform,
        contest_id=contest.contest_id,
    ).values("problem_index", "tags")
    for row in tags_rows.iterator():
        index_label = row.get("problem_index")
        if not index_label:
            continue
        tags_raw = row.get("tags") or ""
        tags_by_index.setdefault(index_label, set()).update(
            {t.strip() for t in tags_raw.split(",") if t.strip()}
        )

    problem_urls = [problem.problem_url for problem in problems if problem.problem_url]
    cache_by_url = {
        rating_cache.problem_url: rating_cache
        for rating_cache in ProblemRatingCache.objects.filter(problem_url__in=problem_urls).only(
            "problem_url",
            "effective_rating",
            "status",
        )
    }

    cards = []
    for problem in problems:
        rating_cache = cache_by_url.get(problem.problem_url)
        rating, status = _resolve_effective_rating(problem, rating_cache)
        rating_label, rating_color, rating_fill, rating_text = _rating_badge(
            rating,
            status,
            platform,
        )
        cards.append(
            {
                "id": problem.id,
                "index_label": problem.index_label,
                "name": problem.name or problem.index_label,
                "problem_url": problem.problem_url,
                "rating_label": rating_label,
                "rating_color": rating_color,
                "rating_fill": rating_fill,
                "rating_text": rating_text,
                "tags": sorted(tags_by_index.get(problem.index_label, set())),
            }
        )
    return cards


def _build_solvers_map(platform: str, contest_id: str, index_labels: list[str]) -> dict[str, list[dict]]:
    accepted = {"CF": {"OK"}, "AC": {"AC"}}.get(platform, set())
    if not accepted or not index_labels:
        return {}

    subs = (
        Submissao.objects.filter(
            plataforma=platform,
            contest_id=contest_id,
            problem_index__in=index_labels,
            verdict__in=accepted,
        )
        .values(
            "problem_index",
            "aluno_id",
            "aluno__user_id",
            "aluno__user__username",
            "aluno__handle_codeforces",
            "aluno__handle_atcoder",
            "external_id",
            "submission_time",
        )
        .order_by("submission_time")
    )

    solvers_by_problem: dict[str, list[dict]] = {label: [] for label in index_labels}
    seen_by_problem: dict[str, set[int]] = {label: set() for label in index_labels}
    solver_user_ids: set[int] = set()

    for sub in subs.iterator():
        label = sub.get("problem_index")
        if label not in seen_by_problem:
            continue
        aluno_id = sub.get("aluno_id")
        if aluno_id in seen_by_problem[label]:
            continue
        seen_by_problem[label].add(aluno_id)
        user_id = sub.get("aluno__user_id")
        if user_id is not None:
            solver_user_ids.add(int(user_id))
        handle = sub.get("aluno__handle_codeforces") if platform == "CF" else sub.get("aluno__handle_atcoder")
        solvers_by_problem[label].append(
            {
                "username": sub.get("aluno__user__username") or "unknown",
                "handle": handle,
                "submission_url": _submission_url(platform, contest_id, sub.get("external_id")),
                "submitted_at": sub.get("submission_time"),
                "user_id": user_id,
            }
        )

    group_map = _solver_group_map(solver_user_ids)
    for solvers in solvers_by_problem.values():
        for solver in solvers:
            group_meta = group_map.get(solver.get("user_id"), {})
            solver["group_name"] = group_meta.get("name", "")
            solver["group_color"] = group_meta.get("color", "")
            solver["is_villain_group"] = bool(group_meta.get("is_villain"))
            solver["pill_style"] = group_meta.get("style", "")
            solver.pop("user_id", None)

    return solvers_by_problem


@login_required
def contest_solvers_snippet(request, platform, contest_id):
    from core.services.problem_urls import normalize_problem_url

    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    problem_url = normalize_problem_url(request.GET.get("problem_url") or "")
    if not problem_url:
        raise Http404("Problema invalido.")

    contest = get_object_or_404(
        Contest,
        platform=platform,
        contest_id=str(contest_id),
    )
    problem = ContestProblem.objects.filter(
        contest=contest,
        problem_url=problem_url,
    ).first()
    if not problem:
        raise Http404("Problema nao encontrado.")

    solvers_map = _build_solvers_map(platform, contest_id, [problem.index_label])
    solvers = solvers_map.get(problem.index_label, [])
    max_display = 10
    solvers_display = solvers[:max_display]
    solvers_remaining = solvers[max_display:]
    extra_count = max(0, len(solvers_remaining))

    return render(
        request,
        "core/partials/contest_solvers_snippet.html",
        {
            "solvers": solvers,
            "solvers_display": solvers_display,
            "solvers_remaining": solvers_remaining,
            "extra_count": extra_count,
        },
    )


@login_required
def contest_solvers_chunk(request, platform, contest_id):
    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    contest = get_object_or_404(
        Contest,
        platform=platform,
        contest_id=str(contest_id),
    )

    try:
        offset = int(request.GET.get("offset") or 0)
    except ValueError:
        offset = 0
    try:
        limit = int(request.GET.get("limit") or 4)
    except ValueError:
        limit = 4

    problems = list(
        ContestProblem.objects.filter(contest=contest).order_by("order")[offset : offset + limit]
    )
    index_labels = [problem.index_label for problem in problems]
    solvers_map = _build_solvers_map(platform, contest_id, index_labels)

    items = []
    for problem in problems:
        solvers = solvers_map.get(problem.index_label, [])
        max_display = 10
        solvers_display = solvers[:max_display]
        solvers_remaining = solvers[max_display:]
        extra_count = max(0, len(solvers_remaining))
        items.append(
            {
                "problem_id": problem.id,
                "solvers": solvers,
                "solvers_display": solvers_display,
                "solvers_remaining": solvers_remaining,
                "extra_count": extra_count,
            }
        )

    return render(
        request,
        "core/partials/contest_solvers_chunk.html",
        {
            "items": items,
        },
    )


def _get_or_create_profile(request) -> PerfilAluno:
    profile = getattr(request, "profile", None)
    if profile:
        return profile
    if request.user.is_authenticated:
        profile, created = PerfilAluno.objects.get_or_create(
            user=request.user,
            defaults={"created_via": _default_profile_origin_for_user(request.user)},
        )
        if created:
            request.profile = profile
            return profile
        request.profile = profile
        return profile
    raise Http404("Perfil não disponível.")


@login_required
def train(request):
    student = _get_or_create_profile(request)
    is_privileged_user = bool(request.user.is_staff or request.user.is_superuser)
    custom_min_minutes = 1 if is_privileged_user else 10

    mode = (request.GET.get("mode") or "evolution").lower()
    if mode not in {"consistency", "general", "evolution", "challenge"}:
        mode = "evolution"

    try:
        target_minutes = int(request.GET.get("minutes") or 90)
    except ValueError:
        target_minutes = 90
    if target_minutes not in {60, 90, 120}:
        target_minutes = 90

    plan = get_session_plan(mode, target_minutes)

    cf_zone, cf_suggestions, cf_meta = build_cf_suggestions(
        student,
        mode=mode,
        count=int(plan.get("cf_count") or 10),
        duration_minutes=target_minutes,
    )
    ac_ranges, ac_suggestions, ac_meta = build_ac_suggestions(
        student,
        mode=mode,
        count_easy=int(plan.get("ac_easy") or 2),
        count_medium=int(plan.get("ac_medium") or 2),
        count_stretch=int(plan.get("ac_stretch") or 0),
        duration_minutes=target_minutes,
    )
    session_custom_defaults = {
        "minutes": target_minutes,
        "rating_min": cf_zone.low,
        "rating_max": cf_zone.high,
        "cf_count": int(plan.get("cf_count") or 2),
        "ac_count": int(plan.get("ac_easy") or 0) + int(plan.get("ac_medium") or 0) + int(plan.get("ac_stretch") or 0),
    }
    for s in cf_suggestions:
        label, color, fill, text = _rating_badge(s.get("rating"), "OK", "CF")
        s["rating_label"] = label
        s["rating_color"] = color
        s["rating_fill"] = fill
        s["rating_text"] = text
    for s in ac_suggestions:
        label, color, fill, text = _rating_badge(s.get("rating"), "OK", "AC")
        s["rating_label"] = label
        s["rating_color"] = color
        s["rating_fill"] = fill
        s["rating_text"] = text

    train_inventory = build_training_inventory(
        student,
        mode=mode,
        duration_minutes=target_minutes,
    )

    # CF rating trend (from rating history if available).
    now = timezone.now()
    cf_trend_7d = None
    cf_trend_30d = None
    changes_qs = student.cf_rating_changes.order_by("rating_update_time")
    latest = changes_qs.last()
    if latest:
        prev_7 = changes_qs.filter(rating_update_time__lte=now - timedelta(days=7)).last()
        prev_30 = changes_qs.filter(rating_update_time__lte=now - timedelta(days=30)).last()
        if prev_7:
            cf_trend_7d = int(latest.rating_new) - int(prev_7.rating_new)
        if prev_30:
            cf_trend_30d = int(latest.rating_new) - int(prev_30.rating_new)

    # Stuck pattern (from training feedback).
    stuck_rows = list(
        TrainingSessionItem.objects.filter(
            session__aluno=student,
            result="STUCK",
            created_at__gte=now - timedelta(days=30),
        )
        .values("stuck_reason")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    stuck_reason = stuck_rows[0]["stuck_reason"] if stuck_rows else None
    stuck_count = stuck_rows[0]["n"] if stuck_rows else 0

    active_session = (
        TrainingSession.objects.filter(aluno=student, is_active=True)
        .order_by("-started_at")
        .first()
    )

    queue_items = list(
        TrainingQueueItem.objects.filter(aluno=student, status="QUEUED").order_by("priority", "created_at")[:12]
    )
    for qi in queue_items:
        qi.tags_list = [t.strip() for t in (qi.tags or "").split(",") if t.strip()]
        label, color, fill, text = _rating_badge(qi.rating, "OK", qi.platform)
        qi.rating_label = label
        qi.rating_color = color
        qi.rating_fill = fill
        qi.rating_text = text

    history_qs = TrainingSession.objects.filter(aluno=student, is_active=False).order_by("-started_at")
    history_page_number = request.GET.get("history_page") or 1
    history_paginator = Paginator(history_qs, 4)
    history_page = history_paginator.get_page(history_page_number)
    history = list(history_page.object_list)
    history_ids = [s.id for s in history]
    counts_by_session = {
        row["session_id"]: row
        for row in TrainingSessionItem.objects.filter(session_id__in=history_ids)
        .values("session_id")
        .annotate(
            total=Count("id"),
            done=Count("id", filter=~Q(result="TODO")),
            solved=Count("id", filter=Q(result="SOLVED")),
            editorial=Count("id", filter=Q(result="EDITORIAL")),
            stuck=Count("id", filter=Q(result="STUCK")),
        )
    }
    history_rows = []
    for s in history:
        c = counts_by_session.get(s.id, {"total": 0, "done": 0, "solved": 0, "editorial": 0, "stuck": 0})
        history_rows.append({"session": s, "counts": c})

    upsolving_items = list(
        TrainingQueueItem.objects.filter(
            aluno=student,
            status="QUEUED",
            source="manual",
        )
        .order_by("priority", "created_at")[:30]
    )
    upsolving_total = TrainingQueueItem.objects.filter(
        aluno=student,
        status="QUEUED",
        source="manual",
    ).count()
    manual_metadata = _bulk_manual_problem_metadata(upsolving_items)
    upsolving_updates: list[TrainingQueueItem] = []
    upsolving_updated_at = timezone.now()
    for up_item in upsolving_items:
        key = (str(up_item.platform or "").upper(), str(up_item.problem_url or ""))
        if up_item.platform in {"CF", "AC"} and (up_item.rating is None or not up_item.title or not up_item.tags):
            rating, inferred_title, inferred_tags = manual_metadata.get(key, (None, "", ""))
            changed = False
            if rating is not None and up_item.rating != rating:
                up_item.rating = rating
                changed = True
            if inferred_title and not up_item.title:
                up_item.title = inferred_title
                changed = True
            if inferred_tags and not up_item.tags:
                up_item.tags = inferred_tags
                changed = True
            if changed:
                up_item.updated_at = upsolving_updated_at
                upsolving_updates.append(up_item)

        label, color, fill, text = _rating_badge(up_item.rating, "OK", up_item.platform)
        up_item.rating_label = label
        up_item.rating_color = color
        up_item.rating_fill = fill
        up_item.rating_text = text
        up_item.tags_list = [t.strip() for t in (up_item.tags or "").split(",") if t.strip()]

    if upsolving_updates:
        TrainingQueueItem.objects.bulk_update(
            upsolving_updates,
            ["rating", "title", "tags", "updated_at"],
        )

    upsolving_status = (request.GET.get("upsolving_status") or "").strip().lower()
    try:
        upsolving_added = int(request.GET.get("upsolving_added") or 0)
    except ValueError:
        upsolving_added = 0
    try:
        upsolving_skipped = int(request.GET.get("upsolving_skipped") or 0)
    except ValueError:
        upsolving_skipped = 0
    upsolving_feedback = None
    if upsolving_status == "ok":
        if upsolving_added == 1:
            upsolving_feedback = "Link salvo na sua lista de upsolving."
        elif upsolving_added > 1:
            upsolving_feedback = f"{upsolving_added} links salvos na sua lista de upsolving."
    elif upsolving_status == "partial":
        upsolving_feedback = (
            f"{upsolving_added} links salvos. "
            f"{upsolving_skipped} ignorados por formato/plataforma nao reconhecida."
        )
    elif upsolving_status == "empty":
        upsolving_feedback = "Cole pelo menos um link para salvar na lista."
    elif upsolving_status == "invalid":
        upsolving_feedback = "Nao consegui identificar links validos. Para outros sites, selecione a plataforma 'Outros'."

    history_params = request.GET.copy()
    history_params.pop("history_page", None)
    history_params.pop("upsolving_status", None)
    history_params.pop("upsolving_added", None)
    history_params.pop("upsolving_skipped", None)
    history_query_prefix = history_params.urlencode()
    if history_query_prefix:
        history_query_prefix += "&"

    cf_baseline = int(cf_meta.get("baseline") or get_baseline_cf(student))
    ac_baseline = int(ac_meta.get("baseline") or get_baseline_ac(student))

    context = {
        "student": student,
        "mode": mode,
        "target_minutes": target_minutes,
        "session_plan": plan,
        "cf_baseline": cf_baseline,
        "cf_trend_7d": cf_trend_7d,
        "cf_trend_30d": cf_trend_30d,
        "cf_zone": cf_zone,
        "cf_suggestions": cf_suggestions,
        "cf_meta": cf_meta,
        "ac_baseline": ac_baseline,
        "ac_ranges": ac_ranges,
        "ac_suggestions": ac_suggestions,
        "ac_meta": ac_meta,
        "stuck_reason": stuck_reason,
        "stuck_count": stuck_count,
        "queue_items": queue_items,
        "active_session": active_session,
        "history_rows": history_rows,
        "history_page": history_page,
        "history_query_prefix": history_query_prefix,
        "upsolving_items": upsolving_items,
        "upsolving_total": upsolving_total,
        "upsolving_feedback": upsolving_feedback,
        "train_inventory": train_inventory,
        "session_custom_defaults": session_custom_defaults,
        "custom_min_minutes": custom_min_minutes,
    }
    context.update(_build_welcome_tour_context(request, "train"))
    return render(request, "core/train.html", context)


def _infer_platform_from_problem_url(problem_url: str) -> str | None:
    try:
        host = (urlsplit(problem_url).netloc or "").lower()
    except Exception:
        return None
    if "codeforces.com" in host:
        return "CF"
    if "atcoder.jp" in host:
        return "AC"
    return None


def _extract_candidate_problem_urls(raw_urls: str) -> list[str]:
    if not raw_urls:
        return []
    urls: list[str] = []
    for line in str(raw_urls).splitlines():
        normalized_line = line.replace(",", " ").replace(";", " ")
        for token in normalized_line.split():
            url = token.strip()
            if not url:
                continue
            if url.startswith(("http://", "https://")):
                urls.append(url)
    return urls


def _resolve_manual_problem_metadata(
    *,
    platform: str,
    problem_url: str,
    schedule_missing_rating: bool = False,
) -> tuple[int | None, str, str]:
    """
    Best effort metadata lookup for manual links:
    - rating from cache/problem if available
    - optional scheduling for missing CF/AC ratings
    - title/tags from known contest problem table
    """
    platform = (platform or "").upper()
    if not problem_url or platform not in {"CF", "AC"}:
        return None, "", ""

    problem = (
        ContestProblem.objects.filter(platform=platform, problem_url=problem_url)
        .only("name", "index_label", "tags", "cf_rating", "rating_status")
        .order_by("-id")
        .first()
    )
    cache = (
        ProblemRatingCache.objects.filter(problem_url=problem_url)
        .only("effective_rating", "status")
        .first()
    )

    rating, _ = _resolve_effective_rating(problem, cache)
    if rating is None and schedule_missing_rating:
        try:
            from core.services.problem_ratings import get_or_schedule_problem_rating

            cache = get_or_schedule_problem_rating(platform, problem_url, schedule=True)
            rating, _ = _resolve_effective_rating(problem, cache)
        except Exception:
            # Keep manual add resilient even if scheduler layer is unavailable.
            pass

    title = ""
    tags = ""
    if problem:
        title = (problem.name or problem.index_label or "").strip()
        tags = (problem.tags or "").strip()
    return rating, title, tags


def _bulk_manual_problem_metadata(
    items: list[TrainingQueueItem],
) -> dict[tuple[str, str], tuple[int | None, str, str]]:
    """
    Bulk metadata hydration for manual upsolving links.
    Returns {(platform, url): (rating, title, tags)}.
    """
    keys = {
        (str(item.platform or "").upper(), str(item.problem_url or ""))
        for item in items
        if str(item.platform or "").upper() in {"CF", "AC"} and str(item.problem_url or "")
    }
    if not keys:
        return {}

    urls = {url for _, url in keys}
    problem_rows = (
        ContestProblem.objects.filter(platform__in=["CF", "AC"], problem_url__in=urls)
        .values("id", "platform", "problem_url", "name", "index_label", "tags", "cf_rating")
        .order_by("id")
    )
    latest_problem_by_key: dict[tuple[str, str], dict] = {}
    for row in problem_rows:
        platform = str(row.get("platform") or "").upper()
        problem_url = str(row.get("problem_url") or "")
        if not platform or not problem_url:
            continue
        latest_problem_by_key[(platform, problem_url)] = row

    cache_map = {
        str(row["problem_url"]): row
        for row in ProblemRatingCache.objects.filter(problem_url__in=urls).values(
            "problem_url",
            "effective_rating",
        )
    }

    result: dict[tuple[str, str], tuple[int | None, str, str]] = {}
    for key in keys:
        platform, problem_url = key
        problem = latest_problem_by_key.get(key)
        cache = cache_map.get(problem_url)

        rating = None
        if cache and cache.get("effective_rating") is not None:
            rating = int(cache["effective_rating"])
        elif problem and problem.get("cf_rating") is not None:
            rating = int(problem["cf_rating"])

        title = ""
        tags = ""
        if problem:
            title = str(problem.get("name") or problem.get("index_label") or "").strip()
            tags = str(problem.get("tags") or "").strip()

        result[key] = (rating, title, tags)
    return result


@login_required
def train_upsolving_add(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    student = _get_or_create_profile(request)
    platform_hint = (request.POST.get("platform_hint") or "AUTO").strip().upper()
    if platform_hint not in {"AUTO", "CF", "AC", "OT"}:
        platform_hint = "AUTO"
    raw_urls = (request.POST.get("problem_urls") or "").strip()
    title_input = (request.POST.get("title") or "").strip()

    candidate_urls = _extract_candidate_problem_urls(raw_urls)
    if not candidate_urls:
        return redirect(f"{reverse('train')}?{urlencode({'upsolving_status': 'empty'})}")

    from core.services.problem_urls import normalize_problem_url

    seen: set[str] = set()
    unique_urls: list[str] = []
    for raw_url in candidate_urls:
        normalized_url = normalize_problem_url(raw_url)
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        unique_urls.append(normalized_url)

    max_priority = TrainingQueueItem.objects.filter(aluno=student).aggregate(Max("priority")).get("priority__max") or 0
    next_priority = int(max_priority)
    added = 0
    skipped = 0
    single_title = title_input if len(unique_urls) == 1 else ""

    for problem_url in unique_urls:
        platform = _infer_platform_from_problem_url(problem_url)
        if platform is None and platform_hint in {"CF", "AC", "OT"}:
            platform = platform_hint
        if platform not in {"CF", "AC", "OT"}:
            skipped += 1
            continue

        rating, inferred_title, inferred_tags = _resolve_manual_problem_metadata(
            platform=platform,
            problem_url=problem_url,
            schedule_missing_rating=True,
        )
        final_title = single_title or inferred_title

        existing = TrainingQueueItem.objects.filter(aluno=student, platform=platform, problem_url=problem_url).first()
        if existing:
            update_fields = ["status", "source", "updated_at"]
            if final_title and existing.title != final_title:
                existing.title = final_title
                update_fields.append("title")
            if rating is not None and existing.rating != rating:
                existing.rating = rating
                update_fields.append("rating")
            if inferred_tags and existing.tags != inferred_tags:
                existing.tags = inferred_tags
                update_fields.append("tags")
            existing.status = "QUEUED"
            existing.source = "manual"
            existing.save(update_fields=update_fields)
            added += 1
            continue

        next_priority += 1
        TrainingQueueItem.objects.create(
            aluno=student,
            platform=platform,
            problem_url=problem_url,
            title=final_title,
            rating=rating,
            tags=inferred_tags,
            status="QUEUED",
            source="manual",
            priority=next_priority,
        )
        added += 1

    if added == 0:
        params = {"upsolving_status": "invalid"}
    elif skipped > 0:
        params = {
            "upsolving_status": "partial",
            "upsolving_added": added,
            "upsolving_skipped": skipped,
        }
    else:
        params = {"upsolving_status": "ok", "upsolving_added": added}

    return redirect(f"{reverse('train')}?{urlencode(params)}")

@login_required
def train_session_start(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    student = _get_or_create_profile(request)
    mode = (request.POST.get("mode") or "evolution").lower()
    if mode not in {"consistency", "general", "evolution", "challenge", "custom"}:
        mode = "evolution"

    def _parse_loose_int(value, default: int) -> int:
        raw = (str(value or "")).strip()
        if not raw:
            return int(default)
        # Accept comma decimal separator and non-integer numeric strings.
        normalized = raw.replace(",", ".")
        try:
            return int(round(float(normalized)))
        except (TypeError, ValueError):
            return int(default)

    target_minutes = _parse_loose_int(request.POST.get("minutes"), 90)
    custom_min_minutes = 1 if (request.user.is_staff or request.user.is_superuser) else 10
    if mode == "custom":
        target_minutes = max(custom_min_minutes, min(240, target_minutes))
    else:
        if target_minutes not in {60, 90, 120}:
            target_minutes = 90

    now = timezone.now()
    TrainingSession.objects.filter(aluno=student, is_active=True).update(is_active=False, ended_at=now)

    if mode == "custom":
        custom_rating_low = _parse_loose_int(request.POST.get("rating_min"), 800)
        custom_rating_high = _parse_loose_int(request.POST.get("rating_max"), 1600)
        custom_rating_low = max(0, min(4000, custom_rating_low))
        custom_rating_high = max(0, min(4000, custom_rating_high))
        if custom_rating_high < custom_rating_low:
            custom_rating_low, custom_rating_high = custom_rating_high, custom_rating_low

        custom_cf_count = _parse_loose_int(request.POST.get("cf_count"), 2)
        custom_ac_count = _parse_loose_int(request.POST.get("ac_count"), 2)
        custom_cf_count = max(0, min(20, custom_cf_count))
        custom_ac_count = max(0, min(20, custom_ac_count))
        if custom_cf_count + custom_ac_count == 0:
            custom_cf_count = 1

        plan = {
            "cf_count": custom_cf_count,
            "ac_easy": 0,
            "ac_medium": custom_ac_count,
            "ac_stretch": 0,
            "objective": (
                f"Personalizada • CF {custom_cf_count} + AC {custom_ac_count} "
                f"• Rating {custom_rating_low}–{custom_rating_high}"
            ),
        }
    else:
        custom_rating_low = None
        custom_rating_high = None
        custom_cf_count = 0
        custom_ac_count = 0
        plan = get_session_plan(mode, target_minutes)

    session_mode = mode if mode in {"consistency", "general", "evolution", "challenge", "custom"} else "general"
    session = TrainingSession.objects.create(
        aluno=student,
        mode=session_mode,
        target_minutes=target_minutes,
        objective=str(plan.get("objective") or "CF + AC"),
        started_at=now,
        is_active=True,
    )
    baseline_cf = get_baseline_cf(student)
    baseline_ac = get_baseline_ac(student)

    if mode == "custom":
        _, cf_suggestions, _ = build_cf_suggestions(
            student,
            mode="general",
            count=custom_cf_count,
            duration_minutes=target_minutes,
            rating_low=custom_rating_low,
            rating_high=custom_rating_high,
        )
        _, ac_suggestions, _ = build_ac_suggestions(
            student,
            mode="general",
            count_easy=0,
            count_medium=custom_ac_count,
            count_stretch=0,
            duration_minutes=target_minutes,
            rating_low=custom_rating_low,
            rating_high=custom_rating_high,
            custom_count=custom_ac_count,
        )
        item_origin = "custom_suggestion"
    else:
        _, cf_suggestions, _ = build_cf_suggestions(
            student,
            mode=mode,
            count=int(plan.get("cf_count") or 0),
            duration_minutes=target_minutes,
        )
        _, ac_suggestions, _ = build_ac_suggestions(
            student,
            mode=mode,
            count_easy=int(plan.get("ac_easy") or 0),
            count_medium=int(plan.get("ac_medium") or 0),
            count_stretch=int(plan.get("ac_stretch") or 0),
            duration_minutes=target_minutes,
        )
        item_origin = "normal_suggestion"

    items = []
    order = 1
    for s in cf_suggestions:
        items.append(
            TrainingSessionItem(
                session=session,
                platform="CF",
                order=order,
                problem_url=s["problem_url"],
                contest_id=s.get("contest_id"),
                index_label=s.get("index_label"),
                title=s.get("title") or "",
                rating=s.get("rating"),
                tags=",".join(s.get("tags") or []),
                expected_minutes=estimate_expected_minutes("CF", s.get("rating"), baseline_cf),
                is_optional=False,
                origin=item_origin,
            )
        )
        order += 1
    for s in ac_suggestions:
        items.append(
            TrainingSessionItem(
                session=session,
                platform="AC",
                order=order,
                problem_url=s["problem_url"],
                contest_id=s.get("contest_id"),
                index_label=s.get("index_label"),
                title=s.get("title") or "",
                rating=s.get("rating"),
                tags="",
                expected_minutes=estimate_expected_minutes("AC", s.get("rating"), baseline_ac),
                is_optional=(s.get("tier") == "stretch"),
                origin=item_origin,
            )
        )
        order += 1

    if items:
        TrainingSessionItem.objects.bulk_create(items)

    return redirect("train_session", session_id=session.id)


@login_required
def train_session(request, session_id: int):
    student = _get_or_create_profile(request)
    session = get_object_or_404(TrainingSession, id=session_id, aluno=student)
    items = list(session.items.order_by("order", "id"))
    for it in items:
        it.tags_list = [t.strip() for t in (it.tags or "").split(",") if t.strip()]
        label, color, fill, text = _rating_badge(it.rating, "OK", it.platform)
        it.rating_label = label
        it.rating_color = color
        it.rating_fill = fill
        it.rating_text = text

    counts = session.items.values("result").annotate(n=Count("id"))
    counts_map = {row["result"]: row["n"] for row in counts}
    total = sum(counts_map.values()) if counts_map else len(items)
    done = total - int(counts_map.get("TODO", 0))
    solved = int(counts_map.get("SOLVED", 0))
    editorial = int(counts_map.get("EDITORIAL", 0))
    stuck = int(counts_map.get("STUCK", 0))

    mandatory_items = [it for it in items if not it.is_optional]
    expected_total = sum(int(it.expected_minutes or 0) for it in mandatory_items)
    expected_done = sum(
        int(it.expected_minutes or 0)
        for it in mandatory_items
        if it.result != "TODO"
    )

    return render(request, "core/train_session.html", {
        "student": student,
        "session": session,
        "items": items,
        "total": total,
        "done": done,
        "solved": solved,
        "editorial": editorial,
        "stuck": stuck,
        "expected_total": expected_total,
        "expected_done": expected_done,
    })


@login_required
def train_session_end(request, session_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")
    student = _get_or_create_profile(request)
    session = get_object_or_404(TrainingSession, id=session_id, aluno=student)
    if not session.is_active:
        return redirect("train")
    now = timezone.now()
    session.is_active = False
    session.ended_at = now
    session.save(update_fields=["is_active", "ended_at", "updated_at"])
    return redirect("train")


@login_required
def train_session_item_result(request, session_id: int, item_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    student = _get_or_create_profile(request)
    session = get_object_or_404(TrainingSession, id=session_id, aluno=student)
    item = get_object_or_404(TrainingSessionItem, id=item_id, session=session)

    result = (request.POST.get("result") or "").upper()
    valid_results = {c[0] for c in TrainingSessionItem.RESULT_CHOICES}
    if result not in valid_results:
        return HttpResponseBadRequest("Resultado invalido.")

    stuck_reason = None
    if result == "STUCK":
        stuck_reason = (request.POST.get("stuck_reason") or "").strip()
        valid_reasons = {c[0] for c in TrainingSessionItem.STUCK_REASON_CHOICES}
        if stuck_reason not in valid_reasons:
            stuck_reason = "idea"

    item.result = result
    item.stuck_reason = stuck_reason
    item.save(update_fields=["result", "stuck_reason", "updated_at"])

    if result == "BLOCKED":
        TrainingBlockedProblem.objects.get_or_create(
            aluno=student,
            platform=item.platform,
            problem_url=item.problem_url,
        )
        TrainingQueueItem.objects.filter(
            aluno=student,
            platform=item.platform,
            problem_url=item.problem_url,
        ).update(status="BLOCKED")
    if result in {"SOLVED", "EDITORIAL"}:
        TrainingQueueItem.objects.filter(
            aluno=student,
            platform=item.platform,
            problem_url=item.problem_url,
        ).update(status="DONE")

    item.tags_list = [t.strip() for t in (item.tags or "").split(",") if t.strip()]
    label, color, fill, text = _rating_badge(item.rating, "OK", item.platform)
    item.rating_label = label
    item.rating_color = color
    item.rating_fill = fill
    item.rating_text = text
    counts = session.items.values("result").annotate(n=Count("id"))
    counts_map = {row["result"]: row["n"] for row in counts}
    total = sum(counts_map.values()) if counts_map else session.items.count()
    done = total - int(counts_map.get("TODO", 0))
    solved = int(counts_map.get("SOLVED", 0))
    editorial = int(counts_map.get("EDITORIAL", 0))
    stuck = int(counts_map.get("STUCK", 0))
    mandatory_items = list(session.items.filter(is_optional=False))
    expected_total = sum(int(it.expected_minutes or 0) for it in mandatory_items)
    expected_done = sum(
        int(it.expected_minutes or 0)
        for it in mandatory_items
        if it.result != "TODO"
    )

    return render(request, "core/partials/train_session_item_row.html", {
        "session": session,
        "item": item,
        "total": total,
        "done": done,
        "solved": solved,
        "editorial": editorial,
        "stuck": stuck,
        "expected_total": expected_total,
        "expected_done": expected_done,
    })


@login_required
def train_queue_add(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    student = _get_or_create_profile(request)
    platform = (request.POST.get("platform") or "").upper()
    if platform not in {"CF", "AC"}:
        return HttpResponseBadRequest("Plataforma invalida.")

    problem_url = (request.POST.get("problem_url") or "").strip()
    if not problem_url:
        return HttpResponseBadRequest("problem_url requerido.")

    title = (request.POST.get("title") or "").strip()
    tags = (request.POST.get("tags") or "").strip()
    source = (request.POST.get("source") or "manual").strip()
    if source not in {c[0] for c in TrainingQueueItem.SOURCE_CHOICES}:
        source = "manual"

    rating_val = request.POST.get("rating")
    rating = None
    if rating_val and str(rating_val).isdigit():
        rating = int(rating_val)

    existing = TrainingQueueItem.objects.filter(aluno=student, platform=platform, problem_url=problem_url).first()
    if existing:
        existing.title = title
        existing.rating = rating
        existing.tags = tags
        existing.status = "QUEUED"
        existing.source = source
        existing.save(update_fields=["title", "rating", "tags", "status", "source", "updated_at"])
    else:
        max_priority = TrainingQueueItem.objects.filter(aluno=student).aggregate(Max("priority")).get("priority__max") or 0
        TrainingQueueItem.objects.create(
            aluno=student,
            platform=platform,
            problem_url=problem_url,
            title=title,
            rating=rating,
            tags=tags,
            status="QUEUED",
            source=source,
            priority=int(max_priority) + 1,
        )

    next_url = request.POST.get("next") or reverse("train")
    return redirect(next_url)


@login_required
def train_queue_remove(request, item_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")
    student = _get_or_create_profile(request)
    item = get_object_or_404(TrainingQueueItem, id=item_id, aluno=student)
    item.delete()
    next_url = request.POST.get("next") or reverse("train")
    return redirect(next_url)


@login_required
def train_block_problem(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    student = _get_or_create_profile(request)
    platform = (request.POST.get("platform") or "").upper()
    if platform not in {"CF", "AC"}:
        return HttpResponseBadRequest("Plataforma invalida.")

    problem_url = (request.POST.get("problem_url") or "").strip()
    if not problem_url:
        return HttpResponseBadRequest("problem_url requerido.")

    TrainingBlockedProblem.objects.get_or_create(
        aluno=student,
        platform=platform,
        problem_url=problem_url,
    )
    TrainingQueueItem.objects.filter(aluno=student, platform=platform, problem_url=problem_url).update(status="BLOCKED")

    next_url = request.POST.get("next") or reverse("train")
    return redirect(next_url)
