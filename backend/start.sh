#!/usr/bin/env bash
# Render start step. The free plan has no background workers, so the
# email sender runs alongside the API in the same instance, restarting
# if it ever exits. While the instance sleeps, queued emails wait.
set -o errexit

(
    while true; do
        python manage.py send_notifications --loop || true
        sleep 5
    done
) &

# Daphne (ASGI) rather than gunicorn: the community forum uses WebSockets.
exec daphne -b 0.0.0.0 -p "$PORT" config.asgi:application
