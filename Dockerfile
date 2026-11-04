# syntax=docker/dockerfile:1
#
# One container: Himalaya (mail), ortie (OAuth tokens) and a FastMCP gateway
# that serves every Himalaya account over streamable HTTP behind PocketID OAuth.

# `himalaya mcp` is on master but not in a tagged release yet (v2.2.1 lacks it),
# so build a pinned commit from source. Bump the rev to upgrade.
ARG HIMALAYA_REV=8196891f891cf9aa0fb83cd859e1c4c86fa5abdb
ARG ORTIE_VERSION=2.3.0
ARG ORTIE_SHA256=1b421f28cb2bc7e6acfe964df548d568ddbab359bc791c43e5c4f7b091910f10
ARG FASTMCP_VERSION=4.1.6

# ---------------------------------------------------------------------------
FROM rust:1-alpine AS himalaya
ARG HIMALAYA_REV
RUN apk add --no-cache build-base git perl
RUN cargo install --locked \
      --git https://github.com/pimalaya/himalaya.git --rev "${HIMALAYA_REV}" \
      --root /out himalaya \
 && /out/bin/himalaya --version

# ---------------------------------------------------------------------------
FROM alpine:3 AS ortie
ARG ORTIE_VERSION
ARG ORTIE_SHA256
RUN apk add --no-cache curl \
 && curl -sSfL -o /tmp/ortie.tgz \
      "https://github.com/pimalaya/ortie/releases/download/v${ORTIE_VERSION}/ortie.x86_64-linux.tgz" \
 && echo "${ORTIE_SHA256}  /tmp/ortie.tgz" | sha256sum -c - \
 && tar -xzf /tmp/ortie.tgz -C /usr/local/bin ortie \
 && /usr/local/bin/ortie --version

# ---------------------------------------------------------------------------
FROM python:3.13-slim
ARG FASTMCP_VERSION

RUN pip install --no-cache-dir "fastmcp==${FASTMCP_VERSION}" \
 && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin himalaya \
 && mkdir -p /data/ortie /data/oauth /config \
 && chown -R himalaya:himalaya /data

COPY --from=himalaya /out/bin/himalaya /usr/local/bin/himalaya
COPY --from=ortie /usr/local/bin/ortie /usr/local/bin/ortie
COPY server.py /app/server.py
COPY himalaya-login /usr/local/bin/himalaya-login
RUN chmod 0755 /usr/local/bin/himalaya-login

ENV HIMALAYA_CONFIG=/config/himalaya.toml \
    ORTIE_CONFIG=/config/ortie.toml \
    HOME=/data \
    PYTHONUNBUFFERED=1

USER himalaya
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "/app/server.py"]
