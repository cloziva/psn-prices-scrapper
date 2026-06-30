"""Diagnostico: que precio da Loaded/CDKeys desde ESTA IP (en GitHub = EE.UU.).

Muestra, por importe: precio crudo + divisa servida + conversion a EUR (BCE) y la compara
con el precio REAL en Espana (conocido). Sirve para ver si Loaded es usable desde EE.UU.
"""
from scrape_reference import LOADED_URLS, _fx_rates, _get, _product_price, _to_eur

# Precios reales observados desde Espana (EUR) para comparar.
KNOWN_ES = {10: 9.79, 18: 15.49, 20: 18.99, 25: 22.49, 50: 44.99, 100: 94.99}

rates = _fx_rates()
print(f"Tipos BCE: 1 EUR = {rates.get('USD')} USD / {rates.get('GBP')} GBP\n")
print(f"{'importe':>7} {'crudo':>9} {'divisa':>6} {'->EUR':>8} {'ES real':>8} {'gap':>7}")
print("-" * 52)
for d in [10, 18, 20, 25, 50, 100]:
    url = LOADED_URLS.get(d)
    if not url:
        continue
    try:
        price, cur, in_stock = _product_price(_get(url))
    except Exception as exc:  # noqa: BLE001
        print(f"{d:>6}E  ERROR: {exc}")
        continue
    eur = _to_eur(price, cur, rates) if price is not None else None
    es = KNOWN_ES.get(d)
    gap = f"{(eur/es - 1) * 100:+.1f}%" if (eur and es) else "?"
    stock = "" if in_stock else " (AGOTADO)"
    print(f"{d:>6}E {str(price):>9} {str(cur):>6} {str(eur):>8} {str(es):>8} {gap:>7}{stock}")
