# Applique la config « proxies résidentiels » sur Scalingo et/ou prépare le worker local.
param(
    [ValidateSet("scalingo", "local", "all")]
    [string]$Target = "all",
    [string]$ScalingoApp = "veliora"
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Get-ScalingoCli {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\scalingo_1.44.0_windows_amd64\scalingo.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\scalingo.exe"),
        "scalingo"
    )
    foreach ($c in $candidates) {
        if ($c -eq "scalingo" -or (Test-Path $c)) { return $c }
    }
    throw "Scalingo CLI introuvable. Installez : https://cli.scalingo.com"
}

function Import-EnvFile([string]$Path) {
    if (-not (Test-Path $Path)) {
        Write-Host "Fichier manquant : $Path" -ForegroundColor Yellow
        Write-Host "  copy $($Path).example $Path" -ForegroundColor Cyan
        return @{}
    }
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '^\s*([^=]+)=(.*)$') { return }
        $vars[$matches[1].Trim()] = $matches[2].Trim()
    }
    return $vars
}

if ($Target -in @("scalingo", "all")) {
    $envFile = Join-Path $PSScriptRoot "scalingo-env-residential.env"
    $vars = Import-EnvFile $envFile
    if ($vars.Count -eq 0) { exit 1 }

    $scalingo = Get-ScalingoCli
    & $scalingo --app $ScalingoApp env 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Connexion Scalingo requise : scalingo login --password-only" -ForegroundColor Yellow
        exit 1
    }

    $ok = 0
    $skip = 0
    foreach ($key in $vars.Keys) {
        $val = $vars[$key]
        if ([string]::IsNullOrWhiteSpace($val)) {
            Write-Host "[skip] $key (vide - renseignez dans scalingo-env-residential.env)" -ForegroundColor Yellow
            $skip++
            continue
        }
        & $scalingo --app $ScalingoApp env-set "${key}=${val}" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[ok] $key"
            $ok++
        } else {
            Write-Host "[!!] $key" -ForegroundColor Red
        }
    }

    if ($skip -gt 0) {
        Write-Host "`nCRAWL_PROXIES vide : après achat IPRoyal, lancez :" -ForegroundColor Cyan
        Write-Host "  scalingo --app $ScalingoApp env-set CRAWL_PROXIES=`"http://USER:PASS_country-fr_session-rotate@geo.iproyal.com:12321`"" -ForegroundColor Cyan
    }
    & $scalingo --app $ScalingoApp restart 2>&1 | Out-Null
    Write-Host "`nScalingo : $ok variables, restart demandé." -ForegroundColor Green
}

if ($Target -in @("local", "all")) {
    $example = Join-Path $PSScriptRoot "crawl-worker-local.env.example"
    $local = Join-Path $PSScriptRoot "crawl-worker-local.env"
    if (-not (Test-Path $local)) {
        Copy-Item $example $local
        Write-Host "Créé : $local — remplissez DATABASE_URL et CRAWL_PROXIES" -ForegroundColor Cyan
    } else {
        Write-Host "Worker local : $local déjà présent." -ForegroundColor Green
    }
    Write-Host "Ensuite : playwright install chromium ; .\scripts\run-crawl-worker.ps1" -ForegroundColor Cyan
}
