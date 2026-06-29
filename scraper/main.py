"""Orquestador: scrapea Eneba, calcula el precio EFECTIVO y avisa cuando comprar.

Modelo de decision (comparar contra tienda de referencia):
    base     = oferta mas barata listada en Eneba
    tarifa   = eneba_service_fee_eur + base * service_fee_percent/100
               (la tarifa de servicio se anade en el checkout y depende del metodo
                de pago; con monedero Eneba suele ser 0. No es scrapeable: se configura)
    cashback = lo que Eneba te devuelve en monedero (dato real EXACTO por producto)
    efectivo = base + tarifa - cashback        <- coste real comparable

    referencia = precio FIJO de Loaded/CDKeys o Instant Gaming para ese importe
    margen     = colchon de seguridad (por si el cashback tarda o caduca)

    -> COMPRAR (avisar) si  efectivo <= referencia - margen

Si un importe no tiene 'reference_prices' pero si 'thresholds', se usa el umbral
absoluto (efectivo <= umbral). Si no tiene ninguno, solo se registra el precio.

Anti-spam: avisa al cruzar y solo repite si el efectivo baja aun mas; si vuelve a
subir por encima del objetivo, se rearma. El estado se guarda en data/state.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import notify
from scrape_eneba import DEFAULT_STORE_URL, fetch_prices
from scrape_reference import fetch_references

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


def _effective(product: dict, pricing: dict) -> dict:
    """Calcula el precio efectivo de un producto de Eneba."""
    base = product["price_min"]
    fee = float(pricing.get("eneba_service_fee_eur", 0) or 0)
    fee += base * float(pricing.get("service_fee_percent", 0) or 0) / 100
    fee = round(fee, 2)
    cashback = product.get("cashback", 0.0) or 0.0
    if not pricing.get("count_cashback", True):
        cashback = 0.0
    efectivo = round(base + fee - cashback, 2)
    return {"base": base, "fee": fee, "cashback": cashback, "efectivo": efectivo}


def main() -> int:
    config = _load_json(CONFIG_PATH, {})
    store_url = config.get("store_url", DEFAULT_STORE_URL)
    pricing = config.get("pricing") or {}
    margin = float(pricing.get("safety_margin_eur", 0) or 0)
    reference_prices = {str(k): float(v) for k, v in (config.get("reference_prices") or {}).items()}
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

    # Referencias EN VIVO (Loaded/CDKeys + Instant Gaming). Si falla, se usan las de config.
    live_refs: dict[int, dict] = {}
    if pricing.get("use_live_references", True):
        try:
            live_refs = fetch_references(
                include_instant_gaming=pricing.get("include_instant_gaming", True)
            )
            print(f"Referencias en vivo: {len(live_refs)} importes")
        except Exception as exc:  # noqa: BLE001 - sin referencias en vivo usamos las de config
            print(f"[warn] no se pudieron obtener referencias en vivo: {exc} (uso config)")

    alerts_sent = 0
    print(f"{'importe':>8} {'base':>8} {'tarifa':>7} {'cashbk':>7} {'efectivo':>9} "
          f"{'referen':>8} {'ahorro':>7}  estado / fuente")
    print("-" * 92)

    for key in sorted(by_denom, key=lambda k: int(k)):
        p = by_denom[key]
        c = _effective(p, pricing)
        efectivo = c["efectivo"]

        # Referencia: primero la EN VIVO (Loaded/IG); si no, la de config como respaldo.
        live = live_refs.get(int(key))
        if live:
            reference = live["price"]
            ref_store = live["store"]
            ref_url = live.get("url") or None
        else:
            reference = reference_prices.get(key)
            ref_store = "config" if reference is not None else None
            ref_url = None
        threshold = thresholds.get(key)

        # Objetivo a batir por el precio efectivo.
        if reference is not None:
            target = round(reference - margin, 2)
        elif threshold is not None:
            target = threshold
            ref_store = "umbral"
        else:
            target = None

        savings = round(reference - efectivo, 2) if reference is not None else None

        prices_state[key] = {
            "name": p["name"],
            "base_price": c["base"],
            "service_fee": c["fee"],
            "cashback": c["cashback"],
            "cashback_percent": p.get("cashback_percent"),
            "effective_price": efectivo,
            "reference_price": reference,
            "reference_store": ref_store,
            "reference_url": ref_url,
            "savings_vs_reference": savings,
            "currency": p["currency"],
            "url": p["url"],
            "merchant": p["merchant"],
            "updated_at": now,
        }
        hist = history_state.setdefault(key, [])
        if not hist or hist[-1].get("p") != efectivo:
            hist.append({"t": now, "p": efectivo, "base": c["base"]})
            if len(hist) > HISTORY_LIMIT:
                del hist[:-HISTORY_LIMIT]

        # Decision + aviso (con anti-spam).
        if target is None:
            status = "(sin referencia)"
        else:
            is_deal = efectivo <= target
            last_alerted = alerts_state.get(key)
            if is_deal and (last_alerted is None or efectivo < last_alerted):
                if reference is not None:
                    title = f"COMPRAR PSN {key} EUR"
                    body = (
                        f"Eneba {_fmt(efectivo)} EUR efectivo  <  {ref_store} {_fmt(reference)} EUR"
                        f"  ->  ahorras {_fmt(savings)} EUR\n"
                        f"Desglose: {_fmt(c['base'])} base + {_fmt(c['fee'])} tarifa"
                        + (f" - {_fmt(c['cashback'])} cashback" if c["cashback"] else "")
                        + f"\nVendedor Eneba: {p['merchant'] or 'desconocido'}\n{p['url']}"
                    )
                else:
                    title = f"Chollo PSN {key} EUR"
                    body = (
                        f"Eneba {_fmt(efectivo)} EUR efectivo (objetivo {_fmt(target)} EUR)\n"
                        f"Desglose: {_fmt(c['base'])} base + {_fmt(c['fee'])} tarifa"
                        + (f" - {_fmt(c['cashback'])} cashback" if c["cashback"] else "")
                        + f"\nVendedor: {p['merchant'] or 'desconocido'}\n{p['url']}"
                    )
                try:
                    if notify.send(title, body, url=p["url"], priority="high",
                                   tags="money_with_wings"):
                        alerts_sent += 1
                        alerts_state[key] = efectivo
                        status = f"COMPRAR (<= {_fmt(target)})"
                    else:
                        status = "deal (notif. off)"
                except Exception as ne:  # noqa: BLE001
                    print(f"[warn] fallo al notificar {key} EUR: {ne}")
                    status = "deal (fallo notif.)"
            elif not is_deal:
                if alerts_state.get(key) is not None:
                    alerts_state[key] = None  # rearmar
                status = f"esperar (obj. {_fmt(target)})"
            else:
                status = f"ya avisado (<= {_fmt(target)})"

        print(f"{key:>6}EUR {_fmt(c['base']):>8} {_fmt(c['fee']):>7} {_fmt(c['cashback']):>7} "
              f"{_fmt(efectivo):>9} {(_fmt(reference) if reference is not None else '-'):>8} "
              f"{(_fmt(savings) if savings is not None else '-'):>7}  {status}"
              f"{(' [' + ref_store + ']') if ref_store else ''}")

    state["updated_at"] = now
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    n_ref = sum(1 for k in by_denom if int(k) in live_refs or k in reference_prices or k in thresholds)
    print(f"\nImportes con referencia: {n_ref} de {len(by_denom)} - Alertas enviadas: {alerts_sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
