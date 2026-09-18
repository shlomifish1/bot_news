$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DisabledMarker = Join-Path $Root "bot_news.disabled"
$Python = Join-Path $Root "venv\Scripts\python.exe"
$Main = Join-Path $Root "main.py"
$OutLog = Join-Path $Root "bot_news.stdout.log"
$ErrLog = Join-Path $Root "bot_news.stderr.log"

if (Test-Path -LiteralPath $DisabledMarker) {
    "bot_news is intentionally disabled locally; cloud service is the active owner."
    exit 0
}

if (-not (Test-Path -LiteralPath $Main)) {
    throw "main.py not found at $Main"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found under $Root\venv"
}

try {
    & $Python -c "print('ok')" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Python venv health check failed"
    }
} catch {
    throw "Python venv is not usable under $Root\venv"
}

$existing = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($Root) -and
        $_.CommandLine -match "main\.py"
    }

if ($existing) {
    "bot_news already running: $($existing.ProcessId -join ',')"
    exit 0
}

Start-Process `
    -FilePath $Python `
    -ArgumentList @($Main) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

"bot_news start requested"
