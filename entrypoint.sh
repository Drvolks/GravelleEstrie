#!/bin/sh
set -e

# Apply migrations and collect admin static files on every start.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Optionally create an admin user from env vars (idempotent).
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null \
        && echo "Superuser '$DJANGO_SUPERUSER_USERNAME' created." \
        || echo "Superuser already exists (or could not be created); continuing."
fi

exec "$@"
