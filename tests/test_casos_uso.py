"""Pruebas de los casos de uso con UnidadDeTrabajoEnMemoria.

No hay base de datos: corren en milisegundos. Se pueden usar con confianza
porque test_contrato_repositorios.py garantiza que la unidad de trabajo en
memoria se comporta igual que la de SQL.

Aquí no se vuelven a probar las reglas (eso es test_pedidos.py): se prueba la
ORQUESTACIÓN. Que se guarde lo que se tiene que guardar, que se confirme una
sola vez y que, si algo falla, no se confirme nada. También los avisos: que
lleguen después de confirmar, solo si algo cambió, y que un aviso fallido no
deshaga el pedido.
"""

from datetime import datetime

import pytest

from libreria.casos_uso import (
    AgregarLibro,
    CambiarEstatusComando,
    CambiarEstatusPedido,
    CrearPedido,
    CrearPedidoComando,
)
from libreria.excepciones import (
    LibroDuplicadoError,
    LibroInvalidoError,
    PedidoNoEncontradoError,
    ServicioExternoError,
    StockInsuficienteError,
    TransicionEstatusError,
    UsuarioNoEncontradoError,
)
from libreria.modelos import Autor, Libro, Usuario
from libreria.notificadores import Notificacion, NotificadorEnMemoria
from libreria.pedidos import Pedido
from libreria.puertos import Notificador
from libreria.repositorios_memoria import UnidadDeTrabajoEnMemoria

HOY = datetime(2026, 9, 29, 12, 0)
ANA = 1


def hacer_libro(isbn: str, cantidad: int, precio: float = 100.0) -> Libro:
    return Libro(
        isbn=isbn,
        titulo=f"Libro {isbn}",
        autor=Autor(nombre="Autora de Prueba"),
        genero=["Novela"],
        año_publicacion=2000,
        precio=precio,
        en_stock=cantidad > 0,
        cantidad_disponible=cantidad,
        editorial="Editorial X",
    )


@pytest.fixture
def uow() -> UnidadDeTrabajoEnMemoria:
    """A tiene 5 ejemplares y B tiene 1. Ana es la única usuaria."""
    return UnidadDeTrabajoEnMemoria(
        libros=[hacer_libro("A", 5, precio=200.0), hacer_libro("B", 1)],
        usuarios=[Usuario(id=ANA, nombre="Ana", email="ana@mail.com")],
    )


def stock(uow: UnidadDeTrabajoEnMemoria, isbn: str) -> int:
    libro = uow.libros.obtener(isbn)
    assert libro is not None
    return libro.cantidad_disponible


def crear(
    uow: UnidadDeTrabajoEnMemoria, lineas: dict[str, int], notificador: Notificador | None = None
) -> Pedido:
    caso = CrearPedido(uow, notificador or NotificadorEnMemoria(), ahora=lambda: HOY)  # reloj fijo
    return caso.ejecutar(CrearPedidoComando(usuario_id=ANA, lineas=lineas))


def cambiar(
    uow: UnidadDeTrabajoEnMemoria,
    pedido: Pedido,
    estatus: str,
    notificador: Notificador | None = None,
) -> Pedido:
    assert pedido.id is not None
    comando = CambiarEstatusComando(pedido_id=pedido.id, estatus=estatus)
    return CambiarEstatusPedido(uow, notificador or NotificadorEnMemoria()).ejecutar(comando)


# ── CrearPedido ──────────────────────────────────────────────────────────────


