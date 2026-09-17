# inhCHAT

Локальный веб-клиент для LLM с интерфейсом в стиле современного AI-чата, обычным API-режимом и автономным **«Режимом работы»**.

## Что уже есть

- Flask backend на `127.0.0.1:698`.
- HTML/CSS/JS без тяжёлого frontend-фреймворка.
- Локальные аккаунты inhCHAT: регистрация, вход и выход прямо из клиента.
- История чатов в SQLite.
- Подключение произвольных OpenAI-compatible API из окна настроек: URL, модель и ключ.
- FreeDeepseekAPI автоматически устанавливается в `.vendor/`, пользователю не требуется отдельно скачивать его репозиторий.
- Кнопка входа в DeepSeek из настроек inhCHAT: сервер запускает авторизацию FreeDeepseekAPI, которая открывает отдельный Chrome-профиль.
- Обычный режим чата.
- Автономный «Режим работы»: LLM получает инструменты и может самостоятельно выполнять несколько шагов, использовать калькулятор и безопасное файловое пространство `workspace/`, проверять результаты и продолжать работу до финального ответа.
- Трассировка выполненных инструментов показывается прямо под ответом.

## Архитектура

```text
Browser
   │
   ▼
inhCHAT UI (HTML/CSS/JS)
   │ HTTP
   ▼
Flask :698
   ├── SQLite: accounts / providers / chats
   ├── LLM adapter: OpenAI-compatible APIs
   ├── Work Mode agent loop + tools
   └── DeepSeek control
          │
          ▼
   FreeDeepseekAPI :9655
          │
          ▼
   DeepSeek Web account
```

FreeDeepseekAPI уже предоставляет OpenAI-compatible `/v1/chat/completions`, streaming, tool calling, Responses/Anthropic shims и отдельные agent sessions. Это делает его удобным upstream для inhCHAT. При этом сам upstream является экспериментальным web-proxy и зависит от внутреннего API DeepSeek, поэтому для критичных production-сценариев стоит предусмотреть обычный официальный API-провайдер. citeturn1search0

## Полный запуск на macOS

Нужны Git, Python 3 и Node.js 18+.

```bash
cd ~/Desktop
git clone https://github.com/tranzistrina/inhChatWorkLLM.git
cd inhChatWorkLLM
chmod +x setup.sh start.sh
./setup.sh
source .venv/bin/activate
./start.sh
```

После запуска открой:

```text
http://127.0.0.1:698
```

Первый запуск сам:

1. создаст Python virtualenv;
2. установит Flask и зависимости;
3. скачает FreeDeepseekAPI внутрь `.vendor/`;
4. установит его npm-зависимости;
5. создаст локальный `.env` со случайным секретом;
6. подготовит SQLite и `workspace/`.

### Первый вход в DeepSeek

В inhCHAT открой `⚙ Настройки` → `DeepSeek` → `Войти в DeepSeek`. Будет запущена штатная авторизация FreeDeepseekAPI и отдельный Chrome-профиль. После завершения входа вернись в inhCHAT.

После появления `deepseek-auth.json` следующий запуск `./start.sh` автоматически поднимает FreeDeepseekAPI на `127.0.0.1:9655`.

### Добавление обычного API

В `⚙ Настройки` → `Провайдеры` можно добавить любой совместимый endpoint, например:

```text
Название: OpenAI
Base URL: https://api.openai.com/v1
Model: <нужная модель>
API key: <ключ>
```

Также можно использовать локальные OpenAI-compatible серверы.

## Режим работы

Переключатель `◉ Режим работы` включает автономный агентный цикл. Сейчас доступны четыре базовых инструмента:

- `calculator`;
- `list_workspace`;
- `read_file`;
- `write_file`.

Файловые операции ограничены каталогом `workspace/`. Это намеренное ограничение: автономному агенту не требуется выдавать права на весь Mac только потому, что он умеет складывать два числа. В следующей итерации можно добавить явно подтверждаемые shell/web/browser tools.

Лимит автономного цикла по умолчанию составляет 12 шагов на запрос. Это защищает от бесконечной цепочки tool calls.

## Безопасность

- `.env`, база данных, логи, рабочие файлы и FreeDeepseekAPI vendor-копия исключены из Git.
- Пароли inhCHAT хранятся как password hashes.
- API ключи сейчас предназначены для локального использования. Для публикации сервера в сеть нужно добавить полноценное шифрование секретов и CSRF/rate-limit защиту.
- Не публикуй `deepseek-auth.json`: это credential браузерной сессии DeepSeek. Сам FreeDeepseekAPI также рекомендует не коммитить этот файл и хранить его с ограниченными правами доступа. citeturn1search2

## Структура

```text
inhChatWorkLLM/
├── app.py
├── requirements.txt
├── setup.sh
├── start.sh
├── .env                 # создаётся локально
├── data/
│   ├── inhchat.db
│   └── deepseek-proxy.log
├── workspace/           # файлы автономного агента
├── .vendor/
│   └── FreeDeepseekAPI/ # устанавливается автоматически
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Важное замечание

FreeDeepseekAPI работает через залогиненную web-сессию DeepSeek, а не через официальный платный API. Его README прямо предупреждает, что внутренний web-контракт DeepSeek может измениться. Поэтому inhCHAT проектируется как **API-агностичный клиент**: DeepSeek является удобным бесплатным upstream, но обычные OpenAI-compatible провайдеры остаются полноценным вариантом. citeturn1search0
