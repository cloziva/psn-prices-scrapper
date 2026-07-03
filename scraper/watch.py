"""Modo VIGILANCIA: escanea muy a menudo (cada ~60s) para cazar ofertas fugaces de Eneba.

Eneba es un marketplace: las buenas ofertas vuelan en minutos. Este bucle re-escanea Eneba
en cada vuelta (las tiendas oficiales usan lo guardado/cache, que casi no cambia) y avisa AL
INSTANTE cuando aparece un buen precio nuevo (con anti-spam, no repite el mismo).

Se usa:
  - En GitHub Actions: bucle de PSN_WATCH_MINUTES minutos (el workflow lo repite cada hora) -> 24/7.
  - En tu PC (watch.bat): bucle infinito mientras tengas la ventana abierta.

Variables de entorno:
  PSN_WATCH_INTERVAL  segundos entre escaneos (por defecto 60).
  PSN_WATCH_MINUTES   duracion del bucle; 0 = infinito (por defecto 0).
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timezone

import main as scan


def run() -> None:
    interval = max(20, int(float(os.environ.get("PSN_WATCH_INTERVAL", "60"))))
    max_minutes = float(os.environ.get("PSN_WATCH_MINUTES", "0") or 0)

    # La vigilancia SIEMPRE trabaja en modo "solo avisa novedades" (no informe cada vuelta).
    os.environ.pop("PSN_ON_DEMAND", None)
    os.environ.pop("GITHUB_EVENT_NAME", None)

    print(f"== Vigilancia PSN: escaneo cada ~{interval}s"
          + (f", durante {max_minutes:.0f} min" if max_minutes else ", indefinido")
          + ". ==")

    start = time.monotonic()
    n = 0
    while True:
        n += 1
        print(f"\n----- escaneo #{n}  {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC -----")
        try:
            scan.main()
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - nunca romper el bucle por un fallo puntual
            print(f"[watch] fallo puntual en el escaneo: {exc}")

        if max_minutes and (time.monotonic() - start) / 60 >= max_minutes:
            print("\nFin del ciclo de vigilancia.")
            break
        time.sleep(max(5, interval + random.uniform(-5, 5)))


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nVigilancia detenida.")
