# Exporte les variables Scalingo vers un fichier local pour remplir le .env du VPS.
# Usage : .\scripts\export-scalingo-for-vps.ps1
# Sortie : scripts\ovh-vps-fill.env (gitignored)

$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot "ovh-vps-fill.env"
$app = "veliora"

function Get-ScalingoCli {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\scalingo_1.44.0_windows_amd64\scalingo.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\scalingo.exe"),
        "scalingo"
    )
    foreach ($c in $candidates) {
        if ($c -eq "scalingo" -or (Test-Path $c)) { return $c }
    }
    throw "Scalingo CLI introuvable. Lancez scalingo login --password-only"
}

$scalingo = Get-ScalingoCli
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$raw = & $scalingo --app $app env 2>&1 | ForEach-Object { "$_" }
$ErrorActionPreference = $prevEap
if ($LASTEXITCODE -ne 0) {
    Write-Host $raw
    throw "Echec scalingo env - connectez-vous avec scalingo login --password-only"
}

$keep = @(
    "DATABASE_URL", "VELIORA_AUTO_SCHEMA", "FLASK_SECRET_KEY", "APP_PUBLIC_URL",
    "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID",
    "STRIPE_REQUIRE_PAYMENT", "STRIPE_TRIAL_DAYS",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS",
    "GOOGLE_MAPS_API_KEY", "GOOGLE_GEOCODING_API_KEY", "SUPPORT_EMAIL", "LEGAL_COMPANY_NAME"
)

$lines = @(
    "# Genere par export-scalingo-for-vps.ps1 - copier dans /opt/veliora/.env sur le VPS",
    "# Ne jamais commiter ce fichier",
    ""
)

foreach ($key in $keep) {
    $m = $raw | Select-String -Pattern "^$key=" | Select-Object -First 1
    if ($m) { $lines += $m.Line }
}

$lines += ""
$lines += "# A ajouter pour le VPS (Decodo)"
$lines += "CRAWL_PROXIES=http://USER:PASS@fr.decodo.com:40000"
$lines += "CRAWL_AUTO_START=true"
$lines += "CRAWL_PLAYWRIGHT_ENABLED=true"
$lines += "CRAWL_ANTIBOT_PORTALS_ENABLED=true"
$lines += "CRAWL_SKIP_STREAMESTATE=true"
$lines += "CRAWL_AUTO_FREE_PROXIES=false"

$lines | Set-Content -Path $out -Encoding UTF8
Write-Host "Export OK : $out" -ForegroundColor Green
Write-Host "Copiez ces lignes dans nano /opt/veliora/.env sur le VPS."
