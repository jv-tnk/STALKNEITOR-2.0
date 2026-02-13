#!/bin/bash
set -euo pipefail

SHOW_HELP=false
FORCE_CONFIGURE=false

for arg in "$@"; do
  case "$arg" in
    --configure)
      FORCE_CONFIGURE=true
      ;;
    --help|-h)
      SHOW_HELP=true
      ;;
    *)
      echo "Opcao invalida: $arg"
      echo "Use --help para ver as opcoes."
      exit 1
      ;;
  esac
done

if [ "$SHOW_HELP" = true ]; then
  cat <<'EOF'
Uso: ./stack-up.sh [--configure]

Opcoes:
  --configure   Reabre o assistente e recria o .env.
  --help, -h    Mostra esta ajuda.
EOF
  exit 0
fi

if [ ! -f ".env" ]; then
  echo "Arquivo .env nao encontrado. Iniciando assistente..."
  ./configure-env.sh
fi

if [ "$FORCE_CONFIGURE" = true ]; then
  echo "Reconfigurando ambiente..."
  ./configure-env.sh --force
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Docker Compose nao encontrado. Instale Docker Compose v2 (docker compose)."
  exit 1
fi

wait_for_web_ready() {
  local attempts=30
  local sleep_seconds=2
  local attempt=1

  while [ "$attempt" -le "$attempts" ]; do
    if ${COMPOSE_CMD} exec -T web python manage.py check >/dev/null 2>&1; then
      return 0
    fi
    echo "Aguardando web ficar pronta para update inicial... (${attempt}/${attempts})"
    sleep "$sleep_seconds"
    attempt=$((attempt + 1))
  done

  return 1
}

web_port="$(grep -E '^WEB_PORT=' .env | head -n 1 | cut -d'=' -f2- || true)"
if [ -z "$web_port" ]; then
  web_port="8000"
fi

su_username="$(grep -E '^DJANGO_SUPERUSER_USERNAME=' .env | head -n 1 | cut -d'=' -f2- || true)"
su_password="$(grep -E '^DJANGO_SUPERUSER_PASSWORD=' .env | head -n 1 | cut -d'=' -f2- || true)"
if [ -z "$su_username" ] || [ -z "$su_password" ]; then
  echo "Aviso: DJANGO_SUPERUSER_USERNAME/DJANGO_SUPERUSER_PASSWORD estao vazios no .env."
  echo "Nenhum superusuario sera criado automaticamente no bootstrap."
  echo "Use ./configure-env.sh --force ou preencha manualmente esses campos."
  echo ""
fi

echo "Subindo stack completa (db, redis, init, web, worker, beat)..."
${COMPOSE_CMD} up --build -d

echo "Tentando forcar uma atualizacao inicial..."
if wait_for_web_ready; then
  if ! ${COMPOSE_CMD} exec -T web python manage.py shell <<'PY'
from core.models import Contest
from core.tasks import (
    contests_catalog_refresh,
    contests_problems_scheduler,
    ratings_backfill_scheduler,
    process_rating_fetch_jobs,
    sync_all_students,
)

if Contest.objects.exists():
    print("[init-update] contests ja existem; pulando ciclo pesado de catalogo.")
else:
    print("[init-update] banco sem contests; executando ciclo inicial.")
    catalog = contests_catalog_refresh()
    scheduler = contests_problems_scheduler(max_cf_per_run=12, max_ac_per_run=12)
    backfill = ratings_backfill_scheduler(limit=10)
    jobs = process_rating_fetch_jobs(limit=10)
    print(
        "[init-update] resumo:",
        f"catalog_runs={int(catalog.get('runs') or 0)}",
        f"scheduler_enqueued={int(scheduler.get('enqueued') or 0)}",
        f"backfill_enqueued={int(backfill.get('enqueued') or 0)}",
        f"jobs_processed={int(jobs.get('processed') or 0)}",
    )

try:
    sync_result = sync_all_students()
    print(f"[init-update] {sync_result}")
except Exception as exc:
    print(f"[init-update] falha ao enfileirar sync de alunos: {exc}")
PY
  then
    echo "Aviso: nao foi possivel executar o update inicial agora."
    echo "A stack continua no ar; tente novamente em alguns segundos."
  fi
else
  echo "Aviso: web nao ficou pronta a tempo para o update inicial automatico."
fi

echo ""
echo "Stack iniciada."
echo "Web: http://localhost:${web_port}"
echo "Logs web: ${COMPOSE_CMD} logs -f web"
echo "Logs worker: ${COMPOSE_CMD} logs -f worker"
echo "Logs beat: ${COMPOSE_CMD} logs -f beat"
