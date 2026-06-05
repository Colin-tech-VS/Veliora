# Config Decodo + worker Playwright (crawl complet sans StreamEstate).
# Prérequis : forfait Decodo Residential (recommandé 10 Go) + DATABASE_URL Supabase.
param(
    [ValidateSet("scalingo", "local", "all")]
    [string]$Target = "all",
    [string]$ScalingoApp = "veliora"
)

$example = Join-Path $PSScriptRoot "crawl-worker-local.env.example"
$local = Join-Path $PSScriptRoot "crawl-worker-local.env"
$scalingoExample = Join-Path $PSScriptRoot "scalingo-env-residential.env.example"
$scalingoEnv = Join-Path $PSScriptRoot "scalingo-env-residential.env"
$decodoExample = Join-Path $PSScriptRoot "proxies-decodo.example.txt"

if (-not (Test-Path $local)) { Copy-Item $example $local }
if (-not (Test-Path $scalingoEnv)) { Copy-Item $scalingoExample $scalingoEnv }
if (-not (Test-Path $decodoExample)) {
    Write-Host "Manquant : proxies-decodo.example.txt" -ForegroundColor Red
    exit 1
}

Write-Host "=== Veliora × Decodo ===" -ForegroundColor Cyan
Write-Host "Forfait recommandé : 10 Go (~35 USD/mois) — veille LBC+PAP+catalogue pour 1 agence."
Write-Host "Essai : 3 Go - 11 USD le 1er mois, puis passer a 10 Go si OK."
Write-Host ""

& (Join-Path $PSScriptRoot "apply-residential-crawl.ps1") -Target $Target -ScalingoApp $ScalingoApp

Write-Host ""
Write-Host "Decodo — étapes restantes :" -ForegroundColor Yellow
Write-Host "1. Dashboard Decodo → copier user/pass → scripts/proxies-decodo.example.txt"
Write-Host "2. python scripts/configure_proxy_rotation.py --file scripts/proxies-decodo.example.txt --test"
Write-Host "3. Coller l'URL OK dans crawl-worker-local.env (CRAWL_PROXIES=...)"
Write-Host "4. playwright install chromium"
Write-Host "5. .\scripts\run-crawl-worker.ps1"
Write-Host ""
Write-Host "StreamEstate : CRAWL_SKIP_STREAMESTATE=true (code conservé, crawl ignoré)." -ForegroundColor Green
