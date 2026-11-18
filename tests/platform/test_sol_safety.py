import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from coverage_qa.control_panel import Manager, create_app
from coverage_qa.gitlab_ci import GitLabClient
from coverage_qa.tracking import TestExecution as Execution

SAFE = 'tests/e2e/test_shop.py::test_storefront_catalog_and_cart'
UNSAFE = 'tests/e2e/test_shop.py::test_storefront_checkout_and_cancel'


def test_deployed_panel_rejects_unsafe_e2e_before_validation(tmp_path):
    manager = Manager(tmp_path / 'reports')
    manager._catalog = [{'id': UNSAFE, 'group': 'e2e'}]
    with patch('coverage_qa.control_panel.validate_deployed_run') as validate:
        with pytest.raises(HTTPException) as error:
            manager.start([UNSAFE], 'Unsafe', False, str(uuid4()))
        assert error.value.status_code == 400
        validate.assert_not_called()


@pytest.mark.parametrize('host', ['http://127.0.0.1', 'http://169.254.169.254', 'https://10.0.0.1',
    'https://[::1]', 'file:///etc/passwd', 'https://user:secret@gitlab.com', 'https://evil.test',
    'https://gitlab.com@evil.test', 'https://gitlab.com/evil', 'https://gitlab.com?token=secret'])
def test_gitlab_rejects_untrusted_host_without_http(host):
    with patch('httpx.Client') as client:
        with pytest.raises(ValueError):
            GitLabClient(host=host, token='secret').get_pipeline('project', 1)
        client.assert_not_called()


@pytest.mark.parametrize('path,method', [('/api/gitlab/trigger', 'POST'), ('/api/gitlab/pipelines/1/2?host=http://127.0.0.1', 'GET'), ('/api/gitlab/gate', 'POST')])
def test_redundant_panel_gitlab_endpoints_are_disabled(tmp_path, path, method):
    manager = Manager(tmp_path / 'reports')
    with TestClient(create_app(manager)) as client, patch('coverage_qa.gitlab_ci.GitLabClient', side_effect=AssertionError('network')), patch('coverage_qa.gitlab_ci.CoverageQualityGate', side_effect=AssertionError('network')):
        response = client.request(method, path, headers={'X-Panel-Token': manager.token, 'X-GitLab-Token': 'secret'},
                                  json={'project_id': '1', 'token': 'secret', 'host': 'http://169.254.169.254', 'run_id': str(uuid4())})
    assert response.status_code == 410
    assert 'secret' not in response.text and '169.254' not in response.text


def test_tracking_request_pages_are_scoped_to_execution(monkeypatch):
    execution = Execution('http://coverage.test', str(uuid4()), 'key', 'test', 'Test')
    rows = [{'id': str(i), 'testExecutionId': execution.id} for i in range(1003)]
    seen = []
    def response(method, path, payload=None):
        url = httpx.URL('http://coverage.test' + path)
        if url.path.endswith('/requests'):
            assert url.params['testExecutionId'] == execution.id
            offset = int(url.params['offset'])
            seen.append(offset)
            return httpx.Response(200, json=rows[offset:offset+500], headers={'X-Total-Count': str(len(rows))})
        if url.path.endswith('/fields'):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={'id': execution.id, 'runId': execution.run_id})
    monkeypatch.setattr(execution, '_request', response)
    assert execution.read()['requests'] == rows
    assert seen == [0, 500, 1000]


def test_cli_rejects_malformed_deployed_run_before_subprocess(monkeypatch):
    from coverage_qa import runner
    monkeypatch.setenv('COVERAGE_API_KEY', 'server-key')
    monkeypatch.setattr('sys.argv', ['coverage-qa', '--suite', 'e2e', '--deployed-run-id', 'invalid',
        '--coverage-url', 'http://coverage.test', '--upstream-url', 'http://shop.test', '--', SAFE, '--collect-only'])
    with patch.object(runner.subprocess, 'call') as child:
        with pytest.raises(SystemExit) as error:
            runner.main()
        assert error.value.code == 2
        child.assert_not_called()


def test_reporting_request_pages_do_not_mix_parallel_executions(monkeypatch):
    from tests.support.coverage_client import CoverageRun
    run = CoverageRun(None, SimpleNamespace(proxy=SimpleNamespace(base='http://coverage.test')), {})
    run.run_id, run.execution = 'run', SimpleNamespace(id='own')
    own = [{'id': str(i), 'testExecutionId': 'own'} for i in range(1003)]
    other = [{'id': 'foreign', 'testExecutionId': 'other'}] * 501
    offsets = []
    def rest(title, method, path):
        params = httpx.URL('http://coverage.test' + path).params
        rows = own if params.get('testExecutionId') == 'own' else other + own
        offset = int(params.get('offset', 0))
        offsets.append(offset)
        return rows[offset:offset+int(params['limit'])]
    monkeypatch.setattr(run, 'rest', rest)
    assert run.request_pages('own requests') == own
    assert offsets == [0, 500, 1000]


