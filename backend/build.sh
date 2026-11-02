#!/usr/bin/env bash
# Render build step. On the free plan there's no pre-deploy step or shell,
# so migrations and the first Super Admin are handled here too.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py ensure_superuser
