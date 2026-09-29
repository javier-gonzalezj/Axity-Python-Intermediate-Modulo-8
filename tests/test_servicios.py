"""Pruebas de ServicioCatalogo usando adaptadores falsos del puerto RepositorioCatalogo.

Casi ninguna prueba toca el disco: el servicio recibe un repositorio en memoria
(o uno que falla a propósito). Eso es justo lo que permite depender de un puerto.
Al final hay una prueba de integración con el adaptador real, CatalogoJSON.
"""

import shutil
from pathlib import Path

import pytest

from libreria.almacenamiento import CatalogoJSON
from libreria.excepciones import (
    ArchivoNoEncontradoError,
    CatalogoNoGuardadoError,
    LibroInvalidoError,
    PermisoArchivoError,
)
from libreria.modelos import Autor, Libreria, Libro
from libreria.puertos import RepositorioCatalogo
from libreria.servicios import ServicioCatalogo

RAIZ = Path(__file__).parent.parent
CATALOGO = RAIZ / "data" / "libreria.json"

ENCABEZADO_CSV = (
    "isbn,titulo,autor,nacionalidad_autor,genero,año_publicacion,precio,"
    "cantidad_disponible,editorial\n"
)


# ── Adaptadores falsos ───────────────────────────────────────────────────────
# No heredan de RepositorioCatalogo: cumplen el puerto por tener cargar() y guardar().


class RepositorioEnMemoria:
    """Guarda en una variable y cuenta cuántas veces se llamó a guardar()."""

    def __init__(self, data: Libreria) -> None:
        self._data = data
        self.guardados = 0
        self.isbns_guardados: list[str] = []

    def cargar(self) -> Libreria:
        return self._data

    def guardar(self, data: Libreria) -> None:
        self.guardados += 1
        self.isbns_guardados = [libro.isbn for libro in data["libros"]]


class RepositorioQueFalla(RepositorioEnMemoria):
    """Carga bien, pero guardar() siempre falla como si no hubiera permisos."""

    def guardar(self, data: Libreria) -> None:
        self.guardados += 1
        raise PermisoArchivoError("Sin permisos para escribir el archivo")


# ── Datos de prueba ──────────────────────────────────────────────────────────


def hacer_libro(isbn: str, titulo: str = "Libro de prueba") -> Libro:
    return Libro(
        isbn=isbn,
        titulo=titulo,
        autor=Autor(nombre="Autora de Prueba", nacionalidad="Mexicana"),
        genero=["Novela"],
        año_publicacion=2000,
        precio=100.0,
        en_stock=True,
        cantidad_disponible=3,
        editorial="Editorial X",
    )


@pytest.fixture
def libreria() -> Libreria:
    """Catálogo pequeño con dos libros, armado a mano (sin leer archivos)."""
    return {
        "nombre": "Librería de prueba",
        "direccion": {"calle": "Calle 1", "colonia": "Centro", "ciudad": "CDMX", "cp": "06000"},
        "telefono": "555-0000",
        "horario": "9 a 18",
        "libros": [hacer_libro("111"), hacer_libro("222")],
    }


def isbns(servicio: ServicioCatalogo) -> list[str]:
    return [libro.isbn for libro in servicio.libros]


def escribir_csv(carpeta: Path, *filas: str) -> Path:
    ruta = carpeta / "libros.csv"
    ruta.write_text(ENCABEZADO_CSV + "\n".join(filas) + "\n", encoding="utf-8")
    return ruta


# ── Agregar un libro ─────────────────────────────────────────────────────────


class TestAgregar:
    def test_carga_el_catalogo_al_crearse(self, libreria: Libreria) -> None:
        servicio = ServicioCatalogo(RepositorioEnMemoria(libreria))
        assert isbns(servicio) == ["111", "222"]
        assert servicio.datos["nombre"] == "Librería de prueba"

    def test_agrega_y_guarda_una_vez(self, libreria: Libreria) -> None:
        repo = RepositorioEnMemoria(libreria)
        servicio = ServicioCatalogo(repo)

        servicio.agregar(hacer_libro("333"))

        assert isbns(servicio) == ["111", "222", "333"]
        assert repo.guardados == 1
        assert repo.isbns_guardados == ["111", "222", "333"]

    def test_isbn_repetido_no_agrega_ni_guarda(self, libreria: Libreria) -> None:
        repo = RepositorioEnMemoria(libreria)
        servicio = ServicioCatalogo(repo)

        with pytest.raises(LibroInvalidoError, match="Ya existe"):
            servicio.agregar(hacer_libro("111"))

        assert isbns(servicio) == ["111", "222"]
        assert repo.guardados == 0

    def test_si_falla_el_guardado_se_revierte(self, libreria: Libreria) -> None:
        repo = RepositorioQueFalla(libreria)
        servicio = ServicioCatalogo(repo)

        with pytest.raises(CatalogoNoGuardadoError, match="se descartaron los cambios"):
            servicio.agregar(hacer_libro("333"))

        assert repo.guardados == 1  # sí intentó guardar...
        assert isbns(servicio) == ["111", "222"]  # ...y al fallar dejó todo como estaba

    def test_el_error_original_queda_encadenado(self, libreria: Libreria) -> None:
        servicio = ServicioCatalogo(RepositorioQueFalla(libreria))

        with pytest.raises(CatalogoNoGuardadoError) as info:
            servicio.agregar(hacer_libro("333"))

        # `raise ... from e` conserva la causa: útil en el log
        assert isinstance(info.value.__cause__, PermisoArchivoError)


