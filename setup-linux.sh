#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Этот установщик предназначен для Linux." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3.10+ (python3)." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Нужен Python 3.10 или новее." >&2
  exit 1
fi

if ! python3 -m venv .venv; then
  echo "Не удалось создать venv. На Debian/Ubuntu установите: sudo apt install python3-venv python3-pip" >&2
  exit 1
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  secret="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > .env <<EOF
APP_SECRET_KEY=$secret
PORT=6767
DEEPSEEK_BASE_URL=http://127.0.0.1:9655/v1
EOF
  echo "Создан .env (случайный APP_SECRET_KEY)."
else
  echo "Существующий .env сохранён."
fi

if [[ ! -f users.json ]]; then
  admin_password="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(18))')"
  printf '[{"username":"admin","password":"%s"}]\n' "$admin_password" > users.json
  echo "Создан users.json."
  echo "Логин: admin"
  echo "Пароль: $admin_password"
  echo "Сохраните пароль: при повторной установке он не выводится."
else
  echo "Существующий users.json сохранён."
fi

mkdir -p data workspace
chmod 700 data workspace
chmod 600 .env users.json

if [[ "${SKIP_DEEPSEEK:-0}" == "1" ]]; then
  echo "Установка FreeDeepseekAPI пропущена (SKIP_DEEPSEEK=1)."
elif command -v git >/dev/null 2>&1 && command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)'; then
    vendor=".vendor/FreeDeepseekAPI"
    if [[ ! -f "$vendor/package.json" ]]; then
      if [[ -e "$vendor" ]]; then
        echo "Каталог $vendor уже существует, но package.json отсутствует. Проверьте его вручную." >&2
        exit 1
      fi
      mkdir -p .vendor
      git clone --depth 1 https://github.com/ForgetMeAI/FreeDeepseekAPI.git "$vendor"
    fi
    (cd "$vendor" && npm install --ignore-scripts --no-audit --no-fund)
    echo "FreeDeepseekAPI подготовлен."
    if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
      echo "Для браузерной авторизации DeepSeek потребуется Node.js 22+."
    fi
  else
    echo "DeepSeek пропущен: для прокси требуется Node.js 18+, для входа через браузер 22+."
  fi
else
  echo "DeepSeek пропущен: не найдены git, node или npm. Обычные OpenAI-compatible API доступны."
fi

echo
echo "Linux-установка завершена."
echo "Запуск: bash start-linux.sh"
echo "Адрес: http://127.0.0.1:6767"
echo "Инструкция: README_LINUX.md"
