class LibreriaError(Exception):
    """Excepción base para errores del proyecto libreria."""


class ArchivoNoEncontradoError(LibreriaError):
    """Se lanza cuando el archivo JSON de la librería no existe."""


class ArchivoJSONInvalidoError(LibreriaError):
    """Se lanza cuando el archivo existe pero su contenido no es JSON válido."""


class ArchivoCSVInvalidoError(LibreriaError):
    """Se lanza cuando un archivo CSV no tiene las columnas esperadas o está dañado."""


class PermisoArchivoError(LibreriaError):
    """Se lanza cuando no hay permisos para leer o escribir el archivo."""


class CodificacionArchivoError(LibreriaError):
    """Se lanza cuando el archivo no está codificado en UTF-8 (o similar)."""


class LibroInvalidoError(LibreriaError):
    """Se lanza cuando un diccionario de libro no cumple con la estructura esperada."""


class ServicioExternoError(LibreriaError):
    """Se lanza cuando no se puede consultar un servicio de internet (p. ej. Open Library)."""


class LibroNoEncontradoError(LibroInvalidoError):
    """Se lanza cuando el libro indicado no existe."""


class UsuarioNoEncontradoError(LibreriaError):
    """Se lanza cuando el usuario indicado no existe."""


class PedidoNoEncontradoError(LibreriaError):
    """Se lanza cuando el pedido indicado no existe."""


class RegistroEnUsoError(LibreriaError):
    """Se lanza al borrar un registro del que otros dependen (p. ej. un libro con ventas)."""


class PersistenciaError(LibreriaError):
    """Se lanza cuando la base de datos no pudo completar una operación."""


class CatalogoNoGuardadoError(LibreriaError):
    """Se lanza cuando no se pudo guardar el catálogo y se descartaron los cambios."""


class DatosInsuficientesError(LibreriaError):
    """Se lanza cuando no hay suficientes libros o ventas para entrenar un modelo."""


# ── Reglas de pedidos (dominio, ver pedidos.py) ─────────────────────────────


class PedidoInvalidoError(LibreriaError):
    """Se lanza cuando un pedido no tiene libros o pide una cantidad no positiva."""


class StockInsuficienteError(LibreriaError):
    """Se lanza cuando no hay suficientes ejemplares para surtir un pedido."""


class TransicionEstatusError(LibreriaError):
    """Se lanza cuando un pedido no puede pasar de su estatus actual al solicitado."""
