"""Pruebas del clasificador de best sellers (prediccion.py).

Entrenar tarda un par de segundos, así que el modelo se entrena UNA sola vez
para todo el archivo (fixture con scope="module") con datos sintéticos.
"""

from collections.abc import Iterator
from pathlib import Path

import joblib
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria import prediccion as pr
from libreria.datos_sinteticos import poblar
from libreria.excepciones import ArchivoNoEncontradoError, DatosInsuficientesError, LibreriaError
from libreria.modelos import Autor, Libro

CIEN_AÑOS = "978-607-07-1234-5"  # del catálogo de prueba: 12 ejemplares
RAYUELA = "978-84-9793-563-2"  # 5 ejemplares


@pytest.fixture(scope="module")
def datos_sinteticos() -> Iterator[pd.DataFrame]:
    motor = bd.crear_motor("sqlite://")
    bd.crear_esquema(motor)
    with Session(motor) as s:
        poblar(s, libros=300, pedidos=3000, usuarios=50, semilla=42)
        yield pr.cargar_datos(s)
    motor.dispose()


@pytest.fixture(scope="module")
def resultado(datos_sinteticos: pd.DataFrame) -> pr.ResultadoEntrenamiento:
    return pr.entrenar(datos_sinteticos, semilla=42)


def _libro(isbn: str = "N-1", **cambios: object) -> Libro:
    datos: dict[str, object] = {
        "isbn": isbn,
        "titulo": f"Libro {isbn}",
        "autor": Autor(nombre="Alguien", nacionalidad="Mexicana"),
        "genero": ["Thriller"],
        "año_publicacion": 2020,
        "precio": 150.0,
        "en_stock": True,
        "cantidad_disponible": 5,
        "editorial": "Planeta",
    }
    datos.update(cambios)
    return Libro.model_validate(datos)


# ── Etiqueta ─────────────────────────────────────────────────────────────────


def test_etiquetar_marca_el_20_por_ciento_superior() -> None:
    unidades = pd.Series([0, 0, 0, 1, 2, 3, 4, 5, 10, 20])
    # El percentil 80 de estos valores es 6: solo 10 y 20 lo superan
    assert pr.etiquetar(unidades).tolist() == [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]


def test_sin_ventas_nunca_es_best_seller() -> None:
    assert pr.etiquetar(pd.Series([0, 0, 0, 0])).sum() == 0


@pytest.mark.parametrize("percentil", [0, 1, 80])
def test_etiquetar_rechaza_percentil_invalido(percentil: float) -> None:
    with pytest.raises(ValueError, match="percentil"):
        pr.etiquetar(pd.Series([1, 2, 3]), percentil)


# ── Datos ────────────────────────────────────────────────────────────────────


def test_caracteristicas_sin_fuga_de_datos() -> None:
    """Nada que dependa de las ventas puede ser entrada del modelo."""
    assert "cantidad_disponible" not in pr.CARACTERISTICAS
    assert "en_stock" not in pr.CARACTERISTICAS
    assert "unidades" not in pr.CARACTERISTICAS
    assert "best_seller" not in pr.CARACTERISTICAS


def test_cargar_datos_cuenta_ventas_sin_cancelados(sesion: Session) -> None:
    ana = bd.crear_usuario(sesion, "Ana", "ana@mail.com")
    assert ana.id is not None
    bd.crear_pedido(sesion, ana.id, {CIEN_AÑOS: 3})
    cancelado = bd.crear_pedido(sesion, ana.id, {CIEN_AÑOS: 2, RAYUELA: 1})
    bd.cancelar_pedido(sesion, cancelado.id)

    df = pr.cargar_datos(sesion)

    assert df.loc[CIEN_AÑOS, "unidades"] == 3
    assert df.loc[RAYUELA, "unidades"] == 0  # solo estaba en el pedido cancelado
    assert df.loc[CIEN_AÑOS, "best_seller"] == 1
    assert len(df) == len(bd.listar_libros(sesion))  # también los que no se venden


def test_a_dataframe_usa_isbn_como_indice() -> None:
    df = pr.a_dataframe([_libro("A"), _libro("B", genero=["Poesía", "Ensayo"])])
    assert list(df.index) == ["A", "B"]
    assert df.loc["B", "generos"] == ["Poesía", "Ensayo"]


# ── Entrenamiento ────────────────────────────────────────────────────────────


def test_entrenar_aprende_la_tendencia(resultado: pr.ResultadoEntrenamiento) -> None:
    m = resultado.metricas
    assert m.roc_auc > 0.75  # mucho mejor que adivinar (0.5)
    assert m.recall > 0.5  # encuentra a la mayoría de los best sellers
    assert m.libros_entrenamiento + m.libros_prueba == 300
    assert "best seller" in resultado.reporte


