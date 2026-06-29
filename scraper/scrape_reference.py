"""Scrapers de tiendas de REFERENCIA (precio fijo, sin tarifas ni cashback).

Sirven para saber el "precio justo" de cada importe y decidir si Eneba compensa.

- Loaded / CDKeys: ficha de producto por importe (Product JSON-LD con precio). Se usa
  un mapa de URLs de Espana. (La pagina de categoria NO vale desde un servidor: Loaded
  geolocaliza el catalogo por IP y desde EE.UU. no muestra los productos de Espana; las
  fichas de producto, en cambio, son fijas por region.) Referencia principal.
- Instant Gaming: precio en el HTML (`itemprop="price"`), una ficha por importe.

DOS detalles para que funcione desde un servidor (GitHub corre en EE.UU.):
  1) loaded.com bloquea la libreria `requests` por su huella TLS (403). Usamos
     `curl_cffi` con impersonate="chrome", que imita a un navegador real.
  2) Ambas tiendas muestran el precio en la divisa de la IP del visitante (USD/GBP desde
     EE.UU.). Leemos la divisa servida y la CONVERTIMOS a EUR con tipos del BCE
     (Frankfurter, gratis). Asi funciona desde cualquier IP (conversion aproximada;
     para eso esta el margen de seguridad).

`fetch_references(denoms)` combina ambas y devuelve, por importe, el precio (EUR) MAS
barato: {denom: {'price', 'url', 'store', 'src_currency'}}.
"""
from __future__ import annotations

import json
import re
import time

from curl_cffi import requests as cffi

_HEADERS = {"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
_IMPERSONATE = "chrome"

# Loaded/CDKeys: ficha de producto de Espana por importe.
LOADED_URLS = {
    5: "https://www.loaded.com/playstation-network-psn-card-5-eur-spain-cd-key",
    6: "https://www.loaded.com/playstation-network-psn-card-6-eur-spain-cd-key",
    10: "https://www.loaded.com/playstation-network-psn-card-10-eur-spain-cd-key",
    12: "https://www.loaded.com/playstation-network-psn-card-12-eur-spain-cd-key",
    15: "https://www.loaded.com/playstation-network-psn-card-15-eur-spain-cd-key",
    18: "https://www.loaded.com/playstation-network-psn-card-18-eur-spain-cd-key",
    20: "https://www.loaded.com/playstation-network-psn-card-20-eur-spain",
    25: "https://www.loaded.com/playstation-network-psn-card-25eur-spain-cd-key",
    30: "https://www.loaded.com/playstation-network-psn-card-30-eur-spain",
    35: "https://www.loaded.com/playstation-network-psn-card-35-eur-spain",
    40: "https://www.loaded.com/playstation-network-psn-card-40eur-spain-cd-key",
    45: "https://www.loaded.com/playstation-network-psn-card-45-eur-spain",
    50: "https://www.loaded.com/playstation-network-psn-card-50-eur-spain",
    60: "https://www.loaded.com/playstation-network-psn-card-60-eur-spain",
    75: "https://www.loaded.com/playstation-network-psn-card-75-eur-spain",
    90: "https://www.loaded.com/playstation-network-psn-card-90-eur-spain",
    100: "https://www.loaded.com/playstation-network-psn-card-100-eur-spain",
    120: "https://www.loaded.com/playstation-network-psn-card-120-eur-spain",
}

# Instant Gaming: ficha por importe (URL con id).
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


def _product_price(html: str):
    """(price, currency) del primer Product JSON-LD de la pagina. (None, None) si no hay."""
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or node.get("@type") != "Product":
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price") or offers.get("lowPrice")
            if price is not None:
                return price, offers.get("priceCurrency") or "EUR"
    return None, None


def _selected(mapping: dict, denoms) -> dict:
    return mapping if denoms is None else {d: u for d, u in mapping.items() if d in denoms}


def fetch_loaded(rates: dict, denoms=None, timeout: int = 30) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for denom, url in _selected(LOADED_URLS, denoms).items():
        try:
            price, cur = _product_price(_get(url, timeout))
        except Exception:  # noqa: BLE001 - si una ficha falla, seguimos
            continue
        eur = _to_eur(price, cur, rates) if price is not None else None
        if eur is not None:
            out[denom] = {"price": eur, "url": url, "store": "Loaded", "src_currency": cur}
        time.sleep(0.4)  # ser educados con su servidor
    return out


def fetch_instant_gaming(rates: dict, denoms=None, timeout: int = 30) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for denom, url in _selected(INSTANT_GAMING_URLS, denoms).items():
        try:
            html = _get(url, timeout)
        except Exception:  # noqa: BLE001
            continue
        m = _IG_PRICE_RE.search(html)
        cur = _IG_CURRENCY_RE.search(html)
        if m:
            src = cur.group(1).upper() if cur else "EUR"
            eur = _to_eur(m.group(1), src, rates)
            if eur is not None:
                out[denom] = {"price": eur, "url": url, "store": "Instant Gaming", "src_currency": src}
        time.sleep(0.4)
    return out


def fetch_references(denoms=None, timeout: int = 30, include_instant_gaming: bool = True) -> dict[int, dict]:
    """Combina las tiendas de referencia y se queda con el precio (EUR) mas barato por importe.

    `denoms`: iterable de importes a consultar (para no pedir mas de la cuenta). None = todos.
    """
    denoms = set(denoms) if denoms is not None else None
    rates = _fx_rates(timeout)
    refs: dict[int, dict] = {}
    try:
        refs.update(fetch_loaded(rates, denoms, timeout))
    except Exception as exc:  # noqa: BLE001
        print(f"[ref] Loaded fallo: {exc}")

    if include_instant_gaming:
        try:
            for denom, info in fetch_instant_gaming(rates, denoms, timeout).items():
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
        conv = "" if r["src_currency"] == "EUR" else f"  (de {r['src_currency']})"
        print(f"{d:>6}EUR {r['price']:>9.2f}  {r['src_currency']:>6}  {r['store']}{conv}")
    print(f"\nTotal importes con referencia: {len(refs)}")
