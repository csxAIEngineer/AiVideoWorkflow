#!/usr/bin/env bash
# 从开发机拉取 data/web_projects 调试日志。IP 不要写进脚本，稍后填配置即可。
# 在本机仓库根目录或任意位置运行均可。不要对生产机使用，除非你有意拉取生产数据。
#
# 先填写主机：
#   cp scripts/dev-host.env.example scripts/dev-host.env
#   编辑 AIVIDEO_DEV_HOST=ubuntu@<ip>
# 或：
#   export AIVIDEO_DEV_HOST=ubuntu@<ip>
#   ./scripts/pull-dev-debug.sh --project <id>
#
# 用法：
#   ./scripts/pull-dev-debug.sh --project ac4fb14b98e8
#   ./scripts/pull-dev-debug.sh --project ac4fb14b98e8 --project 2e60a5098932
#   ./scripts/pull-dev-debug.sh --latest 3
#   ./scripts/pull-dev-debug.sh --all-projects
#   ./scripts/pull-dev-debug.sh --project ac4fb14b98e8 --dry-run
#   ./scripts/pull-dev-debug.sh --docker --all-projects
#   ./scripts/pull-dev-debug.sh --project ac4fb14b98e8 --all
#
# 默认拉取（AIVIDEO_DEBUG_TRACE=1 时的调试产物，不含大媒体）：
#   - 项目 debug/：trace/*.json、*.prompts.jsonl、feed-attempt-*.txt、feed-debug.jsonl
#   - 项目根文本元数据：*.json *.md *.html *.log（spec / run_state / reviews 等）
#   - _内部_/ 与 history/ 下的文本稿和快照
#   - outputs/ 下的 render.log、concat_list.txt、字幕.ass 等日志/文本
#   - 仓库 data/ 根上的 feed-debug.jsonl、feed-attempt-*.txt（无项目目录时的回退日志）
#   - 仓库 logs/ai.log、logs/end.log（原生进程日志，若存在）
#   - 若走 docker：再尝试导出容器 aivideo / aivideo-ai 的尾部日志
# 默认排除：outputs/ 里的视频和图片，以及 mp4/mov/webm/mkv/png/jpg/webp/wav/mp3 等媒体。
# --all 则把整个项目目录都拉下来，包括视频和图片，体积会大很多。
#
# 本地默认写入：
#   data/dev_debug_pulls/<host>/<时间戳>/<project>/
# 不会覆盖本机正在用的 data/web_projects。
#
# 开启远端调试：在开发机 .env 里设 AIVIDEO_DEBUG_TRACE=1 后重启服务。本脚本不改配置。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

HOST=""
SSH_USER=""
SSH_PORT=""
SSH_KEY=""
REMOTE_DIR=""
REMOTE_DATA=""
DOCKER_MODE=""
DOCKER_CONTAINER=""
DOCKER_VOLUME=""
DEST_ROOT=""
DRY_RUN=0
PULL_MEDIA=0
LATEST_N=""
ALL_PROJECTS=0
PROJECTS=()
USE_SUDO=0
RSYNC_PATH="rsync"

