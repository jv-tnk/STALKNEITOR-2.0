from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.db.models import Max
from django.urls import reverse
from django.db import models
from django.http import Http404
from django.core.paginator import Paginator
from django.utils import timezone
from .models import PerfilAluno, ProblemaReferencia, NivelUSACO, SolucaoCompartilhada, Submissao, Turma
from .services.api_client import get_all_solved_problems
from .services.ranking import (
    build_activity_ranking,
    build_ranking_with_delta,
    build_rating_ranking,
)


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

    if fill_percent >= 70:
        text_color = "#0f172a" if color in {"#FFD700", "#BFBFBF"} else "#ffffff"
    else:
        text_color = color

    return f"{int(rating)}", color, fill_percent, text_color

def add_student(request):
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        cf_handle = (request.POST.get('cf_handle') or '').strip() or None
        ac_handle = (request.POST.get('ac_handle') or '').strip() or None
        
        # Simple validation
        if not username:
            return render(request, 'core/add_student.html', {'error': 'Username is required'})
        if User.objects.filter(username=username).exists():
            return render(request, 'core/add_student.html', {'error': 'Username already exists'})
        if cf_handle and PerfilAluno.objects.filter(handle_codeforces=cf_handle).exists():
            return render(request, 'core/add_student.html', {'error': 'Codeforces handle already in use'})
        if ac_handle and PerfilAluno.objects.filter(handle_atcoder=ac_handle).exists():
            return render(request, 'core/add_student.html', {'error': 'AtCoder handle already in use'})
            
        user = User.objects.create_user(username=username, password='password123') # Default password for now
        perfil = PerfilAluno.objects.create(
            user=user,
            handle_codeforces=cf_handle,
            handle_atcoder=ac_handle
        )
        if hasattr(request, 'htmx') and request.htmx:
            return render(request, 'core/partials/student_row.html', {'student': perfil})
        return redirect('dashboard')
        
    return render(request, 'core/add_student.html')

