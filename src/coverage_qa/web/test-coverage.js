'use strict';

function renderTestCoverage(snapshots, box) {
  box.append(el('p', 'Серверные выполнения всех сценариев этого запуска. Поля запроса, наличие в ответе и результат теста показаны отдельно.'));
  if (!snapshots.length) {
    box.append(el('div', 'Ответы Test Coverage ещё не получены. Прямые запросы в обход прокси не создают связь с полями.', 'empty'));
    return;
  }
  const toolbar = el('div', undefined, 'test-coverage-toolbar');
  const search = el('input');
  search.id = 'test-coverage-filter';
  search.placeholder = 'Найти тесты по полю, например User.id';
  search.setAttribute('aria-label', 'Поле для поиска тестов');
  search.value = S.coverageField || '';
  const status = el('select');
  status.setAttribute('aria-label', 'Результат серверного теста');
  for (const value of ['all', 'passed', 'failed', 'skipped', 'interrupted', 'running']) {
    const option = el('option', value === 'all' ? 'Все результаты' : labels[value] || value);
    option.value = value;
    status.append(option);
  }
  status.value = S.coverageStatus || 'all';
  toolbar.append(search, status);
  const matrix = el('a', 'Матрица тест–поле (CSV)');
  matrix.href = PANEL_BASE + `reports/runs/${S.view}/test-matrix.csv`;
  matrix.download = 'test-matrix.csv';
  const source = el('a', 'Ответы backend (JSON)');
  source.href = PANEL_BASE + `reports/runs/${S.view}/server-test-coverage.json`;
  source.download = 'server-test-coverage.json';
  if (S.run?.reportUrl) toolbar.append(matrix, source);
  const counter = el('p', '', 'muted');
  const list = el('div');
  box.append(toolbar, counter, list);

  function refresh() {
    S.coverageField = search.value;
    S.coverageStatus = status.value;
    const needle = search.value.trim().toLowerCase();
    const selected = snapshots.filter(snapshot =>
      (status.value === 'all' || snapshot.execution.status === status.value) &&
      (!needle || snapshot.fields.some(field => `${field.typeName}.${field.fieldName}`.toLowerCase().includes(needle))));
    counter.textContent = `Выполнений: ${selected.length} из ${snapshots.length}. Повторные попытки показаны отдельно.`;
    list.replaceChildren();
    for (const snapshot of selected) {
      const execution = snapshot.execution;
      const section = el('section', undefined, 'server-test-coverage');
      section.dataset.testid = 'server-test-execution';
      const heading = el('div', undefined, 'server-test-heading');
      heading.append(el('h3', execution.name), badge(execution.status));
      section.append(heading, el('p', `${execution.framework} · попытка ${execution.attempt} · ${execution.durationMs === null ? 'выполняется' : execution.durationMs + ' мс'}`));
      if (execution.failureCode) {
        const reasons = {assertion_failed: 'Ошибка проверки значения', setup_failed: 'Ошибка подготовки теста',
          teardown_failed: 'Ошибка очистки после теста', timeout: 'Таймаут теста',
          test_failed: 'Тест завершился ошибкой', runner_interrupted: 'Выполнение прервано'};
        section.append(el('p', reasons[execution.failureCode] || execution.failureCode, 'test-result-missing'));
      }
      section.append(el('p', `Записей журнала: ${execution.requestCount} · учтено: ${execution.countedRequestCount} · покрыто полей: ${execution.coveredFieldCount}/${execution.totalFields} (${execution.coveragePercent}%)`));
      if (execution.resultMissing) section.append(el('p', 'Сбор завершён, но результат этого теста ещё не подтверждён.', 'test-result-missing'));
      section.append(code(`testId: ${execution.testId}\nexecutionId: ${execution.id}\nrunId: ${execution.runId}\nсбор: ${execution.collectionStatus}\nисточник: backend · ${snapshot.fetchedAt}`));
      const table = el('table', undefined, 'server-fields-table');
      const header = el('tr');
      for (const title of ['Поле', 'Выбрано', 'Hits', 'Наблюдалось', 'Отсутствовало', 'Неопределённо', 'Недоступно']) header.append(el('th', title));
      const head = el('thead');
      head.append(header);
      table.append(head);
      const body = el('tbody');
      for (const field of snapshot.fields.filter(field => !needle || `${field.typeName}.${field.fieldName}`.toLowerCase().includes(needle))) {
        const row = el('tr');
        for (const value of [`${field.typeName}.${field.fieldName}`, field.selectedRequestCount, field.hits,
          field.observedCount, field.notObservedCount, field.indeterminateCount, field.unavailableCount]) row.append(el('td', value));
        body.append(row);
      }
      table.append(body);
      const wrapper = el('div', undefined, 'server-fields-scroll');
      wrapper.append(table);
      section.append(wrapper);
      if (!snapshot.fields.length) section.append(el('p', 'Поля не зафиксированы. Результат теста сохраняется и без GraphQL-запросов.'));
      const requests = el('details');
      requests.append(el('summary', `Связанные запросы: ${snapshot.requests.length}`));
      for (const request of snapshot.requests) requests.append(code(`${request.id} · ${request.operationName || 'Без имени'} · HTTP ${request.httpStatus} · ${request.analysisStatus}`));
      section.append(requests);
      list.append(section);
    }
    if (!selected.length) list.append(el('div', 'По выбранным полю и результату тестов нет.', 'empty'));
  }
  search.oninput = refresh;
  status.onchange = refresh;
  refresh();
}
