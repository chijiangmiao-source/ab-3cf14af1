#!/bin/sh
# 一次性验收：镜像构建 → 启动服务（健康等待）→ 引擎测试 + HTTP 冒烟。
# 名为 verify 的 Compose 服务执行后退出，并把测试/冒烟的状态码透传。
set -eu

HOST_PORT="${HOST_PORT:-8080}"
export HOST_PORT

COMPOSE=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "docker compose / docker-compose not found" >&2
  exit 2
fi

cleanup() {
  $COMPOSE --profile verify down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "== [build] grammar engine image =="
$COMPOSE --profile verify build

echo "== [up] grammar service on host port ${HOST_PORT} =="
$COMPOSE up -d grammar

echo "== [verify] unit tests + HTTP smoke (one-shot, exit code propagated) =="
set +e
$COMPOSE run --rm verify
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
  echo "== VERIFY PASSED =="
else
  echo "== VERIFY FAILED (exit code $rc) ==" >&2
fi
exit "$rc"
