# GitLab CI для QA

Исходники QA, бэкенда и фронтенда могут оставаться на GitHub. Основная конфигурация QA находится в корне репозитория в `.gitlab-ci.yml`. Есть два способа подключить её к GitLab.

## GitHub остаётся источником кода

1. Создайте в GitLab служебный проект. В его основную защищённую ветку положите файл [github-bridge.yml](../examples/gitlab/github-bridge.yml) под именем `.gitlab-ci.yml`. Это единственный файл, который надо перенести. Для запуска API разрешите владельцу токена создавать pipeline с переменными.
2. Подключите к служебному проекту выделенный GitLab Runner на Ubuntu 24.04 с executor **shell**, Bash и тегом **coverage-qa-linux**. Runner должен обслуживать только доверенный проект и защищённые ветки. На хосте нужны Git, Python 3.12 с venv, Node.js 22.12+ с npm, Docker Engine с Compose v2 и системные библиотеки Chromium. Установите библиотеки командой `python -m playwright install-deps chromium` из окружения с установленным Playwright. Пользователю runner нужен доступ к локальному Docker: тесты запускают PostgreSQL и подключаются через порт на 127.0.0.1. Для закрытых репозиториев настройте на runner read-only Git credential helper для github.com. Запись в командные репозитории не требуется.
3. Защитите исходные ветки GitHub, указанные в [workflow](../.github/workflows/gitlab.yml). В GitHub → Settings → Secrets and variables → Actions создайте переменные **GITLAB_API_URL** (например, `https://gitlab.com/api/v4`), **GITLAB_PROJECT_ID** (числовой ID служебного проекта), **GITLAB_REF** (его защищённая ветка). В secrets положите **GITLAB_PROJECT_TOKEN** с правом API для создания и чтения pipeline в этом проекте. URL и ref задаёт администратор. Не помещайте токен в Git.
4. После публикации QA выполните push в `feat/qa-platform-and-demo-shop` или `main`. Для повторного запуска откройте Actions → GitLab QA → Run workflow. GitHub передаст точный SHA в GitLab и покажет ссылку на pipeline в Summary. Пока переменная GITLAB_API_URL пуста, этот workflow пропускается.
5. GitLab проверяет принадлежность SHA разрешённой ветке, извлекает CI-конфигурацию из этого же коммита и запускает дочерний pipeline. Каждый job проверяет SHA исходников перед исполнением. GitHub ждёт итог родительского pipeline и завершает проверку ошибкой при сбое. Для ручного старта в GitLab укажите `QA_GITHUB_SHA` полностью (40 символов). Артефакты тестов доступны в дочернем pipeline.

Если в GitLab уже настроено зеркало QA, запускайте корневой файл напрямую, без служебного проекта и переменной QA_GITHUB_SHA. Выберите один способ запуска, чтобы один push не создавал два pipeline. [Официальное подключение GitHub](https://docs.gitlab.com/ci/ci_cd_for_external_repos/github_integration/) · [Shell runner](https://docs.gitlab.com/runner/executors/shell/).

## Что выполняется

| Job | Действие и результат |
| --- | --- |
| frontend-build | Скачивает указанный FRONTEND_SHA, устанавливает зависимости из npm lockfile, запускает ESLint, Vitest и сборку. Передаёт dist в acceptance. |
| acceptance | Скачивает BACKEND_SHA. Создаёт Python venv, устанавливает оба клиента Playwright и Chromium. Выполняет приёмку MVP с перезапуском бэка и весь локальный набор QA с PostgreSQL. TST-07 открывает переданную сборку, выбирает конкретный прогон и сверяет метрики и hits с REST. |
| graphql-coverage-gate | Берёт coverage JSON из job acceptance этого pipeline. Файл содержит UUID реально выполненного MVP-прогона и счётчики после рестарта. Сохраняет JUnit, CSV и JSON, печатает процент для GitLab и возвращает код 1 при недоборе. |
| external-run-gate | Дополнительно запускается на защищённой ветке при переданном COVERAGE_RUN_ID. Получает граф существующего прогона из API стенда. |

QA использует временные базы и локальные upstream-сервисы. Для миграции пары компонентов измените полные **BACKEND_SHA** и **FRONTEND_SHA** в корневой конфигурации. Их значения попадают в лог checkout. Обычный запуск не требует паролей действующего стенда.

**MVP_MIN_PERCENT** по умолчанию равен 66.67. Для зафиксированного сценария это 6 из 9 полей после округления до сотых. Чтобы проверить отрицательный исход, запустите pipeline с порогом 80: тесты выполнятся, gate станет красным, JUnit покажет причину. Это процент полей GraphQL данного прогона. JUnit всего набора и HTML-отчёты находятся в job acceptance. XML, CSV со счётчиками и JSON порога находятся в job gate.

## Проверка внешнего прогона и запуск из TestLab

Сначала создайте прогон в Coverage Proxy и направьте запросы приложения через прокси с заголовком `X-Coverage-Run-Id`, содержащим возвращённый API UUID. Настройте **COVERAGE_URL** в доверенных настройках проекта GitLab и при необходимости **COVERAGE_API_KEY** как masked/protected secret. По умолчанию порог внешнего прогона **COVERAGE_MIN_PERCENT** равен 80. Проект с копией QA или зеркалом можно запускать из TestLab либо через [trigger_pipeline.sh](../examples/gitlab/trigger_pipeline.sh). Для служебного проекта также требуется QA_GITHUB_SHA: вставьте его в поле SHA панели, передайте CLI через `-V QA_GITHUB_SHA=…` или задайте переменную для shell-скрипта.

Для запуска достаточно pipeline trigger token. Чтение статуса требует отдельного права GitLab API: для CLI с `--wait` используйте project access token с нужными правами. Включите доверенный HTTPS-адрес GitLab. Передавайте ключи через защищённое окружение и удаляйте временные доступы после демонстрации.

## Проверка вручную

```sh
python -m acceptance.mvp.run --export-graph reports/gate-input.json
python -m coverage_qa gitlab gate --graph-file reports/gate-input.json --min-coverage 66.67 --junit-out reports/gate.xml --csv-out reports/fields.csv --summary-out reports/summary.json
```

Для воспроизведения TST-07 задайте `QA_FRONTEND_DIST` абсолютным путём к собранной папке dist и выполните `python -m coverage_qa test -- tests/e2e/test_run_coverage_ui.py`. Если сборка по указанному пути отсутствует, тест завершится ошибкой.
