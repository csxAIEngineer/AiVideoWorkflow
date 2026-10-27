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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Three processes share this image: front is static, end serves it, AI is the model process.
COPY AI/__init__.py AI/script.py AI/api.py AI/server.py AI/vision.py ./AI/
COPY AI/agent ./AI/agent
COPY AI/qwen_api ./AI/qwen_api
COPY AI/skills ./AI/skills
COPY end ./end
COPY front ./front
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/web_projects

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
