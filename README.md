# PSN Prices Scrapper

Vigila el precio del **saldo de PlayStation Store (región España)** en **Eneba** y te manda un
**push gratis al iPhone** (vía [ntfy](https://ntfy.sh)) en cuanto un importe baja del precio que
tú marques. Se ejecuta solo en **GitHub Actions cada 15 minutos** — sin servidor, sin tener el
ordenador encendido y **sin coste**.

## Cómo funciona

1. Cada 15 min, GitHub Actions ejecuta `scraper/main.py`.
2. `scrape_eneba.py` hace **un solo GET** a la página de categoría de Eneba y extrae los precios
   del JSON que la propia página trae incrustado (`__APOLLO_STATE__`). Para cada importe coge el
   **precio mínimo** (la oferta más barata, no la "recomendada").
3. `main.py` compara cada precio con tu **umbral** de `config.json`. Si está por debajo, llama a
   `notify.py`, que manda el push a tu móvil con el **enlace directo de compra**.
4. El estado (últimos precios, último aviso e histórico) se guarda en `data/state.json` y el
   workflow lo **commitea** de vuelta al repo. Así no te repite el mismo aviso una y otra vez.

```
scraper/
  scrape_eneba.py   # GET + parseo del JSON de Eneba  -> [{denom, price_min, url, ...}]
  notify.py         # POST a ntfy (push al iPhone)
  main.py           # orquesta: scrape -> comparar umbrales -> avisar -> guardar estado
  config.json       # importes a vigilar y umbral en € por importe  (PÚBLICO, sin secretos)
data/state.json     # estado e histórico de precios (se actualiza solo)
.github/workflows/scrape.yml   # cron cada 15 min
```

## Puesta en marcha (una sola vez, ~10 min)

### 1. Instala la app ntfy en el iPhone
- Descarga **ntfy** desde la App Store.
- Ábrela → botón **+** → **Subscribe to topic**.
- Inventa un **nombre de topic largo y aleatorio** (actúa como contraseña). Usa un
  placeholder del tipo `psn-PON-AQUI-ALGO-ALEATORIO` con tus propios caracteres.
  Apúntalo y **no lo publiques en ningún sitio** (ni en el código ni en el README):
  irá solo en los Secrets de GitHub.
- El servidor se queda en el por defecto `https://ntfy.sh`.

> En iPhone, usando el servidor público `ntfy.sh` los avisos llegan al instante sin más ajustes.

### 2. Sube este proyecto a un repositorio **público** de GitHub
- Crea un repo nuevo (público) y sube estos archivos (con GitHub Desktop o `git`).
- Repo **público** = minutos de Actions **ilimitados y gratis**. No te preocupes: las claves no
  van en el código (ver más abajo).

### 3. Guarda el topic como **Secret** (no en el código)
En el repo: **Settings → Secrets and variables → Actions → New repository secret**:
- Nombre: `NTFY_TOPIC` · Valor: el topic que inventaste en el paso 1.
- *(Opcional)* `NTFY_TOKEN` y `NTFY_SERVER` solo si proteges el topic con autenticación o usas tu
  propio servidor ntfy.

### 4. Ajusta tus precios objetivo
Edita `scraper/config.json`. En `thresholds`, cada línea es `"importe": precio_objetivo_en_euros`.
Te avisa cuando el precio mínimo sea **igual o menor** a ese número. Ejemplo:

```json
"thresholds": {
  "80": 66.00,
  "50": 42.00,
  "100": 88.00
}
```

Los importes que no pongas aquí **se siguen registrando** (verás su histórico en `state.json`),
pero **no generan aviso**.

### 5. Activa el workflow
- Pestaña **Actions** → si pide habilitarlas, acepta.
- Abre **scrape-psn-prices** → **Run workflow** para lanzarlo a mano la primera vez.
- Revisa el log: verás la tabla de precios. Cuando un importe cruce tu umbral, te llegará el push.
- A partir de ahí corre solo cada 15 min (puede haber retrasos de GitHub; a veces 15–30 min).

## Precio real: tarifa de servicio y cashback

El "precio final real" depende de dos cosas además del precio listado:

- **Cashback** (se detecta solo): cada oferta indica cuánto te devuelve Eneba en
  **monedero** (normalmente ~5%). Es un dato real que el scraper ya extrae y muestra.
- **Tarifa de servicio** (no se puede scrapear): Eneba la añade **en el checkout** y
  **depende del método de pago** (con tarjeta se suma; pagando con **monedero Eneba**
  suele ser **0**). No aparece en el listado ni en la página de producto, así que se
  configura como una **estimación** que mides tú una vez.

Se configura en el bloque `pricing` de `scraper/config.json`:

```json
"pricing": {
  "service_fee_percent": 0,     // tu tarifa: mídela una vez en el checkout y ponla aquí
  "count_cashback": true,       // restar el cashback para el precio neto
  "alert_on": "pay"             // sobre qué precio salta el aviso: base | pay | net
}
```

Cálculo por importe:

```
base   = oferta más barata listada
tarifa = base × service_fee_percent / 100
pagas  = base + tarifa                 (lo que sale de tu bolsillo en el checkout)
neto   = pagas − cashback              (coste efectivo si gastas el cashback)
```

- `alert_on: "pay"` (por defecto) → avisa según lo que **pagas** (base + tarifa).
- `alert_on: "net"` → avisa según el **neto** (descuenta el cashback). Úsalo si quieres
  que el cashback cuente para decidir el aviso.
- `alert_on: "base"` → solo el precio listado, sin tarifa ni cashback.

**Cómo medir tu tarifa:** añade una tarjeta al carrito en Eneba, ve al checkout, elige tu
método de pago habitual y mira el % de "service fee" que aparece. Pon ese número en
`service_fee_percent`. El aviso siempre te muestra el desglose completo (base + tarifa −
cashback) para que veas el precio real.

## Cómo se guardan las claves (repo público)

- El **código y `config.json` son públicos**; **nada sensible** vive en ellos.
- El único dato sensible es **`NTFY_TOPIC`** (quien lo sepa puede leer tus avisos o mandarte
  mensajes falsos). Va en **GitHub Actions Secrets**: cifrado, fuera del código y del historial,
  enmascarado en los logs, e **inaccesible para forks** del repo.
- Los umbrales y el histórico de precios **no son secretos**, por eso sí están en el repo.
- El commit del estado lo hace el `GITHUB_TOKEN` integrado del workflow; **no** necesitas crear
  ningún token personal.

## Probar en local (opcional)

```bash
pip install -r requirements.txt

# Solo scraping (imprime la tabla de precios actuales de Eneba):
python scraper/scrape_eneba.py

# Flujo completo. En Windows PowerShell:
$env:NTFY_TOPIC = "tu-topic"
python scraper/main.py

# Probar solo la notificación:
python scraper/notify.py "Hola desde mi PC"
```

Para forzar un aviso de prueba, baja temporalmente un umbral en `config.json` por encima del
precio actual, ejecuta `main.py` y comprueba que llega el push. (Borra el cambio luego.)

## Precios de referencia observados (para calibrar tus umbrales)

Snapshot de junio de 2026 (cambian a menudo; solo orientativo):

| Importe | Precio mínimo visto |
|--------:|--------------------:|
| 10 €    | 9,95 €              |
| 20 €    | 19,75 €             |
| 50 €    | 47,62 €             |
| 60 €    | 57,56 €             |
| 80 €    | 78,31 €             |
| 100 €   | 94,77 €             |

Los importes "raros" (45, 75, 90, 150, 200, 250…) suelen estar **por encima** de su valor nominal,
así que con umbrales razonables simplemente no te avisarán hasta que haya un chollo real.

## Si Eneba activa Cloudflare (Plan B)

Hoy la página se sirve sin protección, pero si algún día las IPs de GitHub empiezan a recibir un
reto de Cloudflare, `scrape_eneba.py` lo detecta y lanza `EnebaScrapeError`. Vías de escalada, de
menor a mayor coste:
1. `curl_cffi` con *impersonation* (imita la huella TLS de Chrome).
2. Navegador real con **SeleniumBase UC Mode** o **nodriver** (no uses `playwright-stealth`: ya no
   supera Cloudflare).
3. Último recurso: un servicio tipo **FlareSolverr/Byparr** o un proxy residencial.

## Notas

- Solo Eneba en esta v1. La estructura permite añadir otras webs creando más módulos `scrape_*.py`.
- Las tarjetas PSN son **regionales**: este proyecto vigila las de **España (ES)**. Úsalas con una
  cuenta PSN de España.
