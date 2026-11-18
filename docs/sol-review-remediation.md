# Повторное ревью Sol

Полная таблица B1, B2, Q1–Q5, первопричины, регрессии, изменённые файлы,
команды и ограничения: [отчёт Backend + QA](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/codex/sol-execution-run-fixes/docs/sol-review-remediation.md).

QA: [ветка исправлений](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-QA/tree/codex/sol-qa-safety-fixes).
QA PR не создаётся по указанию пользователя.

Порядок обновления: Backend с attempt:null и миграцией 20261125_run_in_flight,
затем QA. Оба CI pin указывают на совместимый code SHA
148187f2ec28123fdf8f02d562f435729ca84ac6. Форматы отчётов сохраняются.
QA trigger/status/gate HTTP endpoints намеренно отключены (410); pipeline lifecycle
принадлежит основному Backend. CLI artifact gate сохранён.

Действующий GitLab магазин не изменялся; его неизменённые шесть тестов и gate
проверены на локальном HTTPS: 88.57%, 31/35 полей. Destructive сценарии выполнялись
только в изолированных fixture-owned commerce БД. Merge/deployment не выполнялись.
