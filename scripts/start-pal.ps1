# Started at logon by the "pal" scheduled task (see scripts/install-autostart.ps1).
# Starts Docker Desktop if needed, waits for the database, then runs pal on 127.0.0.1:8765.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA 'pal\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'pal.log'
function Log($msg) { Add-Content -Path $log -Value "$(Get-Date -Format s) $msg" }

$docker = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
$dockerDesktop = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
$uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'

if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
    Log 'starting Docker Desktop'
    Start-Process -FilePath $dockerDesktop
}

# Ollama's own startup entry is disabled on this PC; pal starts it (its settings come from user env vars).
$ollamaApp = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
if (-not (Get-Process 'ollama*' -ErrorAction SilentlyContinue) -and (Test-Path $ollamaApp)) {
    Log 'starting Ollama'
    Start-Process -FilePath $ollamaApp
}

$deadline = (Get-Date).AddMinutes(5)
do {
    Start-Sleep -Seconds 3
    # While Docker is still starting, docker.exe writes errors to stderr; with 'Stop' that would end the script.
    try { $health = & $docker inspect -f '{{.State.Health.Status}}' pal-db-1 2>$null } catch { $health = $null }
} until ($health -eq 'healthy' -or (Get-Date) -gt $deadline)

if ($health -ne 'healthy') { Log "database not healthy after 5 minutes (status: $health); giving up"; exit 1 }

Log 'database healthy; starting pal'
Set-Location $repo
# uv reports progress (e.g. installing a new dependency) on stderr; with 'Stop' PowerShell would treat
# that as a fatal error and end the script silently before pal starts.
$ErrorActionPreference = 'Continue'
& $uv run pal serve *>> $log
Log "pal exited with code $LASTEXITCODE"
exit $LASTEXITCODE