class TestCrearPedido:
    def test_registra_el_pedido_descuenta_stock_y_confirma_una_vez(
        self, uow: UnidadDeTrabajoEnMemoria
    ) -> None:
        pedido = crear(uow, {"A": 2, "B": 1})

        assert pedido.id is not None
        assert pedido.fecha == HOY  # la fecha sale del reloj inyectado
        assert pedido.total == pytest.approx(2 * 200.0 + 100.0)
        assert uow.pedidos.obtener(pedido.id) == pedido
        assert stock(uow, "A") == 3
        assert stock(uow, "B") == 0
        assert uow.confirmaciones == 1

    def test_usuario_inexistente_no_toca_nada(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        caso = CrearPedido(uow, NotificadorEnMemoria(), ahora=lambda: HOY)

        with pytest.raises(UsuarioNoEncontradoError):
            caso.ejecutar(CrearPedidoComando(usuario_id=999, lineas={"A": 1}))

        assert stock(uow, "A") == 5
        assert uow.confirmaciones == 0

    def test_sin_stock_no_se_guarda_nada(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        with pytest.raises(StockInsuficienteError):
            crear(uow, {"A": 2, "B": 5})  # A alcanza, B no

        assert stock(uow, "A") == 5
        assert uow.pedidos.obtener(1) is None
        assert uow.confirmaciones == 0

    def test_libro_inexistente_se_rechaza(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        with pytest.raises(LibroInvalidoError, match="No existe el libro Z"):
            crear(uow, {"Z": 1})

        assert uow.confirmaciones == 0


# ── CambiarEstatusPedido ─────────────────────────────────────────────────────


class TestCambiarEstatusPedido:
    def test_avanzar_guarda_el_estatus(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 1})

        resultado = cambiar(uow, pedido, "pagado")

        assert resultado.estatus == "pagado"
        assert pedido.id is not None
        guardado = uow.pedidos.obtener(pedido.id)
        assert guardado is not None and guardado.estatus == "pagado"
        assert uow.confirmaciones == 2  # crear + pagar

    def test_cancelar_regresa_el_stock_y_lo_guarda(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 2, "B": 1})

        cambiar(uow, pedido, "cancelado")

        assert stock(uow, "A") == 5
        assert stock(uow, "B") == 1

    def test_cancelar_dos_veces_no_confirma_ni_regresa_stock_otra_vez(
        self, uow: UnidadDeTrabajoEnMemoria
    ) -> None:
        pedido = crear(uow, {"A": 2})
        cambiar(uow, pedido, "cancelado")

        resultado = cambiar(uow, pedido, "cancelado")

        assert resultado.estatus == "cancelado"
        assert stock(uow, "A") == 5
        assert uow.confirmaciones == 2  # crear + la primera cancelación

    def test_transicion_invalida_no_cambia_nada(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 1})

        with pytest.raises(TransicionEstatusError):
            cambiar(uow, pedido, "enviado")  # no se salta el pago

        assert pedido.id is not None
        guardado = uow.pedidos.obtener(pedido.id)
        assert guardado is not None and guardado.estatus == "pendiente"
        assert uow.confirmaciones == 1

    def test_pedido_inexistente(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        with pytest.raises(PedidoNoEncontradoError):
            CambiarEstatusPedido(uow, NotificadorEnMemoria()).ejecutar(
                CambiarEstatusComando(pedido_id=999, estatus="pagado")
            )


# ── AgregarLibro ─────────────────────────────────────────────────────────────


class TestAgregarLibro:
    def test_agrega_y_confirma(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        AgregarLibro(uow).ejecutar(hacer_libro("C", 3))

        assert stock(uow, "C") == 3
        assert uow.confirmaciones == 1

    def test_isbn_repetido_no_confirma(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        with pytest.raises(LibroDuplicadoError, match="Ya existe"):
            AgregarLibro(uow).ejecutar(hacer_libro("A", 1))

        assert stock(uow, "A") == 5  # el original no se tocó
        assert uow.confirmaciones == 0


# ── Avisos al cliente ────────────────────────────────────────────────────────


class NotificadorQueFalla:
    """Como si el servicio de avisos estuviera caído."""

    def enviar(self, destinatario: str, asunto: str, mensaje: str) -> None:
        raise ServicioExternoError("El servicio de avisos no responde")


class TestNotificaciones:
    def test_crear_avisa_al_cliente_con_el_resumen(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        avisos = NotificadorEnMemoria()

        pedido = crear(uow, {"A": 2}, avisos)

        assert len(avisos.enviados) == 1
        aviso = avisos.enviados[0]
        assert aviso.destinatario == "ana@mail.com"
        assert aviso.asunto == f"Recibimos tu pedido #{pedido.id}"
        assert "Libro A x2" in aviso.mensaje
        assert "Total: $400.00" in aviso.mensaje

    def test_si_crear_falla_no_se_avisa(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        avisos = NotificadorEnMemoria()

        with pytest.raises(StockInsuficienteError):
            crear(uow, {"B": 5}, avisos)

        assert avisos.enviados == []  # nunca se avisa de algo que no se guardó

    def test_cambiar_estatus_avisa_el_nuevo_estatus(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 1})
        avisos = NotificadorEnMemoria()

        cambiar(uow, pedido, "pagado", avisos)

        assert avisos.enviados == [
            Notificacion(
                "ana@mail.com",
                f"Tu pedido #{pedido.id} ahora está pagado",
                "Recibimos tu pago. Pronto enviaremos tus libros.",
            )
        ]

    def test_sin_cambio_no_hay_aviso(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 1})
        cambiar(uow, pedido, "cancelado")
        avisos = NotificadorEnMemoria()

        cambiar(uow, pedido, "cancelado", avisos)  # ya estaba cancelado

        assert avisos.enviados == []

    def test_transicion_invalida_no_avisa(self, uow: UnidadDeTrabajoEnMemoria) -> None:
        pedido = crear(uow, {"A": 1})
        avisos = NotificadorEnMemoria()

        with pytest.raises(TransicionEstatusError):
            cambiar(uow, pedido, "enviado", avisos)

        assert avisos.enviados == []

    def test_un_aviso_fallido_no_deshace_el_pedido(
        self, uow: UnidadDeTrabajoEnMemoria, caplog: pytest.LogCaptureFixture
    ) -> None:
        pedido = crear(uow, {"A": 2}, NotificadorQueFalla())  # no lanza

        assert pedido.id is not None
        assert uow.pedidos.obtener(pedido.id) is not None  # el pedido sí quedó
        assert stock(uow, "A") == 3
        assert "No se pudo avisar a ana@mail.com" in caplog.text
