# Интеграция с GitLab CI/CD

В данном каталоге представлены готовые конфигурационные файлы и сценарии для подключения **GraphQL Coverage Proxy** к инфраструктуре GitLab CI/CD вашей компании.

Поддерживаются две модели внедрения (согласно архитектуре проекта):

---

## Вариант А: Режим наблюдения (Observer / In-Pipeline) — Рекомендуемый

Компания выполняет существующие автотесты на своих раннерах в GitLab CI. Код автотестов и инфраструктура остаются полностью на вашей стороне.

### Как это работает:
1. Запросы от ваших тестов направляются через **GraphQL Coverage Proxy** (заголовок `X-Coverage-Run-Id: $CI_PIPELINE_IID` или `$COVERAGE_RUN_ID`).
2. Прокси в реальном времени фиксирует все задействованные поля схемы, типы, операции и ошибки.
3. В конце пайплайна шаг `verify-graphql-coverage` запускает проверку порога покрытия (Quality Gate):
   ```bash
   coverage-qa gitlab gate \
     --coverage-url "$COVERAGE_URL" \
     --api-key "$COVERAGE_API_KEY" \
     --run-id "$COVERAGE_RUN_ID" \
     --min-coverage 80.0 \
     --junit-out reports/coverage-junit.xml
   ```
4. Если покрытие полей схемы ниже заданного порога (например, `< 80%`), джоба падает с кодом `1` и подробным перечнем непокрытых полей.
5. GitLab автоматически отображает:
   - Процент покрытия в Merge Request и бейджах репозитория (по регулярному выражению `Total coverage: \d+\.\d+%`).
   - Результаты Quality Gate во вкладке **Tests / Test Summary** благодаря сгенерированному `junit.xml`.

---

## Вариант В: Удалённый запуск пайплайна (Pipeline Trigger)

Тестировщик или аналитик нажимает кнопку в веб-интерфейсе **TestLab** (или выполняет команду в CLI), а система через официальный GitLab API v4 инициирует выполнение существующего пайплайна в вашей компании.

### Использование через CLI:
```bash
# Запустить пайплайн и дождаться завершения с автоматической проверкой Quality Gate
coverage-qa gitlab trigger \
  --host https://gitlab.yourcompany.com \
  --project 12345 \
  --token "$GITLAB_TRIGGER_TOKEN" \
  --ref main \
  --run-id "forma-run-001" \
  --coverage-url "http://coverage-proxy.internal:8000" \
  --min-coverage 80.0 \
  --wait
```

### Использование через curl / bash-скрипт:
```bash
chmod +x examples/gitlab/trigger_pipeline.sh
./examples/gitlab/trigger_pipeline.sh 12345 "$GITLAB_TRIGGER_TOKEN" main "my-run-uuid"
```

---

## Переменные окружения GitLab CI (Settings > CI/CD > Variables)

| Переменная | Описание | Обязательная |
| :--- | :--- | :--- |
| `COVERAGE_URL` | Базовый адрес инстанса GraphQL Coverage Proxy (например, `http://proxy.internal:8000`) | Да |
| `COVERAGE_API_KEY` | Сервисный токен авторизации прокси (Masked / Protected) | Да |
| `COVERAGE_MIN_PERCENT` | Минимально допустимый процент покрытия схемы (по умолчанию `80.0`) | Нет |
| `GITLAB_TRIGGER_TOKEN` | Токен Pipeline Trigger для внешнего запуска (для Варианта B) | Для Варианта B |
