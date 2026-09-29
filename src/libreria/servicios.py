"""Capa de servicio: casos de uso del catálogo.

ServicioCatalogo coordina las reglas de catalogo.py con la persistencia, sin
saber si el catálogo vive en un JSON, en SQLite o en memoria: solo conoce el
puerto RepositorioCatalogo. Tampoco hace print() ni input(); eso le toca a
main.py.
"""

import logging
from pathlib import Path

from libreria.catalogo import agregar_libro
from libreria.excepciones import CatalogoNoGuardadoError, LibreriaError
from libreria.intercambio import ResultadoImportacion, importar_csv
from libreria.modelos import Libreria, Libro
from libreria.puertos import RepositorioCatalogo

log = logging.getLogger(__name__)


class ServicioCatalogo:
    """Casos de uso del catálogo con una garantía: memoria y almacenamiento coinciden.

    Si una operación modifica el catálogo y el guardado falla, los cambios en
    memoria se deshacen y se lanza CatalogoNoGuardadoError.
    """

    def __init__(self, repo: RepositorioCatalogo) -> None:
        self._repo = repo
        self._data: Libreria = repo.cargar()

    @property
    def datos(self) -> Libreria:
        """Datos completos de la librería (nombre, dirección, libros...)."""
        return self._data

    @property
    def libros(self) -> list[Libro]:
        return self._data["libros"]

    def agregar(self, libro: Libro) -> None:
        """Agrega un libro y guarda. Lanza LibroInvalidoError si el ISBN ya existe."""
        respaldo = list(self.libros)
        agregar_libro(self._data, libro)
        self._guardar_o_revertir(respaldo)

    def importar_csv(self, ruta: str | Path) -> ResultadoImportacion:
        """Agrega los libros válidos de un CSV y guarda una sola vez.

        Las filas inválidas o repetidas no detienen la importación: quedan en
        `resultado.rechazados`. Si no se agregó ningún libro, no se guarda.
        """
        respaldo = list(self.libros)
        resultado = importar_csv(self._data, ruta)
        if resultado.agregados:
            self._guardar_o_revertir(respaldo)
        return resultado

    def _guardar_o_revertir(self, respaldo: list[Libro]) -> None:
        """Guarda el catálogo; si falla, restaura `respaldo` y avisa."""
        try:
            self._repo.guardar(self._data)
        except LibreriaError as e:
            self._data["libros"] = respaldo  # memoria vuelve a coincidir con el disco
            log.warning("No se pudo guardar; se revirtieron los cambios: %s", e)
            raise CatalogoNoGuardadoError(
                f"No se pudo guardar el catálogo, se descartaron los cambios: {e}"
            ) from e
