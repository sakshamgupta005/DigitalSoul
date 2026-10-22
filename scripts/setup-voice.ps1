param([switch]$SkipModel)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$voicePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $voicePython)) { throw 'Create the project .venv first; see desktop/README.md.' }
$voiceTemp = Join-Path $projectRoot '.ghost\pip-temp'
New-Item -ItemType Directory -Force -Path $voiceTemp | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
Push-Location $projectRoot
try {
    $env:TEMP = $voiceTemp
    $env:TMP = $voiceTemp
    Write-Host 'Installing local voice dependencies into the project Python environment...'
    & $voicePython -m pip install -e '.[voice]' --disable-pip-version-check --timeout 20 --retries 1
    if ($LASTEXITCODE -ne 0) {
        throw 'Voice install failed. Check the pip output: DNS/connection failures require Internet access to pypi.org and files.pythonhosted.org. If a dependency has no compatible wheel, use Python 3.12 or 3.13. Then run this script again.'
    }
    & $voicePython -c "import faster_whisper, sounddevice, numpy; print('Voice imports OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Voice imports failed. Check the dependency error above.' }
    if (-not $SkipModel) {
        Write-Host 'Downloading the English speech model once from Hugging Face...'
        & $voicePython -m agent.ghost.desktop.voice_worker --download
        if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Check access to huggingface.co and try again.' }
    }
    Write-Host 'Voice setup finished. Click the notch mic again. No app restart is needed.'
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
