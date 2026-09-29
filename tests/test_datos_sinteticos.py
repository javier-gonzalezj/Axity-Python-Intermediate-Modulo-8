"""Pruebas del generador de datos sintéticos (datos_sinteticos.py)."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria import datos_sinteticos as ds
from libreria.excepciones import LibreriaError
from libreria.modelos import Autor, Libro


@pytest.fixture
def sesion_vacia() -> Iterator[Session]:
    """Base en memoria SIN catálogo: el generador pone sus propios libros."""
    motor = bd.crear_motor("sqlite://")
    bd.crear_esquema(motor)
    with Session(motor) as s:
        yield s
    motor.dispose()


def _libro(genero: str, precio: float) -> Libro:
    return Libro(
        isbn="X-1",
        titulo="Prueba",
        autor=Autor(nombre="Alguien", nacionalidad="Chilena"),
        genero=[genero],
        año_publicacion=2000,
        precio=precio,
        en_stock=True,
        cantidad_disponible=1,
        editorial="Anagrama",
    )


def _ventas(sesion: Session) -> dict[str, int]:
    consulta = select(bd.PedidoItemDB.isbn, func.sum(bd.PedidoItemDB.cantidad)).group_by(
        bd.PedidoItemDB.isbn
    )
    return {isbn: int(total) for isbn, total in sesion.execute(consulta)}


def test_popularidad_sigue_la_regla_escondida() -> None:
    assert ds.popularidad(_libro("Thriller", 150)) > ds.popularidad(_libro("Poesía", 600))
    # Mismo libro, más caro → menos popular
    assert ds.popularidad(_libro("Novela", 200)) > ds.popularidad(_libro("Novela", 400))


def test_generar_libros_crea_libros_validos_y_unicos() -> None:
    libros = ds.generar_libros(50, random.Random(1))
    assert len(libros) == 50
    assert len({libro.isbn for libro in libros}) == 50
    assert all(1 <= len(libro.genero) <= 3 for libro in libros)
    assert all(libro.en_stock == (libro.cantidad_disponible > 0) for libro in libros)


def test_poblar_genera_los_registros_pedidos(sesion_vacia: Session) -> None:
    resumen = ds.poblar(sesion_vacia, libros=40, pedidos=200, usuarios=10, semilla=7)

    assert resumen.libros == 40
    assert len(bd.listar_libros(sesion_vacia)) == 40
    assert len(bd.listar_usuarios(sesion_vacia)) == 10
    assert len(bd.listar_pedidos(sesion_vacia)) == 200
    assert 0 < resumen.unidades <= sum(_ventas(sesion_vacia).values())


def test_misma_semilla_mismos_datos() -> None:
    ventas = []
    for _ in range(2):
        motor = bd.crear_motor("sqlite://")
        bd.crear_esquema(motor)
        with Session(motor) as s:
            ds.poblar(s, libros=30, pedidos=150, usuarios=5, semilla=123)
            ventas.append(_ventas(s))
        motor.dispose()
    assert ventas[0] == ventas[1]


def test_poblar_rechaza_cantidades_invalidas(sesion_vacia: Session) -> None:
    with pytest.raises(LibreriaError):
        ds.poblar(sesion_vacia, libros=0)


def test_main_crea_la_base(tmp_path: Path) -> None:
    ruta = tmp_path / "sint.db"
    ds.main(["--bd", str(ruta), "--libros", "20", "--pedidos", "50", "--usuarios", "5"])
    assert ruta.exists()


def test_main_no_sobrescribe_sin_reemplazar(tmp_path: Path) -> None:
    ruta = tmp_path / "sint.db"
    ruta.write_text("no me borres")
    with pytest.raises(SystemExit):
        ds.main(["--bd", str(ruta)])
    assert ruta.read_text() == "no me borres"


def test_main_protege_la_base_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # data/libreria.db relativo a esta carpeta temporal
    with pytest.raises(SystemExit):
        ds.main(["--bd", "data/libreria.db", "--reemplazar"])
    assert not (tmp_path / "data" / "libreria.db").exists()
