"""Scrapers de tiendas de REFERENCIA (precio fijo, sin tarifas ni cashback).

Sirven para saber el "precio justo" de cada importe y decidir si Eneba compensa.

- Loaded / CDKeys (mismo backend): una sola peticion a la categoria devuelve TODOS
  los importes de Espana con su precio, en un JSON-LD `ItemList`. Referencia principal.
- Instant Gaming: el precio va en el HTML (`itemprop="price"`), una ficha por importe.
  Sus URLs llevan un id, asi que se usa un mapa curado. Referencia secundaria.

DOS detalles importantes para que funcione desde un servidor (GitHub corre en EE.UU.):
  1) loaded.com bloquea la libreria `requests` por su huella TLS (403). Usamos
     `curl_cffi` con impersonate="chrome", que imita a un navegador real.
  2) Ambas tiendas muestran el precio en la divisa de la IP del visitante (USD/GBP
     desde EE.UU.). No es facil forzar EUR, asi que leemos la divisa que sirvan y la
     CONVERTIMOS a EUR con los tipos de cambio del BCE (Frankfurter, gratis). Asi
     funciona desde cualquier IP. La conversion es aproximada (margen de seguridad).

`fetch_references()` combina ambas y devuelve, por importe, el precio (en EUR) MAS
barato: {denom: {'price', 'url', 'store', 'src_currency'}}.
"""
from __future__ import annotations

import json
import re
import time

from curl_cffi import requests as cffi

_HEADERS = {"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
_IMPERSONATE = "chrome"

LOADED_CATEGORY_URL = "https://www.cdkeys.com/playstation-network-psn/psn-cards"

INSTANT_GAMING_URLS = {
    10: "https://www.instant-gaming.com/en/3567-buy-game-playstation-playstation-network-card-10e-spain/",
    20: "https://www.instant-gaming.com/en/619-buy-playstation-store-gift-card-20eur-eur20-card-playstation-4-playstation-5-game-playstation-store-spain/",
    35: "https://www.instant-gaming.com/en/620-buy-playstation-network-card-35eur-35-euros-card-playstation-3-playstation-4-playstation-5-game-playstation-store-spain/",
    50: "https://www.instant-gaming.com/en/621-buy-playstation-network-card-50eur-50-euros-card-playstation-3-playstation-4-playstation-5-game-playstation-store-spain/",
    60: "https://www.instant-gaming.com/en/12014-buy-playstation-store-gift-card-60eur-eur60-card-playstation-5-playstation-4-game-playstation-store-spain/",
}

# Tipos de cambio del BCE (sin clave). rates['USD'] = USD por 1 EUR.
_FX_URL = "https://api.frankfurter.app/latest?base=EUR"

_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_IG_PRICE_RE = re.compile(r'itemprop="price"[^>]*content="([0-9.]+)"', re.I)
_IG_CURRENCY_RE = re.compile(r'itemprop="priceCurrency"[^>]*content="([A-Z]{3})"', re.I)
_DENOM_RE = re.compile(r"(\d+)\s*EUR", re.I)


def _get(url: str, timeout: int = 30) -> str:
    resp = cffi.get(url, headers=_HEADERS, impersonate=_IMPERSONATE, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _fx_rates(timeout: int = 15) -> dict:
    """{divisa: unidades por 1 EUR}. {} si falla (entonces solo valdran precios ya en EUR)."""
    try:
        resp = cffi.get(_FX_URL, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("rates", {}) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[ref] tipos de cambio fallo: {exc}")
        return {}


def _to_eur(amount, currency: str, rates: dict):
    """Convierte 'amount' en 'currency' a EUR. None si no hay tipo de cambio."""
    if currency == "EUR":
        return round(float(amount), 2)
    rate = rates.get(currency)
    if not rate:
        return None
    return round(float(amount) / float(rate), 2)


def fetch_loaded(rates: dict, timeout: int = 30) -> dict[int, dict]:
    """{denom: {'price'(EUR), 'url', 'store':'Loaded', 'src_currency'}} para PSN Espana."""
    html = _get(LOADED_CATEGORY_URL, timeout)
    out: dict[int, dict] = {}
    _seen_spain = 0
    _currencies: dict[str, int] = {}
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or node.get("@type") != "ItemList":
                continue
            for el in node.get("itemListElement", []):
                item = el.get("item", el) if isinstance(el, dict) else {}
                name = item.get("name", "") or ""
                if "(Spain)" not in name:
                    continue
                _seen_spain += 1
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                raw_price = offers.get("price") or offers.get("lowPrice")
                src_cur = offers.get("priceCurrency") or "EUR"
                _currencies[src_cur] = _currencies.get(src_cur, 0) + 1
                m = _DENOM_RE.search(name)
                if not m or raw_price is None:
                    continue
                eur = _to_eur(raw_price, src_cur, rates)
                if eur is None:
                    continue
                denom = int(m.group(1))
                if denom not in out or eur < out[denom]["price"]:
                    out[denom] = {
                        "price": eur,
                        "url": item.get("url") or offers.get("url") or "",
                        "store": "Loaded",
                        "src_currency": src_cur,
                    }
    print(f"[ref] Loaded: pagina {len(html)//1024}KB, {_seen_spain} productos (Spain), "
          f"divisas={_currencies or '∅'}, {len(out)} en EUR")
    return out


def fetch_instant_gaming(rates: dict, timeout: int = 30) -> dict[int, dict]:
    """{denom: {'price'(EUR), 'url', 'store':'Instant Gaming', 'src_currency'}}."""
    out: dict[int, dict] = {}
    for denom, url in INSTANT_GAMING_URLS.items():
        try:
            html = _get(url, timeout)
        except Exception:  # noqa: BLE001 - si una ficha falla, seguimos
            continue
        m = _IG_PRICE_RE.search(html)
        cur = _IG_CURRENCY_RE.search(html)
        if m:
            src_cur = cur.group(1).upper() if cur else "EUR"
            eur = _to_eur(m.group(1), src_cur, rates)
            if eur is not None:
                out[denom] = {"price": eur, "url": url, "store": "Instant Gaming", "src_currency": src_cur}
        time.sleep(0.5)  # ser educados con su servidor
    return out


def fetch_references(timeout: int = 30, include_instant_gaming: bool = True) -> dict[int, dict]:
    """Combina las tiendas de referencia y se queda con el precio (EUR) mas barato por importe."""
    rates = _fx_rates(timeout)
    refs: dict[int, dict] = {}
    try:
        refs.update(fetch_loaded(rates, timeout))
    except Exception as exc:  # noqa: BLE001
        print(f"[ref] Loaded fallo: {exc}")

    if include_instant_gaming:
        try:
            for denom, info in fetch_instant_gaming(rates, timeout).items():
                if denom not in refs or info["price"] < refs[denom]["price"]:
                    refs[denom] = info
        except Exception as exc:  # noqa: BLE001
            print(f"[ref] Instant Gaming fallo: {exc}")

    return refs


if __name__ == "__main__":
    refs = fetch_references()
    print(f"{'importe':>8} {'ref EUR':>9}  {'divisa':>6}  tienda")
    print("-" * 48)
    for d in sorted(refs):
        r = refs[d]
        conv = "" if r["src_currency"] == "EUR" else f" (de {r['src_currency']})"
        print(f"{d:>6}EUR {r['price']:>9.2f}  {r['src_currency']:>6}  {r['store']}{conv}")
    print(f"\nTotal importes con referencia: {len(refs)}")
