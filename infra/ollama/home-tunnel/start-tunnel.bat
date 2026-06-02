@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  Veliora — Tunnel Cloudflare gratuit pour exposer Ollama local
REM  Double-clique sur ce fichier pour lancer le tunnel. L'URL HTTPS
REM  s'affichera dans la console : copie-la et colle-la dans Scalingo
REM  (variable OLLAMA_BASE_URL).
REM  Prérequis : Ollama installé et tournant, cloudflared.exe à côté.
REM ─────────────────────────────────────────────────────────────────────

setlocal
set "SCRIPT_DIR=%~dp0"
set "CLOUDFLARED=%SCRIPT_DIR%cloudflared.exe"

REM Cherche cloudflared dans ce dossier, sinon dans C:\Veliora\, sinon dans PATH
if not exist "%CLOUDFLARED%" set "CLOUDFLARED=C:\Veliora\cloudflared.exe"
if not exist "%CLOUDFLARED%" set "CLOUDFLARED=cloudflared.exe"

echo.
echo === Veliora — Tunnel Ollama (gratuit, sans engagement) ===
echo.

REM Vérification : Ollama répond-il ?
echo Verification Ollama sur http://localhost:11434 ...
curl -s -o NUL -w "HTTP %%{http_code}\n" http://localhost:11434/api/tags
if errorlevel 1 (
    echo.
    echo [ERREUR] Ollama ne semble pas demarrer. Lance Ollama (icone llama dans
    echo la barre des taches), puis re-double-clique sur ce fichier.
    echo Telecharge Ollama ici : https://ollama.com/download/windows
    pause
    exit /b 1
)

echo.
echo Demarrage du tunnel Cloudflare...
echo Garde cette fenetre ouverte tant que tu veux que l'IA Veliora
echo soit accessible depuis Scalingo.
echo.

"%CLOUDFLARED%" tunnel --url http://localhost:11434

pause
