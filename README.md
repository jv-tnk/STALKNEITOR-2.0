# Stalkineitor 2.0

Plataforma de treinamento para programacao competitiva.

## Rodar localmente

### 1) Pre-requisitos

- Docker Engine 24+ (ou Docker Desktop)
- Docker Compose v2 (`docker compose`)
- Git

Checagem rapida:

```bash
docker --version
docker compose version
git --version
```

### 2) Clonar o projeto

```bash
git clone https://github.com/jv-tnk/STALKNEITOR-2.0.git
cd STALKNEITOR-2.0
```

### 3) Criar `.env`

```bash
cp .env.example .env
```

### 4) Gerar a `SECRET_KEY` do Django

Rode:

```bash
docker compose run --rm web python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Use o valor gerado para preencher `SECRET_KEY` no arquivo `.env`.

### 5) Configurar variaveis principais no `.env`

- `SECRET_KEY`
- `ALLOWED_HOSTS` (local: `127.0.0.1,localhost`)
- `WEB_PORT`, `DB_PORT`, `REDIS_PORT`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `CLIST_USERNAME` e `CLIST_API_KEY` (opcional, mas recomendado)
- `DJANGO_SUPERUSER_*` (opcional, para criar admin no bootstrap)

### 6) Subir a stack

```bash
docker compose up --build -d
```

Servicos:

- `db` (PostgreSQL)
- `redis`
- `app-init` (migracoes + bootstrap de usuario/perfil)
- `web` (Django)
- `worker` (Celery worker)
- `beat` (Celery beat)

### 7) Validar

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f beat
```

Aplicacao: `http://localhost:8000` (ou porta definida em `WEB_PORT`).

## Comandos uteis

```bash
# parar stack
docker compose down

# parar stack e limpar volumes (zera banco local)
docker compose down -v

# reiniciar apenas web
docker compose restart web

# rodar migracoes manualmente
docker compose run --rm web python manage.py migrate

# criar superuser manualmente
docker compose run --rm web python manage.py createsuperuser
```

## Opcional: forcar ciclo inicial manual

```bash
docker compose exec -T web python manage.py shell <<'PY'
from core.tasks import contests_catalog_refresh, contests_problems_scheduler, ratings_backfill_scheduler, process_rating_fetch_jobs, sync_all_students
print(contests_catalog_refresh())
print(contests_problems_scheduler(max_cf_per_run=12, max_ac_per_run=12))
print(ratings_backfill_scheduler(limit=10))
print(process_rating_fetch_jobs(limit=10))
print(sync_all_students())
PY
```

## Atualizar do GitHub

```bash
git pull
docker compose up --build -d
```
