param([switch]$Offline, [switch]$Release)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Project Python venv not found. Follow desktop/README.md.'
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    if (Test-Path -LiteralPath (Join-Path $cargoBin 'cargo.exe')) {
        $env:PATH = "$cargoBin;$env:PATH"
    }
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required. Install the Rust MSVC toolchain and Windows build tools.'
}
& $pythonPath -c "import win32api, pythoncom, psutil; from pywinauto import Desktop; print('Windows automation imports OK')"
if ($LASTEXITCODE -ne 0) {
    throw 'Windows dependencies unavailable. Run .\.venv\Scripts\python.exe -m pip install -e ".[windows,test]"; see desktop/README.md.'
}
$msysBin = 'C:\msys64\ucrt64\bin'
if (Test-Path -LiteralPath (Join-Path $msysBin 'gcc.exe')) {
    $env:PATH = "$msysBin;$env:PATH"
}
# Prefer the writable cache prepared during sandbox builds, when present.
$projectCache = Join-Path $projectRoot '.ghost\cargo-home'
if (-not $env:CARGO_HOME -and (Test-Path -LiteralPath $projectCache)) {
    $env:CARGO_HOME = $projectCache
}
if (-not $env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR = Join-Path $env:USERPROFILE '.notch-target'
}
# Stop any running notch instance and orphaned WebView2 processes so Cargo and WebView2 can start cleanly without locks.
Get-Process -Name 'digitalsoul-notch' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { 
    ($_.CommandLine -like '*in.digitalsoul.notch*' -and $_.Name -eq 'msedgewebview2.exe') -or 
    ($_.CommandLine -like '*notch_bridge*' -and $_.Name -like 'python*.exe')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 250
$env:DIGITALSOUL_PROJECT_DIR = $projectRoot
$cargoArgs = @('run', '--manifest-path', (Join-Path $projectRoot 'desktop\src-tauri\Cargo.toml'))
if ($Offline) { $cargoArgs += '--offline' }
if ($Release) { $cargoArgs += '--release' }
& cargo @cargoArgs
if ($LASTEXITCODE -ne 0) { throw "Notch launch failed (exit $LASTEXITCODE)." }
