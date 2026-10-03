#!/bin/sh
set -eu

mkdir -p /app/data/web_projects

exec python -m web.server
