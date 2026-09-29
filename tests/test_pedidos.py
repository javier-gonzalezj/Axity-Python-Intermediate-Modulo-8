"""Pruebas de dominio de los pedidos (pedidos.py y el inventario de Libro).

Ni SQLite ni FastAPI: solo objetos en memoria. Por eso son instantáneas y se
pueden escribir en lenguaje de negocio. Que las filas se guarden bien ya lo
revisan test_basedatos.py y test_crud.py; aquí se revisan las REGLAS.
"""

from datetime import datetime

import pytest

from libreria.excepciones import (
    LibroInvalidoError,
    PedidoInvalidoError,
    StockInsuficienteError,
    TransicionEstatusError,
)
from libreria.modelos import Autor, Libro
from libreria.pedidos import Pedido, PedidoItem, cambiar_estatus, crear_pedido

HOY = datetime(2026, 9, 29, 12, 0)


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
def libros() -> dict[str, Libro]:
    """Inventario: A tiene 5 ejemplares, B tiene 1 y C está agotado."""
    return {
        "A": hacer_libro("A", 5, precio=200.0),
        "B": hacer_libro("B", 1, precio=150.5),
        "C": hacer_libro("C", 0),
    }


def pedido_en(estatus: str) -> Pedido:
    item = PedidoItem(isbn="A", titulo="Libro A", cantidad=2, precio_unitario=200.0)
    return Pedido.model_validate(
        {"id": 1, "usuario_id": 7, "fecha": HOY, "estatus": estatus, "items": [item]}
    )


# ── Inventario de un libro ───────────────────────────────────────────────────


class TestInventario:
    def test_retirar_descuenta_ejemplares(self) -> None:
        libro = hacer_libro("A", 5)

        libro.retirar(2)

        assert libro.cantidad_disponible == 3
        assert libro.en_stock

    def test_retirar_el_ultimo_ejemplar_lo_marca_agotado(self) -> None:
        libro = hacer_libro("A", 1)

        libro.retirar(1)

        assert libro.cantidad_disponible == 0
        assert not libro.en_stock

    def test_no_se_retiran_mas_ejemplares_de_los_que_hay(self) -> None:
        libro = hacer_libro("A", 2)

        with pytest.raises(StockInsuficienteError, match="pediste 3, hay 2"):
            libro.retirar(3)

        assert libro.cantidad_disponible == 2  # el fallo no deja el libro a medias

    def test_reponer_un_libro_agotado_lo_vuelve_a_poner_en_stock(self) -> None:
        libro = hacer_libro("A", 0)

        libro.reponer(4)

        assert libro.cantidad_disponible == 4
        assert libro.en_stock


# ── Crear un pedido ──────────────────────────────────────────────────────────


class TestCrearPedido:
    def test_un_pedido_nuevo_queda_pendiente_y_descuenta_stock(
        self, libros: dict[str, Libro]
    ) -> None:
        pedido = crear_pedido(7, {"A": 2, "B": 1}, libros, fecha=HOY)

        assert pedido.estatus == "pendiente"
        assert pedido.usuario_id == 7
        assert pedido.fecha == HOY
        assert pedido.id is None  # todavía no se guarda
        assert libros["A"].cantidad_disponible == 3
        assert libros["B"].cantidad_disponible == 0

    def test_el_total_usa_el_precio_del_momento_de_la_compra(
        self, libros: dict[str, Libro]
    ) -> None:
        pedido = crear_pedido(7, {"A": 2, "B": 1}, libros, fecha=HOY)
        libros["A"].precio = 999.0  # subir el precio después no cambia el pedido

        assert pedido.total == pytest.approx(2 * 200.0 + 150.5)

    def test_un_pedido_sin_libros_se_rechaza(self, libros: dict[str, Libro]) -> None:
        with pytest.raises(PedidoInvalidoError, match="no tiene libros"):
            crear_pedido(7, {}, libros, fecha=HOY)

    @pytest.mark.parametrize("cantidad", [0, -1])
    def test_la_cantidad_debe_ser_positiva(self, libros: dict[str, Libro], cantidad: int) -> None:
        with pytest.raises(PedidoInvalidoError, match="Cantidad inválida"):
            crear_pedido(7, {"A": cantidad}, libros, fecha=HOY)

    def test_no_se_puede_pedir_un_libro_que_no_existe(self, libros: dict[str, Libro]) -> None:
        with pytest.raises(LibroInvalidoError, match="No existe el libro Z"):
            crear_pedido(7, {"Z": 1}, libros, fecha=HOY)

    def test_no_se_puede_pedir_un_libro_agotado(self, libros: dict[str, Libro]) -> None:
        with pytest.raises(StockInsuficienteError):
            crear_pedido(7, {"C": 1}, libros, fecha=HOY)

    def test_si_una_linea_falla_ningun_libro_se_descuenta(self, libros: dict[str, Libro]) -> None:
        # A sí alcanza, pero B no: el pedido completo se rechaza (todo o nada)
        with pytest.raises(StockInsuficienteError):
            crear_pedido(7, {"A": 2, "B": 5}, libros, fecha=HOY)

        assert libros["A"].cantidad_disponible == 5
        assert libros["B"].cantidad_disponible == 1


