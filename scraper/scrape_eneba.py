"""Scraper de precios de saldo PSN (region Espana) en Eneba.

Estrategia (validada): la pagina de categoria de Eneba sirve TODO el catalogo
con los precios incrustados en un JSON de Apollo (`__APOLLO_STATE__`). Hacemos
un GET normal y parseamos ese JSON. No hace falta navegador headless ni esquivar
Cloudflare.

Estructura relevante del JSON:
  - Nodos `Product::<slug>` con `name`, `slug`, `regions` y `cheapestAuction`.
  - `cheapestAuction` es una referencia (`{"__ref": "Auction::..."}`) al nodo de
    la subasta MAS BARATA, que contiene `price` (precio minimo) y `msrp` (valor
    nominal), ambos en CENTIMOS dentro de un objeto Money {amount, currency}.

Si Eneba cambiara el formato o activara Cloudflare para estas IPs, este modulo
lanza EnebaScrapeError; ver el Plan B en el README.
"""
from __future__ import annotations

import json
import re

import requests

DEFAULT_STORE_URL = "https://www.eneba.com/store/psn-gift-cards/spain"
PRODUCT_URL_TEMPLATE = "https://www.eneba.com/{slug}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Eneba sirve precios en la divisa de la IP del visitante (USD desde un servidor en
# EE.UU. como los de GitHub). La cookie `exchange` fija la divisa: la forzamos a EUR
# para ver siempre los precios de Espana, no los del pais del servidor.
_COOKIES = {"exchange": "EUR"}
_WANT_CURRENCY = "EUR"

_APOLLO_RE = re.compile(
    r'<script id="__APOLLO_STATE__" type="application/json">(.*?)</script>',
    re.S,
)
_DENOM_RE = re.compile(r"(\d+)\s*EUR", re.I)
_CLOUDFLARE_MARKERS = ("Just a moment", "cf-chl-", "Attention Required", "Enable JavaScript and cookies")


class EnebaScrapeError(RuntimeError):
    """Se lanza cuando no se puede extraer/parsear el precio (formato cambiado, bloqueo, etc.)."""


def _money(node: dict, field_prefix: str, want_currency: str = _WANT_CURRENCY) -> dict | None:
    """Devuelve el objeto Money de un campo parametrizado (p. ej. 'price(...)').

    Eneba puede incrustar el precio en varias divisas segun la IP/cookies; aqui
    preferimos SIEMPRE `want_currency` (EUR) para no leer por error precios en USD.
    """
    candidates = [v for k, v in node.items()
                  if k.startswith(field_prefix) and isinstance(v, dict)]
    for v in candidates:
        if v.get("currency") == want_currency:
            return v
    return candidates[0] if candidates else None


def _cashback(auction: dict) -> float:
    """Cashback (en euros) que da la oferta. La estructura es
    cashback({"currency":"EUR"}) -> {"price": {"amount": 238, ...}}."""
    for key, value in auction.items():
        if key.startswith("cashback(") and isinstance(value, dict):
            price = value.get("price") or {}
            if price.get("amount") is not None:
                return round(price["amount"] / 100, 2)
    return 0.0


def fetch_prices(store_url: str = DEFAULT_STORE_URL, timeout: int = 30) -> list[dict]:
    """Devuelve una lista de dicts, uno por importe encontrado:

        {denom, name, price_min, currency, msrp, merchant, in_stock, url}

    `price_min` y `msrp` van en euros (float). Lanza EnebaScrapeError si algo
    falla en la obtencion o el parseo.
    """
    resp = requests.get(store_url, headers=_HEADERS, cookies=_COOKIES, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    if any(marker in html for marker in _CLOUDFLARE_MARKERS):
        raise EnebaScrapeError(
            "Respuesta parece un reto de Cloudflare; aplicar Plan B (ver README)."
        )

    match = _APOLLO_RE.search(html)
    if not match:
        raise EnebaScrapeError(
            "No se encontro __APOLLO_STATE__ en el HTML; Eneba pudo cambiar el formato."
        )
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise EnebaScrapeError(f"JSON de Apollo no parseable: {exc}") from exc

    def deref(ref):
        if isinstance(ref, dict) and "__ref" in ref:
            return data.get(ref["__ref"])
        return ref

    results: list[dict] = []
    for key, node in data.items():
        if not key.startswith("Product::") or not isinstance(node, dict):
            continue

        name = node.get("name") or ""
        slug = node.get("slug") or ""

        auction = deref(node.get("cheapestAuction"))
        if not isinstance(auction, dict):
            continue

        price = _money(auction, "price(")
        if not price or price.get("amount") is None:
            continue
        currency = price.get("currency", "EUR")
        if currency != _WANT_CURRENCY:
            # Defensa: si pese a la cookie llegara un precio en otra divisa, lo saltamos.
            continue
        price_min = round(price["amount"] / 100, 2)

        msrp = _money(auction, "msrp(")
        msrp_eur = round(msrp["amount"] / 100, 2) if msrp and msrp.get("amount") else None

        denom_match = _DENOM_RE.search(name)
        denom = int(denom_match.group(1)) if denom_match else (int(msrp_eur) if msrp_eur else None)
        if denom is None:
            continue

        merchant = (auction.get("merchant") or {}).get("displayname")
        cashback = _cashback(auction)
        # % de cashback del producto concreto (la PSN puede tener distinto % que un juego).
        prod_cb = node.get("cashback")
        cashback_percent = prod_cb.get("valuePercent") if isinstance(prod_cb, dict) else None

        results.append(
            {
                "denom": denom,
                "name": name,
                "price_min": price_min,
                "currency": currency,
                "msrp": msrp_eur,
                "cashback": cashback,
                "cashback_percent": cashback_percent,
                "merchant": merchant,
                "in_stock": bool(auction.get("isInStock")),
                "url": PRODUCT_URL_TEMPLATE.format(slug=slug) if slug else store_url,
            }
        )

    if not results:
        raise EnebaScrapeError(
            "Se parseo el JSON pero no se encontro ningun producto con precio."
        )

    results.sort(key=lambda r: r["denom"])
    return results


if __name__ == "__main__":
    # Ejecucion directa: imprime una tabla con todos los importes y su precio minimo.
    items = fetch_prices()
    print(f"{'importe':>8} {'min EUR':>9} {'cashback':>9} {'cb%':>5} {'nominal':>8}  vendedor")
    print("-" * 70)
    for it in items:
        msrp = f"{it['msrp']:.2f}" if it["msrp"] else "-"
        pct = f"{it['cashback_percent']}%" if it.get("cashback_percent") else "-"
        print(f"{it['denom']:>6}EUR {it['price_min']:>9.2f} {it['cashback']:>9.2f} {pct:>5} {msrp:>8}  {it['merchant'] or '-'}")
    print(f"\nTotal importes: {len(items)}")
