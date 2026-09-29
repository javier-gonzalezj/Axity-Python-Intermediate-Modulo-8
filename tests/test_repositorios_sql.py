"""Pruebas de integración de los adaptadores SQL (repositorios_sql.py).

Usan la fixture `sesion` de conftest.py: SQLite en memoria con el catálogo de
prueba ya importado. Revisan dos cosas:

- que cada repositorio traduzca bien entre filas y objetos del dominio, y
- que la unidad de trabajo guarde todo junto o nada.

La variable `uow` está anotada con el PUERTO (UnidadDeTrabajo): las pruebas
solo usan lo que promete el contrato, igual que lo harán los casos de uso.
"""

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria import pedidos
from libreria.excepciones import (
    LibroInvalidoError,
    LibroNoEncontradoError,
    PedidoNoEncontradoError,
)
from libreria.modelos import Autor, Libro
from libreria.pedidos import Pedido, PedidoItem
from libreria.puertos import UnidadDeTrabajo
from libreria.repositorios_sql import UnidadDeTrabajoSQL

# Catálogo fijo, ver conftest.py
CIEN_AÑOS = "978-607-07-1234-5"  # 12 ejemplares, $349.90
RAYUELA = "978-84-9793-563-2"  # 5 ejemplares, $399.50
HOY = datetime(2026, 9, 29, 12, 0)


@pytest.fixture
def uow(sesion: Session) -> UnidadDeTrabajo:
    return UnidadDeTrabajoSQL(sesion)


@pytest.fixture
def ana_id(sesion: Session) -> int:
    ana = bd.crear_usuario(sesion, "Ana", "ana@mail.com")
    assert ana.id is not None
    return ana.id


def libro_nuevo(isbn: str = "978-607-16-0001-1") -> Libro:
    return Libro(
        isbn=isbn,
        titulo="Pedro Páramo",
        autor=Autor(nombre="Juan Rulfo", nacionalidad="Mexicana"),
        genero=["Novela", "Realismo mágico"],
        año_publicacion=1955,
        precio=189.0,
        en_stock=True,
        cantidad_disponible=10,
        editorial="Fondo de Cultura Económica",
    )


def pedido_nuevo(usuario_id: int) -> Pedido:
    item = PedidoItem(isbn=RAYUELA, titulo="Rayuela", cantidad=2, precio_unitario=399.50)
    return Pedido(usuario_id=usuario_id, fecha=HOY, items=[item])


# ── Libros ───────────────────────────────────────────────────────────────────


