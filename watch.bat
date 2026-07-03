@echo off
chcp 65001 >nul
title PSN Prices - VIGILANCIA (escaneo continuo)
cd /d "%~dp0"

REM === Vigilancia agresiva desde tu PC: escanea Eneba cada pocos segundos y te avisa AL
REM     INSTANTE de ofertas nuevas. Dejalo abierto mientras cazas. Cierra la ventana para parar. ===

if not exist ".env" ( echo Falta el archivo .env (copia .env.example). & pause & exit /b 1 )
for /f "usebackq eol=# tokens=1,2 delims==" %%a in (".env") do set "%%a=%%b"

REM Segundos entre escaneos (baja el numero para mas agresivo; ojo con que Eneba te bloquee).
if "%PSN_WATCH_INTERVAL%"=="" set "PSN_WATCH_INTERVAL=45"
set "PSN_WATCH_MINUTES=0"

python -c "import curl_cffi, requests" 2>nul || python -m pip install -q -r requirements.txt

echo.
echo === VIGILANCIA ACTIVA: escaneo cada %PSN_WATCH_INTERVAL%s. Cierra la ventana para parar. ===
echo.
python scraper\watch.py
pause
