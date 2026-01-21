# Stalkineitor 2.1

Plataforma de monitoria e treinamento para programacao competitiva.

## Setup rapido

1. Configure o arquivo `.env` (veja o exemplo em `.env`).
2. Suba os servicos de suporte:
   - `docker compose up -d`
3. Crie o venv e instale dependencias:
   - `python3 -m venv venv`
   - `source venv/bin/activate`
   - `pip install -r requirements.txt`
4. Rode as migracoes:
   - `python manage.py migrate`
5. Inicie os processos:
   - `celery -A stalkineitor_project worker -l info -Q celery`
   - `celery -A stalkineitor_project worker -l info -Q ratings -c 2`
   - `celery -A stalkineitor_project beat -l info`
   - `python manage.py runserver`

O script `setup.sh` automatiza boa parte desse fluxo.
