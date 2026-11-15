# Сверка backend, frontend и QA

Дата сверки: 15 ноября 2026 года. Проверены удалённые main и чистые checkout. Ветки с незавершёнными изменениями не объявляются релизом.

| Часть | Проверенная версия | Способ проверки |
| --- | --- | --- |
| Backend | `e6504cb892081f3a99d21eac825967478f45cfde` | Исходники, контракты и настоящие HTTP-запросы к процессу с временной PostgreSQL |
| Frontend | `f928663ceab77cf31cf6f80cf2fb4b33623cbf84` | Чтение точки входа, источника данных и моделей; без изменения кода и без заявления о полном UI E2E |
| QA | Эта feature-ветка | Локальный all и GitHub Actions; конкретный backend commit сохраняется в результатах каждого управляемого прогона |

## Контракты, по которым сверяемся

- [Backend API](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/e6504cb892081f3a99d21eac825967478f45cfde/docs/frontend-api.md).
- [Cookie/roles/CSRF](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/e6504cb892081f3a99d21eac825967478f45cfde/docs/frontend-auth.md).
- [Multi-service routing](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/e6504cb892081f3a99d21eac825967478f45cfde/docs/frontend-multi-service.md).
- [Журнал и история](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/e6504cb892081f3a99d21eac825967478f45cfde/docs/frontend-history-requests.md).
- [Актуальное дополнение: P95, AST и метаданные схемы](https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend/blob/e6504cb892081f3a99d21eac825967478f45cfde/docs/frontend-final-features.md).

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
| Browser access | Настоящие cookie-сессии viewer/analyst/operator, CSRF, grants/revocation, закрытая metadata и logout |
| Бизнес-API и магазин | Проверки заказа/остатков напрямую и через proxy; отдельный browser E2E магазина |

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

В проверенном `src/main.tsx` при `VITE_DATA_SOURCE=api` показывается сообщение ожидания контракта; обычный AnalyticsApp работает с локальным store. Старый `src/App.tsx` имеет другой data source, но не является текущей точкой входа. Поэтому этот QA не объявляет основной frontend соединённым с backend.

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

1. Заменить заглушку API-режима на отдельный адаптер с относительными `/api/...`.
2. Подключить `/api/auth/session`, вход/выход, Origin и X-CSRF-Token для изменений. Ключ Coverage API остаётся только на сервере/в CLI.
3. Подключить service → snapshot → run без смешения UUID и отображаемых названий.
4. Подключить graph/coverage/stats, затем историю, фильтры журнала и detail.
5. Добавить настоящий E2E основного frontend после появления API-адаптера. Текущие E2E относятся к тестовой странице proxy и FORMA.

## Границы

Полный DNS-rebinding/SSRF/resource-limit аудит, concurrency races авторизации, last-admin и TLS/Nginx остаются в специализированном наборе backend. Frontend здесь только сопоставлен с контрактом, его build/test и настоящее browser E2E не заявлены выполненными. Это дополнение к командным наборам, а не замена их тестов.

Source identity фиксируется в `results.json`, `manifest.json` и HTML: путь, commit, dirty-флаг. При ZIP без Git commit явно не определён. Для точного повторения используйте сохранённый backend commit и `requirements.lock.txt`.
