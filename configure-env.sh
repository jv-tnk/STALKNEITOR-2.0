#!/bin/bash
set -euo pipefail

ENV_FILE=".env"
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --force)
      FORCE=true
      ;;
    *)
      echo "Opcao invalida: $arg"
      echo "Uso: ./configure-env.sh [--force]"
      exit 1
      ;;
  esac
done

prompt_default() {
  local label="$1"
  local default_value="$2"
  local value=""
  read -r -p "$label [$default_value]: " value
  if [ -z "$value" ]; then
    value="$default_value"
  fi
  printf '%s' "$value"
}

prompt_hidden() {
  local label="$1"
  local value=""
  read -r -s -p "$label: " value
  echo ""
  printf '%s' "$value"
}

is_valid_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]
}

ask_port() {
  local label="$1"
  local default_port="$2"
  local port=""
  while true; do
    port="$(prompt_default "$label" "$default_port")"
    if is_valid_port "$port"; then
      printf '%s' "$port"
      return 0
    fi
    echo "Porta invalida. Use um numero entre 1 e 65535."
  done
}

generate_secret_key() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import secrets
import string

alphabet = string.ascii_letters + string.digits + "!@#$%^&*(-_=+)"
print("django-insecure-" + "".join(secrets.choice(alphabet) for _ in range(50)))
PY
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    printf 'django-insecure-%s\n' "$(openssl rand -base64 48 | tr -d '\n')"
    return
  fi

  echo "Erro: nao foi possivel gerar SECRET_KEY (python3/openssl ausentes)." >&2
  exit 1
}

if [ -f "$ENV_FILE" ] && [ "$FORCE" = false ]; then
  echo "Arquivo .env ja existe. Use --force para sobrescrever."
  exit 1
fi

if [ -f "$ENV_FILE" ] && [ "$FORCE" = true ]; then
  backup_file=".env.backup.$(date +%Y%m%d%H%M%S)"
  cp "$ENV_FILE" "$backup_file"
  echo "Backup criado: $backup_file"
fi

echo "Assistente de configuracao do .env"
echo "Pressione Enter para aceitar os valores padrao."
echo ""

secret_key="$(generate_secret_key)"
echo "SECRET_KEY gerada automaticamente."

debug_value="$(prompt_default "DEBUG (True/False)" "False")"
allowed_hosts="$(prompt_default "ALLOWED_HOSTS (separados por virgula)" "127.0.0.1,localhost")"

web_port="$(ask_port "Porta web (localhost)" "8000")"
db_port="$(ask_port "Porta Postgres (localhost)" "5433")"
redis_port="$(ask_port "Porta Redis (localhost)" "6379")"

db_name="$(prompt_default "DB_NAME" "stalkineitor_db")"
db_user="$(prompt_default "DB_USER" "postgres")"
db_password="$(prompt_default "DB_PASSWORD" "postgres")"

clist_username="$(prompt_default "CLIST_USERNAME (opcional)" "")"
clist_api_key="$(prompt_default "CLIST_API_KEY (opcional)" "")"

create_superuser="$(prompt_default "Criar superuser automaticamente no primeiro boot? (s/N)" "N")"
superuser_username=""
superuser_email=""
superuser_password=""

if [[ "$create_superuser" =~ ^[sS]$ ]]; then
  while true; do
    superuser_username="$(prompt_default "DJANGO_SUPERUSER_USERNAME" "admin")"
    if [ -n "$superuser_username" ]; then
      break
    fi
    echo "Username nao pode ser vazio."
  done
  superuser_email="$(prompt_default "DJANGO_SUPERUSER_EMAIL (opcional)" "")"
  while true; do
    superuser_password="$(prompt_hidden "DJANGO_SUPERUSER_PASSWORD")"
    confirm_password="$(prompt_hidden "Confirme a senha do superuser")"
    if [ -z "$superuser_password" ]; then
      echo "Senha nao pode ser vazia."
      continue
    fi
    if [ "$superuser_password" != "$confirm_password" ]; then
      echo "As senhas nao conferem. Tente novamente."
      continue
    fi
    break
  done
fi

cat > "$ENV_FILE" <<EOF
DEBUG=$debug_value
SECRET_KEY=$secret_key
ALLOWED_HOSTS=$allowed_hosts

WEB_PORT=$web_port
DB_PORT=$db_port
REDIS_PORT=$redis_port

DB_NAME=$db_name
DB_USER=$db_user
DB_PASSWORD=$db_password

CLIST_USERNAME=$clist_username
CLIST_API_KEY=$clist_api_key

# Opcional: cria superuser no primeiro bootstrap.
DJANGO_SUPERUSER_USERNAME=$superuser_username
DJANGO_SUPERUSER_EMAIL=$superuser_email
DJANGO_SUPERUSER_PASSWORD=$superuser_password
EOF

echo ""
echo "Arquivo .env criado com sucesso."
echo "Proximo passo: ./stack-up.sh"
