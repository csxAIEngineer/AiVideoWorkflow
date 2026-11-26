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

# Root-level shared constants (process-split neutral: end/AI/Agents all import it).
COPY product_names.py locks.py storyboard_contract.py ./

# Front is static, End serves it, Agents is the JSON-RPC process. AI stays on disk as its backend.
COPY AI/__init__.py AI/script.py AI/api.py AI/server.py AI/vision.py ./AI/
COPY AI/agent ./AI/agent
COPY AI/qwen_api ./AI/qwen_api
COPY AI/minimax_api ./AI/minimax_api
COPY AI/skills ./AI/skills
COPY Agents ./Agents
COPY end ./end
COPY front ./front
# Only the video catalog ships in the image. config/qwen.json (env=test) must
# not override QWEN_BASE_URL / key-var selection in container deployments.
COPY config/video_catalog.json ./config/
COPY deploy/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/web_projects

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
