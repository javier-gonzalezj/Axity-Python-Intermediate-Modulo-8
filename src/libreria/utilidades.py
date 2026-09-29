import asyncio
import functools
import logging
import os
import random
import tempfile
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Coroutine, Generator, Hashable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)


def _espera_con_jitter(
    intento: int, espera_inicial: float, factor: float, espera_maxima: float
) -> float:
    """Backoff exponencial con jitter: un valor al azar entre 0 y la espera calculada."""
    espera = min(espera_inicial * factor ** (intento - 1), espera_maxima)
    return random.uniform(0, espera)


def _avisar_reintento(
    nombre: str, error: BaseException, intento: int, intentos: int, espera: float
) -> None:
    log.warning(
        "%s falló (%s). Intento %d/%d, reintentando en %.2fs",
        nombre,
        error,
        intento,
        intentos,
        espera,
    )


def reintentar[**P, R](
    intentos: int = 3,
    espera_inicial: float = 0.5,
    factor: float = 2.0,
    espera_maxima: float = 10.0,
    excepciones: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Reintenta la función decorada con backoff exponencial y jitter.

    Solo reintenta si la excepción es de alguno de los tipos en `excepciones`.
    Tras el último intento fallido, la excepción se propaga sin cambios.
    """
    if intentos < 1:
        raise ValueError("intentos debe ser al menos 1")

    def decorador(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def envoltura(*args: P.args, **kwargs: P.kwargs) -> R:
            for intento in range(1, intentos + 1):
                try:
                    return func(*args, **kwargs)
                except excepciones as e:
                    if intento == intentos:
                        log.error("%s falló tras %d intentos", func.__name__, intentos)
                        raise
                    espera = _espera_con_jitter(intento, espera_inicial, factor, espera_maxima)
                    _avisar_reintento(func.__name__, e, intento, intentos, espera)
                    time.sleep(espera)
            # Nunca se llega aquí: el último intento siempre retorna o relanza.
            # Este raise existe para que mypy no reporte "Missing return statement".
            raise AssertionError("reintentar: el bucle terminó sin retornar")

        return envoltura

    return decorador


def reintentar_async[**P, R](
    intentos: int = 3,
    espera_inicial: float = 0.5,
    factor: float = 2.0,
    espera_maxima: float = 10.0,
    excepciones: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Coroutine[Any, Any, R]]]:
    """Versión de @reintentar para funciones `async def`.

    Funciona igual, con una diferencia importante: entre intentos espera con
    `await asyncio.sleep()` en lugar de `time.sleep()`. time.sleep() congelaría
    el event loop completo, y con él todas las demás descargas en curso;
    asyncio.sleep() solo pausa esta corrutina y deja avanzar a las demás.
    """
    if intentos < 1:
        raise ValueError("intentos debe ser al menos 1")

    def decorador(func: Callable[P, Awaitable[R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @functools.wraps(func)
        async def envoltura(*args: P.args, **kwargs: P.kwargs) -> R:
            for intento in range(1, intentos + 1):
                try:
                    return await func(*args, **kwargs)
                except excepciones as e:
                    if intento == intentos:
                        log.error("%s falló tras %d intentos", func.__name__, intentos)
                        raise
                    espera = _espera_con_jitter(intento, espera_inicial, factor, espera_maxima)
                    _avisar_reintento(func.__name__, e, intento, intentos, espera)
                    await asyncio.sleep(espera)
            raise AssertionError("reintentar_async: el bucle terminó sin retornar")

        return envoltura

    return decorador


class _FuncionConCache[**P, R]:
    """Lo que devuelve @cache_temporal.

    Se llama igual que la función original y además tiene limpiar() y los
    contadores `aciertos` y `fallos`. Es una clase y no una función envoltura
    porque así mypy sabe que existen esos atributos extra.
    """

    def __init__(
        self,
        func: Callable[P, R],
        segundos: float,
        max_elementos: int,
        reloj: Callable[[], float],
    ) -> None:
        functools.update_wrapper(self, func)  # copia __name__, __doc__, ...
        self._func = func
        self._segundos = segundos
        self._max = max_elementos
        self._reloj = reloj
        # clave -> (momento en que se guardó, valor). El orden del OrderedDict es
        # el orden de uso: al principio el que lleva más tiempo sin usarse.
        self._guardado: OrderedDict[Hashable, tuple[float, R]] = OrderedDict()
        self.aciertos = 0
        self.fallos = 0

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        # Todos los argumentos deben ser hasheables (str, int, tuple, None...)
        clave = (args, tuple(sorted(kwargs.items())))
        ahora = self._reloj()

        if clave in self._guardado:
            momento, valor = self._guardado[clave]
            if ahora - momento < self._segundos:
                self._guardado.move_to_end(clave)  # recién usado: al final
                self.aciertos += 1
                log.debug("Caché: acierto en %s%r", self._func.__name__, args)
                return valor
            del self._guardado[clave]  # caducó

        self.fallos += 1
        valor = self._func(*args, **kwargs)  # si lanza excepción, no se guarda nada
        self._guardado[clave] = (ahora, valor)
        if len(self._guardado) > self._max:
            self._guardado.popitem(last=False)  # saca el que lleva más tiempo sin usarse
        return valor

    def limpiar(self) -> None:
        """Olvida todos los resultados guardados y reinicia los contadores."""
        self._guardado.clear()
        self.aciertos = self.fallos = 0


def cache_temporal[**P, R](
    segundos: float = 300,
    max_elementos: int = 128,
    reloj: Callable[[], float] = time.monotonic,
) -> Callable[[Callable[P, R]], _FuncionConCache[P, R]]:
    """Guarda el resultado de la función por `segundos`, según sus argumentos.

    Cuando hay más de `max_elementos`, descarta el que lleva más tiempo sin
    usarse (LRU). Las excepciones no se guardan: el siguiente intento vuelve a
    llamar a la función. `reloj` solo se cambia en las pruebas, para simular el
    paso del tiempo sin esperar.

    No es seguro para usarse desde varios hilos a la vez (p. ej. en la API);
    para eso habría que proteger el diccionario con un threading.Lock.
    """
    if segundos <= 0 or max_elementos < 1:
        raise ValueError("segundos y max_elementos deben ser positivos")

    def decorador(func: Callable[P, R]) -> _FuncionConCache[P, R]:
        return _FuncionConCache(func, segundos, max_elementos, reloj)

    return decorador


@reintentar(intentos=3, espera_inicial=0.2, excepciones=(PermissionError,))
def _reemplazar(origen: str | Path, destino: str | Path) -> None:
    """Reemplaza `destino` por `origen`.

    En Windows, os.replace puede fallar con PermissionError si otro proceso
    (antivirus, editor, indexador) tiene el archivo abierto un instante.
    """
    os.replace(origen, destino)


@contextmanager
def cronometro(etiqueta: str = "Bloque") -> Generator[None]:
    """Context manager de temporizacion: Imprime cuanto tiempo
    tardo en ejecutarse el bloque with aunque falle.
    """
    inicio = time.perf_counter()
    try:
        yield
    finally:
        duracion = time.perf_counter() - inicio
        log.debug("%s: %.4fs", etiqueta, duracion)


@contextmanager
def escritura_atomica(ruta: str | Path, encoding: str = "utf-8") -> Generator[TextIO]:
    """Escribe en un archivo temporal y reemplaza `ruta` solo si todo salió bien.

    Si ocurre un error durante la escritura, el archivo original queda intacto.
    """
    ruta = Path(ruta)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            yield f
        _reemplazar(tmp, ruta)
    except BaseException:
        os.remove(tmp)
        raise
