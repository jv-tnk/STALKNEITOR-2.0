#!/bin/bash
set -euo pipefail

echo "Setup rapido via Docker Compose."
if [ ! -f ".env" ]; then
  echo "Configurando .env..."
  ./configure-env.sh
fi
exec ./stack-up.sh
