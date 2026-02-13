#!/bin/sh
set -eu

echo "[bootstrap] waiting for database..."
python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlparse

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("[bootstrap] DATABASE_URL ausente; seguindo sem espera ativa.")
    sys.exit(0)

parsed = urlparse(db_url)
host = parsed.hostname or "db"
port = parsed.port or 5432

attempts = 30
for attempt in range(1, attempts + 1):
    try:
        socket.getaddrinfo(host, port)
        with socket.create_connection((host, port), timeout=2):
            print(f"[bootstrap] database reachable at {host}:{port}")
            sys.exit(0)
    except Exception as exc:
        print(f"[bootstrap] waiting db ({attempt}/{attempts}): {exc}")
        time.sleep(2)

print("[bootstrap] database did not become reachable in time.", file=sys.stderr)
sys.exit(1)
PY

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

if username and password:
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": email},
    )
    if email:
        user.email = email
    user.is_superuser = True
    user.is_staff = True
    user.is_active = True
    user.set_password(password)
    user.save(update_fields=["email", "is_superuser", "is_staff", "is_active", "password"])
    action = "created" if created else "updated"
    print(f"{action} superuser credentials: {username}")
else:
    print("superuser vars missing")
PY
fi

echo "[bootstrap] ensuring app profile for all users..."
python manage.py shell <<'PY'
from django.contrib.auth import get_user_model
from core.models import PerfilAluno

User = get_user_model()
created_count = 0

for user in User.objects.all():
    origin = "admin" if (user.is_staff or user.is_superuser) else "legacy"
    _, created = PerfilAluno.objects.get_or_create(
        user=user,
        defaults={"created_via": origin},
    )
    if created:
        created_count += 1

print(f"ensured app profile for users (created={created_count})")
PY

echo "[bootstrap] done."
