#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo 'Нужен Python 3'; exit 1; }
command -v node >/dev/null || { echo 'Нужен Node.js 18+'; exit 1; }
command -v npm >/dev/null || { echo 'Нужен npm'; exit 1; }

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

mkdir -p .vendor
if [ ! -d .vendor/FreeDeepseekAPI/.git ]; then
  git clone https://github.com/ForgetMeAI/FreeDeepseekAPI.git .vendor/FreeDeepseekAPI
fi
(cd .vendor/FreeDeepseekAPI && npm install)

if [ ! -f .env ]; then
  APP_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > .env <<EOF
APP_SECRET_KEY=$APP_SECRET_KEY
PORT=698
DEEPSEEK_BASE_URL=http://127.0.0.1:9655/v1
EOF
fi
mkdir -p data workspace
chmod 700 data workspace
chmod +x setup.sh start.sh

echo ''
echo 'inhCHAT установлен.'
echo '1) source .venv/bin/activate'
echo '2) ./start.sh'
echo '3) открой http://127.0.0.1:698'
