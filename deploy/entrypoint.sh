#!/bin/sh
set -eu

mkdir -p /app/data/web_projects

if [ "$#" -eq 0 ]; then
  set -- python -m end.server
fi

exec "$@"
