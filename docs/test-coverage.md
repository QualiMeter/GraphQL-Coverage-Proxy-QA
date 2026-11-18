# Test Coverage

Test Coverage связывает попытку pytest/Playwright-теста с GraphQL-обращениями,
полями схемы и окончательным результатом. Источник данных — API и PostgreSQL
backend. Панель и HTML-отчёт сохраняют полученные серверные снимки.

## Запуск

Нужны QA-ветка `feat/forma-e2e-test-coverage` и актуальный backend `main` с API
`/api/runs/{id}/tests`. Более старые версии backend не поддерживают эту интеграцию.

```sh
git clone --branch main https://github.com/QualiMeter/GraphQL-Coverage-Proxy-Beckend.git
git clone --branch feat/forma-e2e-test-coverage https://github.com/QualiMeter/GraphQL-Coverage-Proxy-QA.git
cd GraphQL-Coverage-Proxy-QA
python -m venv .venv
```

Активируйте окружение. Нужны Python 3.12+, Docker с Linux engine, Node.js 22.

```sh
python -m pip install -r requirements.lock.txt -r acceptance/mvp/requirements.txt
python -m pip install -e . --no-deps
python -m playwright install chromium
npm ci --prefix acceptance/mvp
python -m coverage_qa test --coverage-backend-dir ../GraphQL-Coverage-Proxy-Beckend
```

На Linux для браузера используйте `python -m playwright install --with-deps chromium`.
Две новые интеграционные проверки запускают дочерние pytest и Node Playwright.
Намеренно упавшие дочерние тесты проверяют failed и retry; родительская проверка
должна завершиться успешно. Отдельный запуск:

```sh
python -m coverage_qa test tests/api/coverage/test_test_coverage.py tests/platform/test_tracking.py
```

Для интерфейса запустите `python -m coverage_qa panel`, откройте указанный URL,
перейдите в «История», выберите запуск и вкладку **Test Coverage**. Там доступны
результаты и попытки, фильтры по статусу и полю `Type.field`, поля и журнал
каждой попытки. Без серверного подтверждения интерфейс не подменяет результат
локальным passed.

## FORMA: два режима E2E

Изолированный режим создаёт временные PostgreSQL, commerce API и Coverage backend.
Storefront поднимается отдельно для каждого сценария; сценарий получает собственные
runId и executionId. Обычные E2E запускаются прежней командой:

```sh
python -m coverage_qa test --suite e2e tests/e2e/test_shop.py --coverage-backend-dir ../GraphQL-Coverage-Proxy-Beckend
```

Регрессия обоих режимов работает только с управляемым окружением. Она запускает
два существующих браузерных сценария и намеренные assertion/teardown/skip,
сверяет серверные обращения, поля, идемпотентность метаданных и TestLab:

```sh
python -m coverage_qa test tests/api/coverage/test_forma_e2e_coverage.py --coverage-backend-dir ../GraphQL-Coverage-Proxy-Beckend
```

Во втором варианте этой регрессии родитель создаёт временный активный прогон,
а дочерний runner использует его как существующий: не создаёт другой прогон
и не завершает выбранный. Только владелец временного окружения закрывает его
после проверок. Тестовые заказы отменяются прежним cleanup, в том числе после
провала assertion. Регрессия не использует адреса или БД VPS.

В рабочем режиме TestLab использует серверные QA_DEPLOYED_COVERAGE_URL,
QA_DEPLOYED_SHOP_API_URL и COVERAGE_API_KEY из существующего защищённого
окружения сервиса. Пользователь выбирает активный прогон сервиса
`forma-demo-shop`. Панель проверяет сервис и статус и передаёт runner
`--deployed-run-id`; каждый сценарий получает отдельный executionId в этом
прогоне. Даже skip до setup storefront сохраняется с нулём обращений.
В изолированном режиме skip до setup не создаёт инфраструктуру или пустой прогон.

