#!/bin/sh
set -e

echo "Starting n8n ExApp..."
echo "APP_ID: ${APP_ID:-n8n}"
echo "APP_HOST: ${APP_HOST:-0.0.0.0}"
echo "APP_PORT: ${APP_PORT:-23000}"

exec python3 ex_app/lib/main.py
