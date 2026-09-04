# setup_tunnel.ps1
# Cloudflare Tunnel setup helper for GeMSentry on Windows

param (
    [switch]$Quick,
    [switch]$Help
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "         GeMSentry Cloudflare Tunnel Setup Helper         " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$CloudflaredPath = Join-Path $PSScriptRoot "..\tools\cloudflared.exe"
$CloudflaredPath = [System.IO.Path]::GetFullPath($CloudflaredPath)

# Check if cloudflared exists in tools or system PATH
$cloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflaredCmd -and -not (Test-Path $CloudflaredPath)) {
    Write-Host "`n[1/3] cloudflared.exe not found. Downloading official Cloudflare binary..." -ForegroundColor Yellow
    $ToolsDir = Split-Path $CloudflaredPath
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    }
    $DownloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $CloudflaredPath -UseBasicParsing
        Write-Host "Successfully downloaded cloudflared.exe to $CloudflaredPath" -ForegroundColor Green
    } catch {
        Write-Error "Failed to auto-download cloudflared: $_"
        Write-Host "You can manually download cloudflared-windows-amd64.exe from:" -ForegroundColor Yellow
        Write-Host "https://github.com/cloudflare/cloudflared/releases/latest" -ForegroundColor Cyan
        Write-Host "And place it at: $CloudflaredPath" -ForegroundColor Yellow
        Exit
    }
}

$Executable = if ($cloudflaredCmd) { "cloudflared" } else { $CloudflaredPath }

# Read configured port from server_config.json
$ConfigPath = Join-Path $PSScriptRoot "..\config\server_config.json"
$Port = 5000
if (Test-Path $ConfigPath) {
    try {
        $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($cfg.port) { $Port = $cfg.port }
    } catch {}
}

Write-Host "`nTarget Local Service: http://localhost:$Port" -ForegroundColor Green
Write-Host "Select an option below:" -ForegroundColor White
Write-Host "  [1] Quick Tunnel (Instant public HTTPS URL, no domain needed, great for testing)" -ForegroundColor Cyan
Write-Host "  [2] Custom Domain Setup Guide (Persistent domain like tenders.yourcompany.com)" -ForegroundColor Cyan
Write-Host "  [3] Install as Windows Service (Runs in background on desktop boot)" -ForegroundColor Cyan
Write-Host "  [Q] Quit" -ForegroundColor DarkGray

if ($Quick) {
    $choice = "1"
} else {
    $choice = Read-Host "`nEnter your choice (1/2/3/Q)"
}

switch ($choice) {
    "1" {
        Write-Host "`nStarting Quick Tunnel to http://localhost:$Port..." -ForegroundColor Green
        Write-Host "Look for the URL ending with '.trycloudflare.com' below:`n" -ForegroundColor Yellow
        & $Executable tunnel --url "http://localhost:$Port"
    }
    "2" {
        Write-Host "`n================ Custom Domain Setup Guide ================" -ForegroundColor Cyan
        Write-Host "To link GeMSentry to your own domain (e.g. tenders.yourcompany.com):" -ForegroundColor White
        Write-Host "1. Log in to Cloudflare from your terminal:" -ForegroundColor Yellow
        Write-Host "   & `"$Executable`" tunnel login" -ForegroundColor White
        Write-Host "2. Create a named tunnel:" -ForegroundColor Yellow
        Write-Host "   & `"$Executable`" tunnel create gemsentry-tunnel" -ForegroundColor White
        Write-Host "3. Route DNS for your subdomain:" -ForegroundColor Yellow
        Write-Host "   & `"$Executable`" tunnel route dns gemsentry-tunnel tenders.yourcompany.com" -ForegroundColor White
        Write-Host "4. Run your tunnel:" -ForegroundColor Yellow
        Write-Host "   & `"$Executable`" tunnel run --url http://localhost:$Port gemsentry-tunnel" -ForegroundColor White
        Write-Host "==========================================================" -ForegroundColor Cyan
    }
    "3" {
        Write-Host "`n================ Windows Service Setup ================" -ForegroundColor Cyan
        Write-Host "To make the tunnel run automatically in the background on boot:" -ForegroundColor White
        Write-Host "1. Run an elevated (Run as Administrator) PowerShell window." -ForegroundColor Yellow
        Write-Host "2. Execute:" -ForegroundColor Yellow
        Write-Host "   & `"$Executable`" service install" -ForegroundColor White
        Write-Host "   Start-Service cloudflared" -ForegroundColor White
        Write-Host "==========================================================" -ForegroundColor Cyan
    }
    Default {
        Write-Host "Exiting setup." -ForegroundColor DarkGray
    }
}