usage() {
  cat <<'EOF'
用法: pull-dev-debug.sh [选项]

从开发机 SSH 拉取 web_projects 调试日志。必须指定主机，且必须指定一种项目范围。

主机（必填，任选一种，不要把 IP 写进脚本）：
  --host user@ip          SSH 目标。也可只写 IP，再配合 --user
  --user NAME             SSH 用户。只写了 IP 时默认 ubuntu
  --port N                SSH 端口，默认 22
  --identity PATH         SSH 私钥
  环境变量 / 配置文件：
    AIVIDEO_DEV_HOST 或 DEV_HOST
    DEV_SSH_USER  DEV_SSH_PORT  DEV_SSH_KEY
    配置文件（已存在的环境变量优先）：
      scripts/dev-host.env
      .env.dev
    示例：cp scripts/dev-host.env.example scripts/dev-host.env

远端路径：
  --remote-dir PATH       远端仓库根目录，默认 /home/ubuntu/aivideo
                          （与 deploy/deploy.sh 的 REMOTE_DIR 默认值一致）
  --remote-data PATH      远端 data 目录。不写则自动探测：
                          先看 <remote-dir>/data/web_projects（非空才用），
                          没有再查 docker 容器/volume 挂到 /app/data 的路径
  --docker                直接从 docker 的 /app/data 读
                          （compose 默认 volume：aivideo-data，
                          容器名 aivideo；读 volume 可能需要远端免密 sudo）
  --docker-container NAME 默认 aivideo
  --docker-volume NAME    默认 aivideo-data

项目范围（必填，三选一）：
  -p, --project ID        指定一个项目。可重复传入多个
  -n, --latest N          按 debug/（没有则用项目目录）修改时间取最近 N 个
  --all-projects          所有带 debug/ 的项目；与 --all 连用时包含全部项目

拉取范围：
  默认只拉调试产物和文本，不拉视频/图片，避免把巨大成片拉回本机。
  --all                   连同 outputs/ 里的视频和图片一起拉
  --dest PATH             本地根目录。默认
                          <仓库>/data/dev_debug_pulls/<host>/<时间戳>
  --dry-run               只打印将要复制的文件，不落盘

示例：
  ./scripts/pull-dev-debug.sh --host ubuntu@x.x.x.x --project ac4fb14b98e8
  ./scripts/pull-dev-debug.sh --all-projects
  ./scripts/pull-dev-debug.sh --latest 3 --dry-run
  ./scripts/pull-dev-debug.sh --docker --project ac4fb14b98e8
  ./scripts/pull-dev-debug.sh --project ac4fb14b98e8 --all

EOF
}

fail() {
  echo "错误: $*" >&2
  exit 1
}

# 只导入尚未设置的 KEY=VALUE，不执行命令替换。
load_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    line="${line#export }"
    case "$line" in
      *[=]*) ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$key" in
      *[!A-Za-z0-9_]*|'') continue ;;
    esac
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    if [ "${val#\"}" != "$val" ] && [ "${val%\"}" != "$val" ]; then
      val="${val#\"}"
      val="${val%\"}"
    elif [ "${val#\'}" != "$val" ] && [ "${val%\'}" != "$val" ]; then
      val="${val#\'}"
      val="${val%\'}"
    fi
    if eval "[ -z \"\${$key+x}\" ]"; then
      printf -v "$key" '%s' "$val"
      export "$key"
    fi
  done < "$file"
}

