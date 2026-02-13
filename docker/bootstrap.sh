#!/bin/sh
set -eu

echo "[bootstrap] running migrations..."
python manage.py migrate --noinput

# Optional superuser bootstrap (idempotent).
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "[bootstrap] ensuring superuser ${DJANGO_SUPERUSER_USERNAME}..."
  python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

if username and password and not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"created superuser: {username}")
else:
    print("superuser already exists or vars missing")
PY
fi

echo "[bootstrap] done."
