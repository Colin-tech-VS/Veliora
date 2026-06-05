# Applique scripts/scalingo-env-apply.env sur l'app Scalingo « veliora ».
# Prérequis : scalingo login (ou scalingo login --api-token <token>)
param(
    [string]$App = "veliora",
    [string]$EnvFile = (Join-Path $PSScriptRoot "scalingo-env-apply.env")
)

$cliCandidates = @(
    (Join-Path $env:USERPROFILE ".local\bin\scalingo_1.44.0_windows_amd64\scalingo.exe"),
    (Join-Path $env:USERPROFILE ".local\bin\scalingo.exe"),
    "scalingo"
)

$scalingo = $cliCandidates | Where-Object { $_ -eq "scalingo" -or (Test-Path $_) } | Select-Object -First 1
if (-not $scalingo) {
    Write-Error "Scalingo CLI introuvable. Installez-le : https://cli.scalingo.com"
    exit 1
}

$cmd = if ($scalingo -eq "scalingo") { "scalingo" } else { $scalingo }
& $cmd --app $App env 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Connexion Scalingo requise. Lancez :" -ForegroundColor Yellow
    Write-Host "  & `"$cmd`" login --password-only" -ForegroundColor Cyan
    Write-Host "  # ou : scalingo login --api-token <token depuis dashboard Scalingo>" -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    Write-Error "Fichier introuvable : $EnvFile"
    exit 1
}

$lines = Get-Content $EnvFile | Where-Object { $_ -and $_ -notmatch '^\s*#' }
$ok = 0
$fail = 0
foreach ($line in $lines) {
    & $cmd --app $App env-set $line 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ok++
        Write-Host "[ok] $line"
    } else {
        $fail++
        Write-Host "[!!] $line" -ForegroundColor Red
    }
}

Write-Host "`nTerminé : $ok variables appliquées, $fail échec(s)." -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
if ($fail -eq 0) {
    Write-Host "Redéployez ou redémarrez l'app pour recharger Gunicorn : scalingo --app $App restart" -ForegroundColor Cyan
}
