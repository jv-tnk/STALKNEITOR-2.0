from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from core.models import PerfilAluno
from core.tasks import (
    fetch_student_data,
    recompute_score_windows,
    snapshot_rankings_task,
    sync_all_students,
)


@dataclass
class RankingForceUpdateResult:
    status: str
    message: str
    details: dict
    auto_refresh: bool = False
    refresh_until_ms: int | None = None


def _wait_minutes(seconds: int) -> int:
    return max(1, int((seconds + 59) // 60))


def _refresh_until_ms(seconds: int) -> int:
    return int((timezone.now().timestamp() + seconds) * 1000)


def _queue_snapshot(details: dict) -> None:
    try:
        snapshot_rankings_task.delay()
        details["snapshots_queued"] = True
    except Exception as exc:
        details["snapshots_queued"] = False
        details["snapshot_error"] = str(exc)


def _recompute_windows_inline(details: dict) -> None:
    try:
        recompute_score_windows()
        details["score_windows_recomputed"] = True
    except Exception as exc:
        details["score_windows_recomputed"] = False
        details["score_windows_error"] = str(exc)


def force_ranking_update_for_user(user) -> RankingForceUpdateResult:
    if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return RankingForceUpdateResult(
            status="error",
            message="Login necessario para atualizar o ranking.",
            details={},
        )

    is_privileged = bool(user.is_staff or user.is_superuser)
    if is_privileged:
        return _force_global_ranking_update()
    return _force_personal_ranking_update(user)


def _force_global_ranking_update() -> RankingForceUpdateResult:
    lock_seconds = max(
        60,
        int(getattr(settings, "FORCE_RANKING_UPDATE_GLOBAL_LOCK_SECONDS", 180)),
    )
    lock_key = "force_ranking_update:global"
    if cache.get(lock_key):
        return RankingForceUpdateResult(
            status="warning",
            message=f"Atualizacao global ja solicitada recentemente. Aguarde ~{_wait_minutes(lock_seconds)} min.",
            details={"cooldown_seconds": lock_seconds, "scope": "global"},
        )
    cache.set(lock_key, True, timeout=lock_seconds)

    students = list(PerfilAluno.objects.select_related("user").order_by("id"))
    total_students = len(students)
    inline_max = max(
        0,
        int(getattr(settings, "FORCE_RANKING_UPDATE_INLINE_MAX_STUDENTS_ADMIN", 20)),
    )
    details = {
        "scope": "global",
        "students_total": total_students,
        "students_processed": 0,
        "students_queued": 0,
        "mode": "inline" if total_students <= inline_max else "queued",
        "cooldown_seconds": lock_seconds,
        "errors": [],
    }

    if total_students <= inline_max:
        for student in students:
            try:
                fetch_student_data(student.id)
                details["students_processed"] += 1
            except Exception as exc:
                details["errors"].append(f"{student.user.username}: {exc}")

        _recompute_windows_inline(details)
        _queue_snapshot(details)
        status = "warning" if details["errors"] else "success"
        message = (
            f"Ranking atualizado agora para {details['students_processed']} alunos."
            if not details["errors"]
            else f"Ranking atualizado com {len(details['errors'])} falha(s)."
        )
        return RankingForceUpdateResult(
            status=status,
            message=message,
            details=details,
            refresh_until_ms=_refresh_until_ms(15),
        )

    try:
        sync_all_students.delay()
        details["students_queued"] = total_students
    except Exception as exc:
        details["errors"].append(str(exc))

    try:
        recompute_score_windows.delay()
        details["score_windows_queued"] = True
    except Exception as exc:
        details["score_windows_queued"] = False
        details["score_windows_error"] = str(exc)

    _queue_snapshot(details)
    if details["errors"]:
        return RankingForceUpdateResult(
            status="error",
            message="Falha ao enfileirar a atualizacao global do ranking.",
            details=details,
        )

    return RankingForceUpdateResult(
        status="success",
        message=f"Atualizacao global enfileirada para {total_students} alunos.",
        details=details,
        auto_refresh=True,
        refresh_until_ms=_refresh_until_ms(120),
    )


def _force_personal_ranking_update(user) -> RankingForceUpdateResult:
    lock_seconds = max(
        60,
        int(getattr(settings, "FORCE_RANKING_UPDATE_USER_LOCK_SECONDS", 120)),
    )
    lock_key = f"force_ranking_update:user:{user.id}"
    if cache.get(lock_key):
        return RankingForceUpdateResult(
            status="warning",
            message=f"Sua atualizacao ja foi solicitada recentemente. Aguarde ~{_wait_minutes(lock_seconds)} min.",
            details={"cooldown_seconds": lock_seconds, "scope": "personal"},
        )
    cache.set(lock_key, True, timeout=lock_seconds)

    profile, _ = PerfilAluno.objects.get_or_create(user=user)
    details = {
        "scope": "personal",
        "students_total": 1,
        "students_processed": 0,
        "students_queued": 0,
        "mode": "inline",
        "cooldown_seconds": lock_seconds,
        "errors": [],
    }
    try:
        fetch_student_data(profile.id)
        details["students_processed"] = 1
    except Exception as exc:
        details["errors"].append(str(exc))

    _recompute_windows_inline(details)
    _queue_snapshot(details)

    if details["errors"]:
        return RankingForceUpdateResult(
            status="warning",
            message="Atualizacao da sua conta terminou com falha parcial.",
            details=details,
            refresh_until_ms=_refresh_until_ms(15),
        )

    return RankingForceUpdateResult(
        status="success",
        message="Sua conta foi sincronizada e o ranking foi recalculado.",
        details=details,
        refresh_until_ms=_refresh_until_ms(15),
    )
