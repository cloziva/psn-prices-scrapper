@echo off
chcp 65001 >nul
title PSN Prices - actualizar Loaded y ver el mejor precio
cd /d "%~dp0"

REM === Doble clic: refresca los precios de Loaded (desde tu IP espanola), busca el mejor
REM     saldo PSN por euro (Eneba + Loaded + Instant Gaming), te avisa al movil y sube los
REM     precios de Loaded actualizados a GitHub. ===

if not exist ".env" (
  echo Falta el archivo .env  (copia .env.example y pon tu topic de ntfy).
  pause & exit /b 1
)
for /f "usebackq eol=# tokens=1,2 delims==" %%a in (".env") do set "%%a=%%b"
set "PSN_ON_DEMAND=1"
set "PSN_REFRESH_LOADED=1"

python -c "import curl_cffi, requests" 2>nul || (
  echo Instalando dependencias por primera vez...
  python -m pip install -q -r requirements.txt
)

echo.
echo === Poniendome al dia con GitHub... ===
git pull --rebase --autostash origin main 2>nul

echo.
echo === Actualizando Loaded (solo en stock) y buscando el mejor saldo PSN/euro... ===
echo.
python scraper\main.py

echo.
echo === Subiendo los precios de Loaded a GitHub... ===
git add data\loaded_prices.json
git commit -m "Actualizar precios de Loaded [skip ci]" 1>nul 2>nul && (
  git push origin main && echo Precios de Loaded actualizados en GitHub.
) || echo Loaded sin cambios (nada que subir).

echo.
echo === Hecho. Si habia un buen precio, te ha llegado al movil. ===
pause >nul
