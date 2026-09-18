$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DisabledMarker = Join-Path $Root "bot_news.disabled"
$LogPath = Join-Path $Root "bot_news.log"

$processes = @(Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape($Root) -and
        $_.CommandLine -match "main\.py"
    })

$lastLog = ""
if (Test-Path -LiteralPath $LogPath) {
    $lastLog = (Get-Content -LiteralPath $LogPath -Tail 1 -Encoding UTF8) -join ""
}

[pscustomobject]@{
    ok = ($processes.Count -gt 0)
    running = ($processes.Count -gt 0)
    disabled = (Test-Path -LiteralPath $DisabledMarker)
    pid = @($processes | ForEach-Object { $_.ProcessId })
    root = $Root
    last_log = $lastLog
} | ConvertTo-Json -Compress
