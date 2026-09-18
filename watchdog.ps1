$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DisabledMarker = Join-Path $Root "bot_news.disabled"
$WatchdogLog = Join-Path $Root "bot_news_watchdog.log"

function Write-WatchdogLog($Message) {
    Add-Content -LiteralPath $WatchdogLog -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

if (Test-Path -LiteralPath $DisabledMarker) {
    Write-WatchdogLog "disabled marker exists; not starting"
    exit 0
}

$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($Root) -and
        $_.CommandLine -match "main\.py"
    }

if ($running) {
    exit 0
}

Write-WatchdogLog "process not found; starting hidden"
& (Join-Path $Root "start_hidden.ps1") | Out-Null
