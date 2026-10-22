"""Hermes proposes compositions of demonstrated routines; it never operates the desktop."""
import copy
import json
import os
from pathlib import Path
import subprocess

from ..integrations.omniroute import OmniRouteSettings
from .steps import validate


def runtime():
    root = Path(os.environ.get('DIGITALSOUL_HERMES_ROOT') or
        str(Path(os.environ.get('LOCALAPPDATA', '')) / 'hermes/hermes-agent'))
    python = os.environ.get('DIGITALSOUL_HERMES_PYTHON')
    if not python:
        candidates = list((root.parent / 'installs').glob('*/pm-runtime/generations/*/Scripts/python.exe'))
        python = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else ''
    if not (root / 'run_agent.py').is_file() or not python or not Path(python).is_file():
        raise ValueError('Hermes is unavailable. Configure DIGITALSOUL_HERMES_ROOT and DIGITALSOUL_HERMES_PYTHON.')
    return root.resolve(), python


def infer(request):
    settings = OmniRouteSettings.from_env()
    if not settings.model:
        raise ValueError('Choose an OmniRoute model before using Hermes.')
    root, python = runtime()
    worker = Path(__file__).with_name('hermes_worker.py')
    payload = {'root': str(root), 'base_url': settings.base_url, 'api_key': settings.api_key,
               'model': settings.model, 'request': request}
    result = subprocess.run([python, str(worker), str(root)], input=json.dumps(payload),
        capture_output=True, text=True, encoding='utf-8', timeout=45,
        cwd=root, creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('Hermes could not plan this task. No computer actions were taken.')
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ValueError('Hermes returned an invalid plan; no computer actions were taken.') from None


def propose(text, catalog, learning, planner=None):
    """Copy whole evidence-backed routines, never model-supplied paths or UI locators."""
    from dotenv import load_dotenv
    load_dotenv(override=False)
    if os.environ.get('DIGITALSOUL_HERMES_CONSENT') != '1' and planner is None:
        return {'kind': 'unknown', 'message': 'No matching workflow. Record a demonstration, or enable Hermes planning in settings (.env).'}
    sources = {}
    if catalog:
        for row in catalog.list()[:50]:
            value = catalog.get(row['id'])
            if value.get('steps') and not value.get('flow'):
                sources['recording:' + row['id']] = value
    if learning:
        for pattern in learning.list_patterns()[:50]:
            sources['pattern:' + pattern['id']] = {**pattern, 'steps': pattern['skeleton']}
    if not sources:
        return {'kind': 'unknown', 'message': 'I need a demonstration first. Record this task so I can learn its controls.'}
    # No field values, URLs, window titles, screenshots or filesystem paths leave this process.
    # Names are user-authored metadata, sent only with explicit model consent.
    request = {'task': text[:512], 'routines': [
        {'id': key, 'name': value['name'], 'actions': [s['kind'] for s in value['steps']],
         'history': learning.outcome_summary(key) if learning else {}}
        for key, value in sources.items()]}
    plan = (planner or infer)(request)
    ids = plan.get('routines') if isinstance(plan, dict) else None
    if not isinstance(ids, list) or not 1 <= len(ids) <= 5 or any(not isinstance(i, str) or i not in sources for i in ids):
        return {'kind': 'unknown', 'message': 'I cannot ground that task in my demonstrations yet. Please record it once.'}
    if len(set(ids)) != len(ids):
        raise ValueError('Hermes proposed repeating a routine; review a new demonstration instead.')
    steps = [copy.deepcopy(step) for key in ids for step in sources[key]['steps']]
    validate(steps)
    saved = catalog.save({'name': text.strip()[:80], 'steps': steps,
                          'source_routines': ids, 'planner': 'hermes',
                          'apps': list(dict.fromkeys(s.get('app', '') for s in steps))})
    return {'kind': 'workflow_created', 'id': saved['id'], 'name': saved['name'],
            'workflow': saved, 'message': 'Hermes prepared a workflow from your demonstrations. Review every step before replay.'}
