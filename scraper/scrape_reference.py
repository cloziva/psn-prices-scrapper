"""Scrapers de tiendas de REFERENCIA (precio fijo, sin tarifas ni cashback).

Sirven para saber el "precio justo" de cada importe y decidir si Eneba compensa.

- Loaded / CDKeys (mismo backend): una sola peticion a la categoria devuelve TODOS
  los importes de Espana con su precio, en un JSON-LD `ItemList`. Es la referencia
  principal (cubre todos los importes de forma robusta).
- Instant Gaming: el precio va en el HTML (`itemprop="price"`), una ficha por importe.
  Sus URLs no son generables (llevan un id), asi que se usa un mapa curado de los
  importes mas habituales. Es una referencia secundaria.

IMPORTANTE: loaded.com bloquea las peticiones de la libreria `requests` por su huella
TLS (devuelve 403). Por eso aqui usamos `curl_cffi` con impersonate="chrome", que imita
la huella TLS/HTTP de un navegador real. (curl normal tambien funciona; requests no.)

`fetch_references()` combina ambas y devuelve, por importe, el precio MAS BARATO
(la mejor alternativa real frente a la que comparar Eneba).
"""
from __future__ import annotations

import json
import re
import time

from curl_cffi import requests as cffi

_HEADERS = {"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
_IMPERSONATE = "chrome"

# Loaded/CDKeys: categoria con todas las PSN de Espana (precios en JSON-LD ItemList).
LOADED_CATEGORY_URL = "https://www.cdkeys.com/playstation-network-psn/psn-cards"

# Instant Gaming: una ficha por importe (URL con id; mapa curado, ampliable).
INSTANT_GAMING_URLS = {
    10: "https://www.instant-gaming.com/en/3567-buy-game-playstation-playstation-network-card-10e-spain/",
    20: "https://www.instant-gaming.com/en/619-buy-playstation-store-gift-card-20eur-eur20-card-playstation-4-playstation-5-game-playstation-store-spain/",
    35: "https://www.instant-gaming.com/en/620-buy-playstation-network-card-35eur-35-euros-card-playstation-3-playstation-4-playstation-5-game-playstation-store-spain/",
    50: "https://www.instant-gaming.com/en/621-buy-playstation-network-card-50eur-50-euros-card-playstation-3-playstation-4-playstation-5-game-playstation-store-spain/",
    60: "https://www.instant-gaming.com/en/12014-buy-playstation-store-gift-card-60eur-eur60-card-playstation-5-playstation-4-game-playstation-store-spain/",
}

_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_IG_PRICE_RE = re.compile(r'itemprop="price"[^>]*content="([0-9.]+)"', re.I)
_IG_CURRENCY_RE = re.compile(r'itemprop="priceCurrency"[^>]*content="([A-Z]{3})"', re.I)
_DENOM_RE = re.compile(r"(\d+)\s*EUR", re.I)
_WANT_CURRENCY = "EUR"  # estas tiendas tambien pueden geolocalizar la divisa segun la IP


def _get(url: str, timeout: int = 30) -> str:
    resp = cffi.get(url, headers=_HEADERS, impersonate=_IMPERSONATE, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_loaded(timeout: int = 30) -> dict[int, dict]:
    """{denom: {'price': float, 'url': str, 'store': 'Loaded'}} para PSN Espana."""
    html = _get(LOADED_CATEGORY_URL, timeout)
    out: dict[int, dict] = {}
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
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if offers.get("priceCurrency") != _WANT_CURRENCY:
                    continue  # solo EUR (desde otra IP podria servir USD/GBP)
                price = offers.get("price") or offers.get("lowPrice")
                m = _DENOM_RE.search(name)
                if not m or price is None:
                    continue
                denom = int(m.group(1))
                p = round(float(price), 2)
                if denom not in out or p < out[denom]["price"]:
                    out[denom] = {
                        "price": p,
                        "url": item.get("url") or offers.get("url") or "",
                        "store": "Loaded",
                    }
    return out


def fetch_instant_gaming(timeout: int = 30) -> dict[int, dict]:
    """{denom: {'price', 'url', 'store': 'Instant Gaming'}} para los importes del mapa."""
    out: dict[int, dict] = {}
    for denom, url in INSTANT_GAMING_URLS.items():
        try:
            html = _get(url, timeout)
        except Exception:  # noqa: BLE001 - si una ficha falla, seguimos con las demas
            continue
        m = _IG_PRICE_RE.search(html)
        cur = _IG_CURRENCY_RE.search(html)
        if m and cur and cur.group(1).upper() == _WANT_CURRENCY:
            out[denom] = {"price": round(float(m.group(1)), 2), "url": url, "store": "Instant Gaming"}
        time.sleep(0.5)  # ser educados con su servidor
    return out


def fetch_references(timeout: int = 30, include_instant_gaming: bool = True) -> dict[int, dict]:
    """Combina las tiendas de referencia y se queda con el precio mas barato por importe."""
    refs: dict[int, dict] = {}
    try:
        refs.update(fetch_loaded(timeout))
    except Exception as exc:  # noqa: BLE001
        print(f"[ref] Loaded fallo: {exc}")

    if include_instant_gaming:
        try:
            for denom, info in fetch_instant_gaming(timeout).items():
                if denom not in refs or info["price"] < refs[denom]["price"]:
                    refs[denom] = info
        except Exception as exc:  # noqa: BLE001
            print(f"[ref] Instant Gaming fallo: {exc}")

    return refs


if __name__ == "__main__":
    refs = fetch_references()
    print(f"{'importe':>8} {'ref EUR':>9}  tienda")
    print("-" * 40)
    for d in sorted(refs):
        print(f"{d:>6}EUR {refs[d]['price']:>9.2f}  {refs[d]['store']}")
    print(f"\nTotal importes con referencia: {len(refs)}")
