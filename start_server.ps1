# start_server.ps1
# Start the GeMSentry server on Windows for office desktop hosting

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "           Starting GeMSentry Server Daemon               " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python 3.8+ is required. Please install Python and ensure it is in your PATH."
    Exit
}

# 2. Virtual environment check
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

# 3. Activate venv
Write-Host "Activating virtual environment..." -ForegroundColor DarkGray
& .venv\Scripts\Activate.ps1

# 4. Check dependencies
Write-Host "Checking dependencies..." -ForegroundColor DarkGray
python -m pip install -r requirements.txt --quiet

# 5. Playwright browser check
python -m playwright install chromium

# 6. Launch Server
Write-Host "`nLaunching GeMSentry Backend Server..." -ForegroundColor Green
python run.py
