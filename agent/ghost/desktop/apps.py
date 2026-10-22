"""Launch names resolved from Windows' installed application inventory."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

_cache = (0, [])


def key(value):
    return ' '.join(re.sub(r'[^\w\s]', ' ', value.casefold()).split())


def installed_apps():
    global _cache
    if os.name != 'nt':
        return []
    if time.monotonic() - _cache[0] < 60:
        return _cache[1]
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
            '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress'],
            capture_output=True, text=True, encoding='utf-8-sig', timeout=8, check=True,
            creationflags=0x08000000)
        rows = json.loads(result.stdout)
        if isinstance(rows, dict):
            rows = [rows]
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('Name'), str)
                and isinstance(r.get('AppID'), str) and r['AppID'] and not any(c in r['AppID'] for c in '\r\n\x00')]
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    _cache = (time.monotonic(), rows)
    return rows


def match_app(name, rows=None):
    name = key(name)
    aliases = {'chrome': 'google chrome', 'edge': 'microsoft edge', 'word': 'word',
               'vs code': 'visual studio code', 'calc': 'calculator'}
    name = aliases.get(name, name)
    rows = installed_apps() if rows is None else rows
    matches = [r for r in rows if key(r['Name']) == name]
    return matches


def launch_installed(name, opener=None):
    matches = match_app(name)
    if len(matches) != 1:
        return {'kind': 'ambiguous' if matches else 'unknown',
                'message': 'Use the exact installed app name; that name is missing or ambiguous.'}
    app = matches[0]
    # Only an inventory AppID reaches the shell namespace, never the user's command.
    target = 'shell:AppsFolder\\' + app['AppID']
    try:
        (opener or os.startfile)(target)
    except OSError:
        return {'kind': 'error', 'message': f"Windows could not open {app['Name']}."}
    return {'kind': 'app_opened', 'app': app['Name'], 'message': f"Opening {app['Name']}"}
