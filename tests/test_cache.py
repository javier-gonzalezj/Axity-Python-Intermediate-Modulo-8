"""Pruebas del decorador @cache_temporal de utilidades.py.

El paso del tiempo se simula con un reloj falso (RelojFalso, que solo avanza
cuando la prueba lo indica), así las pruebas de caducidad no tienen que esperar de verdad.
"""

import pytest

from libreria.utilidades import cache_temporal


class RelojFalso:
    """Reloj que solo avanza cuando la prueba lo pide."""

    def __init__(self) -> None:
        self.ahora = 0.0

    def __call__(self) -> float:
        return self.ahora


def test_reutiliza_el_resultado() -> None:
    llamadas: list[int] = []

    @cache_temporal()
    def doble(x: int) -> int:
        llamadas.append(x)
        return x * 2

    assert doble(3) == 6
    assert doble(3) == 6
    assert llamadas == [3]
    assert (doble.aciertos, doble.fallos) == (1, 1)


def test_argumentos_distintos_son_entradas_distintas() -> None:
    llamadas: list[tuple[int, int]] = []

    @cache_temporal()
    def multiplicar(a: int, b: int = 2) -> int:
        llamadas.append((a, b))
        return a * b

    assert multiplicar(3) == 6
    assert multiplicar(3, b=5) == 15
    assert multiplicar(3, b=5) == 15
    assert len(llamadas) == 2


def test_caduca_despues_de_los_segundos_indicados() -> None:
    reloj = RelojFalso()
    llamadas: list[str] = []

    @cache_temporal(segundos=10, reloj=reloj)
    def saludo(nombre: str) -> str:
        llamadas.append(nombre)
        return f"Hola, {nombre}"

    saludo("Ana")
    reloj.ahora = 9.9
    saludo("Ana")
    assert len(llamadas) == 1  # todavía vigente

    reloj.ahora = 10.0
    saludo("Ana")
    assert len(llamadas) == 2  # caducó y se volvió a calcular


def test_descarta_el_que_lleva_mas_tiempo_sin_usarse() -> None:
    llamadas: list[int] = []

    @cache_temporal(max_elementos=2)
    def identidad(x: int) -> int:
        llamadas.append(x)
        return x

    identidad(1)
    identidad(2)
    identidad(1)  # 1 se usa: ahora 2 es el más viejo
    identidad(3)  # no cabe: sale 2
    llamadas.clear()

    identidad(1)
    identidad(3)
    assert llamadas == []  # siguen guardados
    identidad(2)
    assert llamadas == [2]  # fue descartado


def test_no_guarda_excepciones() -> None:
    intentos: list[int] = []

    @cache_temporal()
    def falla_la_primera_vez(x: int) -> int:
        intentos.append(x)
        if len(intentos) == 1:
            raise ValueError("fallo pasajero")
        return x

    with pytest.raises(ValueError):
        falla_la_primera_vez(1)
    assert falla_la_primera_vez(1) == 1
    assert len(intentos) == 2


def test_limpiar() -> None:
    llamadas: list[int] = []

    @cache_temporal()
    def identidad(x: int) -> int:
        llamadas.append(x)
        return x

    identidad(1)
    identidad.limpiar()
    identidad(1)
    assert llamadas == [1, 1]
    assert (identidad.aciertos, identidad.fallos) == (0, 1)


def test_conserva_nombre_y_documentacion() -> None:
    @cache_temporal()
    def funcion_documentada() -> None:
        """Mi documentación."""

    # update_wrapper copia los atributos al objeto; vars() los muestra
    assert vars(funcion_documentada)["__name__"] == "funcion_documentada"
    assert funcion_documentada.__doc__ == "Mi documentación."


@pytest.mark.parametrize(("segundos", "max_elementos"), [(0, 10), (-1, 10), (10, 0)])
def test_parametros_invalidos(segundos: float, max_elementos: int) -> None:
    with pytest.raises(ValueError):
        cache_temporal(segundos=segundos, max_elementos=max_elementos)
