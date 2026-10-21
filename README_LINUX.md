# inhCHAT: запуск на Linux

Эта инструкция относится к ветке `linux`. Приложение работает локально на `127.0.0.1:6767`. Не требуются Docker, systemd или `sudo` для самого приложения.

## Подготовка

Нужны Linux, Git и Python **3.10+** с модулем `venv`. Для Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Для Fedora: `sudo dnf install git python3 python3-pip`. Для Arch: `sudo pacman -S git python`.

Для DeepSeek-прокси дополнительно потребуются Node.js **18+** и npm. Для **браузерной авторизации** через Chromium/Chrome нужна версия Node.js **22+**, установленный браузер и графический сеанс.

## Установка и запуск

```bash
git clone --branch linux https://github.com/tranzistrina/inhChatWorkLLM.git
cd inhChatWorkLLM
bash setup-linux.sh
bash start-linux.sh
```

Откройте [http://127.0.0.1:6767](http://127.0.0.1:6767).

При первом запуске `setup-linux.sh` создаёт `.venv/`, `.env`, `users.json`, `data/` и `workspace/`. Пароль администратора генерируется случайно и **один раз показывается в терминале** при первом создании `users.json`. Логин `admin`. Если пароль потерян, откройте локальный `users.json` и измените его; новая запись применяется после перезапуска приложения. Повторный запуск установщика не перезаписывает `.env` и `users.json`.

Если DeepSeek не нужен или Node.js отсутствует, можно выполнить:

```bash
SKIP_DEEPSEEK=1 bash setup-linux.sh
bash start-linux.sh
```

Тогда добавьте свой OpenAI-compatible API в настройках приложения.

## DeepSeek в Linux с графическим окружением

После установки Node.js 22+, npm и Chromium/Chrome:

```bash
cd .vendor/FreeDeepseekAPI
CHROME_PATH="$(command -v chromium || command -v chromium-browser || command -v google-chrome)" npm run auth
cd ../..
bash start-linux.sh
```

В меню авторизации выберите вход, откройте DeepSeek в браузере и выполните указания скрипта. Авторизация создаст `.vendor/FreeDeepseekAPI/deepseek-auth.json`. При следующем запуске `start-linux.sh` прокси будет автоматически стартовать на `127.0.0.1:9655`. Для проверки auth:

```bash
(cd .vendor/FreeDeepseekAPI && npm run doctor -- --offline)
```

**Важно:** файл `deepseek-auth.json` предоставляет доступ к web-сессии, поэтому не выкладывайте его в Git и храните с правами `600`. Каталог `.vendor/FreeDeepseekAPI/` игнорируется Git.

## VPS / сервер без графической среды

Войдите в DeepSeek через `npm run auth` на компьютере с графическим окружением, затем безопасно перенесите `deepseek-auth.json` на сервер в каталог `.vendor/FreeDeepseekAPI/` и выполните:

```bash
chmod 600 .vendor/FreeDeepseekAPI/deepseek-auth.json
bash start-linux.sh
```

На VPS достаточно Node.js 18+ для работы самого прокси. Оба сервиса слушают только локальный интерфейс. Для удалённого использования безопаснее SSH-туннель, чем открывать Flask в интернет: в проекте есть пользовательские данные и ключи API.

## Проверки и диагностика

```bash
bash -n setup-linux.sh start-linux.sh
.venv/bin/python -m unittest discover -s tests
curl -fsS http://127.0.0.1:6767/api/me
tail -n 50 data/deepseek-proxy.log
```

- `python3 -m venv` сообщает `ensurepip is not available`: установите `python3-venv` для вашей версии Python.
- Для входа в DeepSeek не найден Chromium: проверьте `command -v chromium` или укажите `CHROME_PATH`.
- DeepSeek не стартует: убедитесь в наличии auth-файла и выполните `npm run doctor -- --offline` в каталоге прокси.
- Порт занят: проверьте `ss -ltnp | grep ':6767'`. Адрес inhCHAT задан локальным `run_server.py`; менять настройки стоит только с пониманием безопасности.

Остановка: `Ctrl+C` в терминале запуска. При этом скрипт завершает оба запущенных им процесса.

### Обновление ветки

```bash
git switch linux
git pull --ff-only origin linux
bash setup-linux.sh
bash start-linux.sh
```

Существующие `users.json`, `.env`, SQLite и файлы `workspace/` при обновлении сохраняются.
