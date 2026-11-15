# GraphQL Coverage Proxy — QA

QA-проект команды QualiMeter: тесты API, проверка учёта полей GraphQL, браузерные сценарии и панель управления запусками.

**Что можно сделать:** запустить проверки одной кнопкой, увидеть реальные запросы и ответы, проверить затронутые поля схемы, сравнить результаты разных запусков и продемонстрировать работу API на примере магазина FORMA.

## Навигация

- [Установка](#установка)
- [Запуск через панель](#запуск-через-панель)
- [Запуск из терминала](#запуск-из-терминала)
- [Что проверяют тесты](#что-проверяют-тесты)
- [Результаты и история](#результаты-и-история)
- [Магазин FORMA](#магазин-forma)
- [Добавление своего теста](#добавление-своего-теста)
- [Структура и совместная работа](#структура-и-совместная-работа)
- [Ошибки запуска](#ошибки-запуска)

## Установка

### 1. Подготовьте окружение

Нужны **Git, Python 3.12+ и Docker с Compose v2**. На Windows используйте Docker Desktop с Linux engine; на Linux — Docker Engine и Compose plugin. Перед тестами запустите Docker.

Проверьте инструменты:

```sh
python --version
docker info
docker compose version
```

### 2. Клонируйте QA и backend

Выполните обе команды из одной рабочей папки:

```sh
git clone https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend.git
git clone --branch feat/qa-platform-and-demo-shop https://github.com/QualiMeter/GraphQL-Coverage-Proxy-QA.git
cd GraphQL-Coverage-Proxy-QA
```

Пока QA находится в feature-ветке, указывайте `--branch`, как в примере. После объединения изменений в `main` его можно убрать.

Ожидаемая структура:

```text
workspace/
├── GraphQL-Coverage-Proxy-Beckend/
└── GraphQL-Coverage-Proxy-QA/
```

Backend нужен как исходный код: runner сам запустит его на временной БД. Отдельно поднимать рабочий backend, заполнять его `.env` и подключать production-БД не требуется. Frontend команды для этого набора не нужен.

### 3. Установите зависимости и команду запуска

**Windows / PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m pip install -e . --no-deps
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/.browsers"
.\.venv\Scripts\python -m playwright install chromium
```

**Linux / macOS:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e . --no-deps
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" .venv/bin/python -m playwright install --with-deps chromium
```

Первая установка загружает Python-пакеты, браузер и системные зависимости Playwright. На Linux установка системных пакетов может потребовать права администратора. Docker дополнительно скачает PostgreSQL при первом прогоне.

`requirements.lock.txt` фиксирует проверенные версии зависимостей. `pip install -e . --no-deps` регистрирует команду `coverage-qa` и связывает её с этим checkout; изменения кода подхватываются без переустановки. Папку проекта после установки не переносите: если перенесли, повторите установку `-e .`.

Проверьте установку:

```powershell
.\.venv\Scripts\coverage-qa --help
```

На Linux/macOS далее заменяйте `.\.venv\Scripts\coverage-qa` на `.venv/bin/coverage-qa`. Эквивалент команды — `python -m coverage_qa` из того же виртуального окружения.

### Если backend лежит в другом месте

Для всех последующих запусков, включая панель:

```powershell
$env:COVERAGE_BACKEND_DIR = 'C:/projects/backend'
```

Linux/macOS: `export COVERAGE_BACKEND_DIR=/path/to/backend`.

Для одного прогона можно передать `--coverage-backend-dir ../backend`. Параметр имеет приоритет над переменной окружения. Указанная папка должна содержать `alembic.ini`. [Подробности установки →](docs/installation.md)

## Запуск через панель

```powershell
.\.venv\Scripts\coverage-qa panel
```

Откройте **http://localhost:9020/**.

1. Выберите группы или отдельные сценарии. Поиск работает по названию и ID теста.
2. Укажите название запуска, например «Проверка перед PR».
3. Нажмите **«Запустить тесты»**.
4. Во время выполнения выберите сценарий, чтобы увидеть запросы, ответы и проверки ожидания/факта. На вкладке полей отображаются измерения Coverage API.
5. После завершения откройте полный отчёт или перейдите в историю, чтобы посмотреть прошлые запуски.

![Панель тестов](docs/images/test-panel.png)

**Что происходит при запуске:** runner создаёт отдельный Docker project, временную PostgreSQL и случайные порты; выполняет миграции; запускает тестовый upstream и Coverage Proxy; выполняет сценарии; сохраняет отчёт; закрывает процессы и удаляет своё тестовое окружение. Данные рабочего backend и ручного демо-магазина не используются.

**Отмена:** кнопка «Завершить после текущего теста» дожидается текущего сценария и очистки его окружения. Следующий сценарий не начинается. Если тест проверяет таймаут, ожидание может занять до его завершения. Отменённый прогон получает отдельный статус.

**Новый тест:** после добавления файла нажмите «Обновить список». Панель собирает каталог через pytest; сценарии не прописаны вручную.

Панель слушает только localhost. Из одной панели запускается один прогон одновременно. Остановка самой панели — Ctrl+C в терминале. Другой порт: `coverage-qa panel --port 9021`.

## Запуск из терминала

**Все локальные проверки:**

```powershell
.\.venv\Scripts\coverage-qa test --label 'Проверка перед PR'
```

**Отдельные разделы и сценарии:**

```powershell
.\.venv\Scripts\coverage-qa test -- tests/api/coverage
.\.venv\Scripts\coverage-qa test -- tests/api/shop
.\.venv\Scripts\coverage-qa test -- tests/e2e
.\.venv\Scripts\coverage-qa test -- tests/platform
.\.venv\Scripts\coverage-qa test -- -k checkout
.\.venv\Scripts\coverage-qa test -- tests/api/shop/test_order_filters.py::test_cancelled_order_filter
```

После `--` передаются пути и параметры pytest. Пути вида `tests/...` считаются относительно QA checkout, даже если команда вызвана из другой папки. Параметризованный сценарий без указания варианта выполняется со всеми своими вариантами.

**Посмотреть список без выполнения и создания истории:**

```powershell
.\.venv\Scripts\coverage-qa test -- --collect-only -q
```

Доступны также группы `--suite coverage`, `business`, `e2e`, `tooling`, `live`, `all`. `all` — значение по умолчанию; live-проверки в него не входят без явно указанного адреса.

**Проверка опубликованного API:**

```powershell
.\.venv\Scripts\coverage-qa test --suite live --live-base-url https://graphql.rebyte.fun
```

Эти сценарии выполняют только GET-запросы: health, OpenAPI, публичный каталог и схемы, неизвестный run. Они не создают сервисы, заказы и прогоны. Недоступность явно указанного сервера — ошибка. Выбор `tests/live` также требует `--live-base-url`. Пустой каталог или отсутствие публичной схемы может дать явный пропуск соответствующей проверки; это видно в отчёте.

| Параметр | Назначение |
| --- | --- |
| `--label 'Название'` | Название в истории |
| `--report-dir <папка>` | Другая папка для результатов |
| `--coverage-backend-dir <checkout>` | Другой checkout backend |
| `--live-base-url <URL>` | Явно включить проверки развёртывания |
| `-- --collect-only -q` | Вывести каталог без выполнения |
| `-- -k <выражение>` | Выбрать сценарии по имени |

Код завершения `0` означает успешное выполнение выбранного набора, `1` — провал проверок; отмена и ошибки запуска дают ненулевой код. Пропуски видны отдельно. Параллельные pytest-xdist workers не поддерживаются: сценарии используют управляемое последовательное окружение.

## Что проверяют тесты

| Папка | Содержание |
| --- | --- |
| `tests/api/coverage/` | Импорт схемы, прогоны, подсчёт полей и hits, aliases, variables, directives, ошибки анализа, прозрачность proxy, заголовки, сетевые сбои и сохранение после перезапуска; журнал/detail, P95, labels/SDL, метаданные графа, cookie/ACL и multi-service routing |
| `tests/api/shop/` | Каталог, вход, роли, профиль, заказы нескольких товаров, атомарность отказа, чужие заказы, остатки, отмена, idempotency key и конкуренция покупателей |
| `tests/e2e/` | Клик в браузере через proxy и сценарии FORMA: каталог, покупка/отмена, ошибка входа, кабинет администратора, выход при незавершённом запросе и повтор после потери ответа |
| `tests/platform/` | CLI, установка, каталог сценариев, команды панели, отмена, история, экранирование HTML и маскировка credentials |
| `tests/live/` | Отдельные GET-only проверки развёрнутого API |
| `tests/support/` | Вспомогательные HTTP-клиенты, браузерная fixture и контролируемый upstream; это не отдельная группа сценариев |

Бизнес-тесты API параметризованы: одни правила проверяются напрямую и через Coverage Proxy. В браузерных сценариях запросы также идут через proxy, после чего тест отдельно читает состояние заказа и остатков через API.

Количество сценариев собирается pytest и отображается в панели. Оно меняется при добавлении тестов. [Матрица проверок и ограничения →](docs/test-plan.md) · [Сверка с backend →](docs/backend-compatibility.md)

## Результаты и история

Каждый запуск получает уникальную папку `reports/runs/<UTC-время-UUID>/`. Старые запуски не перезаписываются. В интерфейсе время отображается для Екатеринбурга (UTC+5).

| Артефакт | Что содержит |
| --- | --- |
| `index.html` | Полный отчёт для просмотра в браузере |
| `results.json` | Результаты сценариев, шаги, запросы/ответы и проверки |
| `manifest.json` | Название, время, статус и сводка запуска |
| `field-usage.json` | Связь запуска, pytest-сценария, service/schema/run и фактических полей |
| `junit.xml` | Результаты для CI |
| `progress.json`, `live/` | Состояние выполнения для панели |
| `*.png`, `*trace.zip` | Скриншоты и Playwright traces, если выполнялись браузерные сценарии |
| `*.log` | Диагностика процессов, миграций и инфраструктуры |

История доступна в панели, а также в `reports/index.html`; `reports/latest/index.html` открывает последний отчёт. Отдельный просмотрщик:

```powershell
.\.venv\Scripts\coverage-qa reports
```

Откройте **http://localhost:9010/**. Он только показывает уже сохранённые файлы. Другой порт: `coverage-qa reports --port 9011`.

**Как читать покрытие:** ожидаемые поля задаются в тесте независимо от анализатора, фактические `hits` читаются из Coverage API. Процент не означает проверку всех бизнес-правил и успешность каждого резолвера. Для однозначно сопоставленного обращения сохраняются server request ID, `selectedFields` и `countedInCoverage` из `/requests/{requestId}`; поля видны прямо в шаге HTTP. `/operations` остаётся отдельным агрегатом повторов. При неоднозначном сопоставлении отчёт сохраняет общий журнал и не приписывает одному HTTP-вызову чужие поля. В HTML, results.json и manifest.json также записываются commit backend и наличие локальных изменений.

Отчёты, traces, `.env`, `.venv` и браузеры исключены из Git. Используйте тестовые данные: диагностические файлы и traces могут содержать содержимое тестовой страницы. Для команды отчёты публикуются как GitHub Actions artifacts.

## Магазин FORMA

Отдельный мини-проект показывает работу GraphQL на понятном сценарии: **каталог → корзина → вход → заказ → история → отмена**. Цены, остатки и заказы берутся из PostgreSQL; реальных платежей нет.

Из корня QA-репозитория:

```sh
docker compose -p gql-commerce-demo -f examples/shop/compose.yaml up --build -d --wait
```

| Адрес | Назначение |
| --- | --- |
| http://localhost:9110/ | Витрина магазина |
| `POST http://localhost:9100/graphql` | Бизнес-API магазина |
| http://localhost:9100/schema.graphql | SDL схемы магазина |

На форме входа выберите демо-профиль Alice, Bob или Admin и нажмите «Войти». Можно ввести данные вручную:

| Пользователь | Email | Пароль |
| --- | --- | --- |
| Alice | `alice@example.test` | `test-password-2026` |
| Bob | `bob@example.test` | `test-password-2026` |
| Admin | `admin@example.test` | `test-password-2026` |

Alice и Bob видят свои заказы; Admin дополнительно получает статистику. В API-инспекторе видны операция, переменные, реальный ответ и время; пароль и token маскируются.

Обычный демо-магазин работает напрямую со своим API и постоянной БД. Браузерные автотесты поднимают отдельную копию магазина и направляют запросы через Coverage Proxy с отдельным run ID. Тесты не сбрасывают ручное демо.

Остановить магазин с сохранением данных:

```sh
docker compose -p gql-commerce-demo -f examples/shop/compose.yaml down
```

[Сценарий демонстрации, разработка и настройка proxy →](examples/shop/README.md)

## Добавление своего теста

Создайте файл `test_*.py` в подходящем разделе. Например, API магазина — `tests/api/shop/test_my_order.py`. Каталог определяется папкой и pytest collection; вручную регистрировать файл в runner не требуется.

```python
import pytest

@pytest.mark.case("Отмена заказа возвращает остаток товара")
def test_cancellation_restores_stock(business):
    b = business
    before = {p["id"]: p["stock"] for p in b.catalog()}
    order = b.create([{"productId": "cable", "quantity": 1}])
    b.cancel(order["id"])
    after = {p["id"]: p["stock"] for p in b.catalog()}
    b.api.expect("Остатки восстановлены", after, before)
```

Fixture `business` выполнит сценарий напрямую и через proxy и очистит созданные заказы. `@pytest.mark.case` задаёт название для панели и отчёта. Ожидания вычисляйте из исходного состояния, а результат mutation проверяйте отдельным чтением.

```powershell
.\.venv\Scripts\coverage-qa test -- tests/api/shop/test_my_order.py
```

Отдельный сценарий `tests/e2e/test_shop.py::test_storefront_retry_after_lost_checkout_response` проверяет повтор покупки после потери ответа: сервер уже сохранил заказ, браузер получил 502, повтор не создаёт второй заказ. [Подробнее о сценарии →](docs/test-plan.md#дополнительные-сценарии-реальных-сбоев)

Для браузерного теста в `tests/e2e/` доступна fixture `storefront`. [Подробная инструкция и примеры →](docs/writing-tests.md)

## Структура и совместная работа

```text
src/coverage_qa/       команда запуска, панель, управление стендом и отчётность
tests/api/            проверки API proxy и магазина
tests/e2e/            сценарии браузера
tests/platform/       проверки QA-инструментов
tests/live/           проверки развёртывания
tests/support/        клиенты, fixtures и контролируемый upstream
examples/shop/        самостоятельные API, витрина и Compose магазина
docs/                 инструкции, архитектура и матрица проверок
tools/                проверка чувствительности тестов к намеренному дефекту
reports/              результаты выполнения, вне Git
```

Три репозитория команды остаются независимыми:

- [Backend](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend) — продуктовый Coverage Proxy и его API.
- [Frontend](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Frontend) — основной интерфейс продукта.
- [QA](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-QA) — этот набор тестов, его панель и примеры.

QA запускает выбранный checkout backend и не меняет его код. Магазин — отдельный пример upstream и пользовательского приложения.

GitHub Actions запускается при push и pull request. CI использует Ubuntu, Python 3.12, отдельный checkout backend и Chromium. Отчёты сохраняются в artifact `api-test-results`, в том числе при падении тестов. Для просмотра откройте нужный запуск во вкладке Actions и скачайте artifact.

По умолчанию проверяется актуальный backend `main`. Через **Actions → API integration tests → Run workflow → backend_ref** можно выбрать ветку, tag или commit SHA. Для публичного backend секрет не нужен; для приватного предусмотрен `COVERAGE_BACKEND_TOKEN` с правом чтения. В сообщении PR указывайте проверенный backend commit, команду и результат.

[Правила работы команды →](CONTRIBUTING.md) · [Архитектура →](docs/architecture.md)

## Ошибки запуска

| Симптом | Что проверить |
| --- | --- |
| `coverage-qa` не найден | Выполнен ли `pip install -e . --no-deps` тем же Python, из которого запускается команда; используйте путь из `.venv` |
| `Backend not found` | Соседнюю папку backend и `alembic.ini`; при другом расположении задайте `COVERAGE_BACKEND_DIR` |
| Docker не отвечает | Запущен ли Docker Desktop/Linux engine, выполняется ли `docker info` |
| Браузер Playwright не найден | Установлен ли Chromium в `.browsers` с `PLAYWRIGHT_BROWSERS_PATH`; на Windows можно использовать установленный Edge |
| Порт занят | Для панели/просмотрщика задайте `--port`; для магазина измените только host-порт Compose |
| Тест упал | Откройте сценарий в панели или HTML, сравните ожидание/факт, затем посмотрите HTTP-ответ и журналы |
| Live-сервер недоступен | Проверьте адрес и сеть; обычный локальный запуск от этого сервера не зависит |

[Полная диагностика и восстановление окружения →](docs/installation.md)
