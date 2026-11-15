# Архитектура и границы

```mermaid
flowchart LR
    Panel[Панель QA :9020] --> Runner[Runner / pytest]
    CLI[CLI] --> Runner
    Runner --> Proxy[Командный Coverage Proxy]
    Browser[Playwright] --> Shop[FORMA / same-origin bridge]
    Shop --> Proxy
    Proxy --> Upstream[Контролируемый GraphQL upstream]
    Proxy --> CoverageDB[(Временная coverage PostgreSQL)]
    Upstream --> CommerceDB[(Временная commerce PostgreSQL)]
    Runner --> Evidence[HTTP-доказательства и история]
    Proxy --> Runner
    Evidence --> Panel
```

Для проверок самого proxy используется компактная `tests/support/upstream/schema.graphql` с контролируемыми сбоями. Для бизнес-сценариев используется `examples/shop/api` с PostgreSQL, сессиями, заказами нескольких товаров, транзакциями, остатками и idempotency key. Наборы выполняются последовательно, xdist не поддерживается.

`src/coverage_qa/environment.py` создаёт уникальный Compose project и случайные порты, запускает миграции и дочерние HTTP-процессы. Временная БД хранится в tmpfs. `conftest.py` собирает HTTP-события и проверки; `tests/support/coverage_client.py` получает измерения backend; `tests/support/shop_client.py` управляет тестовыми пользователями и очисткой созданных заказов.

`src/coverage_qa/reporting/history.py`, `src/coverage_qa/reporting/progress.py`, `src/coverage_qa/reporting/html.py` сохраняют историю, live-состояние и HTML. Панель `src/coverage_qa/control_panel.py` принимает только известные pytest IDs, защищает команды локальным токеном, слушает loopback и запускает один прогон одновременно. Отмена мягкая: следующий тест не начинается, teardown текущего завершает очистку.

`examples/shop/web/server.py` — BFF витрины: цель задана серверным окружением. При автотестах серверный ключ покрытия и run ID остаются на BFF. Обычный Compose-магазин идёт напрямую в commerce API и хранит свои данные отдельно от тестов.

Основной frontend команды интегрируется с backend в своём репозитории. Для объединения продукта не требуется копировать QA-код в backend или frontend: QA получает checkout конкретного backend ref. Последующие проверки командного UI можно добавить отдельным набором, не изменяя текущую панель тестов.

## Достоверность отчёта

- Ожидаемые поля задаются независимо от анализатора.
- Реальные `hits` читаются из `/coverage` и `/operations`.
- Статус анализа не означает успешность каждого резолвера.
- Detail `/requests/{requestId}` даёт реальные selectedFields отдельного обращения. HTTP-вызов связывается с ним по единственному новому ID в изолированном run; browser exchange — только при единственном совпадении operationKey. Неоднозначные случаи не выдаются за точное сопоставление. `/operations` хранится отдельно как агрегат.
- Тестовые БД удаляются, но доказательства и карта полей остаются в уникальной папке запуска.
- Пароли, tokens и чувствительные заголовки маскируются в журнале API. Браузерные проверки сохраняют скриншоты и очищенные HTTP-события. Сетевые трассы Playwright отключены.

Backend commit и dirty-флаг читаются из выбранного checkout и сохраняются в manifest/results/HTML. Для ZIP без Git commit явно не определён. Тесты multi-service используют два отдельных HTTP-процесса; allowlist содержит только их конкретные loopback origins.
