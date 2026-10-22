import pytest

from agent.ghost.desktop import apps, voice
from agent.ghost.desktop.catalog import Catalog
from agent.ghost.desktop.learning_mode import LearningEngine
from agent.ghost.desktop.hermes_planner import propose


@pytest.fixture
def inventory(monkeypatch):
    rows = [{'Name': 'Notepad', 'AppID': 'Microsoft.Notepad!App'},
            {'Name': 'Google Chrome', 'AppID': 'Chrome'}]
    monkeypatch.setattr(apps, 'installed_apps', lambda: rows)
    return rows


def test_installed_app_launch_is_inventory_bound(inventory):
    launched = []
    assert voice.dispatch('open chrome', [], opener=launched.append)['kind'] == 'app_opened'
    assert launched == ['shell:AppsFolder\\Chrome']
    for command in ['open chrome; notepad', 'open C:/evil.exe', 'open chrome and send mail']:
        assert voice.dispatch(command, [], opener=launched.append)['kind'] == 'unknown'
    assert len(launched) == 1


def test_duplicate_apps_and_busy_do_not_launch(inventory):
    inventory.append({'Name': 'Notepad', 'AppID': 'Other'})
    assert voice.dispatch('open notepad', [])['kind'] == 'ambiguous'
    assert voice.dispatch('open chrome', [], busy=True)['kind'] == 'error'


def test_start_workflow_and_empty_request(inventory):
    flow = {'id': 'one', 'name': 'Morning routine'}
    assert voice.resolve('start Morning routine', [flow])['id'] == 'one'
    assert voice.resolve('start workflow Morning routine', [flow])['id'] == 'one'
    assert voice.resolve('start', [flow])['kind'] == 'unknown'
    assert voice.resolve('start Spotify', [{'id': 'music', 'name': 'Spotify'}])['kind'] == 'workflow'


def test_unknown_command_resolves_learned_pattern(inventory):
    class Memory:
        def list_patterns(self):
            return [{'id': 'learned', 'name': 'Morning routine'}]
    assert voice.dispatch('start Morning routine', [], learning=Memory())['kind'] == 'learned_workflow'


@pytest.fixture
def memory(tmp_path):
    catalog = Catalog(tmp_path / 'catalog.db')
    learning = LearningEngine(tmp_path / 'learning.db')
    flow = catalog.save({'name': 'Open notes', 'steps': [
        {'kind': 'open_app', 'path': r'C:\Windows\notepad.exe', 'app': 'notepad.exe'}]})
    yield catalog, learning, flow
    catalog.close()
    learning.close()


def test_hermes_composes_only_supplied_evidence(memory):
    catalog, learning, flow = memory
    def planner(request):
        assert 'path' not in str(request)
        assert 'notepad.exe' not in str(request)
        return {'routines': ['recording:' + flow['id']]}
    result = propose('Prepare my notes', catalog, learning, planner)
    assert result['kind'] == 'workflow_created'
    saved = catalog.get(result['id'])
    assert saved['steps'] == flow['steps']
    assert saved['planner'] == 'hermes'


@pytest.mark.parametrize('plan', [None, {'routines': ['invented']}, {'routines': []}, {'routines': [None]}])
def test_invalid_plan_never_creates_workflow(memory, plan):
    catalog, learning, _ = memory
    assert propose('Do something', catalog, learning, lambda _: plan)['kind'] == 'unknown'
    assert len(catalog.list()) == 1


def test_outcomes_survive_reopen(tmp_path):
    path = tmp_path / 'learning.db'
    engine = LearningEngine(path)
    engine.record_outcome(['recording:a'], True)
    engine.record_outcome(['recording:a'], False)
    engine.close()
    engine = LearningEngine(path)
    assert engine.outcome_summary('recording:a') == {'worker_completed': 1, 'worker_failed': 1}
    engine.close()


def test_without_consent_no_model_or_save(memory, monkeypatch):
    monkeypatch.setenv('DIGITALSOUL_HERMES_CONSENT', '0')
    catalog, learning, _ = memory
    assert propose('Unknown task', catalog, learning)['kind'] == 'unknown'
    assert len(catalog.list()) == 1


def test_learned_replay_rejects_missing_or_stale_review_over_stdio(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    data = tmp_path / '.ghost/notch'
    data.mkdir(parents=True)
    engine = LearningEngine(data / 'learning.sqlite3')
    steps = [{'kind': 'open_app', 'path': r'C:\Windows\notepad.exe', 'app': 'notepad.exe'},
             {'kind': 'set_text', 'path': r'C:\Windows\notepad.exe', 'app': 'notepad.exe',
              'value': 'demo', 'target': {'control_type': 'Edit'}, 'label': 'Notes'}]
    engine.record_episode(steps, name='Notes')
    learned = engine.record_episode(steps, name='Notes')
    pattern_id = learned['pattern_id']
    engine.close()
    requests = [
        {'id': 'detail', 'method': 'learning_detail', 'payload': {'id': pattern_id}},
        {'id': 'missing', 'method': 'learning_execute', 'payload': {'id': pattern_id}},
        {'id': 'stale', 'method': 'learning_execute', 'payload': {'id': pattern_id, 'approved_digest': 'stale'}},
        {'method': 'shutdown'}]
    result = subprocess.run([sys.executable, '-m', 'agent.ghost.desktop.notch_bridge', str(tmp_path)],
        input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2], timeout=20)
    assert result.returncode == 0
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    by_id = {r['id']: r for r in replies if 'id' in r}
    assert by_id['detail']['result']['approved_digest']
    assert by_id['missing']['ok'] is False
    assert by_id['stale']['ok'] is False
    assert not any(r.get('event') == 'replay_started' for r in replies)
