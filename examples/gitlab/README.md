# Примеры для GitLab

- **github-bridge.yml** — конфигурация служебного GitLab-проекта для QA, размещённого на GitHub. Сохраните её как .gitlab-ci.yml в служебном проекте. Пошаговая настройка приведена в [документации](../../docs/gitlab-ci.md).
- **.gitlab-ci.yml** — отдельный пример для проекта-потребителя с Docker executor. Задайте QA_REVISION, COVERAGE_URL и COVERAGE_RUN_ID существующего прогона. Для закрытого QA репозитория потребуется доступ на чтение из job. Получение снимка защищённого сервиса требует COVERAGE_API_KEY.
- **trigger_pipeline.sh** — запуск по токену GitLab. Задайте GITLAB_PROJECT_ID, GITLAB_TRIGGER_TOKEN, GITLAB_REF. Для служебного проекта добавьте QA_GITHUB_SHA, для проверки внешнего прогона — COVERAGE_RUN_ID. Скрипт возвращает JSON созданного pipeline.
