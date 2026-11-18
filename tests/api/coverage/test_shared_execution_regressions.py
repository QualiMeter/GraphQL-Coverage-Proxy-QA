"""Shared-run regressions use only the fixture-owned disposable infrastructure."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from uuid import UUID, uuid4

import asyncpg
import pytest

from coverage_qa.environment import ROOT
from coverage_qa.tracking import TestExecution as Execution, CoverageTrackingError


def test_repeated_pytest_launches_allocate_distinct_attempts(coverage_run, tmp_path):
    run = coverage_run
    child = tmp_path / 'test_repeat.py'
    child.write_text('def test_repeat(coverage_test):\n    assert coverage_test.started\n')
    ini = tmp_path / 'pytest.ini'
    ini.write_text('[pytest]\n')
    env = {**os.environ, 'COVERAGE_URL': run.base, 'COVERAGE_TEST_RUN_ID': run.run_id,
           'COVERAGE_API_KEY': run.api.key, 'PYTHONPATH': str(ROOT / 'src')}
    env.pop('PYTEST_ADDOPTS', None)
    for _ in range(3):
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-c', str(ini),
            '-p', 'coverage_qa.pytest_plugin', str(child)], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    rows = run.rest('Executed attempts', 'GET', f'/api/runs/{run.run_id}/tests?limit=500')
    repeated = [row for row in rows if row['testId'].endswith('test_repeat.py::test_repeat')]
    assert sorted(row['attempt'] for row in repeated) == [1, 2, 3]
    assert all(row['status'] == 'passed' for row in repeated)

    def parallel(_):
        execution = Execution(run.base, run.run_id, run.api.key, repeated[0]['testId'], 'test_repeat', attempt=None)
        execution.start()
        # Lost registration response recovered by resending the original UUID.
        execution.start()
        return execution.finish('passed', 1)['execution']
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel_rows = list(pool.map(parallel, range(4)))
    assert sorted(row['attempt'] for row in parallel_rows) == [4, 5, 6, 7]
    with pytest.raises(CoverageTrackingError, match='HTTP 409'):
        Execution(run.base, run.run_id, run.api.key, repeated[0]['testId'], 'test_repeat', attempt=2).start()


def test_more_than_500_requests_remain_scoped_to_parallel_executions(coverage_run):
    run = coverage_run
    own = run.execution
    other = Execution(run.base, run.run_id, run.api.key, 'other-test', 'Other', attempt=None).start()
    async def seed():
        connection = await asyncpg.connect(run.stack.database_url.replace('postgresql+asyncpg:', 'postgresql:'))
        try:
            await connection.executemany('''INSERT INTO observed_requests
                (id, run_id, test_execution_id, http_method, analysis_status)
                VALUES ($1, $2, $3, 'POST', 'unsupported')''',
                [(uuid4(), UUID(run.run_id), UUID(execution))
                 for execution, count in [(own.id, 1003), (other.id, 507)] for _ in range(count)])
        finally:
            await connection.close()
    asyncio.run(seed())
    snapshot = own.read()
    rows = run.request_pages('All own observations')
    assert len(rows) == len(snapshot['requests']) == snapshot['execution']['requestCount'] == 1003
    assert all(row['testExecutionId'] == own.id for row in rows)
    other_rows = other.finish('passed', 1)['requests']
    assert len(other_rows) == 507
    assert not {row['id'] for row in rows} & {row['id'] for row in other_rows}


def test_deployed_cli_validates_run_and_repeats_safe_scenario(business_stack, tmp_path):
    import httpx
    stack = business_stack
    with httpx.Client(base_url=stack.proxy.base, headers={'X-Coverage-Api-Key': stack.key}, trust_env=False) as client:
        service = client.post('/api/services', json={'key': 'forma-demo-shop', 'name': 'Disposable FORMA',
                              'endpointUrl': stack.upstream.base + '/graphql'})
        assert service.status_code == 201, service.text
        schema = client.post('/api/schemas/import/introspection', json={'serviceId': service.json()['id']})
        assert schema.status_code == 201, schema.text
        run = client.post('/api/runs', json={'snapshotId': schema.json()['id'], 'name': 'Safe repeated CLI'})
        assert run.status_code == 201, run.text
        run_id = run.json()['id']
        env = {**os.environ, 'QA_DEPLOYED_COVERAGE_URL': stack.proxy.base,
               'QA_DEPLOYED_SHOP_API_URL': stack.upstream.base, 'COVERAGE_API_KEY': stack.key,
               'PYTHONPATH': str(ROOT / 'src')}
        command = [sys.executable, '-m', 'coverage_qa', 'test', '--suite', 'e2e',
            '--deployed-run-id', run_id, '--coverage-url', stack.proxy.base,
            '--upstream-url', stack.upstream.base, '--report-dir', str(tmp_path / 'reports'), '--']
        safe = 'tests/e2e/test_shop.py::test_storefront_catalog_and_cart'
        try:
            for _ in range(2):
                result = subprocess.run([*command, safe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
                assert result.returncode == 0, result.stdout + result.stderr
            rows = client.get(f'/api/runs/{run_id}/tests').json()
            assert sorted(row['attempt'] for row in rows) == [1, 2]
            assert all(row['status'] == 'passed' for row in rows)
            operations = client.get(f'/api/runs/{run_id}/operations').json()
            assert all(row['operationType'] == 'query' for row in operations)
            # A forged selection file cannot expand the server-reviewed allowlist.
            selection = tmp_path / 'selection.json'
            selection.write_text('["tests/e2e/test_shop.py::test_storefront_checkout_and_cancel"]')
            unsafe = subprocess.run([*command[:-1], '--selection-file', str(selection)], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=30)
            assert unsafe.returncode != 0
            assert len(client.get(f'/api/runs/{run_id}/tests').json()) == 2
            assert client.get(f'/api/runs/{run_id}/test-coverage').json()['collectionStatus'] == 'running'
        finally:
            assert client.post(f'/api/runs/{run_id}/finish').status_code == 200
