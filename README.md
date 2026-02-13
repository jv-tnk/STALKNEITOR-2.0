# Stalkineitor 2.1

Plataforma de monitoria e treinamento para programacao competitiva.

## Rodar localmente para testar (passo a passo)

### 1) Pre-requisitos

- Docker Engine 24+ (ou Docker Desktop)
- Docker Compose v2 (`docker compose`)
- Git

Cheque rapido:

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

### 3) Criar arquivo de ambiente

Opcao automatica (recomendada):

```bash
chmod +x configure-env.sh
./configure-env.sh
```

O assistente:

- gera `SECRET_KEY` aleatoria
- pergunta portas (`WEB_PORT`, `DB_PORT`, `REDIS_PORT`)
- pergunta dados de banco local
- permite configurar superuser inicial (username/email/senha)
- grava tudo no `.env`

Opcao manual:

```bash
cp .env.example .env
```

Campos mais importantes:

- `SECRET_KEY`: chave do Django
- `ALLOWED_HOSTS`: hosts permitidos (local: `127.0.0.1,localhost`)
- `WEB_PORT`, `DB_PORT`, `REDIS_PORT`: portas expostas no localhost
- `CLIST_USERNAME` e `CLIST_API_KEY`: habilitam integracoes com clist (se nao preencher, essa parte fica limitada)
- `DJANGO_SUPERUSER_*`: opcional, para criar admin automaticamente no primeiro boot

### 4) Subir tudo com um comando

```bash
chmod +x stack-up.sh
./stack-up.sh
```

Esse comando sobe:

- `db` (PostgreSQL)
- `redis`
- `app-init` (migracoes + bootstrap opcional de superuser)
- `web` (Django em `:8000`)
- `worker` (Celery)
- `beat` (agendador Celery)

Obs.: se o `.env` nao existir, o `./stack-up.sh` abre o assistente automaticamente.

### 5) Acessar e validar

- Aplicacao: `http://localhost:8000`
- Login admin (se configurado no `.env`): use `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`

Checagens uteis:

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f worker beat
```

Se `worker` e `beat` estiverem ativos, os processos de sincronizacao automatica estao rodando.

## Comandos do dia a dia

- Subir stack: `./stack-up.sh`
- Reconfigurar `.env`: `./stack-up.sh --configure`
- Parar stack: `docker compose down`
- Parar e remover volumes (limpa banco local): `docker compose down -v`
- Reiniciar somente web: `docker compose restart web`
- Rodar migracoes manualmente: `docker compose run --rm web python manage.py migrate`
- Criar superuser manualmente: `docker compose run --rm web python manage.py createsuperuser`

## Fluxo recomendado para atualizar do GitHub

```bash
git pull
docker compose up --build -d
```
