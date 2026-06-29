# PSN Prices Scrapper

Vigila el precio del **saldo de PlayStation Store (región España)** en **Eneba** y te manda un
**push gratis al iPhone** (vía [ntfy](https://ntfy.sh)) cuando comprar ahí **sale realmente más
barato** que en tus tiendas de referencia (Loaded/CDKeys e Instant Gaming). Se ejecuta solo en
**GitHub Actions cada 15 minutos** — sin servidor, sin tener el ordenador encendido y **sin coste**.

## Cómo funciona

1. Cada 15 min, GitHub Actions ejecuta `scraper/main.py`.
2. `scrape_eneba.py` hace **un solo GET** a la categoría de Eneba y extrae del JSON incrustado
   (`__APOLLO_STATE__`) el **precio mínimo** y el **cashback exacto** de cada importe.
3. `scrape_reference.py` saca el **precio de referencia** del día en **Loaded/CDKeys** (todos los
   importes en una petición) e **Instant Gaming**, y se queda con el **más barato** por importe.
4. `main.py` calcula el **precio efectivo** de Eneba y avisa **COMPRAR** si compensa:

   ```
   efectivo_eneba = precio_base + tarifa_servicio − cashback
   COMPRAR  si  efectivo_eneba  ≤  precio_referencia − margen_seguridad
   ```

   Si compensa, `notify.py` manda el push al iPhone con el desglose y el **enlace de compra**.
5. El estado (precios, último aviso e histórico) se guarda en `data/state.json` y el workflow lo
   **commitea** de vuelta al repo, para no repetir el mismo aviso.

```
scraper/
  scrape_eneba.py      # GET + JSON de Eneba -> precio min + cashback por importe
  scrape_reference.py  # Loaded/CDKeys + Instant Gaming -> precio de referencia (curl_cffi)
  notify.py            # POST a ntfy (push al iPhone)
  main.py              # orquesta: scrape -> efectivo vs referencia -> avisar -> guardar estado
  config.json          # tarifas, margen y respaldos  (PÚBLICO, sin secretos)
data/state.json        # estado e histórico de precios (se actualiza solo)
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

### 4. Ajusta tarifa y margen (opcional)
El sistema ya es **casi automático**: saca solo el precio de referencia del día (Loaded +
Instant Gaming), así que normalmente **no tienes que tocar precios objetivo**. Lo único que
conviene afinar en `scraper/config.json → pricing`:

```json
"pricing": {
  "eneba_service_fee_eur": 2.30,   // tu tarifa de servicio en Eneba (mídela 1 vez en el checkout)
  "service_fee_percent": 0,         // tarifa en % adicional, si tu método de pago la cobra así
  "count_cashback": true,           // restar el cashback (dato real por producto)
  "safety_margin_eur": 1.50,        // cuánto más barato debe ser Eneba para avisar
  "use_live_references": true,      // sacar la referencia en vivo de Loaded + Instant Gaming
  "include_instant_gaming": true
}
```

### 5. Activa el workflow
- Pestaña **Actions** → si pide habilitarlas, acepta.
- Abre **scrape-psn-prices** → **Run workflow** para lanzarlo a mano la primera vez.
- Revisa el log: verás la tabla con `efectivo`, `referencia` y `ahorro`. Si un importe sale
  **COMPRAR**, te llega el push. A partir de ahí corre solo cada 15 min (a veces con 15–30 min
  de retraso de GitHub).

## El precio real: tarifa, cashback y referencia

No basta el precio listado de Eneba: lo que decide si compras es el **precio efectivo** comparado
con lo que cuesta en una tienda **de precio fijo** (Loaded/CDKeys, Instant Gaming):

```
efectivo_eneba = precio_base + tarifa_servicio − cashback
COMPRAR  si  efectivo_eneba  ≤  precio_referencia − margen_seguridad
```

- **Cashback** (automático): cada oferta de Eneba trae su cashback **exacto** en € (y el % del
  producto). El bot lo resta del efectivo.
- **Tarifa de servicio** (configurable): Eneba la añade **en el checkout** y depende del método de
  pago (con **monedero Eneba** suele ser 0). No es scrapeable; mídela una vez y ponla en
  `eneba_service_fee_eur`. Para medirla: añade una tarjeta al carrito, ve al checkout, elige tu
  método de pago y mira la "service fee".
- **Referencia** (automática): `scrape_reference.py` saca el precio del día de **Loaded/CDKeys**
  (todos los importes en una petición) e **Instant Gaming**, y usa el **más barato**. Si el
  scraping fallara, se usan los valores de respaldo de `reference_prices`.
- **Margen de seguridad**: solo avisa si Eneba es al menos `safety_margin_eur` más barato que la
  referencia (cubre que el cashback tarde o caduque, o pequeñas fluctuaciones).

> Nota técnica: loaded.com bloquea la librería `requests` por su huella TLS, así que las
> referencias se piden con `curl_cffi` (imita a Chrome). Por eso está en `requirements.txt`.

## Cómo se guardan las claves (repo público)

- El **código y `config.json` son públicos**; **nada sensible** vive en ellos.
- El único dato sensible es **`NTFY_TOPIC`** (quien lo sepa puede leer tus avisos o mandarte
  mensajes falsos). Va en **GitHub Actions Secrets**: cifrado, fuera del código y del historial,
  enmascarado en los logs, e **inaccesible para forks** del repo.
- La configuración (tarifas, margen) y el histórico de precios **no son secretos**, por eso sí están en el repo.
- El commit del estado lo hace el `GITHUB_TOKEN` integrado del workflow; **no** necesitas crear
  ningún token personal.

## Probar en local (opcional)

```bash
pip install -r requirements.txt

# Solo scraping de Eneba (precio mínimo + cashback por importe):
python scraper/scrape_eneba.py

# Solo precios de referencia (Loaded + Instant Gaming, el más barato por importe):
python scraper/scrape_reference.py

# Flujo completo. En Windows PowerShell:
$env:NTFY_TOPIC = "tu-topic"
python scraper/main.py

# Probar solo la notificación:
python scraper/notify.py "Hola desde mi PC"
```

Para **forzar un COMPRAR de prueba**: en `config.json` pon `"use_live_references": false` y un
respaldo alto, p. ej. `"reference_prices": { "50": 999 }`. Ejecuta `main.py` (con `NTFY_TOPIC`
puesto) y te llegará el push. Deshaz el cambio luego.

## Ejemplo real (junio 2026)

| Importe | Eneba efectivo | Referencia (Loaded/IG) | ¿Comprar? |
|--------:|---------------:|-----------------------:|:--|
| 50 €    | 47,53 €        | 44,99 € (Loaded)       | ❌ esperar |
| 100 €   | 92,33 €        | 94,99 € (Loaded)       | ✅ COMPRAR (ahorras ~2,66 €) |

Con la tarifa de servicio incluida, Eneba **a menudo no compensa** frente a Loaded; por eso recibes
pocas alertas, pero las que llegan son chollos de verdad.

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