def dashboard(request):
    """
    Dashboard atualizado: Mostra matriz de alunos x problemas dos últimos contests.
    Baseado nas submissões salvas no banco de dados (Submissao).
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models.functions import TruncDate
    
    # 1. Identificar os 10 contests mais recentes com base na data da última submissão
    recent_contests_qs = Submissao.objects.values('contest_id', 'plataforma') \
        .annotate(last_activity=Max('submission_time')) \
        .order_by('-last_activity')[:8]

    contest_dates = {
        (item['plataforma'], item['contest_id']): item['last_activity']
        for item in recent_contests_qs
    }

    # 2. Identificar quais problemas existem nesses contests e pegar METADATA
    contest_filters = models.Q()
    for item in recent_contests_qs:
        contest_filters |= models.Q(
            contest_id=item['contest_id'],
            plataforma=item['plataforma'],
        )
    relevant_subs = Submissao.objects.filter(contest_filters) if contest_dates else Submissao.objects.none()
    
    # Metadata map: (platform, contest_id, problem_index) -> {name, tags}
    metadata_map = {}
    unique_problems = set()
    
    # Vamos iterar para popular o metadata e o set de problemas
    # Usamos iterator() para não carregar tudo se for muito grande, mas aqui é pequeno
    for sub in relevant_subs.values('plataforma', 'contest_id', 'problem_index', 'problem_name', 'tags'):
        key = (sub['plataforma'], sub['contest_id'], sub['problem_index'])
        unique_problems.add(key)
        
        # Se ainda não temos metadata ou se o atual tem nome vazio, tenta atualizar
        if key not in metadata_map or not metadata_map[key]['name']:
            metadata_map[key] = {
                'name': sub['problem_name'] or f"Problem {sub['problem_index']}",
                'tags': sub['tags'].split(',') if sub['tags'] else []
            }
        
    # Ordena as colunas
    sorted_columns = sorted(list(unique_problems), key=lambda x: (
        -contest_dates[(x[0], x[1])].timestamp(),
        x[0],
        x[1],
        x[2],
    ))
    
    # 3. Montar a matriz de dados e CALCULAR STREAK
    students = PerfilAluno.objects.select_related('user').all()

    # Mapa de Submissões
    submission_map = {}
    raw_subs = relevant_subs.values(
        'aluno_id',
        'plataforma',
        'contest_id',
        'problem_index',
        'verdict',
        'id',
    )

    for s in raw_subs:
        key = (s['aluno_id'], s['plataforma'], s['contest_id'], s['problem_index'])
        current_data = submission_map.get(key)
        new_verdict = s['verdict']
        new_id = s['id']
        
        if not current_data:
             submission_map[key] = {'verdict': new_verdict, 'id': new_id}
        else:
            if current_data['verdict'] not in ['OK', 'AC']:
                submission_map[key] = {'verdict': new_verdict, 'id': new_id}

    rows = []
    total_problems = len(sorted_columns)
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)

    for student in students:
        # --- Streak Calculation ---
        # Get all distinct days with solved problems
        solved_dates = Submissao.objects.filter(
            aluno=student, 
            verdict__in=['OK', 'AC']
        ).annotate(
            date=TruncDate('submission_time')
        ).values_list('date', flat=True).distinct().order_by('-date')
        
        current_streak = 0
        if solved_dates:
            # Check if streak is alive (solved today or yesterday)
            last_solved = solved_dates[0]
            if last_solved == today or last_solved == yesterday:
                current_streak = 1
                previous_date = last_solved
                
                # Iterate backwards
                for d in solved_dates[1:]:
                    if d == previous_date - timedelta(days=1):
                        current_streak += 1
                        previous_date = d
                    else:
                        break
            else:
                current_streak = 0
        
        # --- Matrix Construction ---
        student_statuses = []
        solved_count = 0

        for (platform, c_id, p_idx) in sorted_columns:
            data = submission_map.get((student.id, platform, c_id, p_idx))
            meta = metadata_map.get((platform, c_id, p_idx), {'name': 'Unknown', 'tags': ''})
            
            verdict = 'NONE'
            sub_id = None

            if data:
                verdict = data['verdict']
                sub_id = data['id']
            
            status = 'NONE'
            if verdict in ['OK', 'AC']:
                status = 'SOLVED'
                solved_count += 1
            elif verdict != 'NONE':
                status = 'ATTEMPTED'
                
            student_statuses.append({
                'platform': platform,
                'contest': c_id,
                'problem': p_idx,
                'status': status,
                'verdict': verdict,
                'submission_id': sub_id,
                'meta': meta # Pass metadata for popover
            })
        
        progress_percent = int((solved_count / total_problems * 100)) if total_problems > 0 else 0

        rows.append({
            'student': student,
            'statuses': student_statuses,
            'progress': progress_percent,
            'streak': current_streak
        })

    return render(request, 'core/dashboard.html', {
        'columns': sorted_columns,
        'rows': rows,
    })


def perfil_aluno(request, username):
    """
    Exibe estatísticas detalhadas e progresso no roteiro USACO.
    """
    student = get_object_or_404(PerfilAluno, user__username=username)
    
    # Busca IDs resolvidos
    solved_ids = get_all_solved_problems(
        student.handle_codeforces,
        student.handle_atcoder,
        student=student,
    )
    
    # Atualiza métricas básicas no objeto (simulando cache)
    student.total_solved = len(solved_ids)
    # student.save() # Opcional salvar agora
    
    # Monta a estrutura do USACO com progresso calculado
    levels_data = []
    levels = NivelUSACO.objects.prefetch_related('modulos__problemas').all()
    
    for level in levels:
        modulos_data = []
        for modulo in level.modulos.all():
            problems = modulo.problemas.exclude(plataforma='OJ')
            total_probs = problems.count()
            
            solved_count = 0
            for p in problems:
                if p.problema_id in solved_ids:
                    solved_count += 1
            
            percent = (solved_count / total_probs * 100) if total_probs > 0 else 0
            is_complete = percent == 100
            
            modulos_data.append({
                'obj': modulo,
                'total': total_probs,
                'solved': solved_count,
                'percent': percent,
                'is_complete': is_complete
            })
            
        levels_data.append({
            'level': level,
            'modulos': modulos_data
        })

    return render(request, 'core/profile.html', {
        'student': student,
        'levels_data': levels_data,
    })

def skill_tree(request, username):
    student = get_object_or_404(PerfilAluno, user__username=username)
    
    # Fetch solved problems (reusing logic from perfil_aluno)
    solved_ids = get_all_solved_problems(
        student.handle_codeforces,
        student.handle_atcoder,
        student=student,
    )
    
    # Get levels and prefetch modules and problems
    levels = NivelUSACO.objects.prefetch_related('modulos__problemas').order_by('ordem')
    
    levels_data = []
    
    # Logic: Level N is locked if Level N-1 completion < 60%.
    # The first level (Bronze/lowest order) is always unlocked.
    previous_level_percent = 100.0 
    
    for i, level in enumerate(levels):
        level_total_problems = 0
        level_solved_problems = 0
        
        modulos_data = []
        for modulo in level.modulos.all():
            problems = modulo.problemas.exclude(plataforma='OJ')
            total_probs = problems.count()
            
            solved_count = 0
            for p in problems:
                if p.problema_id in solved_ids:
                    solved_count += 1
            
            level_total_problems += total_probs
            level_solved_problems += solved_count
            
            mod_percent = (solved_count / total_probs * 100) if total_probs > 0 else 0
            
            modulos_data.append({
                'obj': modulo,
                'total': total_probs,
                'solved': solved_count,
                'percent': mod_percent,
            })
            
        level_percent = (level_solved_problems / level_total_problems * 100) if level_total_problems > 0 else 0
        
        # Determine locking
        is_locked = False
        if i > 0 and previous_level_percent < 60.0:
            is_locked = True
            
        levels_data.append({
            'level': level,
            'modulos': modulos_data,
            'percent': level_percent,
            'is_locked': is_locked
        })
        
        previous_level_percent = level_percent

    return render(request, 'core/skill_tree.html', {
        'student': student,
        'levels_data': levels_data,
    })


def solution_modal(request, submission_id):
    submission = get_object_or_404(
        Submissao.objects.select_related('aluno__user'),
        id=submission_id,
    )
    student = submission.aluno

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
    if problem:
        solution = SolucaoCompartilhada.objects.filter(
            aluno=student,
            problema=problem,
        ).order_by('-data_postagem').first()

    problem_title = problem.titulo if problem else (submission.problem_name or problem_id or "Problema")

    return render(request, 'core/partials/solution_modal_content.html', {
        'student': student,
        'submission': submission,
        'problem': problem,
        'problem_title': problem_title,
        'solution': solution,
    })


def ranking(request):
    mode = request.GET.get("mode", "points")
    if mode not in {"points", "rating", "activity"}:
        mode = "points"
    category = request.GET.get("category", "overall")
    if category not in {"overall", "cf", "ac"}:
        category = "overall"
    window = request.GET.get("window", "season")
    if window not in {"season", "7d", "30d", "all"}:
        window = "season"
    scope = request.GET.get("scope", "global")
    if scope not in {"global", "turma"}:
        scope = "global"
    turma_id = request.GET.get("turma_id")
    turma_id = int(turma_id) if turma_id and turma_id.isdigit() else None

    if mode == "points":
        rows = build_ranking_with_delta(category, window, scope, turma_id)
    elif mode == "rating":
        rows = build_rating_ranking(category, scope, turma_id)
    else:
        rows = build_activity_ranking(category, window, scope, turma_id)
    turmas = Turma.objects.all()

    current_user_rank = None
    if request.user.is_authenticated:
        aluno = PerfilAluno.objects.filter(user=request.user).first()
        if aluno:
            for row in rows:
                if row.aluno.id == aluno.id:
                    current_user_rank = row
                    break

    context = {
        "rows": rows,
        "mode": mode,
        "category": category,
        "window": window,
        "scope": scope,
        "turma_id": turma_id,
        "turmas": turmas,
        "current_user_rank": current_user_rank,
        "mode_unavailable": False,
    }

    return render(request, "core/ranking.html", context)


def ranking_list(request):
    mode = request.GET.get("mode", "points")
    if mode not in {"points", "rating", "activity"}:
        mode = "points"
    category = request.GET.get("category", "overall")
    if category not in {"overall", "cf", "ac"}:
        category = "overall"
    window = request.GET.get("window", "season")
    if window not in {"season", "7d", "30d", "all"}:
        window = "season"
    scope = request.GET.get("scope", "global")
    if scope not in {"global", "turma"}:
        scope = "global"
    turma_id = request.GET.get("turma_id")
    turma_id = int(turma_id) if turma_id and turma_id.isdigit() else None

    if mode == "points":
        rows = build_ranking_with_delta(category, window, scope, turma_id)
    elif mode == "rating":
        rows = build_rating_ranking(category, scope, turma_id)
    else:
        rows = build_activity_ranking(category, window, scope, turma_id)
    current_user_rank = None
    if request.user.is_authenticated:
        aluno = PerfilAluno.objects.filter(user=request.user).first()
        if aluno:
            for row in rows:
                if row.aluno.id == aluno.id:
                    current_user_rank = row
                    break

    return render(request, "core/partials/ranking_list.html", {
        "rows": rows,
        "mode": mode,
        "category": category,
        "window": window,
        "scope": scope,
        "turma_id": turma_id,
        "current_user_rank": current_user_rank,
        "mode_unavailable": False,
    })


def contests_overview(request):
    from datetime import datetime
    from django.utils import timezone
    from django.db.models.functions import ExtractYear
    from core.services.problem_ratings import get_or_schedule_problem_rating
    from core.services.problem_urls import build_problem_url_from_fields
    from core.services.contest_catalog import (
        get_ac_contest_problems,
        get_cf_contest_problems,
    )

    platform_filter = (request.GET.get("platform") or "all").lower()
    if platform_filter not in {"all", "cf", "ac"}:
        platform_filter = "all"

    now = timezone.now()
    current_year = now.year
    year_param = request.GET.get("year")
    selected_year = current_year
    if year_param and year_param.isdigit():
        selected_year = int(year_param)

    start_dt = timezone.make_aware(datetime(selected_year, 1, 1))
    end_dt = timezone.make_aware(datetime(selected_year + 1, 1, 1))

    subs_qs = Submissao.objects.filter(
        plataforma__in=["CF", "AC"],
        submission_time__gte=start_dt,
        submission_time__lt=end_dt,
    )
    if platform_filter != "all":
        subs_qs = subs_qs.filter(plataforma=platform_filter.upper())

    available_years = list(
        Submissao.objects.filter(plataforma__in=["CF", "AC"])
        .annotate(year=ExtractYear("submission_time"))
        .values_list("year", flat=True)
        .distinct()
        .order_by("-year")
    )
    if not available_years:
        available_years = [current_year]

    subs = subs_qs.values(
        "plataforma",
        "contest_id",
        "problem_index",
        "problem_name",
        "tags",
        "verdict",
        "submission_time",
        "aluno__user__username",
    )

    contests = {}
    for sub in subs:
        contest_id = sub.get("contest_id")
        problem_index = sub.get("problem_index")
        if not contest_id or not problem_index:
            continue

        key = (sub["plataforma"], contest_id)
        contest = contests.get(key)
        if not contest:
            contest = {
                "platform": sub["plataforma"],
                "contest_id": contest_id,
                "last_activity": sub.get("submission_time"),
                "problems": {},
            }
            contests[key] = contest
        else:
            sub_time = sub.get("submission_time")
            if sub_time and (
                contest["last_activity"] is None or contest["last_activity"] < sub_time
            ):
                contest["last_activity"] = sub_time

        problem = contest["problems"].get(problem_index)
        if not problem:
            problem_url = build_problem_url_from_fields(
                sub["plataforma"],
                contest_id,
                problem_index,
                sub.get("problem_name"),
            )
            cache_status = None
            rating = None
            if problem_url:
                cache = get_or_schedule_problem_rating(
                    sub["plataforma"],
                    problem_url,
                    problem_name=sub.get("problem_name"),
                )
                cache_status = cache.status
                rating = cache.clist_rating if cache.status == "OK" else None
            rating_label, rating_color, rating_fill, rating_text = _rating_badge(
                rating,
                cache_status,
                sub["plataforma"],
            )
            tags_raw = sub.get("tags") or ""
            tags = {t.strip() for t in tags_raw.split(",") if t.strip()}

            problem = {
                "index": problem_index,
                "name": sub.get("problem_name") or f"Problema {problem_index}",
                "solvers": set(),
                "url": problem_url,
                "rating": rating,
                "rating_label": rating_label,
                "rating_color": rating_color,
                "rating_fill": rating_fill,
                "rating_text": rating_text,
                "tags": tags,
            }
            contest["problems"][problem_index] = problem
        else:
            if sub.get("problem_name") and problem["name"].startswith("Problema "):
                problem["name"] = sub["problem_name"]
            tags_raw = sub.get("tags") or ""
            if tags_raw:
                problem["tags"].update(
                    {t.strip() for t in tags_raw.split(",") if t.strip()}
                )

        verdict = sub.get("verdict") or ""
        if verdict in {"OK", "AC"}:
            username = sub.get("aluno__user__username") or "unknown"
            problem["solvers"].add(username)

    for contest in contests.values():
        if contest["platform"] == "CF":
            catalog = get_cf_contest_problems(contest["contest_id"])
        else:
            catalog = get_ac_contest_problems(contest["contest_id"])

        for item in catalog:
            problem_index = item["index"]
            if not problem_index:
                continue
            problem = contest["problems"].get(problem_index)
            if not problem:
                problem_url = build_problem_url_from_fields(
                    contest["platform"],
                    contest["contest_id"],
                    problem_index,
                    item.get("problem_id") or item.get("name"),
                )
                cache_status = None
                rating = None
                if problem_url:
                    cache = get_or_schedule_problem_rating(
                        contest["platform"],
                        problem_url,
                        problem_name=item.get("name"),
                    )
                    cache_status = cache.status
                    rating = cache.clist_rating if cache.status == "OK" else None
                rating_label, rating_color, rating_fill, rating_text = _rating_badge(
                    rating,
                    cache_status,
                    contest["platform"],
                )
                problem = {
                    "index": problem_index,
                    "name": item.get("name") or f"Problema {problem_index}",
                    "solvers": set(),
                    "url": problem_url,
                    "rating": rating,
                    "rating_label": rating_label,
                    "rating_color": rating_color,
                    "rating_fill": rating_fill,
                    "rating_text": rating_text,
                    "tags": set(item.get("tags") or []),
                }
                contest["problems"][problem_index] = problem
            else:
                if item.get("name") and problem["name"].startswith("Problema "):
                    problem["name"] = item["name"]
                if item.get("tags"):
                    problem["tags"].update(item["tags"])

    contest_list = []
    total_problems = 0
    total_solves = 0

    for contest in contests.values():
        problems = []
        for problem in contest["problems"].values():
            solvers = sorted(problem["solvers"])
            tags = sorted(problem["tags"]) if problem.get("tags") else []
            problems.append(
                {
                    "index": problem["index"],
                    "name": problem["name"],
                    "solvers": solvers,
                    "solve_count": len(solvers),
                    "tags": tags,
                    "rating_label": problem.get("rating_label", "Pendente"),
                    "rating_color": problem.get("rating_color", "#64748b"),
                    "rating_fill": problem.get("rating_fill", 0),
                    "rating_text": problem.get("rating_text", "#e2e8f0"),
                    "url": problem.get("url"),
                }
            )
            total_solves += len(solvers)

        problems.sort(key=lambda p: p["index"])
        total_problems += len(problems)

        contest_list.append(
            {
                "platform": contest["platform"],
                "contest_id": contest["contest_id"],
                "last_activity": contest["last_activity"],
                "problems": problems,
                "detail_url": reverse(
                    "contest_detail",
                    kwargs={
                        "platform": contest["platform"].lower(),
                        "contest_id": contest["contest_id"],
                    },
                ),
                "url": (
                    f"https://codeforces.com/contest/{contest['contest_id']}"
                    if contest["platform"] == "CF"
                    else f"https://atcoder.jp/contests/{contest['contest_id']}"
                ),
            }
        )

    contest_list.sort(
        key=lambda c: (c["last_activity"] or start_dt, c["platform"], c["contest_id"]),
        reverse=True,
    )

    paginator = Paginator(contest_list, 10)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "core/contests.html",
        {
            "contests": page_obj.object_list,
            "current_year": current_year,
            "selected_year": selected_year,
            "platform_filter": platform_filter,
            "available_years": available_years,
            "total_contests": paginator.count,
            "total_problems": total_problems,
            "total_solves": total_solves,
            "page_obj": page_obj,
        },
    )


def contest_detail(request, platform, contest_id):
    from datetime import datetime
    from django.utils import timezone
    from core.services.problem_ratings import get_or_schedule_problem_rating
    from core.services.problem_urls import build_problem_url_from_fields
    from core.services.contest_catalog import (
        get_ac_contest_problems,
        get_cf_contest_problems,
    )

    platform = (platform or "").upper()
    if platform not in {"CF", "AC"}:
        raise Http404("Plataforma invalida.")

    start_dt = timezone.make_aware(datetime(2026, 1, 1))
    subs_qs = Submissao.objects.filter(
        plataforma=platform,
        contest_id=contest_id,
        submission_time__gte=start_dt,
    )
    if not subs_qs.exists():
        raise Http404("Contest nao encontrado.")

    def submission_url(external_id):
        if not external_id:
            return None
        if platform == "CF":
            return f"https://codeforces.com/contest/{contest_id}/submission/{external_id}"
        return f"https://atcoder.jp/contests/{contest_id}/submissions/{external_id}"

    def verdict_badge(verdict):
        label = verdict or "?"
        if verdict in {"OK", "AC"}:
            return label, "bg-emerald-500/15 text-emerald-200 border-emerald-500/30"
        if verdict in {"WRONG_ANSWER", "WA"}:
            return "WA", "bg-rose-500/15 text-rose-200 border-rose-500/30"
        if verdict in {"TIME_LIMIT_EXCEEDED", "TLE"}:
            return "TLE", "bg-amber-500/15 text-amber-200 border-amber-500/30"
        if verdict in {"MEMORY_LIMIT_EXCEEDED", "MLE"}:
            return "MLE", "bg-orange-500/15 text-orange-200 border-orange-500/30"
        return label, "bg-slate-800/60 text-slate-300 border-slate-700"

    if platform == "CF":
        students = list(
            PerfilAluno.objects.select_related("user")
            .exclude(handle_codeforces__isnull=True)
            .exclude(handle_codeforces="")
            .order_by("user__username")
        )
    else:
        students = list(
            PerfilAluno.objects.select_related("user")
            .exclude(handle_atcoder__isnull=True)
            .exclude(handle_atcoder="")
            .order_by("user__username")
        )

    submissions = subs_qs.values(
        "external_id",
        "verdict",
        "submission_time",
        "problem_index",
        "problem_name",
        "tags",
        "aluno_id",
        "aluno__user__username",
    )

    problems = {}
    submissions_map = {}
    total_submissions = 0
    start_time = None
    last_time = None

    for sub in submissions:
        total_submissions += 1
        sub_time = sub.get("submission_time")
        if sub_time:
            if not start_time or sub_time < start_time:
                start_time = sub_time
            if not last_time or sub_time > last_time:
                last_time = sub_time

        problem_index = sub.get("problem_index")
        if not problem_index:
            continue

        problem = problems.get(problem_index)
        if not problem:
            problem_url = build_problem_url_from_fields(
                platform,
                contest_id,
                problem_index,
                sub.get("problem_name"),
            )
            cache_status = None
            rating = None
            if problem_url:
                cache = get_or_schedule_problem_rating(
                    platform,
                    problem_url,
                    problem_name=sub.get("problem_name"),
                )
                cache_status = cache.status
                rating = cache.clist_rating if cache.status == "OK" else None
            rating_label, rating_color, rating_fill, rating_text = _rating_badge(
                rating,
                cache_status,
                platform,
            )
            tags_raw = sub.get("tags") or ""
            tags = {t.strip() for t in tags_raw.split(",") if t.strip()}

            problem = {
                "index": problem_index,
                "name": sub.get("problem_name") or f"Problema {problem_index}",
                "url": problem_url,
                "rating_label": rating_label,
                "rating_color": rating_color,
                "rating_fill": rating_fill,
                "rating_text": rating_text,
                "tags": tags,
            }
            problems[problem_index] = problem
        else:
            if sub.get("problem_name") and problem["name"].startswith("Problema "):
                problem["name"] = sub["problem_name"]
            tags_raw = sub.get("tags") or ""
            if tags_raw:
                problem["tags"].update(
                    {t.strip() for t in tags_raw.split(",") if t.strip()}
                )

        aluno_id = sub.get("aluno_id")
        if not aluno_id:
            continue
        key = (problem_index, aluno_id)
        submissions_map.setdefault(key, [])

        verdict_label, verdict_class = verdict_badge(sub.get("verdict"))
        submissions_map[key].append(
            {
                "url": submission_url(sub.get("external_id")),
                "verdict": verdict_label,
                "verdict_class": verdict_class,
                "submitted_at": sub_time,
            }
        )

    if platform == "CF":
        catalog = get_cf_contest_problems(contest_id)
    else:
        catalog = get_ac_contest_problems(contest_id)

    for item in catalog:
        problem_index = item.get("index")
        if not problem_index:
            continue
        problem = problems.get(problem_index)
        if not problem:
            problem_url = build_problem_url_from_fields(
                platform,
                contest_id,
                problem_index,
                item.get("problem_id") or item.get("name"),
            )
            cache_status = None
            rating = None
            if problem_url:
                cache = get_or_schedule_problem_rating(
                    platform,
                    problem_url,
                    problem_name=item.get("name"),
                )
                cache_status = cache.status
                rating = cache.clist_rating if cache.status == "OK" else None
            rating_label, rating_color, rating_fill, rating_text = _rating_badge(
                rating,
                cache_status,
                platform,
            )
            problem = {
                "index": problem_index,
                "name": item.get("name") or f"Problema {problem_index}",
                "url": problem_url,
                "rating_label": rating_label,
                "rating_color": rating_color,
                "rating_fill": rating_fill,
                "rating_text": rating_text,
                "tags": set(item.get("tags") or []),
            }
            problems[problem_index] = problem
        else:
            if item.get("name") and problem["name"].startswith("Problema "):
                problem["name"] = item["name"]
            if item.get("tags"):
                problem["tags"].update(item["tags"])

    for items in submissions_map.values():
        items.sort(key=lambda s: s["submitted_at"] or start_dt)

    problem_cards = []
    for problem in sorted(problems.values(), key=lambda p: p["index"]):
        rows = []
        for student in students:
            rows.append(
                {
                    "student": student,
                    "submissions": submissions_map.get(
                        (problem["index"], student.id),
                        [],
                    ),
                }
            )

        problem_cards.append(
            {
                "index": problem["index"],
                "name": problem["name"],
                "url": problem["url"],
                "rating_label": problem["rating_label"],
                "rating_color": problem["rating_color"],
                "rating_fill": problem["rating_fill"],
                "rating_text": problem["rating_text"],
                "tags": sorted(problem["tags"]) if problem["tags"] else [],
                "rows": rows,
            }
        )

    return render(
        request,
        "core/contest_detail.html",
        {
            "platform": platform,
            "contest_id": contest_id,
            "contest_url": (
                f"https://codeforces.com/contest/{contest_id}"
                if platform == "CF"
                else f"https://atcoder.jp/contests/{contest_id}"
            ),
            "start_time": start_time,
            "last_time": last_time,
            "total_submissions": total_submissions,
            "students_count": len(students),
            "problems": problem_cards,
        },
    )
