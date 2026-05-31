@echo off
cd /d "%~dp0"
title Veliora - Prechauffage session anti-bot
echo.
echo  Veliora - Prechauffage de session (OPTIONNEL)
echo  -------------------------------------------------
echo  Le crawl normal (demarrer.bat) prechauffe deja la session tout seul.
echo  Cet outil sert uniquement a re-valider la session si DataDome rebloque
echo  (cookies expires). Un vrai Chrome s'ouvre sur LeBonCoin, SeLoger, BienIci.
echo  Resolvez les captchas / acceptez les cookies si demande.
echo.
python -m crawler.warmup_session
echo.
echo  Termine. Lancez ensuite le crawl normalement : demarrer.bat
pause
