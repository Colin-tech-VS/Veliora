@echo off
cd /d "%~dp0"
title Veliora
echo.
echo  Veliora — liberation du port 8000 (anciennes instances)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo    Arret PID %%a
  taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo  Verification : http://localhost:8000/api/health doit afficher api_version 7 et radar_analyze_url true
echo  Demarrage Flask (app.py)...
echo.
echo  Accueil     : http://localhost:8000/
echo  Estimation  : http://localhost:8000/estimation
echo  Catalogue   : http://localhost:8000/annonces  (lecture seule, agences)
echo  Connexion   : http://localhost:8000/crm/auth
echo  CRM         : http://localhost:8000/crm
echo.
echo  IMPORTANT : utilisez ces URLs (Flask). Pas Live Server / fichier HTML seul.
echo  Ctrl+C pour arreter.
echo.
python app.py
pause
