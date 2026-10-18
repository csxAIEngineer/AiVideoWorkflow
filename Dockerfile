FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8787

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Three processes share this image: front is static, end serves it, AI is the model process.
COPY AI/__init__.py AI/script.py AI/api.py AI/server.py AI/vision.py ./AI/
COPY AI/agent ./AI/agent
COPY AI/cr_api ./AI/cr_api
COPY AI/deepseek_api ./AI/deepseek_api
COPY AI/icreat_api ./AI/icreat_api
COPY AI/minimax_api ./AI/minimax_api
COPY AI/skills/registry.py ./AI/skills/registry.py
COPY AI/skills/apple-product ./AI/skills/apple-product
COPY AI/skills/luxury-suv ./AI/skills/luxury-suv
COPY end ./end
COPY front ./front
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/web_projects

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
