# Работа в команде

## Ветки и объединение

Репозиторий QA самостоятельный. Начальная поставка находится в `feat/qa-platform-and-demo-shop`; основной backend и frontend остаются в своих репозиториях. После просмотра изменений создайте pull request этой ветки в `main`, дождитесь зелёного CI и объедините его по правилам команды.

Для следующих задач создавайте ветки от актуального `main`, например `test/order-validation`. Коммитьте сценарии, фикстуры, документацию и lock-файл, если изменились зависимости. Не коммитьте `.venv`, `.browsers`, `.env`, `reports`, дампы БД и runtime-логи; они исключены `.gitignore`.

## Свой сценарий

См. [WRITING_TESTS.md](WRITING_TESTS.md). Файлы `tests/test_business_*.py` автоматически входят в business/all. Новые tooling-проверки оформляйте как `test_reporting_*.py` или другое имя без префикса live/shop. `@pytest.mark.case` задаёт название в панели.

Используйте `business` для API-сценариев и fixture `storefront` для browser E2E. Ожидания вычисляйте независимо, успешную mutation проверяйте отдельным чтением. Проверяйте отказ, права, состояние после ошибки, идемпотентность и очистку собственных данных.

## Проверка перед PR

```powershell
.\.venv\Scripts\python run_tests.py --suite all --local
```

При изменении только одного сценария начните с `-- -k <часть_имени>`, затем выполните подходящие связанные проверки. Приложите backend commit (`git -C ../GraphQL-Coverage-Proxy-Beckend rev-parse HEAD`), команду, результат и известные ограничения. HTML/trace отдавайте как CI artifacts, не добавляйте в Git.

## Выбор версии backend в CI

При обычном push/PR проверяется текущий `main` backend. Для воспроизводимой проверки используйте Actions → API integration tests → Run workflow → `backend_ref`: ветку, tag или полный commit SHA. Checkout backend публичный; для приватного форка установите secret `COVERAGE_BACKEND_TOKEN` с правом чтения этого репозитория. Сам секрет не хранится в коде.

QA не меняет исходники командного backend. Скрипт `prove_coverage_detection.py` намеренно вносит дефект только во временную копию и проверяет, что тест его обнаружил. `prove_detection.py` и набор shop относятся к раннему внешнему примеру и не входят в стандартную поставку окружения all.
