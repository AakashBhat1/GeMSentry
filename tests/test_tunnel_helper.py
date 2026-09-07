"""Exercise the PowerShell preflight with stubbed HTTP commands; never open a tunnel."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='PowerShell is unavailable')


@pytest.mark.parametrize('scenario,success', [('missing_secret', False), ('disabled', False),
                                             ('anonymous_allowed', False), ('wrong_secret', False), ('secure', True)])
def test_tunnel_preflight(tmp_path, scenario, success):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (tmp_path / 'config').mkdir()
    config = {'port': 5999, 'auth_token': '' if scenario == 'missing_secret' else 'test-secret'}
    (tmp_path / 'config/server_config.json').write_text(json.dumps(config))
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/setup_tunnel.ps1', scripts / 'setup_tunnel.ps1')
    harness = scripts / 'test.ps1'
    harness.write_text('''
$ErrorActionPreference = 'Stop'
Remove-Item Env:GEMSENTRY_AUTH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:GEMSENTRY_PORT -ErrorAction SilentlyContinue
$scenario = 'SCENARIO'
function Invoke-RestMethod {
    param($Uri, $TimeoutSec, $Headers)
    if ($Uri.EndsWith('/api/auth/status')) { return @{auth_required = ($scenario -ne 'disabled')} }
    if ($scenario -eq 'wrong_secret') { throw 'Bad token' }
    if ($Headers.Authorization -ne 'Bearer test-secret') { throw 'Missing token' }
    return @{keywords = @()}
}
function Invoke-WebRequest {
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec)
    if ($scenario -eq 'anonymous_allowed') { return @{} }
    $failure = New-Object System.Exception 'Unauthorized'
    $failure | Add-Member -NotePropertyName Response -NotePropertyValue @{StatusCode = 401}
    throw $failure
}
try { & "$PSScriptRoot/setup_tunnel.ps1" -Check } catch { Write-Output 'PREFLIGHT_REJECTED'; exit 1 }
'''.replace('SCENARIO', scenario), encoding='utf-8')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
                            capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) is success, result.stdout + result.stderr