def test_el_precio_es_de_lo_mas_importante(resultado: pr.ResultadoEntrenamiento) -> None:
    importancias = pr.importancias(resultado.modelo)
    assert importancias.sum() == pytest.approx(1.0)
    assert "numericas__precio" in pr.importancias(resultado.modelo, top=3).index


def test_entrenar_sin_ventas_falla_con_mensaje_claro(sesion: Session) -> None:
    df = pr.cargar_datos(sesion)  # catálogo de prueba: pocos libros y ningún pedido
    with pytest.raises(DatosInsuficientesError):
        pr.entrenar(df)


def test_entrenar_con_una_sola_clase_falla(datos_sinteticos: pd.DataFrame) -> None:
    df = datos_sinteticos.assign(best_seller=0)
    with pytest.raises(DatosInsuficientesError, match="best seller"):
        pr.entrenar(df)


# ── Predicción ───────────────────────────────────────────────────────────────


def test_predecir_da_probabilidades_por_isbn(resultado: pr.ResultadoEntrenamiento) -> None:
    libros = [_libro("A"), _libro("B", precio=700.0, genero=["Poesía"])]
    probabilidades = pr.predecir(resultado.modelo, libros)

    assert list(probabilidades.index) == ["A", "B"]
    assert probabilidades.between(0, 1).all()
    # Thriller barato de Planeta vs. poesía cara: la regla escondida favorece al primero
    assert probabilidades["A"] > probabilidades["B"]


def test_predecir_tolera_categorias_nunca_vistas(resultado: pr.ResultadoEntrenamiento) -> None:
    raro = _libro(genero=["Manga"], editorial="Editorial Nueva", autor=Autor(nombre="X"))
    assert 0 <= pr.predecir_libro(resultado.modelo, raro) <= 1


def test_generos_sin_distinguir_mayusculas(resultado: pr.ResultadoEntrenamiento) -> None:
    a = pr.predecir_libro(resultado.modelo, _libro(genero=["Realismo mágico"]))
    b = pr.predecir_libro(resultado.modelo, _libro(genero=["Realismo Mágico"]))
    assert a == b


def test_las_existencias_no_cambian_la_prediccion(resultado: pr.ResultadoEntrenamiento) -> None:
    con_stock = pr.predecir_libro(resultado.modelo, _libro(cantidad_disponible=30))
    agotado = pr.predecir_libro(resultado.modelo, _libro(cantidad_disponible=0, en_stock=False))
    assert con_stock == agotado


def test_predecir_lista_vacia(resultado: pr.ResultadoEntrenamiento) -> None:
    assert pr.predecir(resultado.modelo, []).empty


# ── Guardar y cargar ─────────────────────────────────────────────────────────


def test_guardar_y_cargar_da_las_mismas_predicciones(
    resultado: pr.ResultadoEntrenamiento, tmp_path: Path
) -> None:
    ruta = pr.guardar_modelo(resultado.modelo, tmp_path / "modelos" / "m.joblib")
    cargado = pr.cargar_modelo(ruta)
    libros = [_libro("A"), _libro("B", precio=500.0)]
    pd.testing.assert_series_equal(
        pr.predecir(cargado, libros), pr.predecir(resultado.modelo, libros)
    )


def test_cargar_modelo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ArchivoNoEncontradoError, match="entrenar"):
        pr.cargar_modelo(tmp_path / "no_existe.joblib")


def test_cargar_algo_que_no_es_modelo(tmp_path: Path) -> None:
    ruta = tmp_path / "raro.joblib"
    joblib.dump({"no": "soy un modelo"}, ruta)
    with pytest.raises(LibreriaError, match="no contiene"):
        pr.cargar_modelo(ruta)


# ── Línea de comandos ────────────────────────────────────────────────────────


def test_main_entrena_y_predice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bd_sintetica = tmp_path / "sint.db"
    motor = bd.crear_motor(f"sqlite:///{bd_sintetica}")
    bd.crear_esquema(motor)
    with Session(motor) as s:
        poblar(s, libros=80, pedidos=600, usuarios=10, semilla=1)
    motor.dispose()
    modelo = tmp_path / "m.joblib"

    pr.main(["entrenar", "--bd", str(bd_sintetica), "--modelo", str(modelo)])
    assert modelo.exists()
    assert "ROC AUC" in capsys.readouterr().out

    pr.main(["predecir", "--bd", str(bd_sintetica), "--modelo", str(modelo)])
    assert "Libro sintético" in capsys.readouterr().out


def test_main_sin_modelo_muestra_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bd_vacia = tmp_path / "vacia.db"
    bd.crear_esquema(bd.crear_motor(f"sqlite:///{bd_vacia}"))
    with pytest.raises(SystemExit):
        pr.main(["predecir", "--bd", str(bd_vacia), "--modelo", str(tmp_path / "x.joblib")])
    assert "No existe el modelo" in capsys.readouterr().out
