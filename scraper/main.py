"""Orquestador: scrapea Eneba, calcula el precio real y avisa por ntfy.

Precio real de cada importe:
    base    = oferta mas barata listada en Eneba
    tarifa  = base * service_fee_percent / 100   (estimada; se anade en el checkout
              y depende del metodo de pago -> NO es scrapeable. Con monedero Eneba
              suele ser 0. Mide la tuya una vez en el checkout y ponla en config.json)
    pagas   = base + tarifa                       (lo que sale de tu bolsillo)
    cashback= lo que Eneba te devuelve en monedero (dato real por oferta)
    neto    = pagas - cashback                    (coste efectivo si gastas el cashback)

La alerta se dispara segun `pricing.alert_on` (por defecto "pay" = lo que pagas).
Cambialo a "net" si quieres que el aviso descuente el cashback, o "base" para el
precio listado tal cual.

Logica anti-spam: avisa si  precio_elegido <= umbral  Y  (no se habia avisado  O
ha bajado aun mas). Si vuelve a subir por encima del umbral, se rearma.

El estado se guarda en data/state.json, que el workflow commitea tras cada run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import notify
from scrape_eneba import DEFAULT_STORE_URL, fetch_prices

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scraper" / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
HISTORY_LIMIT = 60  # entradas de historico a conservar por importe


def _load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def _fmt(value: float) -> str:
    """76.9 -> '76,90' (formato espanol)."""
    return f"{value:.2f}".replace(".", ",")


def _compute(product: dict, pricing: dict) -> dict:
    """Devuelve el desglose de precios de un producto segun la config de pricing."""
    base = product["price_min"]
    fee_pct = float(pricing.get("service_fee_percent", 0) or 0)
    fee = round(base * fee_pct / 100, 2)
    pay = round(base + fee, 2)
    cashback = product.get("cashback", 0.0) or 0.0
    if not pricing.get("count_cashback", True):
        cashback = 0.0
    net = round(pay - cashback, 2)

    mode = pricing.get("alert_on", "pay")
    alert_price = {"base": base, "pay": pay, "net": net}.get(mode, pay)
    return {
        "base": base,
        "fee": fee,
        "pay": pay,
        "cashback": cashback,
        "net": net,
        "alert_on": mode,
        "alert_price": alert_price,
    }


def main() -> int:
    config = _load_json(CONFIG_PATH, {})
    store_url = config.get("store_url", DEFAULT_STORE_URL)
    pricing = config.get("pricing") or {}
    thresholds = {str(k): float(v) for k, v in (config.get("thresholds") or {}).items()}

    state = _load_json(STATE_PATH, {})
    prices_state = state.setdefault("prices", {})
    alerts_state = state.setdefault("alerts", {})
    history_state = state.setdefault("history", {})

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        products = fetch_prices(store_url)
    except Exception as exc:  # noqa: BLE001 - no romper; avisar del fallo
        print(f"[error] No se pudieron obtener los precios: {exc}")
        if notify.notifications_enabled():
            try:
                notify.send("PSN scraper", f"El scraper de Eneba fallo: {exc}",
                            priority="low", tags="warning")
            except Exception as ne:  # noqa: BLE001
                print(f"[warn] tampoco se pudo notificar el error: {ne}")
        return 1

    # Quedarnos con el mas barato por importe (por si hubiera duplicados).
    by_denom: dict[str, dict] = {}
    for p in products:
        key = str(p["denom"])
        if key not in by_denom or p["price_min"] < by_denom[key]["price_min"]:
            by_denom[key] = p

    mode = pricing.get("alert_on", "pay")
    alerts_sent = 0
    print(f"{'importe':>8} {'base':>8} {'tarifa':>7} {'cashbk':>7} {'->'+mode:>9} {'umbral':>8}  estado")
    print("-" * 78)

    for key in sorted(by_denom, key=lambda k: int(k)):
        p = by_denom[key]
        c = _compute(p, pricing)
        price = c["alert_price"]

        prices_state[key] = {
            "name": p["name"],
            "base_price": c["base"],
            "service_fee": c["fee"],
            "pay_price": c["pay"],
            "cashback": c["cashback"],
            "net_price": c["net"],
            "alert_on": c["alert_on"],
            "alert_price": price,
            "currency": p["currency"],
            "url": p["url"],
            "merchant": p["merchant"],
            "updated_at": now,
        }
        hist = history_state.setdefault(key, [])
        if not hist or hist[-1].get("p") != price:
            hist.append({"t": now, "p": price, "base": c["base"]})
            if len(hist) > HISTORY_LIMIT:
                del hist[:-HISTORY_LIMIT]

        threshold = thresholds.get(key)
        status = "(sin umbral)"

        if threshold is not None:
            last_alerted = alerts_state.get(key)
            if price <= threshold and (last_alerted is None or price < last_alerted):
                title = f"Chollo PSN {key} EUR"
                body = (
                    f"Saldo PSN {key} EUR a {_fmt(price)} EUR (objetivo {_fmt(threshold)})\n"
                    f"Desglose: {_fmt(c['base'])} base"
                    + (f" + {_fmt(c['fee'])} tarifa" if c["fee"] else "")
                    + (f" - {_fmt(c['cashback'])} cashback" if c["cashback"] else "")
                    + f" = {_fmt(c['net'])} neto\n"
                    f"Vendedor: {p['merchant'] or 'desconocido'}\n{p['url']}"
                )
                try:
                    if notify.send(title, body, url=p["url"], priority="high",
                                   tags="money_with_wings"):
                        alerts_sent += 1
                        alerts_state[key] = price
                        status = f"AVISO (<= {_fmt(threshold)})"
                    else:
                        status = "cruza umbral (notif. off)"
                except Exception as ne:  # noqa: BLE001
                    print(f"[warn] fallo al notificar {key} EUR: {ne}")
                    status = "cruza umbral (fallo notif.)"
            elif price > threshold:
                if alerts_state.get(key) is not None:
                    alerts_state[key] = None  # rearmar
                status = f"objetivo {_fmt(threshold)}"
            else:
                status = f"objetivo {_fmt(threshold)} (ya avisado)"

        print(f"{key:>6}EUR {_fmt(c['base']):>8} {_fmt(c['fee']):>7} {_fmt(c['cashback']):>7} "
              f"{_fmt(price):>9} {(_fmt(threshold) if threshold else '-'):>8}  {status}")

    state["updated_at"] = now
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\nImportes vigilados: {len(by_denom)} - Alertas enviadas: {alerts_sent}  (alerta sobre: {mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