def test_gitlab_redirect_never_sends_token_to_second_host(monkeypatch):
    original = httpx.Client
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(307, headers={'Location': 'http://169.254.169.254/secret'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        GitLabClient(token='glpat-test-only').get_pipeline('project', 1)
    assert len(seen) == 1 and seen[0].url.host == 'gitlab.com'


@pytest.mark.parametrize('node', [UNSAFE, SAFE + '[injected]', 'tests/e2e/../e2e/test_shop.py::test_storefront_catalog_and_cart', 'arbitrary::test'])
def test_deployed_allowlist_is_exact(node):
    from coverage_qa.deployed import validate_deployed_scenarios
    validate_deployed_scenarios([SAFE])
    with pytest.raises(HTTPException):
        validate_deployed_scenarios([SAFE, node])


def test_panel_launches_allowlisted_scenario_with_server_targets(tmp_path):
    manager = Manager(tmp_path / 'reports')
    manager._catalog = [{'id': SAFE, 'group': 'e2e'}]
    run_id = str(uuid4())
    with patch('coverage_qa.control_panel.validate_deployed_run', return_value=(run_id, 'https://coverage.test', 'https://shop.test')), patch('coverage_qa.control_panel.subprocess.Popen') as process, patch('threading.Thread.start'):
        process.return_value.pid = 123
        ident = manager.start([SAFE], 'Safe', False, run_id)
    command = process.call_args.args[0]
    assert command[command.index('--deployed-run-id') + 1] == run_id
    assert command[command.index('--coverage-url') + 1] == 'https://coverage.test'
    assert json.loads((manager.folder(ident) / 'selection.json').read_text()) == [SAFE]


@pytest.mark.parametrize('status,service_exists,matching', [('completed', True, True), ('failed', True, True), ('running', False, True), ('running', True, False)])
def test_deployed_validation_rejects_inactive_or_foreign_runs(monkeypatch, status, service_exists, matching):
    from coverage_qa.deployed import validate_deployed_target
    run_id = str(uuid4())
    monkeypatch.setenv('QA_DEPLOYED_COVERAGE_URL', 'https://coverage.test')
    monkeypatch.setenv('QA_DEPLOYED_SHOP_API_URL', 'https://shop.test')
    monkeypatch.setenv('COVERAGE_API_KEY', 'server-secret')
    original = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == '/api/services':
            return httpx.Response(200, json=[{'id': 'forma', 'key': 'forma-demo-shop'}] if service_exists else [])
        assert request.url.path == '/api/runs'
        assert request.url.params['serviceId'] == 'forma'
        return httpx.Response(200, json=[{'id': run_id if matching else str(uuid4()), 'status': status}])
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(HTTPException) as error:
        validate_deployed_target(run_id, 'https://coverage.test', 'https://shop.test')
    assert error.value.status_code == 409
    assert all(request.url.host == 'coverage.test' for request in calls)
    assert 'server-secret' not in str(error.value.detail)


def test_snapshot_reads_all_own_requests_beyond_first_run_page(monkeypatch):
    from tests.support.coverage_client import CoverageRun
    own = [{'id': str(i), 'testExecutionId': 'own'} for i in range(1003)]
    other = [{'id': 'foreign-' + str(i), 'testExecutionId': 'other'} for i in range(507)]
    run = CoverageRun(SimpleNamespace(entry={"events": []}), SimpleNamespace(proxy=SimpleNamespace(base='http://coverage.test')), {})
    run.run_id = 'run'
    run.execution = SimpleNamespace(id='own', read=lambda: {'execution': {'id': 'own'}})
    run.record = {'requestDetails': {}, 'checkpoints': [], 'exchanges': []}
    def rest(title, method, path):
        url = httpx.URL('http://coverage.test' + path)
        if url.path.endswith('/coverage'):
            return {'types': [], 'summary': {}}
        if url.path.endswith('/operations'):
            return []
        if '/requests/' in url.path:
            return {'id': url.path.rsplit('/', 1)[1]}
        params = url.params
        rows = own if params.get('testExecutionId') == 'own' else other + own
        offset = int(params.get('offset', 0))
        return rows[offset:offset + int(params.get('limit', 100))]
    monkeypatch.setattr(run, 'rest', rest)
    snapshot = run.snapshot('Shared run')
    assert len(snapshot['requests']) == 1003
    assert all(row['testExecutionId'] == 'own' for row in snapshot['requests'])


def test_cli_forwards_normalized_validated_run(monkeypatch, tmp_path):
    from coverage_qa import runner
    from coverage_qa import deployed
    run_id = str(uuid4())
    monkeypatch.setenv('COVERAGE_API_KEY', 'server-key')
    monkeypatch.setattr('sys.argv', ['coverage-qa', '--suite', 'e2e', '--deployed-run-id', run_id.upper(),
        '--coverage-url', 'https://coverage.test/', '--upstream-url', 'https://shop.test/',
        '--report-dir', str(tmp_path / 'reports'), '--', SAFE])
    monkeypatch.setattr(deployed, 'validate_deployed_target', lambda *args: (run_id, 'https://coverage.test', 'https://shop.test'))
    with patch.object(runner.subprocess, 'call', return_value=0) as child:
        assert runner.main() == 0
    command = child.call_args.args[0]
    assert '--deployed-run-id=' + run_id in command
    assert '--coverage-url=https://coverage.test' in command
