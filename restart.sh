#!/bin/zsh
# AiVideo 本地服务重启脚本（End 后端 8787：页面 + RPC；AI 后端 8791）
# 用法:
#   ./restart.sh          一起重启两个后台服务（默认）
#   ./restart.sh stop     只停掉两个服务
#   ./restart.sh status   查看端口和健康状态
# 日志: logs/ai.log  logs/end.log
# Python: 优先仓库 venv（.venv-review / .venv，含 PG 模式所需 psycopg/argon2），
#         可用 AIVIDEO_PYTHON=/path/to/python 覆盖。
set -u

ROOT="${0:A:h}"
cd "$ROOT"

AI_PORT=8791
PAGE_PORT=8787
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# --- Python 选择 -----------------------------------------------------------
if [[ -n "${AIVIDEO_PYTHON:-}" ]]; then
  PYTHON="$AIVIDEO_PYTHON"
elif [[ -x "$ROOT/.venv-review/bin/python" ]]; then
  PYTHON="$ROOT/.venv-review/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

# PG 模式预检：.env 配了 AIVIDEO_PG_DSN 时，所选 Python 必须能导入账户系统依赖。
if grep -qE '^[[:space:]]*AIVIDEO_PG_DSN=' "$ROOT/.env" 2>/dev/null; then
  if ! "$PYTHON" -c 'import psycopg, psycopg_pool, argon2' 2>/dev/null; then
    echo "错误: .env 已配置 AIVIDEO_PG_DSN（PG 模式），但 $PYTHON 缺 psycopg/psycopg_pool/argon2-cffi。"
    echo "      安装: $PYTHON -m pip install 'psycopg[binary]' psycopg_pool argon2-cffi"
    echo "      或设 AIVIDEO_PYTHON 指向已装依赖的解释器；回滚 SQLite 则注释 .env 里的 DSN 行。"
    exit 1
  fi
fi

listeners() {
  lsof -nP -iTCP:$PAGE_PORT -iTCP:$AI_PORT -sTCP:LISTEN -t 2>/dev/null | sort -u
}

wait_ports_free() {
  for i in {1..10}; do
    [[ -z "$(listeners)" ]] && return 0
    sleep 0.3
  done
  return 1
}

stop_services() {
  local pids
  pids="$(listeners)"
  if [[ -n "$pids" ]]; then
    echo "停止旧进程: $pids"
    kill ${=pids} 2>/dev/null
    if ! wait_ports_free; then
      echo "端口未释放，强制结束: $(listeners)"
      kill -9 ${=$(listeners)} 2>/dev/null
      wait_ports_free || { echo "端口仍被占用，退出"; exit 1; }
    fi
  fi
  echo "端口 $PAGE_PORT/$AI_PORT 已空闲"
}

start_services() {
  echo "分支: $(git rev-parse --abbrev-ref HEAD 2>/dev/null)  提交: $(git log -1 --oneline 2>/dev/null)"
  echo "Python: $PYTHON"

  PYTHONUNBUFFERED=1 AI_HOST=127.0.0.1 AI_PORT=$AI_PORT AIVIDEO_AI_AUTOSTART=0 \
    nohup "$PYTHON" -m AI.server >> "$LOG_DIR/ai.log" 2>&1 &
  echo $! > "$LOG_DIR/ai.pid"
  echo "AI 后端已启动 pid=$(cat "$LOG_DIR/ai.pid")  日志: logs/ai.log"

  PYTHONUNBUFFERED=1 AIVIDEO_AI_URL="http://127.0.0.1:$AI_PORT" AIVIDEO_AI_AUTOSTART=0 \
    nohup "$PYTHON" -m end.server >> "$LOG_DIR/end.log" 2>&1 &
  echo $! > "$LOG_DIR/end.pid"
  echo "End 后端已启动 pid=$(cat "$LOG_DIR/end.pid")  页面+RPC: http://127.0.0.1:$PAGE_PORT  日志: logs/end.log"
}

health_check() {
  local page ai
  for i in {1..40}; do
    page=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "http://127.0.0.1:$PAGE_PORT/" || echo fail)
    ai=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 -X POST "http://127.0.0.1:$AI_PORT/rpc" \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}' || echo fail)
    if [[ "$page" == "200" && "$ai" == "200" ]]; then
      echo "End:  http://127.0.0.1:$PAGE_PORT  ($page)"
      echo "AI:   http://127.0.0.1:$AI_PORT/rpc  ($ai)"
      return 0
    fi
    sleep 0.4
  done
  echo "健康检查失败 page:$page ai:$ai"
  echo "--- logs/ai.log 末尾 ---"
  tail -n 15 "$LOG_DIR/ai.log" 2>/dev/null
  echo "--- logs/end.log 末尾 ---"
  tail -n 15 "$LOG_DIR/end.log" 2>/dev/null
  return 1
}

case "${1:-restart}" in
  stop)
    stop_services
    ;;
  status)
    lsof -nP -iTCP:$PAGE_PORT -iTCP:$AI_PORT -sTCP:LISTEN || echo "两个端口都没有进程在听"
    health_check
    ;;
  restart)
    stop_services
    start_services
    health_check || exit 1
    echo "重启完成。rendering 状态的项目启动时会自动续跑；gate/drilling 等待态不受影响。"
    ;;
  *)
    echo "用法: $0 [restart|stop|status]"
    exit 1
    ;;
esac