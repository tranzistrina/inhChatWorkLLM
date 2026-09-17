#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p data

if [ ! -f .vendor/FreeDeepseekAPI/package.json ]; then
  echo 'FreeDeepseekAPI не найден. Выполните ./setup.sh'
  exit 1
fi

cleanup(){
  [ -n "${DS_PID:-}" ] && kill "$DS_PID" 2>/dev/null || true
  [ -n "${FLASK_PID:-}" ] && kill "$FLASK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ -f .vendor/FreeDeepseekAPI/deepseek-auth.json ]; then
  echo 'Запускаю FreeDeepseekAPI на :9655…'
  (cd .vendor/FreeDeepseekAPI && NON_INTERACTIVE=1 npm start >> ../../data/deepseek-proxy.log 2>&1) & DS_PID=$!
else
  echo 'DeepSeek auth ещё не настроен. inhCHAT всё равно запустится.'
  echo 'В интерфейсе открой Настройки → DeepSeek → Войти.'
fi

sleep 2
python app.py & FLASK_PID=$!
echo 'inhCHAT: http://127.0.0.1:698'
wait "$FLASK_PID"
