#!/bin/bash
set -e

echo "Starting n8n ExApp..."
echo "APP_ID: ${APP_ID:-n8n}"
echo "APP_HOST: ${APP_HOST:-0.0.0.0}"
echo "APP_PORT: ${APP_PORT:-9000}"

# Start FRP client if HaRP is configured
if [ -n "$HARP_FRP_SERVER" ]; then
    echo "Starting FRP client for HaRP..."
    cat > /tmp/frpc.toml << EOF
serverAddr = "${HARP_FRP_SERVER}"
serverPort = ${HARP_FRP_PORT:-7000}

[[proxies]]
name = "${APP_ID:-n8n}"
type = "tcp"
localIP = "127.0.0.1"
localPort = ${APP_PORT:-9000}
remotePort = ${HARP_REMOTE_PORT:-0}
EOF
    /usr/local/bin/frpc -c /tmp/frpc.toml &
fi

# Start the AppAPI wrapper
exec python3 /app/ex_app/lib/main.py
