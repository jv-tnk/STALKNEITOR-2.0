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

web_port="$(grep -E '^WEB_PORT=' .env | head -n 1 | cut -d'=' -f2- || true)"
if [ -z "$web_port" ]; then
  web_port="8000"
fi

echo "Subindo stack completa (db, redis, init, web, worker, beat)..."
${COMPOSE_CMD} up --build -d

echo ""
echo "Stack iniciada."
echo "Web: http://localhost:${web_port}"
echo "Logs web: ${COMPOSE_CMD} logs -f web"
echo "Logs worker: ${COMPOSE_CMD} logs -f worker"
echo "Logs beat: ${COMPOSE_CMD} logs -f beat"
