# Lance le worker crawl local (Playwright + proxies residentiels).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $PSScriptRoot "crawl-worker-local.env"

if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $PSScriptRoot "crawl-worker-local.env.example") $envFile
    Write-Host "Cree $envFile - remplissez DATABASE_URL et CRAWL_PROXIES, puis relancez." -ForegroundColor Yellow
    exit 1
}

Set-Location $root

$playwright = Get-Command playwright -ErrorAction SilentlyContinue
if (-not $playwright) {
    Write-Host "Playwright CLI absent. Lancez : pip install -r requirements.txt ; playwright install chromium" -ForegroundColor Yellow
}

Write-Host "Demarrage worker crawl Veliora (Ctrl+C pour arreter)..." -ForegroundColor Green
& python (Join-Path $PSScriptRoot "crawl_worker.py")