Запрос идёт browser → локальный BFF сценария → рабочий Coverage Proxy → FORMA API.
SHOP_COVERAGE_API_KEY, SHOP_COVERAGE_RUN_ID и SHOP_COVERAGE_TEST_EXECUTION_ID
задаёт runner только процессу BFF. Браузер передаёт свой пользовательский
Authorization; административный ключ не включается в config, HTML, JS или
ответы BFF. Заголовки атрибуции из браузера не переопределяют серверные значения.
Не добавляйте административный ключ в page/request headers Playwright.

### Ручная проверка после обновления QA на VPS

1. Оператор вручную получает ветку `feat/forma-e2e-test-coverage` без merge
   и force push, устанавливает закреплённые зависимости и Chromium прежним
   способом, перезапускает только QA/TestLab. Backend и основной frontend
   этой задачей не меняются; новые миграции не нужны. Сохраните reports и
   существующие защищённые env-файлы. Инструкции установки магазина —
   [forma-shop-vps.md](forma-shop-vps.md).
2. Убедитесь, что три серверные настройки выше указывают на нужный стенд.
   Уберите COVERAGE_TEST_RUN_ID/COVERAGE_URL от прежних внешних SDK-примеров.
   Не выводите ключ в терминал, DevTools, отчёт или командную строку.
3. Выберите существующий активный прогон FORMA. Зафиксируйте его UUID,
   статус, список tests и количество requests перед проверкой. При фоновом
   трафике сравнивайте по новым executionId, а не по общему приросту requests.
4. Для безопасной smoke-проверки выберите только
   `test_storefront_catalog_and_cart` и `test_storefront_login_error_and_inspector`
   в рабочем режиме FORMA. Они не создают и не отменяют заказы, не списывают
   остатки. Не запускайте покупочные сценарии на production-БД или рабочих
   заказах. Полная проверка покупок и очистки уже выполняется в управляемой
   регрессии; при необходимости её повторяют на отдельном тестовом стенде.
5. После окончания откройте запуск → **Test Coverage**. Должны быть две
   разные попытки со статусом passed, с полями Query.products/Product.*;
   у сценария неверного входа дополнительно есть его запрос Login.
   Ни одна попытка не должна оставаться running.
6. С серверной стороны через существующий авторизованный API проверьте
   GET `/api/runs/{runId}/tests`, `/tests/{executionId}`,
   `/tests/{executionId}/fields`, `/requests?testExecutionId={executionId}`
   и `/test-coverage`. Все обращения попытки должны иметь выбранный runId
   и свой testExecutionId; счётчики API и TestLab должны совпасть.
   Ошибочный Login остаётся обращением; counted coverage проверяйте отдельно
   от selected/observed fields. Общий прогон может содержать и фоновый трафик.
7. Сверьте список прогонов сервиса до/после: лишнего прогона быть не должно,
   выбранный остаётся running. В DevTools проверьте отсутствие административного
   ключа в браузерных запросах и ответах `/api/config`, HTML, JS.
   Данные и отчёты не удаляйте; выбранный рабочий прогон не закрывайте.

## Свои pytest-тесты

Создайте активный прогон через существующий API backend и передайте его UUID
runner. Плагин внешнего проекта не создаёт и не закрывает прогон автоматически.
Пример PowerShell:

```powershell
$env:COVERAGE_URL = 'http://127.0.0.1:8000'
$env:COVERAGE_TEST_RUN_ID = '<UUID активного прогона>'
$env:COVERAGE_API_KEY = '<ключ из вашего локального окружения>'
python -m pytest -p coverage_qa.pytest_plugin tests_custom
```

```python
import os
import httpx


def test_product_name(coverage_test):
    response = httpx.post(
        os.environ['COVERAGE_URL'] + '/graphql',
        headers={**coverage_test.headers,
                 'X-Coverage-Api-Key': os.environ['COVERAGE_API_KEY']},
        json={'query': '{ products { id name } }'},
    )
    assert response.status_code == 200
    assert response.json()['data']['products'][0]['name'] == 'Товар'
```

Замените запрос и assertion на контракт своего upstream. Заголовки не
добавляются в произвольные httpx-запросы автоматически: используйте
`coverage_test.headers`. Внутренний клиент QA добавляет привязку для своего
текущего прогона. В BFF магазина идентификатор передаётся на сервере, ключ
остаётся там.

