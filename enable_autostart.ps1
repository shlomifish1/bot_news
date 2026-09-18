$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DisabledMarker = Join-Path $Root "bot_news.disabled"

if (Test-Path -LiteralPath $DisabledMarker) {
    Remove-Item -LiteralPath $DisabledMarker -Force
}

& (Join-Path $Root "start_hidden.ps1")
