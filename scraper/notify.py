"""Envio de notificaciones push al iPhone via ntfy (gratis).

Configuracion por variables de entorno (en GitHub: Settings -> Secrets):
  - NTFY_TOPIC   (obligatorio) nombre del topic al que estas suscrito en la app.
  - NTFY_SERVER  (opcional)    por defecto https://ntfy.sh
  - NTFY_TOKEN   (opcional)    token de acceso si proteges el topic con auth.

El cuerpo del mensaje va como UTF-8 (admite tildes, EUR, emojis). Las cabeceras
HTTP (Title, Tags...) se mantienen en ASCII a proposito: los emojis del icono se
envian como "tags" (shortcodes de ntfy, p. ej. money_with_wings -> emoji).
"""
from __future__ import annotations

import os
import sys

import requests

DEFAULT_SERVER = "https://ntfy.sh"


def notifications_enabled() -> bool:
    return bool(os.environ.get("NTFY_TOPIC"))


def send(
    title: str,
    message: str,
    *,
    url: str | None = None,
    priority: str = "default",
    tags: str | None = None,
    timeout: int = 15,
) -> bool:
    """Envia un push. Devuelve True si se envio, False si no hay topic configurado.

    Lanza requests.HTTPError si el servidor responde con error.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[notify] NTFY_TOPIC no definido: notificaciones desactivadas.")
        return False

    server = (os.environ.get("NTFY_SERVER") or DEFAULT_SERVER).rstrip("/")
    token = os.environ.get("NTFY_TOKEN")

    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if url:
        headers["Click"] = url
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return True


if __name__ == "__main__":
    # Prueba rapida:  python scraper/notify.py "mensaje opcional"
    msg = sys.argv[1] if len(sys.argv) > 1 else "Prueba de PSN Prices Scrapper. Si lees esto, funciona."
    ok = send("Test PSN", msg, priority="high", tags="white_check_mark")
    print("Push enviado." if ok else "No enviado: define NTFY_TOPIC primero.")
