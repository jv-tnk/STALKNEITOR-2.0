#!/bin/bash
set -euo pipefail

if [ ! -f ".env" ]; then
  echo "Arquivo .env nao encontrado. Crie o .env antes de subir a stack."
  exit 1
fi

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  COMPOSE_CMD="docker compose"
fi

echo "Subindo stack completa (db, redis, init, web, worker, beat)..."
${COMPOSE_CMD} up --build -d

echo ""
echo "Stack iniciada."
echo "Web: http://localhost:8000"
echo "Logs web: ${COMPOSE_CMD} logs -f web"
echo "Logs worker: ${COMPOSE_CMD} logs -f worker"
echo "Logs beat: ${COMPOSE_CMD} logs -f beat"