# ── Estatus ──────────────────────────────────────────────────────────────────


class TestEstatus:
    def test_flujo_normal_pendiente_pagado_enviado(self) -> None:
        pedido = pedido_en("pendiente")

        pedido.avanzar_a("pagado")
        pedido.avanzar_a("enviado")

        assert pedido.estatus == "enviado"

    @pytest.mark.parametrize(
        ("actual", "nuevo"),
        [
            ("pendiente", "enviado"),  # no se salta el pago
            ("pagado", "pendiente"),  # no se regresa
            ("enviado", "pagado"),
            ("cancelado", "pagado"),  # un cancelado ya no revive
            ("pendiente", "inventado"),  # estatus que no existe
        ],
    )
    def test_transiciones_no_permitidas(self, actual: str, nuevo: str) -> None:
        pedido = pedido_en(actual)

        with pytest.raises(TransicionEstatusError):
            pedido.avanzar_a(nuevo)

        assert pedido.estatus == actual

    def test_avanzar_a_no_sirve_para_cancelar(self) -> None:
        # Cancelar tiene que regresar stock, así que tiene su propio método
        with pytest.raises(TransicionEstatusError, match="cancelar"):
            pedido_en("pendiente").avanzar_a("cancelado")


# ── Cancelar ─────────────────────────────────────────────────────────────────


class TestCancelar:
    @pytest.mark.parametrize("estatus", ["pendiente", "pagado"])
    def test_cancelar_regresa_los_ejemplares(self, estatus: str) -> None:
        pedido = pedido_en(estatus)
        libros = {"A": hacer_libro("A", 3)}

        assert pedido.cancelar(libros) is True

        assert pedido.estatus == "cancelado"
        assert libros["A"].cantidad_disponible == 5

    def test_un_pedido_enviado_no_se_cancela(self) -> None:
        pedido = pedido_en("enviado")
        libros = {"A": hacer_libro("A", 3)}

        with pytest.raises(TransicionEstatusError, match="no se puede cancelar"):
            pedido.cancelar(libros)

        assert pedido.estatus == "enviado"
        assert libros["A"].cantidad_disponible == 3

    def test_cancelar_dos_veces_no_regresa_stock_dos_veces(self) -> None:
        pedido = pedido_en("pendiente")
        libros = {"A": hacer_libro("A", 3)}

        pedido.cancelar(libros)
        assert pedido.cancelar(libros) is False

        assert libros["A"].cantidad_disponible == 5

    def test_si_faltan_libros_no_se_cancela_a_medias(self) -> None:
        pedido = pedido_en("pendiente")

        with pytest.raises(LibroInvalidoError):
            pedido.cancelar({})

        assert pedido.estatus == "pendiente"


# ── Punto único de entrada ───────────────────────────────────────────────────


class TestCambiarEstatus:
    def test_cancelado_pasa_por_cancelar(self) -> None:
        pedido = pedido_en("pagado")
        libros = {"A": hacer_libro("A", 0)}

        cambiar_estatus(pedido, "cancelado", libros)

        assert pedido.estatus == "cancelado"
        assert libros["A"].cantidad_disponible == 2

    def test_otro_estatus_solo_avanza(self) -> None:
        pedido = pedido_en("pendiente")
        libros = {"A": hacer_libro("A", 3)}

        cambiar_estatus(pedido, "pagado", libros)

        assert pedido.estatus == "pagado"
        assert libros["A"].cantidad_disponible == 3
