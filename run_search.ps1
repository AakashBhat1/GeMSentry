# run_search.ps1
# Setup virtual environment and run the scraper on Windows

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "        Setting up GeM RFP Acquisition System...          " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python installation
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCheck) {
    Write-Error "Python is not installed or not in your PATH. Please install Python 3.8+."
    Exit
}

# 2. Virtual Environment Setup
if (-not (Test-Path -Path ".venv")) {
    Write-Host "Creating Python virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

# 3. Activate Virtual Environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .venv\Scripts\Activate.ps1

# 4. Install requirements
Write-Host "Installing/updating dependencies..." -ForegroundColor Yellow
python -m pip install -r requirements.txt

# 5. Install Playwright Chromium
Write-Host "Initializing Playwright Chromium browser..." -ForegroundColor Yellow
python -m playwright install chromium

# 6. Run the scraper script
Write-Host "Launching GeM acquisition script..." -ForegroundColor Green
python run.py

Write-Host "`nExecution completed. Press any key to exit..." -ForegroundColor Cyan
$null = [System.Console]::ReadKey($true)
