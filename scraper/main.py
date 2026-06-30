"""Orquestador: encuentra el saldo PSN mas barato POR EURO, priorizando tiendas oficiales.

Modelo:
- Tiendas OFICIALES (Loaded/CDKeys, Instant Gaming): precio fijo, fiable, sin tarifas ni
  cashback. Son la referencia preferente. Se coge, por importe, la mas barata (y en stock).
- Eneba: marketplace. Precio variable, con tarifa de servicio (que escala con el importe) y
  cashback. Mas barato a veces, pero menos "seguro" que una tienda oficial.

Para cada oferta EN STOCK:  ratio = precio_efectivo / valor_nominal   (menor = mejor)
    Oficial: efectivo = precio
    Eneba:   efectivo = base + tarifa(base) - cashback
             tarifa(base) = eneba_fee_fixed_eur + base * eneba_fee_percent/100   (modelo)

Avisos (programado):
  1) MEJOR OFICIAL: cuando la mejor oferta en tienda oficial alcanza min_discount_percent y
     mejora respecto al ultimo aviso (es lo importante: tienda oficial = compra segura barata).
  2) ENEBA COMPENSA: cuando Eneba le saca >= eneba_worth_extra_percent puntos de descuento a la
     mejor oficial (solo entonces merece la pena el marketplace).
Bajo demanda (workflow_dispatch): manda SIEMPRE el informe actual (mejor oficial + mejor Eneba + top).
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
# El estado puede vivir fuera del repo (util en un servidor: asi `git pull` nunca choca).
STATE_PATH = Path(os.environ.get("PSN_STATE_PATH") or (ROOT / "data" / "state.json"))
REF_TTL_MIN = 45


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
    fee = float(pricing.get("eneba_fee_fixed_eur", 0) or 0)
    fee += base * float(pricing.get("eneba_fee_percent", 0) or 0) / 100
    cashback = product.get("cashback", 0.0) or 0.0
    if not pricing.get("count_cashback", True):
        cashback = 0.0
    return round(base + fee - cashback, 2)


def _get_references(state: dict, pricing: dict, force_fresh: bool) -> tuple[dict, str]:
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
    for key, p in by_denom.items():  # Eneba (marketplace) - ya viene filtrado a en-stock
        denom = int(key)
        offers.append({"store": "Eneba", "official": False, "denom": denom,
                       "price": _eneba_effective(p, pricing), "url": p["url"], "approx": False})
    for denom, info in refs.items():  # oficiales (Loaded/IG), en stock
        offers.append({"store": info["store"], "official": True, "denom": int(denom),
                       "price": info["price"], "url": info.get("url", ""),
                       "approx": info.get("src_currency", "EUR") != "EUR"})
    covered = {(o["store"], o["denom"]) for o in offers}
    for k, price in fallback_refs.items():  # respaldo de config para importes sin referencia viva
        denom = int(k)
        if not any(d == denom and st != "Eneba" for st, d in covered):
            offers.append({"store": "Loaded(config)", "official": True, "denom": denom,
                           "price": float(price), "url": "", "approx": False})
    for o in offers:
        o["ratio"] = round(o["price"] / o["denom"], 4)
        o["discount"] = round((1 - o["ratio"]) * 100, 1)
    offers.sort(key=lambda o: o["ratio"])
    return offers


def _line(o: dict) -> str:
    tag = " ~aprox" if o["approx"] else ""
    return f"{o['denom']} EUR - {o['store']} {_fmt(o['price'])} EUR (-{_fmt(o['discount'])}%){tag}"


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
    min_disc = float(pricing.get("min_discount_percent", 8))
    worth_extra = float(pricing.get("eneba_worth_extra_percent", 2.0))
    top_n = int(pricing.get("top_n", 4))
    fallback_refs = {str(k): float(v) for k, v in (config.get("reference_prices") or {}).items()}

    state = _load_json(STATE_PATH, {})
    on_demand = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

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

    refs, ref_origin = _get_references(state, pricing, force_fresh=on_demand)
    offers = _build_offers(by_denom, refs, fallback_refs, pricing)

    official = [o for o in offers if o["official"]]
    eneba = [o for o in offers if not o["official"]]
    best_off = official[0] if official else None
    best_enb = eneba[0] if eneba else None

    print(f"Ofertas en stock: {len(offers)} (oficiales: {len(official)}, Eneba: {len(eneba)}, "
          f"referencias origen={ref_origin})")
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
        # 1) Mejor oferta en tienda OFICIAL.
        if best_off and best_off["discount"] >= min_disc:
            key = f"{best_off['store']}:{best_off['denom']}"
            if _improved(state.get("official_alert"), key, best_off["discount"]):
                body = "Comprar en tienda oficial (precio fijo, fiable):\n" + _line(best_off) \
                       + (f"\n{best_off['url']}" if best_off['url'] else "") \
                       + "\n\n" + _report(best_off, best_enb, offers, top_n).split("\n\n", 1)[-1]
                try:
                    if notify.send(f"Chollo PSN oficial: {best_off['denom']} EUR", body,
                                   url=best_off["url"] or None, priority="high", tags="money_with_wings"):
                        state["official_alert"] = {"key": key, "discount": best_off["discount"]}
                        sent.append("oficial")
                except Exception as exc:  # noqa: BLE001
                    print(f"[warn] fallo al notificar oficial: {exc}")
        elif not best_off or best_off["discount"] < min_disc:
            state["official_alert"] = None

        # 2) Eneba (marketplace) compensa si le saca ventaja suficiente a la mejor oficial.
        if best_enb and best_off and best_enb["discount"] >= min_disc \
                and best_enb["discount"] - best_off["discount"] >= worth_extra:
            key = f"Eneba:{best_enb['denom']}"
            if _improved(state.get("eneba_alert"), key, best_enb["discount"]):
                body = (f"En Eneba (marketplace) compensa mas que la oficial:\n{_line(best_enb)}\n"
                        f"{best_enb['url']}\n\nMejor oficial: {_line(best_off)}")
                try:
                    if notify.send(f"Eneba compensa: {best_enb['denom']} EUR", body,
                                   url=best_enb["url"] or None, priority="high", tags="money_with_wings"):
                        state["eneba_alert"] = {"key": key, "discount": best_enb["discount"]}
                        sent.append("eneba")
                except Exception as exc:  # noqa: BLE001
                    print(f"[warn] fallo al notificar eneba: {exc}")
        else:
            state["eneba_alert"] = None

    state["ranking"] = [{"denom": o["denom"], "store": o["store"], "price": o["price"],
                         "discount": o["discount"], "official": o["official"], "approx": o["approx"]}
                        for o in offers[:top_n]]
    state["updated_at"] = _now()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    bo = _line(best_off) if best_off else "-"
    be = _line(best_enb) if best_enb else "-"
    print(f"\nMejor oficial: {bo}\nMejor Eneba:   {be}\nAvisos enviados: {sent or 'ninguno'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
