#!/usr/bin/env bash
# Deploy AiVideo to the Hong Kong host.
# Usage:
#   ./deploy/deploy.sh
#   SSH_HOST=ubuntu@43.132.225.71 SSH_KEY=~/.ssh/freqtrade.pem ./deploy/deploy.sh
#
# Rendering keys in project .env:
#   CR_API_KEY=...       MiniMax H3 video
#   ICREAT_API_KEY=...   GPT Image 2 stills
# Redeploy so the container picks up the env.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_HOST="${SSH_HOST:-ubuntu@43.132.225.71}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/freqtrade.pem}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/aivideo}"
PLATFORM="${PLATFORM:-linux/amd64}"

ssh_cmd=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$SSH_HOST")
rsync_ssh="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes"

echo "==> build $PLATFORM image locally (server RAM is tight)"
docker buildx build --platform "$PLATFORM" -t aivideo:latest --load "$ROOT"

echo "==> sync to $SSH_HOST:$REMOTE_DIR"
"${ssh_cmd[@]}" "mkdir -p '$REMOTE_DIR'"
rsync -az --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '.cursor/' \
  --exclude '.comfyui-desktop-2' \
  --exclude 'data/' \
  --exclude '__pycache__/' \
  --exclude '.DS_Store' \
  --exclude 'skills/' \
  --exclude 'studio/' \
  --exclude 'input/' \
  --exclude 'workflows/' \
  -e "$rsync_ssh" \
  "$ROOT/" "$SSH_HOST:$REMOTE_DIR/"

if [[ -f "$ROOT/.env" ]]; then
  echo "==> upload .env"
  scp -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
    "$ROOT/.env" "$SSH_HOST:$REMOTE_DIR/.env"
else
  echo "WARNING: local .env missing; create $REMOTE_DIR/.env on the server"
fi

echo "==> load image on server"
docker save aivideo:latest | gzip -1 | "${ssh_cmd[@]}" 'gunzip | sudo docker load'

echo "==> open firewall port 8787 if ufw is active"
"${ssh_cmd[@]}" 'sudo ufw status | grep -q inactive || sudo ufw allow 8787/tcp comment aivideo || true'

echo "==> docker compose up"
"${ssh_cmd[@]}" "cd '$REMOTE_DIR' && sudo docker compose up -d --no-build && sudo docker compose ps && sleep 2 && curl -fsS -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:8787/"

if grep -q '^ICREAT_API_KEY=.\+' "$ROOT/.env" 2>/dev/null; then
  echo "==> ICREAT_API_KEY is set"
else
  echo "==> WARNING: ICREAT_API_KEY is empty. iCreat 生图会失败。"
fi
if grep -q '^CR_API_KEY=.\+' "$ROOT/.env" 2>/dev/null; then
  echo "==> CR_API_KEY is set"
else
  echo "==> WARNING: CR_API_KEY is empty. MiniMax H3 出镜会失败。"
fi

echo "==> done"
echo "Open http://43.132.225.71:8787"
echo "If the page times out from the public internet, also open TCP 8787 in the Tencent Cloud security group."
