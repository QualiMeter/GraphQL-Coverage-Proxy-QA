# Установка и диагностика

## Структура checkout

```text
workspace/
├── GraphQL-Coverage-Proxy-Beckend/   # git clone backend
├── GraphQL-Coverage-Proxy-Frontend/  # необязательно для QA
└── GraphQL-Coverage-Proxy-QA/        # этот репозиторий
```

Панель и CLI автоматически находят соседний backend по `alembic.ini`. CLI-параметр `--coverage-backend-dir` имеет приоритет над `COVERAGE_BACKEND_DIR`. Не устанавливайте backend как пакет: QA запускает его исходники, используя своё Python-окружение. Его собственная рабочая БД не затрагивается.

Проверьте `python --version`, `docker info`, `docker compose version`, затем выполните команды из корневого README.

## Другой путь к backend

PowerShell, для панели и CLI:

```powershell
$env:COVERAGE_BACKEND_DIR = 'C:/projects/backend'
.\.venv\Scripts\coverage-qa panel
```

Linux/macOS:

```sh
export COVERAGE_BACKEND_DIR=/path/to/backend
.venv/bin/coverage-qa panel
```

Для разового запуска:

```sh
.venv/bin/coverage-qa test --suite all --local --coverage-backend-dir ../backend
```

## Режим без установки Chromium на Windows

При установленном Edge тесты используют отдельный headless-процесс `msedge`. Можно выбрать канал через `API_TEST_BROWSER_CHANNEL=msedge`. Для Chromium предпочтительно не задавать эту переменную; скачайте браузер в `.browsers` с `PLAYWRIGHT_BROWSERS_PATH` из README. Runner использует именно эту папку.

На Ubuntu нужны системные зависимости: `python -m playwright install --with-deps chromium`. Команда может запросить права для установки системных пакетов. GitHub Actions выполняет этот шаг автоматически.

## Типичные проблемы

| Симптом | Решение |
| --- | --- |
| Backend not found | Проверьте соседнюю папку и наличие `alembic.ini`; задайте `COVERAGE_BACKEND_DIR` |
| Docker не отвечает | Запустите Docker Desktop, дождитесь готовности Linux engine; проверьте `docker info` |
| Нет Compose v2 | Установите Compose plugin; должна работать команда `docker compose version` |
| Executable doesn't exist | Установите Chromium в `.browsers` либо используйте установленный Edge на Windows |
| ModuleNotFoundError | Используйте Python из `.venv` и установите `requirements.lock.txt` |
| Порт 9020/9110 занят | Панель принимает `--port`; для магазина измените только host-порт в Compose |
| Не удаётся загрузить образы/pip/browser | Нужен доступ к Docker Hub, Python package index и серверам Playwright при первой установке |
| Live недоступен | Проверка явно выбранного сервера завершается с ошибкой; локальный `all` не зависит от него |

Папки отчётов сохраняют `infrastructure.log`, `proxy.log`, `commerce.log` и `panel.log` там, где соответствующие процессы запускались. При корректной отмене очистка завершается до итогового отчёта. При аварийном завершении машины проверьте свои оставшиеся Docker projects через `docker compose ls -a`; QA не удаляет чужие проекты по общему префиксу.

## Остановка

Панель: Ctrl+C в её терминале. Магазин: `docker compose -p gql-commerce-demo -f examples/shop/compose.yaml down`, данные остаются в volume. Автотесты очищают свои контейнеры автоматически, а отчёты оставляют на диске.


## GitHub Actions

Workflow API integration tests загружает backend и frontend, собирает dist для TST-07, устанавливает Chromium для Python и Node.js Playwright и выполняет приёмку MVP с проверкой порога 66.67%.

Для этого workflow закреплён runner `ubuntu-24.04`: Playwright 1.58.2 из acceptance/mvp/package-lock.json поддерживает этот образ. `ubuntu-latest` перешёл на Ubuntu 26.04, которую эта версия Playwright не поддерживает. Обновляйте версию ОС вместе с браузерными зависимостями и проверкой MVP. [Изменение ubuntu-latest](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration/).

Для закрытых командных репозиториев настройте secrets COVERAGE_BACKEND_TOKEN и COVERAGE_FRONTEND_TOKEN с доступом на чтение соответствующего репозитория. При ручном запуске можно выбрать backend_ref и frontend_ref. Без доступа checkout завершится ошибкой. Артефакты сохраняются и при падении тестов.
