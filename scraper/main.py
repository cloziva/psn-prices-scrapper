"""Orquestador: encuentra el saldo PSN mas barato POR EURO, priorizando tiendas oficiales.

Tiendas:
- Loaded/CDKeys: solo da precios espanoles reales desde una IP europea. Por eso sus precios
  se GUARDAN de forma persistente en data/loaded_prices.json (commiteado a GitHub) y se usan
  siempre como referencia. Se REFRESCAN solo cuando ejecutas el run.bat en tu PC (IP espanola):
  re-scrapea las que esten EN STOCK y actualiza el archivo (las demas mantienen su ultimo valor).
- Instant Gaming: tienda oficial; precio EUR exacto desde cualquier IP -> se scrapea EN VIVO.
- Eneba: marketplace; precio variable + tasa de servicio (fijo + % del importe) + cashback.

Para cada oferta:  ratio = precio_efectivo / valor_nominal   (menor = mejor)
    Loaded/IG: efectivo = precio (tiendas de precio fijo)
    Eneba:     efectivo = base + tasa(importe) - cashback

Avisos programados: (1) mejor OFICIAL si llega a min_discount_percent y mejora; (2) ENEBA si le
saca >= eneba_worth_extra_percent puntos a la mejor oficial. Manual/bajo demanda: informe completo.

Variables de entorno:
  PSN_ON_DEMAND=1       -> manda siempre el informe del mejor precio (lo pone run.bat).
  PSN_REFRESH_LOADED=1  -> re-scrapea Loaded en vivo y actualiza data/loaded_prices.json (run.bat).
  PSN_STATE_PATH=ruta   -> donde guardar el estado (anti-spam/cache); por defecto data/state.json.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import notify
from scrape_eneba import DEFAULT_STORE_URL, fetch_prices
from scrape_reference import LOADED_URLS, _fx_rates, fetch_instant_gaming, fetch_loaded_detailed

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scraper" / "config.json"
STATE_PATH = Path(os.environ.get("PSN_STATE_PATH") or (ROOT / "data" / "state.json"))
LOADED_STORE_PATH = ROOT / "data" / "loaded_prices.json"  # precios de Loaded persistentes (en el repo)
REF_TTL_MIN = 45  # cache de Instant Gaming


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
    # La tasa de servicio de Eneba escala con el IMPORTE del card: ~ fijo + % del valor nominal.
    face = product.get("denom") or base
    fee = float(pricing.get("eneba_fee_fixed_eur", 0) or 0)
    fee += face * float(pricing.get("eneba_fee_percent", 0) or 0) / 100
    cashback = product.get("cashback", 0.0) or 0.0
    if not pricing.get("count_cashback", True):
        cashback = 0.0
    return round(base + fee - cashback, 2)


def _load_loaded() -> dict[int, dict]:
    """{denom: {'price': float, 'in_stock': bool}}. Acepta tambien numeros sueltos (edicion
    manual) tratandolos como disponibles."""
    data = _load_json(LOADED_STORE_PATH, {})
    out: dict[int, dict] = {}
    for k, v in data.items():
        try:
            denom = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict) and v.get("price") is not None:
            out[denom] = {"price": float(v["price"]), "in_stock": bool(v.get("in_stock", True))}
        elif isinstance(v, (int, float)):
            out[denom] = {"price": float(v), "in_stock": True}  # numero suelto = mostrar
    return out


def _save_loaded(prices: dict[int, dict]) -> None:
    LOADED_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {}
    for denom in sorted(prices):
        e = prices[denom]
        entry = {"price": round(float(e["price"]), 2), "in_stock": bool(e.get("in_stock", True))}
        if e.get("updated_at"):
            entry["updated_at"] = e["updated_at"]
        out[str(denom)] = entry
    with open(LOADED_STORE_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _refresh_loaded(stored: dict[int, dict]) -> tuple[dict[int, dict], int]:
    """Re-scrapea Loaded EN VIVO (precio + stock; solo fiable desde IP europea) y fusiona:
    actualiza precio Y disponibilidad de las leidas; las que fallen mantienen su ultimo estado."""
    try:
        detailed = fetch_loaded_detailed()  # {denom: {price(EUR), in_stock, url}}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] no se pudo refrescar Loaded: {exc} (mantengo lo guardado)")
        return stored, 0
    now = _now()
    updated = dict(stored)
    for denom, info in detailed.items():
        updated[denom] = {"price": float(info["price"]), "in_stock": bool(info["in_stock"]),
                          "updated_at": now}
    n_stock = sum(1 for info in detailed.values() if info["in_stock"])
    return updated, n_stock


def _get_instant_gaming(state: dict, force_fresh: bool, pricing: dict) -> dict[int, dict]:
    if not pricing.get("include_instant_gaming", True):
        return {}
    cache = state.get("ig_cache") or {}
    if (not force_fresh) and cache.get("data") and _age_min(cache.get("t", "")) < REF_TTL_MIN:
        return {int(k): v for k, v in cache["data"].items()}
    try:
        ig = fetch_instant_gaming(_fx_rates())
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Instant Gaming fallo: {exc}")
        return {int(k): v for k, v in cache.get("data", {}).items()}
    state["ig_cache"] = {"t": _now(), "data": {str(k): v for k, v in ig.items()}}
    return ig


def _build_offers(by_denom: dict, ig: dict, loaded_prices: dict, pricing: dict) -> list[dict]:
    offers: list[dict] = []
    for key, p in by_denom.items():  # Eneba (marketplace), ya filtrado a en-stock
        offers.append({"store": "Eneba", "official": False, "denom": int(key),
                       "price": _eneba_effective(p, pricing), "url": p["url"]})
    for denom, info in ig.items():  # Instant Gaming (oficial, en vivo)
        offers.append({"store": "Instant Gaming", "official": True, "denom": int(denom),
                       "price": float(info["price"]), "url": info.get("url", "")})
    for denom, entry in loaded_prices.items():  # Loaded (oficial, guardado)
        if not entry.get("in_stock"):
            continue  # agotado: no se puede comprar -> no aparece
        offers.append({"store": "Loaded", "official": True, "denom": int(denom),
                       "price": float(entry["price"]), "url": LOADED_URLS.get(int(denom), "")})
    for o in offers:
        o["ratio"] = round(o["price"] / o["denom"], 4)
        o["discount"] = round((1 - o["ratio"]) * 100, 1)
    offers.sort(key=lambda o: o["ratio"])
    return offers


def _line(o: dict) -> str:
    return f"{o['denom']} EUR - {o['store']} {_fmt(o['price'])} EUR (-{_fmt(o['discount'])}%)"


def _report(best_off, best_enb, offers, top_n) -> str:
    parts = []
    if best_off:
        parts.append("Mejor OFICIAL (compra segura):\n" + _line(best_off)
                     + (f"\n{best_off['url']}" if best_off['url'] else ""))
    if best_enb:
        parts.append("Mejor en Eneba (marketplace):\n" + _line(best_enb)
                     + (f"\n{best_enb['url']}" if best_enb['url'] else ""))
    top = "\n".join(f"{i}) {_line(o)}" for i, o in enumerate(offers[:top_n], 1))
    parts.append("Top por euro:\n" + top)
    return "\n\n".join(parts)


def _improved(last, key, discount) -> bool:
    return last is None or last.get("key") != key or discount > float(last.get("discount", 0)) + 0.3


def main() -> int:
    config = _load_json(CONFIG_PATH, {})
    store_url = config.get("store_url", DEFAULT_STORE_URL)
    pricing = config.get("pricing") or {}
    deal_margin = float(pricing.get("deal_margin_eur", 0.30))
    top_n = int(pricing.get("top_n", 5))

    state = _load_json(STATE_PATH, {})
    on_demand = bool(os.environ.get("PSN_ON_DEMAND")) or \
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    refresh_loaded = bool(os.environ.get("PSN_REFRESH_LOADED"))

    try:
        products = fetch_prices(store_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] Eneba fallo: {exc}")
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

    # Loaded: persistente. Se refresca en vivo SOLO si lo pides (run.bat en tu PC espanol).
    loaded_prices = _load_loaded()
    if refresh_loaded:
        loaded_prices, n_stock = _refresh_loaded(loaded_prices)
        _save_loaded(loaded_prices)
        print(f"Loaded REFRESCADO en vivo: {n_stock} en stock / {len(loaded_prices)} leidos.")
    else:
        n_stock = sum(1 for e in loaded_prices.values() if e.get("in_stock"))
        print(f"Loaded (guardado): {n_stock} en stock / {len(loaded_prices)} importes.")

    ig = _get_instant_gaming(state, force_fresh=on_demand, pricing=pricing)
    offers = _build_offers(by_denom, ig, loaded_prices, pricing)

    official = [o for o in offers if o["official"]]
    eneba = [o for o in offers if not o["official"]]
    best_off = official[0] if official else None
    best_enb = eneba[0] if eneba else None

    print(f"Ofertas: {len(offers)} (oficiales: {len(official)}, Eneba: {len(eneba)}, "
          f"Instant Gaming en vivo: {len(ig)})")
    print(f"{'#':>2} {'importe':>8} {'tienda':>16} {'precio':>9} {'desc%':>7} {'tipo':>11}")
    print("-" * 60)
    for i, o in enumerate(offers[:max(top_n, 12)], 1):
        print(f"{i:>2} {str(o['denom'])+'EUR':>8} {o['store']:>16} {_fmt(o['price']):>9} "
              f"{_fmt(o['discount']):>7} {'oficial' if o['official'] else 'marketplace':>11}")

    sent = []
    if on_demand:
        try:
            if notify.send("Saldo PSN ahora", _report(best_off, best_enb, offers, top_n),
                           url=(best_off or best_enb or {}).get("url") or None,
                           priority="default", tags="moneybag"):
                sent.append("informe")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] fallo al notificar: {exc}")
    else:
        # UN AVISO POR CADA importe cuyo mejor precio COMPRABLE este por debajo de su referencia
        # (el precio de la tienda oficial mas barata: Loaded o Instant Gaming) para ese importe.
        deal_alerts = state.setdefault("deal_alerts", {})
        buyable: dict[int, dict] = {}
        for o in offers:  # 'offers' ya son SOLO ofertas en stock
            d = o["denom"]
            if d not in buyable or o["price"] < buyable[d]["price"]:
                buyable[d] = o
        for denom in sorted(buyable):
            best = buyable[denom]
            refs = []
            if denom in loaded_prices:
                refs.append((float(loaded_prices[denom]["price"]), "Loaded"))
            if denom in ig:
                refs.append((float(ig[denom]["price"]), "Instant Gaming"))
            if not refs:
                continue  # sin referencia oficial para ese importe
            ref_price, ref_store = min(refs, key=lambda r: r[0])
            key = str(denom)
            if best["price"] <= ref_price - deal_margin:
                last = deal_alerts.get(key)
                if last is None or best["price"] < float(last) - 0.001:  # nuevo o mas barato
                    saving = round(ref_price - best["price"], 2)
                    body = (f"{denom} EUR en {best['store']} a {_fmt(best['price'])} EUR\n"
                            f"por debajo de {ref_store} ({_fmt(ref_price)} EUR) -> ahorras {_fmt(saving)} EUR\n"
                            f"{best['url']}")
                    try:
                        if notify.send(f"Chollo PSN {denom} EUR", body, url=best["url"] or None,
                                       priority="high", tags="money_with_wings"):
                            deal_alerts[key] = best["price"]
                            sent.append(f"{denom}EUR")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[warn] fallo al notificar {denom} EUR: {exc}")
            else:
                deal_alerts.pop(key, None)  # ya no hay chollo: rearmar

    state["ranking"] = [{"denom": o["denom"], "store": o["store"], "price": o["price"],
                         "discount": o["discount"], "official": o["official"]} for o in offers[:top_n]]
    state["updated_at"] = _now()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"\nMejor oficial: {_line(best_off) if best_off else '-'}")
    print(f"Mejor Eneba:   {_line(best_enb) if best_enb else '-'}")
    print(f"Avisos enviados: {sent or 'ninguno'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
