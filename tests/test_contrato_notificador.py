"""Pruebas de CONTRATO del puerto Notificador.

Corren contra los tres adaptadores:

- [http]: NotificadorHTTP con httpx.MockTransport. Un "servidor" falso recibe
  cada POST y lo anota, así que no se usa la red.
- [registro]: NotificadorRegistro. Los avisos se leen del log (caplog).
- [memoria]: NotificadorEnMemoria, el que usan las pruebas de casos de uso.

Cada adaptador se observa a su manera (qué recibió el servidor, qué quedó en
el log, qué hay en la lista), pero las aserciones son las mismas.

Los errores (servidor caído, 500...) solo existen en HTTP y se prueban en
test_notificador_http.py.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest

from libreria.notificadores import (
    Notificacion,
    NotificadorEnMemoria,
    NotificadorHTTP,
    NotificadorRegistro,
)
from libreria.puertos import Notificador


@dataclass
class Entorno:
    notificador: Notificador
    recibidos: Callable[[], list[Notificacion]]  # lo que "llegó" del otro lado


@pytest.fixture(params=["http", "registro", "memoria"])
def entorno(request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture) -> Entorno:
    if request.param == "http":
        recibidos: list[Notificacion] = []

        def servidor(peticion: httpx.Request) -> httpx.Response:
            recibidos.append(Notificacion(**json.loads(peticion.content)))
            return httpx.Response(202)

        notificador = NotificadorHTTP(
            "https://avisos.test/webhook", transporte=httpx.MockTransport(servidor)
        )
        return Entorno(notificador, lambda: list(recibidos))

    if request.param == "registro":
        caplog.set_level(logging.INFO, logger="libreria.notificadores")
        return Entorno(
            NotificadorRegistro(),
            lambda: [
                getattr(registro, "notificacion")  # noqa: B009 (LogRecord no lo declara)
                for registro in caplog.records
                if hasattr(registro, "notificacion")
            ],
        )

    memoria = NotificadorEnMemoria()
    return Entorno(memoria, lambda: list(memoria.enviados))


def test_el_aviso_llega_completo(entorno: Entorno) -> None:
    aviso = Notificacion(
        destinatario="ana@mail.com",
        asunto="Tu pedido #7 ahora está enviado",
        mensaje="Línea 1: «Cien años» — ñandú ✓\nLínea 2: Total: $349.90",
    )

    entorno.notificador.enviar(aviso.destinatario, aviso.asunto, aviso.mensaje)

    assert entorno.recibidos() == [aviso]  # acentos, símbolos y saltos de línea intactos


def test_varios_avisos_llegan_en_orden(entorno: Entorno) -> None:
    avisos = [Notificacion(f"cliente{i}@mail.com", f"Aviso {i}", f"Mensaje {i}") for i in range(3)]

    for aviso in avisos:
        entorno.notificador.enviar(aviso.destinatario, aviso.asunto, aviso.mensaje)

    assert entorno.recibidos() == avisos


def test_un_mensaje_vacio_tambien_se_entrega(entorno: Entorno) -> None:
    entorno.notificador.enviar("ana@mail.com", "Solo asunto", "")

    assert entorno.recibidos() == [Notificacion("ana@mail.com", "Solo asunto", "")]
