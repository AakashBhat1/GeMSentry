# Verify the running application before exposing it through Cloudflare.
param ([switch]$Quick, [switch]$Help, [switch]$Check)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Host 'Usage: setup_tunnel.ps1 [-Quick | -Check | -Help]'
    Write-Host 'Configure an access key and start GeMSentry first. -Check only verifies authentication.'
    return
}
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$ConfigPath = Join-Path $RepoRoot 'config/server_config.json'
$Port = 5000
$Token = ''
if (Test-Path -LiteralPath $ConfigPath) {
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($cfg.port) { $Port = [int]$cfg.port }
    if ($cfg.auth_token) { $Token = [string]$cfg.auth_token }
}
if (Test-Path Env:GEMSENTRY_PORT) { $Port = [int]$env:GEMSENTRY_PORT }
if (Test-Path Env:GEMSENTRY_AUTH_TOKEN) { $Token = $env:GEMSENTRY_AUTH_TOKEN }
$Token = $Token.Trim()
if (-not $Token) { throw 'Refusing tunnel startup: configure a GeMSentry access key first.' }
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid GeMSentry port.' }
$Origin = "http://127.0.0.1:$Port"
try {
    $status = Invoke-RestMethod -Uri "$Origin/api/auth/status" -TimeoutSec 5
    if ($status.auth_required -ne $true) { throw 'Authentication is disabled.' }
    $anonymousRejected = $false
    try {
        $null = Invoke-WebRequest -Uri "$Origin/api/keywords" -UseBasicParsing -TimeoutSec 5
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) {
            $anonymousRejected = $true
        } else { throw }
    }
    if (-not $anonymousRejected) { throw 'Anonymous API request was accepted.' }
    $null = Invoke-RestMethod -Uri "$Origin/api/keywords" -Headers @{Authorization = "Bearer $Token"} -TimeoutSec 5
} catch {
    throw 'Refusing tunnel startup: start GeMSentry with the configured access key and verify local authentication first.'
}
Write-Host "Authentication verified on $Origin" -ForegroundColor Green
if ($Check) { return }
$CloudflaredPath = Join-Path $RepoRoot 'tools/cloudflared.exe'
$cloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflaredCmd -and -not (Test-Path -LiteralPath $CloudflaredPath)) {
    $null = New-Item -ItemType Directory -Path (Split-Path $CloudflaredPath) -Force
    $DownloadUrl = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $CloudflaredPath -UseBasicParsing
}
$Executable = if ($cloudflaredCmd) { $cloudflaredCmd.Source } else { $CloudflaredPath }
$choice = if ($Quick) { '1' } else { Read-Host '1: Quick tunnel, 2: Named tunnel guide, Q: Quit' }
switch ($choice) {
    '1' { & $Executable tunnel --url $Origin }
    '2' {
        Write-Host 'Keep the application access key configured for every server restart.'
        Write-Host "& '$Executable' tunnel login"
        Write-Host "& '$Executable' tunnel create gemsentry-tunnel"
        Write-Host "& '$Executable' tunnel route dns gemsentry-tunnel tenders.yourcompany.com"
        Write-Host "& '$Executable' tunnel run --url $Origin gemsentry-tunnel"
        Write-Host 'Run this helper with -Check before starting any persistent tunnel or service.'
    }
}
