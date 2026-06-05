# Deploiement Veliora : PC -> GitHub -> OVH VPS, en une commande.
#
# Usage :
#   .\scripts\deploy-to-ovh.ps1 "Mon message de commit"
#   .\scripts\deploy-to-ovh.ps1            # sans commit : juste pull + restart sur le VPS
#
# Ce que ca fait :
#   1. (si message) git add -A + commit + push vers GitHub
#   2. SSH sur le VPS : git pull (user veliora) + restart du service + statut
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Message
)

$ErrorActionPreference = "Stop"

# ─── Reglages VPS ───
$VpsUser = "ubuntu"
$VpsHost = "37.59.97.63"
$AppDir  = "/opt/veliora"
$Service = "veliora"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Step($txt) { Write-Host "`n=== $txt ===" -ForegroundColor Cyan }

# ─── 1. Push GitHub (si message fourni) ───
# git ecrit des messages normaux sur stderr ("Everything up-to-date"...) :
# avec ErrorActionPreference=Stop, PowerShell les prend pour une erreur fatale
# et stoppe le script avant meme l'etape SSH. On neutralise ca le temps du push.
if ($Message) {
    Step "1/3  Commit + push GitHub"
    git add -A
    # Ne commit que s'il y a quelque chose a committer
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m $Message
        if ($LASTEXITCODE -ne 0) { throw "Echec du commit." }
    } else {
        Write-Host "Rien a committer (deja a jour localement)." -ForegroundColor Yellow
    }
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    git push 2>&1 | Write-Host
    $code = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($code -ne 0) { throw "Echec du push GitHub." }
} else {
    Step "1/3  (pas de message : on saute le commit, push facultatif)"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    git push 2>&1 | Write-Host
    $ErrorActionPreference = $prev
}

# ─── 2. Pull + restart sur le VPS ───
Step "2/3  Deploiement sur le VPS ($VpsHost)"
$remote = @"
set -e
cd $AppDir
echo '--- git pull ---'
sudo -u veliora git pull --ff-only
echo '--- restart $Service ---'
sudo systemctl restart $Service
sleep 3
echo '--- statut ---'
sudo systemctl is-active $Service
"@
# Envoi du script en LF (evite les soucis de fin de ligne Windows)
$remote = $remote -replace "`r", ""
$remote | ssh "$VpsUser@$VpsHost" "bash -s"
if ($LASTEXITCODE -ne 0) { throw "Echec du deploiement sur le VPS." }

# ─── 3. Verif HTTP locale (cote serveur, contourne le proxy ANRH) ───
Step "3/3  Verification (sante de l'app)"
ssh "$VpsUser@$VpsHost" "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8000/api/health"

Write-Host "`nDeploiement termine." -ForegroundColor Green
