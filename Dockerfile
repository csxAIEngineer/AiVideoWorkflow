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

# Only what the site needs at runtime.
COPY deepseek_api ./deepseek_api
COPY icreat_api ./icreat_api
COPY pipeline ./pipeline
COPY end ./end
COPY agent ./agent
COPY task_queue ./task_queue
COPY front ./front
COPY cr_api ./cr_api
COPY skills/aivideo-work/SKILL.md ./skills/aivideo-work/SKILL.md
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/web_projects

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
