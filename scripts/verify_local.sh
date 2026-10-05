#!/bin/sh
# 无 Docker 环境下的本机验收：引擎测试 → 本地起服务 → HTTP 冒烟。
set -eu

ROOTDIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOTDIR"

PORT="${HOST_PORT:-8080}"
TMPDIR=$(mktemp -d)
trap 'kill $SERVER_PID 2>/dev/null || true; rm -rf "$TMPDIR"' EXIT INT TERM

echo "== [1/3] grammar engine unit tests =="
python3 -m unittest discover -s tests -v

echo "== [2/3] start local service on port ${PORT} =="
SEAL_DIR="$TMPDIR/seals" PORT="$PORT" HOST=127.0.0.1 \
  python3 app/server.py >"$TMPDIR/server.log" 2>&1 &
SERVER_PID=$!

echo "== [3/3] HTTP smoke: unique / ambiguous / non-consuming loop =="
BASE_URL="http://127.0.0.1:${PORT}" python3 scripts/smoke_http.py

echo "== LOCAL VERIFY PASSED =="
