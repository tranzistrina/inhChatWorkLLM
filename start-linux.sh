#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python || ! -f users.json ]]; then
  echo "Сначала выполните: bash setup-linux.sh" >&2
  exit 1
fi

mkdir -p data
DS_PID=""
APP_PID=""

cleanup() {
  if [[ -n "$APP_PID" ]]; then
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  if [[ -n "$DS_PID" ]]; then
    kill "$DS_PID" 2>/dev/null || true
    wait "$DS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

vendor=".vendor/FreeDeepseekAPI"
if [[ "${DISABLE_DEEPSEEK:-0}" != "1" && -f "$vendor/package.json" && -f "$vendor/deepseek-auth.json" ]] && command -v node >/dev/null 2>&1; then
  echo "Запуск FreeDeepseekAPI на 127.0.0.1:9655"
  (cd "$vendor" && exec env NON_INTERACTIVE=1 node server.js) >> data/deepseek-proxy.log 2>&1 &
  DS_PID=$!
else
  echo "DeepSeek-прокси не запускается (нет авторизации/Node.js или DISABLE_DEEPSEEK=1)."
  echo "Для других OpenAI-compatible API это не мешает работе."
fi

echo "inhCHAT: http://127.0.0.1:6767"
.venv/bin/python run_server.py &
APP_PID=$!
wait "$APP_PID"
