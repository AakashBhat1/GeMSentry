$ErrorActionPreference = "Stop"
$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Error "Google Chrome was not found."
    exit 1
}
$profile = Join-Path $env:LOCALAPPDATA "GeMSentryGemAssistant\ChromeProfile"
New-Item -ItemType Directory -Force -Path $profile | Out-Null
Write-Host "Launching a separate Chrome profile. Sign in to GeM manually."
Write-Host "Keep this window open. Do not expose debugging port 9222 to your network."
Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=9222",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--no-default-browser-check",
    "https://gem.gov.in/"
)
