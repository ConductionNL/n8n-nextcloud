# n8n Nextcloud ExApp
# Combines n8n workflow automation with AppAPI lifecycle management

# Use Node.js Alpine as base and install n8n + Python
FROM node:20-alpine

# Install system dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    curl \
    bash \
    su-exec \
    tini

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Install n8n globally
RUN npm install -g n8n

# Install FRP client for HaRP support
RUN set -ex; \
    ARCH=$(uname -m); \
    if [ "$ARCH" = "aarch64" ]; then \
      FRP_URL="https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/frp_0.61.1_linux_arm64.tar.gz"; \
      FRP_DIR="frp_0.61.1_linux_arm64"; \
    else \
      FRP_URL="https://raw.githubusercontent.com/nextcloud/HaRP/main/exapps_dev/frp_0.61.1_linux_amd64.tar.gz"; \
      FRP_DIR="frp_0.61.1_linux_amd64"; \
    fi; \
    curl -L "$FRP_URL" -o /tmp/frp.tar.gz; \
    tar -C /tmp -xzf /tmp/frp.tar.gz; \
    cp /tmp/${FRP_DIR}/frpc /usr/local/bin/frpc; \
    chmod +x /usr/local/bin/frpc; \
    rm -rf /tmp/frp* /tmp/${FRP_DIR}

# Copy ExApp wrapper
COPY ex_app /app/ex_app

# Create data directory and user
RUN mkdir -p /data && \
    adduser -D -h /home/node -s /bin/bash node 2>/dev/null || true && \
    chown -R node:node /data /app

WORKDIR /app

# Expose ports
EXPOSE 9000 5678

# Environment defaults
ENV APP_HOST=0.0.0.0
ENV APP_PORT=9000
ENV PYTHONUNBUFFERED=1
ENV N8N_USER_FOLDER=/data

# Entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use tini as init
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
