param([switch]$Offline, [switch]$Release)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Project Python venv not found. Follow desktop/README.md.'
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required. Install the Rust MSVC toolchain and Windows build tools.'
}
& $pythonPath -c "import win32api, pythoncom, psutil; from pywinauto import Desktop; print('Windows automation imports OK')"
if ($LASTEXITCODE -ne 0) {
    throw 'Windows dependencies unavailable. Run .\.venv\Scripts\python.exe -m pip install -e ".[windows,test]"; see desktop/README.md.'
}
# Prefer the writable cache prepared during sandbox builds, when present.
$projectCache = Join-Path $projectRoot '.ghost\cargo-home'
if (-not $env:CARGO_HOME -and (Test-Path -LiteralPath $projectCache)) {
    $env:CARGO_HOME = $projectCache
}
if (-not $env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR = Join-Path $projectRoot '.ghost\notch-target'
}
$env:DIGITALSOUL_PROJECT_DIR = $projectRoot
$cargoArgs = @('run', '--manifest-path', (Join-Path $projectRoot 'desktop\src-tauri\Cargo.toml'))
if ($Offline) { $cargoArgs += '--offline' }
if ($Release) { $cargoArgs += '--release' }
& cargo @cargoArgs
if ($LASTEXITCODE -ne 0) { throw "Notch launch failed (exit $LASTEXITCODE)." }
