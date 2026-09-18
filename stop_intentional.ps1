$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DisabledMarker = Join-Path $Root "bot_news.disabled"

"disabled intentionally at $(Get-Date -Format s)" | Set-Content -LiteralPath $DisabledMarker -Encoding UTF8

$targets = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($Root) -and
        $_.CommandLine -match "main\.py"
    }

foreach ($proc in $targets) {
    taskkill.exe /F /T /PID $proc.ProcessId | Out-Null
}

"bot_news disabled; stopped $($targets.Count) process(es)"
