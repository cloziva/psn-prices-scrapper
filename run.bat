@echo off
chcp 65001 >nul
title PSN Prices - mejor precio ahora
cd /d "%~dp0"

REM === Comprobacion manual del mejor saldo PSN por euro (desde tu IP espanola). ===
REM Doble clic para ejecutar. Lee la config de .env (tu topic de ntfy).

if not exist ".env" (
  echo No encuentro el archivo .env  .
  echo Crea uno copiando .env.example y pon tu topic de ntfy. & echo.
  pause & exit /b 1
)

REM Cargar variables de .env  (lineas TIPO  CLAVE=VALOR)
for /f "usebackq eol=# tokens=1,2 delims==" %%a in (".env") do set "%%a=%%b"

REM Forzar "informe del mejor precio" en cada ejecucion manual.
set "PSN_ON_DEMAND=1"

REM Asegurar dependencias solo si faltan (rapido si ya estan).
python -c "import curl_cffi, requests" 2>nul || (
  echo Instalando dependencias por primera vez...
  python -m pip install -q -r requirements.txt
)

echo.
echo === Buscando el mejor saldo PSN por euro (Eneba + Loaded + Instant Gaming)... ===
echo.
python scraper\main.py

echo.
echo === Hecho. Si habia un buen precio, te ha llegado al movil. ===
pause >nul