Результат публикуется после teardown: HTTP 200 с упавшим assertion — failed.
Различаются ошибки setup/call/teardown, skip и прерывание. Повтор отправки
метаданных использует прежний UUID, поэтому потерянный ответ не создаёт дубль.
Неподтверждённый результат делает QA-прогон неуспешным. Запуск самого QA через
pytest-xdist не поддерживается существующим runner.

## Node Playwright

Перенесите `integrations/playwright/index.cjs` в свой проект или укажите путь
к нему из checkout QA. Фабрика получает `test` из вашей копии Playwright:

```javascript
const {test: base, expect} = require('@playwright/test');
const {createCoverageTest} = require('./coverage/index.cjs');
const test = createCoverageTest(base);

test('Название товара', async ({request, coverage}) => {
  const execution = await coverage.trackRun(process.env.COVERAGE_TEST_RUN_ID);
  const response = await execution.post(request, {query: '{ products { id name } }'});
  expect(response.status()).toBe(200);
  expect((await response.json()).data.products[0].name).toBe('Товар');
});
```

Добавьте reporter в `playwright.config.cjs`:

```javascript
module.exports = {
  retries: 1,
  reporter: [['list'], [require.resolve('./coverage/index.cjs')]],
};
```

Используются те же COVERAGE_URL, COVERAGE_TEST_RUN_ID, COVERAGE_API_KEY.
Фабрика регистрирует попытку для env-прогона; trackRun поддерживает также
прогон, созданный в теле теста. Передайте headers в APIRequestContext либо
настройте отправку через свой BFF. Не передавайте ключ в page-код.
Reporter обязателен: он видит окончательный результат с teardown и ждёт finish
в onEnd. Без него попытки останутся running. Retry хранится отдельной попыткой,
включая предыдущую неудачную; параллельные workers получают разные UUID.

## Отчёты и смысл показателей

| Файл в папке запуска | Содержимое |
| --- | --- |
| `index.html` | Статический отчёт с серверными результатами и полями |
| `results.json` | QA-результаты и serverTestCoverage по тестам |
| `server-test-coverage.json` | Серверные попытки, поля и обращения |
| `test-matrix.csv` | Попытка, статус, поле, hits и evidence |
| `coverage.csv` | Общее покрытие полей |

Для старых отчётов без серверных попыток сохранён прежний формат матрицы.
Node MVP дополнительно сохраняет test-coverage.json и проверяет результат после
перезапуска proxy. Reports, окружения и браузеры исключены из Git; CI публикует
отчёты как job artifacts.

Failed-тест может покрыть поля: ошибка assertion не отменяет обращение.
Selected, counted coverage и observed evidence показаны раздельно. Покрытие
поля не доказывает наличие assertion для него. Запросы без привязки остаются
в общем покрытии и отдельно считаются в API summary. Старые обращения без
evidence имеют unavailable.

Если runner погиб до finish, после закрытия прогона backend возвращает
resultMissing=true. После аварии процесса proxy счётчик inFlightRequests может
остаться ненулевым; interrupted разрешён без ожидания его обнуления.
Полные traceback остаются в QA-отчёте; API получает безопасный код ошибки.
Имена тестов задавайте без секретов и пользовательских данных.

## CI и объединение

GitHub Actions использует backend `main`, а workflow_dispatch позволяет задать
фиксированный backend_ref. Root GitLab pipeline закрепляет проверенный коммит
backend через BACKEND_SHA; обновляйте его после изменения контрактов.

При временном `409/test_requests_in_flight` finish повторяется ограниченное
число раз с тем же UUID и исходным результатом. Параллельные Playwright
`trackRun` используют один регистрационный Promise; метод `execution.post`
отключает HTTP-редиректы, чтобы API-ключ не ушёл на чужой адрес.

Сначала применяются миграция и backend API, затем QA-интеграция. Основной
frontend команды не изменён. Его будущий экран может использовать список
тестов, фильтр по полю, detail, fields и журнал с testExecutionId; контракт
описан в docs/test-coverage.md репозитория backend.