expand_path() {
  local p="$1"
  case "$p" in
    ~/*) printf '%s\n' "$HOME/${p#~/}" ;;
    *) printf '%s\n' "$p" ;;
  esac
}

# 本地文件名清洗：去掉路径分隔符与白名单外字符，防止写出 DEST_ROOT 之外。
sanitize_slug() {
  printf '%s' "$1" | tr '@:/' '___' | tr -cd 'A-Za-z0-9._-'
}

sanitize_host() {
  sanitize_slug "$1"
}

# 白名单与 store.py 的 _PROJECT_ID 对齐：拒绝控制字符、前导 -、通配符与路径段。
is_safe_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

load_env_file "$ROOT/scripts/dev-host.env"
load_env_file "$ROOT/.env.dev"

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --host)
      [ $# -ge 2 ] || fail "--host 需要参数"
      HOST="$2"
      shift 2
      ;;
    --user)
      [ $# -ge 2 ] || fail "--user 需要参数"
      SSH_USER="$2"
      shift 2
      ;;
    --port)
      [ $# -ge 2 ] || fail "--port 需要参数"
      SSH_PORT="$2"
      shift 2
      ;;
    --identity|--key)
      [ $# -ge 2 ] || fail "$1 需要参数"
      SSH_KEY="$2"
      shift 2
      ;;
    --remote-dir)
      [ $# -ge 2 ] || fail "--remote-dir 需要参数"
      REMOTE_DIR="$2"
      shift 2
      ;;
    --remote-data)
      [ $# -ge 2 ] || fail "--remote-data 需要参数"
      REMOTE_DATA="$2"
      shift 2
      ;;
    --docker)
      DOCKER_MODE=1
      shift
      ;;
    --docker-container)
      [ $# -ge 2 ] || fail "--docker-container 需要参数"
      DOCKER_CONTAINER="$2"
      DOCKER_MODE=1
      shift 2
      ;;
    --docker-volume)
      [ $# -ge 2 ] || fail "--docker-volume 需要参数"
      DOCKER_VOLUME="$2"
      DOCKER_MODE=1
      shift 2
      ;;
    -p|--project)
      [ $# -ge 2 ] || fail "--project 需要项目 id"
      PROJECTS+=("$2")
      shift 2
      ;;
    -n|--latest)
      [ $# -ge 2 ] || fail "--latest 需要一个正整数"
      LATEST_N="$2"
      shift 2
      ;;
    --all-projects)
      ALL_PROJECTS=1
      shift
      ;;
    --all)
      PULL_MEDIA=1
      shift
      ;;
    --dest)
      [ $# -ge 2 ] || fail "--dest 需要路径"
      DEST_ROOT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      fail "未知参数: $1  （见 --help）"
      ;;
  esac
done

[ -n "$HOST" ] || HOST="${AIVIDEO_DEV_HOST:-}"
[ -n "$HOST" ] || HOST="${DEV_HOST:-}"
[ -n "$HOST" ] || HOST="${SSH_HOST:-}"
[ -n "$SSH_USER" ] || SSH_USER="${DEV_SSH_USER:-}"
[ -n "$SSH_USER" ] || SSH_USER="${AIVIDEO_DEV_USER:-}"
[ -n "$SSH_PORT" ] || SSH_PORT="${DEV_SSH_PORT:-}"
[ -n "$SSH_KEY" ] || SSH_KEY="${DEV_SSH_KEY:-}"
[ -n "$SSH_KEY" ] || SSH_KEY="${AIVIDEO_DEV_SSH_KEY:-}"
[ -n "$REMOTE_DIR" ] || REMOTE_DIR="${AIVIDEO_DEV_REMOTE_DIR:-/home/ubuntu/aivideo}"
[ -n "$REMOTE_DATA" ] || REMOTE_DATA="${AIVIDEO_DEV_REMOTE_DATA:-}"
if [ -z "$DOCKER_MODE" ] && [ "${AIVIDEO_DEV_DOCKER:-}" = "1" ]; then
  DOCKER_MODE=1
fi
[ -n "$DOCKER_CONTAINER" ] || DOCKER_CONTAINER="${AIVIDEO_DEV_DOCKER_CONTAINER:-aivideo}"
[ -n "$DOCKER_VOLUME" ] || DOCKER_VOLUME="${AIVIDEO_DEV_DOCKER_VOLUME:-aivideo-data}"

if [ -z "$HOST" ]; then
  cat >&2 <<'EOF'
错误: 未设置开发机地址，拒绝继续。

请任选一种方式提供主机（不要把 IP 写进脚本）：
  1) cp scripts/dev-host.env.example scripts/dev-host.env
     编辑 AIVIDEO_DEV_HOST=ubuntu@<ip>
  2) ./scripts/pull-dev-debug.sh --host ubuntu@<ip> ...
  3) export AIVIDEO_DEV_HOST=ubuntu@<ip>   # 或 DEV_HOST
EOF
  exit 1
fi

case "$HOST" in
  *@*)
    if [ -z "$SSH_USER" ]; then
      SSH_USER="${HOST%@*}"
    fi
    SSH_TARGET="$HOST"
    HOST_ONLY="${HOST#*@}"
    ;;
  *)
    [ -n "$SSH_USER" ] || SSH_USER="ubuntu"
    SSH_TARGET="$SSH_USER@$HOST"
    HOST_ONLY="$HOST"
    ;;
esac
[ -n "$SSH_PORT" ] || SSH_PORT=22
# 端口必须纯数字（含配置文件/环境变量来源），防止被拆成额外 ssh 选项。
case "$SSH_PORT" in
  ''|*[!0-9]*) fail "SSH 端口必须是纯数字: $SSH_PORT" ;;
esac

selector_count=0
[ "${#PROJECTS[@]}" -gt 0 ] && selector_count=$((selector_count + 1))
[ -n "$LATEST_N" ] && selector_count=$((selector_count + 1))
[ "$ALL_PROJECTS" -eq 1 ] && selector_count=$((selector_count + 1))
if [ "$selector_count" -ne 1 ]; then
  fail "请三选一：--project <id>、--latest N、或 --all-projects。见 --help"
fi
if [ -n "$LATEST_N" ]; then
  case "$LATEST_N" in
    *[!0-9]*|'') fail "--latest 需要正整数" ;;
    0) fail "--latest 需要正整数" ;;
  esac
fi
for id in "${PROJECTS[@]+"${PROJECTS[@]}"}"; do
  is_safe_id "$id" || fail "非法项目 id: $id"
done

if [ -n "$SSH_KEY" ]; then
  SSH_KEY="$(expand_path "$SSH_KEY")"
  [ -f "$SSH_KEY" ] || fail "找不到 SSH 私钥: $SSH_KEY"
fi

# 指定私钥时才加 IdentitiesOnly，免得 ssh-agent / 默认钥匙用不了。
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -p "$SSH_PORT")
# rsync -e 按空白拆分 RSH 字符串：含空格的路径必须 printf %q 转义，
# 否则会被分词成额外 ssh 选项（选项注入）或打错目标。
RSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -p $(printf '%q' "$SSH_PORT")"
if [ -n "$SSH_KEY" ]; then
  SSH_OPTS+=(-o IdentitiesOnly=yes -i "$SSH_KEY")
  RSH="$RSH -o IdentitiesOnly=yes -i $(printf '%q' "$SSH_KEY")"
fi

ssh_run() {
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

# 读 docker volume 时，远端 listing 也走 sudo -n。
ssh_remote_bash() {
  if [ "$USE_SUDO" -eq 1 ]; then
    ssh_run "sudo -n bash -s"
  else
    ssh_run "bash -s"
  fi
}

# 远端探测 data 目录。优先仓库内非空的 data/web_projects，否则 docker 挂载。
# 输出两行：第一行路径，第二行 rsync-path（rsync 或 sudo rsync）。
resolve_remote_data() {
  local quoted_dir quoted_data quoted_container quoted_volume force_docker
  quoted_dir=$(printf '%q' "$REMOTE_DIR")
  quoted_data=$(printf '%q' "$REMOTE_DATA")
  quoted_container=$(printf '%q' "$DOCKER_CONTAINER")
  quoted_volume=$(printf '%q' "$DOCKER_VOLUME")
  force_docker=0
  [ -n "$DOCKER_MODE" ] && force_docker=1
  ssh_run "REMOTE_DIR=$quoted_dir REMOTE_DATA=$quoted_data CONTAINER=$quoted_container VOLUME=$quoted_volume FORCE_DOCKER=$force_docker bash -s" <<'EOS'
set -e
docker_bin() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo docker
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    echo "sudo -n docker"
    return 0
  fi
  return 1
}

dir_exists() {
  local path="$1"
  if [ -d "$path" ]; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n test -d "$path"; then
    return 0
  fi
  return 1
}

dir_has_entries() {
  local path="$1" out
  if [ -d "$path" ] && [ -r "$path" ]; then
    out=$(ls -A "$path" 2>/dev/null || true)
    [ -n "$out" ]
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    out=$(sudo -n ls -A "$path" 2>/dev/null || true)
    [ -n "$out" ]
    return
  fi
  return 1
}

readable_by_user() {
  [ -d "$1" ] && [ -r "$1" ]
}

emit_path() {
  local path="$1"
  echo "$path"
  if readable_by_user "$path/web_projects" || readable_by_user "$path"; then
    echo "rsync"
  else
    echo "sudo rsync"
  fi
}

docker_src() {
  local dcmd src
  dcmd=$(docker_bin) || return 1
  src=$($dcmd inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$CONTAINER" 2>/dev/null || true)
  if [ -z "$src" ]; then
    src=$($dcmd inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' aivideo-ai 2>/dev/null || true)
  fi
  if [ -z "$src" ]; then
    src=$($dcmd volume inspect -f '{{.Mountpoint}}' "$VOLUME" 2>/dev/null || true)
  fi
  [ -n "$src" ] || return 1
  printf '%s\n' "$src"
}

if [ -n "$REMOTE_DATA" ]; then
  if dir_exists "$REMOTE_DATA/web_projects" || dir_exists "$REMOTE_DATA"; then
    emit_path "$REMOTE_DATA"
    exit 0
  fi
  echo "REMOTE_DATA_MISSING:$REMOTE_DATA" >&2
  exit 2
fi

if [ "$FORCE_DOCKER" = "1" ]; then
  src=$(docker_src || true)
  if [ -n "$src" ]; then
    emit_path "$src"
    exit 0
  fi
  echo "DOCKER_DATA_MISSING:container=$CONTAINER volume=$VOLUME" >&2
  exit 2
fi

# 仓库内 data/ 若是空目录，继续去查 docker volume，避免误用空的检出目录。
if dir_has_entries "$REMOTE_DIR/data/web_projects"; then
  emit_path "$REMOTE_DIR/data"
  exit 0
fi

src=$(docker_src || true)
if [ -n "$src" ]; then
  emit_path "$src"
  exit 0
fi

if dir_exists "$REMOTE_DIR/data/web_projects"; then
  emit_path "$REMOTE_DIR/data"
  exit 0
fi

echo "DATA_NOT_FOUND:tried=$REMOTE_DIR/data and docker $CONTAINER/$VOLUME" >&2
exit 2
EOS
}

echo "==> SSH $SSH_TARGET  仓库 $REMOTE_DIR"
resolved="$(resolve_remote_data)" || {
  fail "找不到远端 data/web_projects。若用 docker compose 部署，请加 --docker；或用 --remote-data 指定实际路径。"
}
REMOTE_DATA="$(printf '%s\n' "$resolved" | sed -n '1p')"
RSYNC_PATH="$(printf '%s\n' "$resolved" | sed -n '2p')"
[ -n "$REMOTE_DATA" ] || fail "未能解析远端 data 路径"
[ -n "$RSYNC_PATH" ] || RSYNC_PATH="rsync"
case "$RSYNC_PATH" in
  sudo*) USE_SUDO=1 ;;
  *) USE_SUDO=0 ;;
esac
echo "==> 远端 data: $REMOTE_DATA  （rsync-path: $RSYNC_PATH）"

list_remote_projects() {
  local mode="$1"
  local limit="${2:-0}"
  # 变量写进 heredoc 再交给远端 bash，避免 sudo -n 丢掉环境变量。
  ssh_remote_bash <<EOF
set -e
REMOTE_DATA=$(printf '%q' "$REMOTE_DATA")
MODE=$(printf '%q' "$mode")
LIMIT=$(printf '%q' "$limit")
cd "\$REMOTE_DATA/web_projects" || { echo "NO_WEB_PROJECTS:\$REMOTE_DATA/web_projects" >&2; exit 2; }
# 按 debug/（没有则用项目目录）mtime 从新到旧列出。
# 不用 head 截断管道，避免 SIGPIPE。
count=0
# shellcheck disable=SC2012
ls -1 | while IFS= read -r d; do
  [ -d "\$d" ] || continue
  case "\$d" in
    *[\\\\/]*) continue ;;
  esac
  if [ "\$MODE" = "debug" ] && [ ! -d "\$d/debug" ]; then
    continue
  fi
  stamp=\$(stat -c %Y "\$d/debug" 2>/dev/null || true)
  if [ -z "\$stamp" ]; then
    stamp=\$(stat -c %Y "\$d" 2>/dev/null || true)
  fi
  [ -n "\$stamp" ] || continue
  printf '%s\\t%s\\n' "\$stamp" "\$d"
done | sort -nr | while IFS= read -r line; do
  id=\${line#*	}
  [ -n "\$id" ] || continue
  count=\$((count + 1))
  if [ "\$LIMIT" -gt 0 ] && [ "\$count" -gt "\$LIMIT" ]; then
    break
  fi
  printf '%s\\n' "\$id"
done
EOF
}

if [ "$ALL_PROJECTS" -eq 1 ]; then
  if [ "$PULL_MEDIA" -eq 1 ]; then
    map_mode="all"
  else
    map_mode="debug"
  fi
  while IFS= read -r id || [ -n "$id" ]; do
    [ -n "$id" ] || continue
    is_safe_id "$id" || continue
    PROJECTS+=("$id")
  done <<EOF
$(list_remote_projects "$map_mode" 0)
EOF
elif [ -n "$LATEST_N" ]; then
  if [ "$PULL_MEDIA" -eq 1 ]; then
    map_mode="all"
  else
    map_mode="debug"
  fi
  while IFS= read -r id || [ -n "$id" ]; do
    [ -n "$id" ] || continue
    is_safe_id "$id" || continue
    PROJECTS+=("$id")
  done <<EOF
$(list_remote_projects "$map_mode" "$LATEST_N")
EOF
fi

if [ "${#PROJECTS[@]}" -eq 0 ]; then
  fail "远端 $REMOTE_DATA/web_projects 没有可拉取的项目（默认会跳过没有 debug/ 的目录）。"
fi

HOST_SLUG="$(sanitize_host "$HOST_ONLY")"
[ -n "$HOST_SLUG" ] || HOST_SLUG="dev"
STAMP="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)"
if [ -z "$DEST_ROOT" ]; then
  DEST_ROOT="$ROOT/data/dev_debug_pulls/$HOST_SLUG/$STAMP"
else
  DEST_ROOT="$(expand_path "$DEST_ROOT")"
fi

echo "==> 项目: ${PROJECTS[*]}"
echo "==> 本地目标: $DEST_ROOT"
if [ "$PULL_MEDIA" -eq 1 ]; then
  echo "==> 范围: 整个项目（含视频和图片）"
else
  echo "==> 范围: 调试产物和文本（不含媒体；需要媒体时加 --all）"
fi
if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> dry-run，不会写入本地文件"
fi

RSYNC_FLAGS=(-az -e "$RSH" --rsync-path="$RSYNC_PATH")
if [ "$DRY_RUN" -eq 1 ]; then
  RSYNC_FLAGS+=(-n -v)
else
  mkdir -p "$DEST_ROOT"
fi

# 在远端列出一个项目里要拉的相对路径（不含媒体）。
remote_file_list() {
  local id="$1"
  ssh_remote_bash <<EOF
set -e
REMOTE_DATA=$(printf '%q' "$REMOTE_DATA")
ID=$(printf '%q' "$id")
base="\$REMOTE_DATA/web_projects/\$ID"
if [ ! -d "\$base" ]; then
  echo "PROJECT_MISSING:\$ID" >&2
  exit 3
fi
cd "\$base"
# debug/（含 trace json、prompts 旁路、feed-attempt、feed-debug）
if [ -d ./debug ]; then
  find ./debug \\( -type f -o -type d \\) -print
fi
# 项目根文本元数据
find . -maxdepth 1 -type f \\( -name '*.json' -o -name '*.md' -o -name '*.html' -o -name '*.log' -o -name '*.txt' \\) -print
# 内部稿和历史快照（文本为主）
if [ -d ./_内部_ ]; then
  find ./_内部_ \\( -type f -o -type d \\) -print
fi
if [ -d ./history ]; then
  find ./history \\( -type f -o -type d \\) -print
fi
# 渲染日志等，不要 clips/images/成片
if [ -d ./outputs ]; then
  echo ./outputs
  find ./outputs -maxdepth 1 -type f \\( -name '*.log' -o -name '*.txt' -o -name '*.ass' \\) -print
fi
EOF
}

pull_project() {
  local id="$1"
  local dest="$DEST_ROOT/$id"
  local src="$SSH_TARGET:$REMOTE_DATA/web_projects/$id/"
  if [ "$PULL_MEDIA" -eq 1 ]; then
    echo "-- 拉取 $id（全部文件）"
    if [ "$DRY_RUN" -eq 0 ]; then
      mkdir -p "$dest"
    fi
    rsync "${RSYNC_FLAGS[@]}" "$src" "$dest/"
    return 0
  fi
  echo "-- 拉取 $id（调试/文本）"
  local list
  if ! list="$(remote_file_list "$id")"; then
    fail "项目不存在或无法列出文件: $id"
  fi
  if ! printf '%s\n' "$list" | grep -q './debug'; then
    echo "   警告: $id 没有 debug/（可能尚未开启 AIVIDEO_DEBUG_TRACE=1，或还没跑过写作）"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "$list" | sed "s#^\\./#   $id/#"
  else
    mkdir -p "$dest"
  fi
  printf '%s\n' "$list" | rsync "${RSYNC_FLAGS[@]}" --files-from=- "$src" "$dest/"
}

for id in "${PROJECTS[@]}"; do
  pull_project "$id"
done

# 仓库级日志：data 根上的 feed 回退日志，以及 checkout 的 logs/。
pull_repo_extras() {
  local dest="$DEST_ROOT/_repo"
  echo "-- 拉取仓库级调试日志"
  local data_flags=("${RSYNC_FLAGS[@]}" --include='feed-debug.jsonl' --include='feed-attempt-*.txt' --exclude='*')
  if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$dest/data" "$dest/logs"
  fi
  rsync "${data_flags[@]}" "$SSH_TARGET:$REMOTE_DATA/" "$dest/data/" || true
  local log_flags=("${RSYNC_FLAGS[@]}" --include='ai.log' --include='end.log' --exclude='*')
  rsync "${log_flags[@]}" "$SSH_TARGET:$REMOTE_DIR/logs/" "$dest/logs/" 2>/dev/null || true
}

pull_docker_logs() {
  local dest="$DEST_ROOT/_repo/docker"
  # 容器名来自配置/命令行，先清洗再拼本地文件名，防止 ../ 写出 DEST_ROOT。
  local container_slug
  container_slug="$(sanitize_slug "${DOCKER_CONTAINER:-aivideo}")"
  [ -n "$container_slug" ] || container_slug="aivideo"
  echo "-- 尝试导出 docker 容器尾部日志"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "   dry-run: docker logs --tail 5000 $DOCKER_CONTAINER 与 aivideo-ai"
    return 0
  fi
  mkdir -p "$dest"
  ssh_run "CONTAINER=$(printf '%q' "$DOCKER_CONTAINER") bash -s" <<'EOS' >"$dest/$container_slug.log" 2>"$dest/$container_slug.err" || true
if docker logs --tail 5000 "$CONTAINER" 2>/dev/null; then
  exit 0
fi
if sudo -n docker logs --tail 5000 "$CONTAINER" 2>/dev/null; then
  exit 0
fi
exit 1
EOS
  ssh_run "bash -s" <<'EOS' >"$dest/aivideo-ai.log" 2>"$dest/aivideo-ai.err" || true
if docker logs --tail 5000 aivideo-ai 2>/dev/null; then
  exit 0
fi
if sudo -n docker logs --tail 5000 aivideo-ai 2>/dev/null; then
  exit 0
fi
exit 1
EOS
  for f in "$dest"/*.err; do
    [ -f "$f" ] || continue
    if [ ! -s "$f" ]; then
      rm -f "$f"
    fi
  done
}

pull_repo_extras
if [ -n "$DOCKER_MODE" ]; then
  pull_docker_logs || true
else
  if ssh_run "command -v docker >/dev/null 2>&1 || sudo -n command -v docker >/dev/null 2>&1" 2>/dev/null; then
    pull_docker_logs || true
  fi
fi

if [ "$DRY_RUN" -eq 0 ]; then
  {
    echo "pulled_at_asia_shanghai: $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "ssh_target: $SSH_TARGET"
    echo "remote_dir: $REMOTE_DIR"
    echo "remote_data: $REMOTE_DATA"
    echo "rsync_path: $RSYNC_PATH"
    echo "docker_mode: ${DOCKER_MODE:-0}"
    echo "include_media: $PULL_MEDIA"
    echo "projects:"
    for id in "${PROJECTS[@]}"; do
      echo "  - $id"
    done
    echo
    echo "default includes: debug/ (trace json, prompts jsonl, feed-attempt, feed-debug),"
    echo "  project metadata json/md/html, _内部_/, history/, outputs/*.log|txt|ass,"
    echo "  repo data/feed-*, repo logs/ai.log end.log, optional docker logs"
    echo "default excludes: videos and images under outputs/ and common media suffixes"
  } > "$DEST_ROOT/MANIFEST.txt"
  echo "==> 完成。清单: $DEST_ROOT/MANIFEST.txt"
else
  echo "==> dry-run 结束。真正拉取时会写入 $DEST_ROOT"
fi
