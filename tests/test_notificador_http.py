"""Pruebas propias de NotificadorHTTP: la petición que arma y sus errores.

Todo con httpx.MockTransport: el "servidor" es una función que recibe la
petición y decide qué responder (o qué error de red simular).
"""

import json

import httpx
import pytest

from libreria.api.dependencies import obtener_notificador
from libreria.excepciones import ServicioExternoError
from libreria.notificadores import NotificadorHTTP, NotificadorRegistro

URL = "https://avisos.test/webhook"


class ServidorFalso:
    """Responde con lo que se le indique, en orden, y anota cada petición."""

    def __init__(self, *respuestas: httpx.Response | Exception) -> None:
        self._respuestas = list(respuestas)
        self.peticiones: list[httpx.Request] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(peticion)
        respuesta = self._respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def notificador(self) -> NotificadorHTTP:
        return NotificadorHTTP(URL, transporte=httpx.MockTransport(self))


def test_hace_un_post_con_json_a_la_url() -> None:
    servidor = ServidorFalso(httpx.Response(202))

    servidor.notificador().enviar("ana@mail.com", "Hola", "Mensaje")

    [peticion] = servidor.peticiones
    assert peticion.method == "POST"
    assert str(peticion.url) == URL
    assert peticion.headers["content-type"] == "application/json"
    assert peticion.headers["user-agent"].startswith("libreria/")
    assert json.loads(peticion.content) == {
        "destinatario": "ana@mail.com",
        "asunto": "Hola",
        "mensaje": "Mensaje",
    }


@pytest.mark.parametrize("codigo", [400, 404, 500, 503])
def test_una_respuesta_de_error_se_traduce_y_no_se_reintenta(codigo: int) -> None:
    servidor = ServidorFalso(httpx.Response(codigo))

    with pytest.raises(ServicioExternoError, match="ana@mail.com"):
        servidor.notificador().enviar("ana@mail.com", "Hola", "Mensaje")

    assert len(servidor.peticiones) == 1


def test_si_no_se_pudo_conectar_se_reintenta_una_vez() -> None:
    servidor = ServidorFalso(httpx.ConnectError("sin red"), httpx.Response(202))

    servidor.notificador().enviar("ana@mail.com", "Hola", "Mensaje")  # no lanza

    assert len(servidor.peticiones) == 2


def test_si_nunca_se_pudo_conectar_lanza_servicio_externo_error() -> None:
    servidor = ServidorFalso(httpx.ConnectError("sin red"), httpx.ConnectError("sin red"))

    with pytest.raises(ServicioExternoError) as info:
        servidor.notificador().enviar("ana@mail.com", "Hola", "Mensaje")

    assert isinstance(info.value.__cause__, httpx.ConnectError)  # la causa se conserva
    assert len(servidor.peticiones) == 2


def test_tiempo_agotado_esperando_respuesta_no_se_reintenta() -> None:
    # El aviso pudo haber llegado: reintentar lo mandaría dos veces
    servidor = ServidorFalso(httpx.ReadTimeout("el servidor no contestó"))

    with pytest.raises(ServicioExternoError):
        servidor.notificador().enviar("ana@mail.com", "Hola", "Mensaje")

    assert len(servidor.peticiones) == 1


# ── Qué notificador usa la API ───────────────────────────────────────────────


def test_la_api_usa_http_solo_si_hay_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIBRERIA_URL_NOTIFICACIONES", raising=False)
    assert isinstance(obtener_notificador(), NotificadorRegistro)

    monkeypatch.setenv("LIBRERIA_URL_NOTIFICACIONES", URL)
    notificador = obtener_notificador()
    assert isinstance(notificador, NotificadorHTTP)
    assert notificador.url == URL
