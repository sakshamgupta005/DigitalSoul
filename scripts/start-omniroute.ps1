param(
    [switch]$Probe,
    [string]$Model = '',
    [string]$DataDirectory = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataDirectory) { $DataDirectory = Join-Path $projectRoot '.ghost\omniroute' }
$resolvedData = [System.IO.Path]::GetFullPath($DataDirectory)
New-Item -ItemType Directory -Path $resolvedData -Force | Out-Null
$launcher = Get-Command omniroute.cmd -ErrorAction Stop
$entryPath = Join-Path (Split-Path -Parent $launcher.Source) 'node_modules\omniroute\bin\omniroute.mjs'
if (-not (Test-Path -LiteralPath $entryPath)) { throw 'OmniRoute npm entry point was not found.' }
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$env:DATA_DIR = $resolvedData
$env:OMNIROUTE_SERVER_HOST = '127.0.0.1'
$env:ANONYMIZED_TELEMETRY = 'false'
if ($Model) { $env:OMNIROUTE_MODEL = $Model }
$stdoutPath = Join-Path $projectRoot '.ghost\omniroute.stdout.log'
$stderrPath = Join-Path $projectRoot '.ghost\omniroute.stderr.log'
$omniProcess = Start-Process -FilePath $nodePath -ArgumentList @($entryPath, 'serve', '--port', '20128', '--no-open', '--no-tray', '--no-recovery', '--ready-timeout', '10000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$omniProcess.Id | Set-Content -LiteralPath (Join-Path $projectRoot '.ghost\omniroute-launch.pid')
Write-Output "OmniRoute launch PID: $($omniProcess.Id); loopback port: 20128"
try {
    if ($Probe) {
        # Keep gateway and probe in the same process job/network context.
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            $omniProcess.Refresh()
            if ($omniProcess.HasExited) { throw "OmniRoute exited with code $($omniProcess.ExitCode). Inspect .ghost logs." }
            if ((Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue) -match 'OmniRoute is running') { break }
            Start-Sleep -Milliseconds 500
        }
        $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python.exe).Source }
        & $pythonPath -m agent.ghost.integrations.connections --browser --inference --output (Join-Path $projectRoot '.ghost\connection-report.json')
    }
    Wait-Process -Id $omniProcess.Id
} finally {
    # Ctrl+C stops this launcher-owned process. No unrelated gateway is killed.
    $omniProcess.Refresh()
    if (-not $omniProcess.HasExited) { Stop-Process -Id $omniProcess.Id }
}
