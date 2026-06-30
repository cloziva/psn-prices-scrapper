#!/usr/bin/env bash
# Configura el scraper de precios PSN en una VM Ubuntu (Oracle Cloud, region Madrid).
# Deja el scraper corriendo cada 15 minutos desde una IP espanola (precios EUR exactos).
#
# Uso (en la VM, por SSH):
#   bash setup_oracle.sh TU_TOPIC_DE_NTFY
#
# Se puede volver a ejecutar para actualizar el codigo (git pull) sin perder el estado.
set -euo pipefail

TOPIC="${1:-}"
if [ -z "$TOPIC" ]; then
  echo "ERROR: pasa tu topic de ntfy.  Ej:  bash setup_oracle.sh psn-XXXXXXXX"
  exit 1
fi

APP_DIR="$HOME/psn-prices-scrapper"
STATE_FILE="$HOME/psn-state.json"   # estado FUERA del repo (asi git pull nunca choca)
REPO="https://github.com/cloziva/psn-prices-scrapper.git"

echo "==> 1/5 Dependencias del sistema..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

echo "==> 2/5 Codigo (clonar o actualizar)..."
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi

echo "==> 3/5 Entorno virtual + paquetes..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 4/5 Guardando configuracion (.env)..."
cat > "$APP_DIR/.env" <<EOF
NTFY_TOPIC=$TOPIC
PSN_STATE_PATH=$STATE_FILE
EOF
chmod 600 "$APP_DIR/.env"

echo "==> Prueba de ejecucion (deberia llegarte un push si hay chollo)..."
( cd "$APP_DIR" && set -a && . ./.env && set +a && ./venv/bin/python scraper/main.py ) || true

echo "==> 5/5 Programando cron cada 15 minutos..."
CRON_CMD="*/15 * * * * cd $APP_DIR && set -a && . ./.env && set +a && ./venv/bin/python scraper/main.py >> $APP_DIR/cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'psn-prices-scrapper' || true ; echo "$CRON_CMD" ) | crontab -

echo ""
echo "LISTO. El scraper corre cada 15 min desde esta VM."
echo "  - Cron:   $(crontab -l | grep psn-prices-scrapper)"
echo "  - Log:    tail -f $APP_DIR/cron.log"
echo "  - Estado: $STATE_FILE"