class TestLibrosSQL:
    def test_obtener_un_libro_existente(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            libro = uow.libros.obtener(CIEN_AÑOS)

        assert libro is not None
        assert libro.cantidad_disponible == 12

    def test_obtener_inexistente_devuelve_none(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            assert uow.libros.obtener("no-existe") is None

    def test_obtener_varios_omite_los_que_no_existen(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            libros = uow.libros.obtener_varios([CIEN_AÑOS, RAYUELA, "no-existe"])

        assert set(libros) == {CIEN_AÑOS, RAYUELA}
        assert libros[RAYUELA].precio == pytest.approx(399.50)

    def test_obtener_varios_sin_isbn_devuelve_vacio(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            assert uow.libros.obtener_varios([]) == {}

    def test_agregar_y_confirmar_guarda_el_libro_completo(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            uow.libros.agregar(libro_nuevo())
            uow.confirmar()

        with uow:
            assert uow.libros.obtener("978-607-16-0001-1") == libro_nuevo()  # ida y vuelta

    def test_agregar_un_isbn_repetido_se_rechaza(self, uow: UnidadDeTrabajo) -> None:
        with uow, pytest.raises(LibroInvalidoError, match="Ya existe"):
            uow.libros.agregar(libro_nuevo(isbn=CIEN_AÑOS))

    def test_guardar_actualiza_el_inventario(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            libro = uow.libros.obtener(RAYUELA)
            assert libro is not None
            libro.retirar(5)  # regla del dominio
            uow.libros.guardar(libro)
            uow.confirmar()

        with uow:
            guardado = uow.libros.obtener(RAYUELA)
        assert guardado is not None
        assert guardado.cantidad_disponible == 0
        assert not guardado.en_stock  # se deriva al leer la fila

    def test_guardar_un_libro_inexistente_se_rechaza(self, uow: UnidadDeTrabajo) -> None:
        with uow, pytest.raises(LibroNoEncontradoError):
            uow.libros.guardar(libro_nuevo())


# ── Pedidos ──────────────────────────────────────────────────────────────────


class TestPedidosSQL:
    def test_agregar_le_asigna_id_al_pedido(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        pedido = pedido_nuevo(ana_id)

        with uow:
            uow.pedidos.agregar(pedido)
            uow.confirmar()

        assert pedido.id is not None

    def test_un_pedido_guardado_se_lee_igual(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        pedido = pedido_nuevo(ana_id)
        with uow:
            uow.pedidos.agregar(pedido)
            uow.confirmar()

        assert pedido.id is not None
        with uow:
            leido = uow.pedidos.obtener(pedido.id)
        assert leido == pedido  # mismo usuario, fecha, estatus, líneas y precios

    def test_guardar_cambia_el_estatus(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        pedido = pedido_nuevo(ana_id)
        with uow:
            uow.pedidos.agregar(pedido)
            uow.confirmar()

        assert pedido.id is not None
        with uow:
            pedido.avanzar_a("pagado")  # regla del dominio
            uow.pedidos.guardar(pedido)
            uow.confirmar()

        with uow:
            leido = uow.pedidos.obtener(pedido.id)
        assert leido is not None and leido.estatus == "pagado"

    def test_obtener_inexistente_devuelve_none(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            assert uow.pedidos.obtener(999) is None

    def test_guardar_un_pedido_inexistente_se_rechaza(
        self, uow: UnidadDeTrabajo, ana_id: int
    ) -> None:
        pedido = pedido_nuevo(ana_id)
        pedido.id = 999

        with uow, pytest.raises(PedidoNoEncontradoError):
            uow.pedidos.guardar(pedido)


# ── Usuarios ─────────────────────────────────────────────────────────────────


class TestUsuariosSQL:
    def test_obtener_un_usuario(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        with uow:
            usuario = uow.usuarios.obtener(ana_id)

        assert usuario is not None
        assert usuario.email == "ana@mail.com"

    def test_obtener_inexistente_devuelve_none(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            assert uow.usuarios.obtener(999) is None


# ── Unidad de trabajo ────────────────────────────────────────────────────────


class TestUnidadDeTrabajo:
    def test_sin_confirmar_los_cambios_se_descartan(self, uow: UnidadDeTrabajo) -> None:
        with uow:
            uow.libros.agregar(libro_nuevo())
            # se olvida confirmar()

        with uow:
            assert uow.libros.obtener("978-607-16-0001-1") is None

    def test_si_hay_una_excepcion_se_revierte_y_se_propaga(self, uow: UnidadDeTrabajo) -> None:
        with pytest.raises(RuntimeError, match="algo falló"), uow:
            uow.libros.agregar(libro_nuevo())
            raise RuntimeError("algo falló")

        with uow:
            assert uow.libros.obtener("978-607-16-0001-1") is None

    def test_varios_cambios_se_guardan_juntos(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        # Así se verá el caso de uso CrearPedido del paso 3: dominio + puertos
        with uow:
            libros = uow.libros.obtener_varios([CIEN_AÑOS, RAYUELA])
            pedido = pedidos.crear_pedido(ana_id, {CIEN_AÑOS: 2, RAYUELA: 1}, libros, fecha=HOY)
            for libro in libros.values():
                uow.libros.guardar(libro)
            uow.pedidos.agregar(pedido)
            uow.confirmar()

        assert pedido.id is not None
        with uow:
            assert uow.pedidos.obtener(pedido.id) is not None
            stock = uow.libros.obtener_varios([CIEN_AÑOS, RAYUELA])
        assert stock[CIEN_AÑOS].cantidad_disponible == 10
        assert stock[RAYUELA].cantidad_disponible == 4

    def test_si_falla_a_la_mitad_no_se_guarda_nada(self, uow: UnidadDeTrabajo, ana_id: int) -> None:
        with pytest.raises(RuntimeError), uow:
            libros = uow.libros.obtener_varios([RAYUELA])
            pedido = pedidos.crear_pedido(ana_id, {RAYUELA: 2}, libros, fecha=HOY)
            uow.libros.guardar(libros[RAYUELA])  # stock descontado...
            uow.pedidos.agregar(pedido)  # ...y pedido registrado (con id)...
            raise RuntimeError("se cayó antes de confirmar")

        with uow:
            libro = uow.libros.obtener(RAYUELA)
            assert libro is not None and libro.cantidad_disponible == 5  # ...pero nada quedó
            assert pedido.id is not None and uow.pedidos.obtener(pedido.id) is None