# ── Importar CSV ─────────────────────────────────────────────────────────────


class TestImportarCSV:
    def test_agrega_validos_rechaza_repetidos_y_guarda_una_vez(
        self, libreria: Libreria, tmp_path: Path
    ) -> None:
        ruta = escribir_csv(
            tmp_path,
            "333,Nuevo uno,Ana,Mexicana,Novela,1990,150.00,2,Ed A",
            "444,Nuevo dos,Beto,Chilena,Ensayo,2001,200.00,0,Ed B",
            "111,Repetido,Carla,Peruana,Novela,1980,99.00,1,Ed C",
        )
        repo = RepositorioEnMemoria(libreria)
        servicio = ServicioCatalogo(repo)

        resultado = servicio.importar_csv(ruta)

        assert [libro.isbn for libro in resultado.agregados] == ["333", "444"]
        assert len(resultado.rechazados) == 1
        assert isbns(servicio) == ["111", "222", "333", "444"]
        assert repo.guardados == 1  # uno solo, no uno por libro

    def test_sin_libros_nuevos_no_guarda(self, libreria: Libreria, tmp_path: Path) -> None:
        ruta = escribir_csv(tmp_path, "111,Repetido,Carla,Peruana,Novela,1980,99.00,1,Ed C")
        repo = RepositorioEnMemoria(libreria)
        servicio = ServicioCatalogo(repo)

        resultado = servicio.importar_csv(ruta)

        assert resultado.agregados == []
        assert repo.guardados == 0

    def test_si_falla_el_guardado_se_revierte(self, libreria: Libreria, tmp_path: Path) -> None:
        ruta = escribir_csv(tmp_path, "333,Nuevo uno,Ana,Mexicana,Novela,1990,150.00,2,Ed A")
        servicio = ServicioCatalogo(RepositorioQueFalla(libreria))

        with pytest.raises(CatalogoNoGuardadoError):
            servicio.importar_csv(ruta)

        assert isbns(servicio) == ["111", "222"]

    def test_archivo_inexistente_no_modifica_nada(self, libreria: Libreria, tmp_path: Path) -> None:
        repo = RepositorioEnMemoria(libreria)
        servicio = ServicioCatalogo(repo)

        with pytest.raises(ArchivoNoEncontradoError):
            servicio.importar_csv(tmp_path / "no_existe.csv")

        assert isbns(servicio) == ["111", "222"]
        assert repo.guardados == 0


# ── Integración con el adaptador real ────────────────────────────────────────


class TestCatalogoJSON:
    def test_lo_agregado_persiste_en_el_archivo(self, tmp_path: Path) -> None:
        copia = tmp_path / "libreria.json"
        shutil.copy(CATALOGO, copia)  # nunca se toca el archivo real del proyecto

        # Anotar la variable con el puerto hace que mypy verifique que
        # CatalogoJSON lo cumple, aunque no herede de él.
        repo: RepositorioCatalogo = CatalogoJSON(copia)
        servicio = ServicioCatalogo(repo)
        total_antes = len(servicio.libros)
        servicio.agregar(hacer_libro("999-PRUEBA"))

        # Un servicio nuevo relee el archivo desde cero
        recargado = ServicioCatalogo(CatalogoJSON(copia))
        assert len(recargado.libros) == total_antes + 1
        assert "999-PRUEBA" in isbns(recargado)

    def test_cargar_archivo_inexistente_lanza_error_de_libreria(self, tmp_path: Path) -> None:
        # Parte del contrato del puerto: errores de LibreriaError, no FileNotFoundError
        with pytest.raises(ArchivoNoEncontradoError):
            ServicioCatalogo(CatalogoJSON(tmp_path / "no_existe.json"))
