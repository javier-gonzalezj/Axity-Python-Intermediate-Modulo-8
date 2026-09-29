"""Adaptadores del puerto Notificador (ver puertos.py).

- NotificadorHTTP: el real. Hace un POST con JSON a una URL (un webhook o un
  servicio de avisos que a su vez manda el correo, SMS, etc.).
- NotificadorRegistro: escribe el aviso en el log. Es el que usa la API
  cuando no se configura LIBRERIA_URL_NOTIFICACIONES (p. ej. en desarrollo).
- NotificadorEnMemoria: guarda los avisos en una lista para revisarlos en las
  pruebas.

Los tres cumplen el mismo contrato (tests/test_contrato_notificador.py).
"""

import logging
from dataclasses import dataclass, field

import httpx

from libreria.excepciones import ServicioExternoError
from libreria.utilidades import reintentar

log = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 5
CABECERAS = {"User-Agent": "libreria/0.1 (proyecto del curso de Python)"}


@dataclass(frozen=True)
class Notificacion:
    """Un aviso tal como se envió."""

    destinatario: str
    asunto: str
    mensaje: str


# ── HTTP (el real) ───────────────────────────────────────────────────────────


class NotificadorHTTP:
    """Publica cada aviso como JSON en `url`:

        POST <url>
        {"destinatario": "...", "asunto": "...", "mensaje": "..."}

    `transporte` solo se usa en las pruebas (httpx.MockTransport), igual que
    en buscador.py. En el programa se deja en None: httpx usa la red.
    """

    def __init__(self, url: str, transporte: httpx.BaseTransport | None = None) -> None:
        self.url = url
        self._transporte = transporte

    def enviar(self, destinatario: str, asunto: str, mensaje: str) -> None:
        cuerpo = {"destinatario": destinatario, "asunto": asunto, "mensaje": mensaje}
        try:
            with httpx.Client(
                headers=CABECERAS, timeout=TIMEOUT_SEGUNDOS, transport=self._transporte
            ) as cliente:
                _publicar(cliente, self.url, cuerpo)
        except httpx.HTTPError as e:
            raise ServicioExternoError(f"No se pudo notificar a {destinatario}: {e}") from e
        log.info("Notificación enviada a %s: %s", destinatario, asunto)


# Un POST solo se reintenta si NUNCA llegó al servidor (no se pudo conectar).
# Si se agotó el tiempo esperando la respuesta, el aviso pudo haber llegado:
# reintentar lo mandaría dos veces. Un 500 tampoco se arregla reintentando.
@reintentar(
    intentos=2,
    espera_inicial=0.2,
    excepciones=(httpx.ConnectError, httpx.ConnectTimeout),
)
def _publicar(cliente: httpx.Client, url: str, cuerpo: dict[str, str]) -> None:
    respuesta = cliente.post(url, json=cuerpo)
    respuesta.raise_for_status()  # 4xx/5xx -> httpx.HTTPStatusError


# ── Registro (log) ───────────────────────────────────────────────────────────


class NotificadorRegistro:
    """Escribe el aviso en el log en lugar de enviarlo. Nunca falla."""

    def enviar(self, destinatario: str, asunto: str, mensaje: str) -> None:
        log.info(
            "Notificación (solo registro) para %s: %s\n%s",
            destinatario,
            asunto,
            mensaje,
            # `extra` agrega el aviso completo al registro: así las pruebas
            # pueden leerlo sin interpretar el texto
            extra={"notificacion": Notificacion(destinatario, asunto, mensaje)},
        )


# ── En memoria (para pruebas) ────────────────────────────────────────────────


@dataclass
class NotificadorEnMemoria:
    """Guarda los avisos en `enviados`, en orden. Nunca falla."""

    enviados: list[Notificacion] = field(default_factory=list)

    def enviar(self, destinatario: str, asunto: str, mensaje: str) -> None:
        self.enviados.append(Notificacion(destinatario, asunto, mensaje))
