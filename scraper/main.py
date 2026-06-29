"""Orquestador: rankea TODAS las ofertas de saldo PSN por euro-gastado-por-euro-de-saldo
y avisa de la MEJOR (sea la tienda o el importe que sea).

Idea: no importa "si Eneba gana a Loaded en el de 100". Importa cual es el saldo PSN
mas barato POR EURO ahora mismo. Ej.: un 50 EUR a 45 (10% desc.) es mejor compra que
un 100 EUR a 95 (5%), aunque ahorres mas euros absolutos en el de 100.

Para cada oferta:  ratio = precio_efectivo / valor_nominal   (menor = mejor)
    Eneba:        efectivo = base + tarifa_servicio - cashback
    Loaded/IG:    efectivo = precio (tiendas de precio fijo, sin tarifa ni cashback)
Se rankea por ratio y se avisa de las mejores.

Modos:
  - Programado (cron): avisa solo si el mejor descuento llega a `min_discount_percent`
    y ademas ha mejorado/cambiado respecto al ultimo aviso (anti-spam).
  - Bajo demanda (workflow_dispatch): SIEMPRE manda el ranking actual (para "preguntar"
    el mejor precio cuando quieras; ver README, seccion de iPhone/Atajos).

Las referencias (Loaded/IG) se cachean ~45 min para no pedir de mas a esas webs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import notify
from scrape_eneba import DEFAULT_STORE_URL, fetch_prices
from scrape_reference import fetch_references

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scraper" / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
REF_TTL_MIN = 45  # cada cuanto refrescar las referencias (Loaded/IG)


def _load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def _fmt(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_min(iso: str) -> float:
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return 1e9


def _eneba_effective(product: dict, pricing: dict) -> float:
    base = product["price_min"]
    fee = float(pricing.get("eneba_service_fee_eur", 0) or 0)
    fee += base * float(pricing.get("service_fee_percent", 0) or 0) / 100
    cashback = product.get("cashback", 0.0) or 0.0
    if not pricing.get("count_cashback", True):
        cashback = 0.0
    return round(base + fee - cashback, 2)


def _get_references(state: dict, pricing: dict, force_fresh: bool) -> tuple[dict, str]:
    """Devuelve ({denom(int): {price,url,store,src_currency}}, origen) con cache de REF_TTL_MIN."""
    if not pricing.get("use_live_references", True):
        return {}, "off"
    cache = state.get("refs_cache") or {}
    if (not force_fresh) and cache.get("data") and _age_min(cache.get("t", "")) < REF_TTL_MIN:
        return {int(k): v for k, v in cache["data"].items()}, "cache"
    try:
        refs = fetch_references(include_instant_gaming=pricing.get("include_instant_gaming", True))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] referencias fallaron: {exc}")
        if cache.get("data"):
            return {int(k): v for k, v in cache["data"].items()}, "cache(tras fallo)"
        return {}, "error"
    state["refs_cache"] = {"t": _now(), "data": {str(k): v for k, v in refs.items()}}
    return refs, "fresco"


def _build_offers(by_denom: dict, refs: dict, fallback_refs: dict, pricing: dict) -> list[dict]:
    offers: list[dict] = []
    # Ofertas de Eneba (con tarifa y cashback).
    for key, p in by_denom.items():
        denom = int(key)
        eff = _eneba_effective(p, pricing)
        offers.append({"store": "Eneba", "denom": denom, "price": eff,
                       "url": p["url"], "approx": False})
    # Ofertas de referencia (Loaded/Instant Gaming) - precio fijo, sin tarifa ni cashback.
    for denom, info in refs.items():
        offers.append({"store": info["store"], "denom": int(denom), "price": info["price"],
                       "url": info.get("url", ""), "approx": info.get("src_currency", "EUR") != "EUR"})
    # Respaldo de config para importes sin referencia en vivo.
    covered = {(o["store"], o["denom"]) for o in offers}
    for k, price in fallback_refs.items():
        denom = int(k)
        if not any(d == denom and s != "Eneba" for s, d in covered):
            offers.append({"store": "Loaded(config)", "denom": denom, "price": float(price),
                           "url": "", "approx": False})
    for o in offers:
        o["ratio"] = round(o["price"] / o["denom"], 4)
        o["discount"] = round((1 - o["ratio"]) * 100, 1)
    offers.sort(key=lambda o: o["ratio"])
    return offers


def _format_ranking(offers: list[dict], top_n: int) -> str:
    lines = ["Mejor saldo PSN por euro ahora:"]
    for i, o in enumerate(offers[:top_n], 1):
        approx = " ~aprox" if o["approx"] else ""
        lines.append(f"{i}) {o['denom']} EUR -> {o['store']} {_fmt(o['price'])} EUR "
                     f"(-{_fmt(o['discount'])}%){approx}")
        if o["url"]:
            lines.append(f"   {o['url']}")
    return "\n".join(lines)


def main() -> int:
    config = _load_json(CONFIG_PATH, {})
    store_url = config.get("store_url", DEFAULT_STORE_URL)
    pricing = config.get("pricing") or {}
    min_disc = float(pricing.get("min_discount_percent", 8))
    top_n = int(pricing.get("top_n", 3))
    fallback_refs = {str(k): float(v) for k, v in (config.get("reference_prices") or {}).items()}

    state = _load_json(STATE_PATH, {})
    on_demand = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    try:
        products = fetch_prices(store_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] No se pudieron obtener los precios de Eneba: {exc}")
        if notify.notifications_enabled():
            try:
                notify.send("PSN scraper", f"El scraper de Eneba fallo: {exc}",
                            priority="low", tags="warning")
            except Exception:  # noqa: BLE001
                pass
        return 1

    by_denom: dict[str, dict] = {}
    for p in products:
        key = str(p["denom"])
        if key not in by_denom or p["price_min"] < by_denom[key]["price_min"]:
            by_denom[key] = p

    refs, ref_origin = _get_references(state, pricing, force_fresh=on_demand)
    offers = _build_offers(by_denom, refs, fallback_refs, pricing)

    print(f"Ofertas: {len(offers)} (referencias: {len(refs)}, origen={ref_origin})")
    print(f"{'#':>2} {'importe':>8} {'tienda':>16} {'precio':>9} {'desc%':>7} {'aprox':>6}")
    print("-" * 56)
    for i, o in enumerate(offers[:max(top_n, 10)], 1):
        print(f"{i:>2} {str(o['denom'])+'EUR':>8} {o['store']:>16} {_fmt(o['price']):>9} "
              f"{_fmt(o['discount']):>7} {'si' if o['approx'] else '':>6}")

    if not offers:
        print("Sin ofertas.")
        return 0

    best = offers[0]
    best_key = f"{best['store']}:{best['denom']}"
    body = _format_ranking(offers, top_n)

    sent = False
    if on_demand:
        # Bajo demanda: responder siempre con el ranking actual.
        try:
            sent = notify.send("Precios PSN ahora", body,
                               url=best["url"] or None, priority="default", tags="moneybag")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] fallo al notificar: {exc}")
    else:
        # Programado: avisar solo si hay un buen chollo y ha mejorado/cambiado.
        last = state.get("best_alert")
        good = best["discount"] >= min_disc
        improved = (last is None or last.get("key") != best_key
                    or best["discount"] > float(last.get("discount", 0)) + 0.3)
        if good and improved:
            try:
                if notify.send(f"Chollo PSN {best['denom']} EUR", body,
                               url=best["url"] or None, priority="high", tags="money_with_wings"):
                    sent = True
                    state["best_alert"] = {"key": best_key, "discount": best["discount"]}
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] fallo al notificar: {exc}")
        elif not good:
            state["best_alert"] = None  # rearmar cuando ya no hay chollo

    state["ranking"] = [{"denom": o["denom"], "store": o["store"], "price": o["price"],
                         "discount": o["discount"], "approx": o["approx"]} for o in offers[:top_n]]
    state["updated_at"] = _now()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\nMejor: {best['denom']} EUR en {best['store']} a {_fmt(best['price'])} EUR "
          f"(-{_fmt(best['discount'])}%). Aviso enviado: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
