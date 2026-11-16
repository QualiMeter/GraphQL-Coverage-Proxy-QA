# Защищённая QA-панель на VPS

Цель: открыть уже существующую панель запуска Playwright/API-тестов по
`https://graphql.rebyte.fun/qa/`, не меняя основной интерфейс Coverage Proxy.

> Важно: в текущем режиме QA runner **намеренно создаёт временные PostgreSQL,
> Coverage Proxy и магазин**. Его проценты покрытия и история хранятся в
> QA-отчётах и **не появляются автоматически** в рабочем
> `https://graphql.rebyte.fun/coverage/`. Подключение runner к уже
> развернутому сервису FORMA Demo Shop — отдельная интеграция. Нельзя выдавать
> готовую панель за готовый production coverage run.

## 0. Подготовка

- VPS: Ubuntu, Docker Engine + Compose v2, Python >=3.12, Nginx с TLS.
- QA checkout на VPS: `/opt/graphql-coverage-qa`.
- Исходники Coverage Backend: `/opt/graphql-coverage`, в каталоге должен
  существовать `alembic.ini`. Если путь другой, исправьте `COVERAGE_BACKEND_DIR`
  в unit.
- Перенесите на VPS код из ветки `feat/secure-qa-panel-vps`. Если на VPS
  QA-папка получена через rsync, обновляйте её также без удаления чужих файлов,
  `.venv`, `.browsers` и `reports`.

## 1. Установить CLI, pytest и Playwright

На VPS от имени root:

```bash
cd /opt/graphql-coverage-qa
python3 --version
docker compose version
test -f /opt/graphql-coverage/alembic.ini
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e . --no-deps
PLAYWRIGHT_BROWSERS_PATH=/opt/graphql-coverage-qa/.browsers .venv/bin/python -m playwright install --with-deps chromium
```

Установка `--with-deps` может потребовать установки системных библиотек.
Перед изменениями в production проверьте свободное место для Docker/Chromium.

## 2. Локальная проверка, ещё без внешнего доступа

```bash
cd /opt/graphql-coverage-qa
COVERAGE_BACKEND_DIR=/opt/graphql-coverage .venv/bin/coverage-qa panel
```

В другой SSH-сессии:

```bash
curl -i http://127.0.0.1:9020/
curl -i http://127.0.0.1:9020/api/state
```

Обе команды должны вернуть HTTP 200. Не открывайте порт 9020 в файрволе:
он уже слушает только `127.0.0.1`.

Завершите ручной запуск Ctrl+C перед включением systemd.

## 3. Запускать панель с systemd

```bash
sudo cp /opt/graphql-coverage-qa/deploy/systemd/graphql-qa-panel.service /etc/systemd/system/graphql-qa-panel.service
sudo systemctl daemon-reload
sudo systemctl enable --now graphql-qa-panel
sudo systemctl status graphql-qa-panel --no-pager
curl -i http://127.0.0.1:9020/
```

## 4. Закрыть панель паролем Nginx

Используем существующий TLS-домен, отдельный DNS и сертификат не нужны.
Оператору нужна отдельная пара логин/пароль; **не передавайте пароль в
аргументах CLI и не коммитьте файл htpasswd**.

```bash
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/.graphql-qa-htpasswd alexei
sudo chown root:www-data /etc/nginx/.graphql-qa-htpasswd
sudo chmod 640 /etc/nginx/.graphql-qa-htpasswd
sudo install -m 644 /opt/graphql-coverage-qa/deploy/nginx/qa-panel-location.conf /etc/nginx/snippets/graphql-qa-panel.conf
sudo cp -a --backup=numbered /etc/nginx/sites-available/graphql.rebyte.fun /etc/nginx/sites-available/graphql.rebyte.fun.bak-qa
sudo nano /etc/nginx/sites-available/graphql.rebyte.fun
```

В существующий блок `server { listen 443 ssl; server_name graphql.rebyte.fun; ... }`
добавить **внутри** (не в блок HTTP:80):

```nginx
include /etc/nginx/snippets/graphql-qa-panel.conf;
```

Проверить и применить:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -I https://graphql.rebyte.fun/qa/
curl -u alexei -i https://graphql.rebyte.fun/qa/
```

Ожидания: без логина — HTTP 401; с логином — HTTP 200 и HTML QA-панели.
Пароль `curl -u alexei` запросит интерактивно. Запросы из браузера к
`/qa/api/...`, `/qa/assets/...`, `/qa/reports/...` проксируются в
loopback; `/api/...` основного Coverage Proxy **не затрагивается**.

## 5. Проверка после публикации

1. Откройте `https://graphql.rebyte.fun/qa/`, введите логин и пароль.
2. Проверьте группы тестов и выберите только одно быстрое тестирование
   (например, платформенный сценарий), не весь набор.
3. Нажмите «Запустить тесты» и дождитесь зелёного результата.
4. Откройте историю и полный отчёт.
5. Затем запустите группу «Браузерные сценарии» (Playwright). Runner создаст
   собственную временную инфраструктуру, не будет сбрасывать существующий
   volume `gql-commerce-demo_commerce-data`.

При ошибке `502`:

```bash
sudo systemctl status graphql-qa-panel --no-pager
sudo journalctl -u graphql-qa-panel -n 80 --no-pager
```

При ошибке `403` на POST проверьте:
`QA_PANEL_PUBLIC_ORIGIN=https://graphql.rebyte.fun` и что браузер
действительно использует HTTPS. `/qa/` доступен через Basic Auth,
а команды дополнительно защищены случайным токеном панели и строгой
проверкой `Origin`.

## Безопасность

- QA-панель может запускать subprocess/pytest/Docker. У неё высокие
  серверные привилегии; **публичный анонимный доступ недопустим**.
- Пароль Nginx должен быть уникальным и сильным; для каждого оператора
  можно добавить отдельную запись `htpasswd` без `-c`.
- Не публикуйте `/etc/nginx/.graphql-qa-htpasswd`, токены и ключи Coverage.
- Basic Auth защищает веб-доступ, но не заменяет обновления сервера и
  ограничения административного доступа к VPS.
