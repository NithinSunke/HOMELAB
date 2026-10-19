# Sets up the virtual environment and dependencies on Windows.
# Run from the project folder:  .\setup.ps1
$ErrorActionPreference = "Stop"

Write-Host "Creating virtual environment..." -ForegroundColor Cyan
python -m venv .venv

Write-Host "Installing dependencies..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $token = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content ".env") -replace "RMAN_CONSOLE_TOKEN=change-me", "RMAN_CONSOLE_TOKEN=$token" |
        Set-Content ".env"
    Write-Host ""
    Write-Host "Generated .env with this API token:" -ForegroundColor Green
    Write-Host "  $token" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Now edit .env and set RMAN_CONSOLE_KNOWN_HOSTS and RMAN_CONSOLE_DB." -ForegroundColor Cyan
} else {
    Write-Host ".env already exists, leaving it alone." -ForegroundColor Yellow
}

Write-Host "Done. Start the app with .\run.ps1" -ForegroundColor Green
