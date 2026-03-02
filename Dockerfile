FROM alpine:3.22 AS python-builder

RUN apk add --no-cache python3 py3-pip python3-dev gcc musl-dev libffi-dev

WORKDIR /build
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages --prefix=/python-packages -r requirements.txt


FROM n8nio/n8n:2.6.3

USER root

# Copy Python runtime and installed packages from builder
COPY --from=python-builder /usr/bin/python3 /usr/bin/python3
COPY --from=python-builder /usr/lib/python3.* /usr/lib/python3.12/
COPY --from=python-builder /usr/lib/libpython3* /usr/lib/
COPY --from=python-builder /usr/lib/libffi* /usr/lib/
COPY --from=python-builder /python-packages/lib/python3.12/site-packages/ /usr/lib/python3.12/site-packages/
COPY --from=python-builder /python-packages/bin/ /usr/bin/

# Create app directory
WORKDIR /app

# Copy application code
COPY ex_app/ ex_app/
COPY img/ ex_app/img/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# n8n data directory
VOLUME /data

ENTRYPOINT ["./entrypoint.sh"]
