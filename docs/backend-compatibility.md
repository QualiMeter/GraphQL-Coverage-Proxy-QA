# Сверка backend, frontend и QA

Дата сверки: 16 ноября 2026 года. Версии ниже соответствуют проверенным локальным checkout.

| Часть | Проверенная версия | Способ проверки |
| --- | --- | --- |
| Backend | `7c7f09354623d6d03ca64453d93daf944a7c62c3` | Исходники, контракты и настоящие HTTP-запросы к процессу с временной PostgreSQL |
| Frontend | `386880265b4d63769f2f49857376e8776bc751ff` | Чтение точки входа, источника данных и моделей; без изменения кода и без заявления о полном UI E2E |
| QA | Эта feature-ветка | Локальный all и GitHub Actions; конкретный backend commit сохраняется в результатах каждого управляемого прогона |

## Контракты, по которым сверяемся

- [Backend API](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/7c7f09354623d6d03ca64453d93daf944a7c62c3/docs/frontend-api.md).
- [Cookie/roles/CSRF](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/7c7f09354623d6d03ca64453d93daf944a7c62c3/docs/frontend-auth.md).
- [Multi-service routing](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/7c7f09354623d6d03ca64453d93daf944a7c62c3/docs/frontend-multi-service.md).
- [Журнал и история](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/7c7f09354623d6d03ca64453d93daf944a7c62c3/docs/frontend-history-requests.md).
- [Актуальное дополнение: P95, AST и метаданные схемы](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/7c7f09354623d6d03ca64453d93daf944a7c62c3/docs/frontend-final-features.md).

Документы разных этапов нужно читать вместе: например, отсутствие safeGraphqlDocument в описании этапа 4 отменено дополнением этапа 5. QA использует актуальные DTO, а не старый текст Swagger description одного маршрута.

## Что QA проверяет сейчас

| Возможность backend | QA-сценарии / результат в артефактах |
| --- | --- |
| `service.endpointUrl`, proxy routing по run/snapshot/service | Два отдельных upstream с разными ответами; каждый run получает свой ответ и свои наблюдения |
| Безопасность endpoint | Отказ file/userinfo/metadata/неразрешённому loopback; запрет URL override в introspection payload |
| Настоящая introspection и SDL import | Дедупликация, сохранение schema ID/hash; отдельная версия SDL |
| История по serviceId/snapshotId | Несколько версий схемы, status filter, pagination и X-Total-Count |
| Journal/detail | AND-фильтры, валидация, pagination, total; selectedFields и countedInCoverage конкретного обращения |
| Безопасный AST | available/redacted/unavailable, scalar/default redaction, отсутствие комментариев и runtime secrets в safe document |
| Статистика | Категории анализа, total, avg и P95 по фактическим durations; null в пустом run |
| Schema labels/SDL/graph | Label trim/reset/dedup; неизменность ID/hash/SDL; roots, interfaces/unions, args, description/deprecation и relation UUIDv5 |
| Browser access | Настоящие cookie-сессии viewer/analyst/operator, CSRF, grants/revocation, закрытая metadata, logout, ротация cookie и публикация агрегатов без открытия журнала |
| Бизнес-API и магазин | Проверки заказа/остатков напрямую и через proxy; отдельный browser E2E магазина, включая повтор после потерянного ответа |

Файлы сценариев перечислены в [test-plan.md](test-plan.md). Все локальные стенды изолированы. Рабочие данные и production не используются.

## Точность связи HTTP → поле

`/requests` даёт список наблюдений; `/requests/{requestId}` даёт реальные `selectedFields`, `countedInCoverage` и безопасный документ.

Для последовательного API-вызова QA сравнивает IDs до/после в собственном run. Единственный новый ID даёт точную связь; несколько IDs обозначаются как неоднозначные. Браузерный вызов связывается по operationKey только при единственном наблюдении этой операции. Повторы с одинаковой identity без надёжной связи остаются агрегатом операции; подстановка конкретного request ID по порядку не выполняется.

- `declaredSelectedFields` — независимое ожидание теста.
- `actualSelectedFields` — ответ request detail, а не вычисление frontend.
- `observedOperationAggregate` — отдельный агрегат повторов.
- `requestDetails` — сохранённые серверные детали в checkpoint/run.

Наличие selectedFields означает разобранный выбор полей, не доказательство успешности каждого резолвера. За включение в hits отвечает отдельный countedInCoverage.

## Frontend: что требуется для подключения

В версии 3868802 React использует cookie-сессию и серверные сервисы, снимки, граф и SDL. История и метрики прогонов работают на локальном демонстрационном источнике.

Важные соответствия для будущего API-адаптера:

| Локальная модель UI | Контракт backend | Как адаптировать |
| --- | --- | --- |
| run.status=`active` | `running` / `completed` | Явное преобразование статуса |
| run.endedAt | `finishedAt` | Переименовать в адаптере, сохранить null |
| snapshot.name | `displayName`, `schemaHash`, `createdAt` | Nullable label + понятный fallback, UUID отдельно от отображаемого имени |
| observation.safeDocument | `safeGraphqlDocument` + `documentAvailability` | Показывать available/redacted/unavailable; не выдумывать query |
| observation.graphqlErrors[] | Журнал даёт `graphqlHasErrors` | Не синтезировать сообщения/path ошибок, которых сервер не возвращает |
| observation.analyzerError | `analysisStatus` + `errorCode` | Unsupported отдельно от parse/validation/internal errors |
| metrics.p95Ms | `p95DurationMs` | Брать серверное значение; demo nearest-rank и backend percentile_cont не эквивалентны |
| UI coverageFor(store) | `/runs/{id}/coverage` | Для API-режима использовать серверные hits и summary |
| operation.id | `operationKey` | Использовать ключ backend и выбирать journal filter по нему |
| данные журнала из localStorage | Защищённые requests/detail | Учитывать роль и grant; публичный сервис сам по себе не открывает журнал |

Порядок подключения для фронтенд-разработчика:

1. Связать выбранный серверный снимок с историей его прогонов.
2. Подключить покрытие и статистику по runId, затем операции и журнал с учётом прав.
3. Проверить один и тот же runId через браузер и REST в сценарии TST-07.

## Границы

Полный DNS-rebinding/SSRF/resource-limit аудит, concurrency races авторизации, last-admin и TLS/Nginx остаются в специализированном наборе backend. Frontend проверен отдельно через lint, Vitest и production-сборку. Сквозная проверка TST-07 остаётся открытой. Это дополнение к командным наборам, а не замена их тестов.

Source identity фиксируется в `results.json`, `manifest.json` и HTML: путь, commit, dirty-флаг. При ZIP без Git commit явно не определён. Для точного повторения используйте сохранённый backend commit и `requirements.lock.txt`.
