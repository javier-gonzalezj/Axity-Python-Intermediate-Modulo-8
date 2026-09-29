"""Clasificador de best sellers: ¿este libro se venderá mucho?

Flujo completo de machine learning con pandas y scikit-learn:

    1. cargar_datos()   libros + unidades vendidas → DataFrame con la etiqueta
    2. entrenar()       separa entrenamiento/prueba, entrena y mide el modelo
    3. guardar_modelo() lo guarda en disco con joblib
    4. predecir()       probabilidad de ser best seller para libros nuevos

La etiqueta ("best seller") es una DEFINICIÓN nuestra: un libro es best seller
si está en el 20% superior de unidades vendidas (sin contar pedidos
cancelados). Se puede cambiar con `percentil`.

Las características son solo datos que se conocen ANTES de vender el libro
(precio, año, géneros, editorial, nacionalidad del autor). Nunca se usa
`cantidad_disponible` ni nada calculado de las ventas: eso sería "fuga de
datos" (el modelo vería la respuesta y parecería perfecto sin servir de nada).

Uso:
    poetry run python -m libreria.prediccion entrenar --bd data/sintetico.db
    poetry run python -m libreria.prediccion predecir --bd data/libreria.db
"""

import argparse
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import joblib
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.basedatos import PedidoDB, PedidoItemDB
from libreria.excepciones import ArchivoNoEncontradoError, DatosInsuficientesError, LibreriaError
from libreria.modelos import Libro

log = logging.getLogger(__name__)

RUTA_MODELO: Final = Path("data/modelo_best_seller.joblib")

# Columnas que el modelo usa como entrada (X). La etiqueta (y) es "best_seller".
CATEGORICAS: Final = ["editorial", "autor_nacionalidad"]
NUMERICAS: Final = ["precio", "año_publicacion"]
GENEROS: Final = "generos"
CARACTERISTICAS: Final = [*NUMERICAS, *CATEGORICAS, GENEROS]

# Mínimos para que entrenar tenga sentido (con menos, las métricas son ruido)
MIN_LIBROS: Final = 20
MIN_POR_CLASE: Final = 4


@dataclass(frozen=True)
class Metricas:
    """Qué tan bien le fue al modelo con los libros de prueba (que no vio al entrenar)."""

    exactitud: float  # % de aciertos en total
    exactitud_base: float  # % de aciertos diciendo SIEMPRE "no es best seller"
    precision: float  # de los que predijo best seller, cuántos lo eran
    recall: float  # de los best sellers reales, cuántos encontró
    f1: float  # balance entre precision y recall
    roc_auc: float  # 0.5 = adivinar al azar, 1.0 = ordena perfecto
    libros_entrenamiento: int
    libros_prueba: int


@dataclass(frozen=True)
class ResultadoEntrenamiento:
    modelo: Pipeline
    metricas: Metricas
    reporte: str  # classification_report de scikit-learn, listo para imprimir


# ── 1. Datos ─────────────────────────────────────────────────────────────────


def a_dataframe(libros: Iterable[Libro]) -> pd.DataFrame:
    """Convierte libros en la tabla de características que entiende el modelo.

    Se usa tanto al entrenar como al predecir: así un libro nuevo siempre se
    transforma exactamente igual que los de entrenamiento.
    """
    filas = [
        {
            "isbn": libro.isbn,
            "precio": libro.precio,
            "año_publicacion": libro.año_publicacion,
            "editorial": libro.editorial,
            "autor_nacionalidad": libro.autor.nacionalidad,
            "generos": list(libro.genero),
        }
        for libro in libros
    ]
    return pd.DataFrame(filas, columns=["isbn", *CARACTERISTICAS]).set_index("isbn")


def unidades_vendidas(sesion: Session) -> pd.Series:
    """Unidades vendidas por ISBN, sin contar pedidos cancelados.

    pd.read_sql acepta directamente una consulta select() de SQLAlchemy.
    Los libros que nunca se han vendido no aparecen (se rellenan después).
    """
    consulta = (
        select(PedidoItemDB.isbn, func.sum(PedidoItemDB.cantidad).label("unidades"))
        .join(PedidoDB, PedidoDB.id == PedidoItemDB.pedido_id)
        .where(PedidoDB.estatus != "cancelado")
        .group_by(PedidoItemDB.isbn)
    )
    ventas = pd.read_sql(consulta, sesion.connection())
    return ventas.set_index("isbn")["unidades"]


def etiquetar(unidades: pd.Series, percentil: float = 0.8) -> pd.Series:
    """1 si el libro está en el `percentil` superior de ventas, 0 si no.

    Un libro sin ventas nunca es best seller, aunque muchos empaten en cero.
    """
    if not 0 < percentil < 1:
        raise ValueError("percentil debe estar entre 0 y 1 (por ejemplo 0.8)")
    umbral = unidades.quantile(percentil)
    return ((unidades >= umbral) & (unidades > 0)).astype(int)


def cargar_datos(sesion: Session, percentil: float = 0.8) -> pd.DataFrame:
    """Tabla con las características de cada libro, sus unidades y la etiqueta."""
    df = a_dataframe(bd.listar_libros(sesion))
    # join por índice (ISBN); los libros sin ventas quedan en NaN → 0
    df = df.join(unidades_vendidas(sesion))
    df["unidades"] = df["unidades"].fillna(0).astype(int)
    df["best_seller"] = etiquetar(df["unidades"], percentil)
    log.info("Datos cargados: %d libros, %d best sellers", len(df), int(df["best_seller"].sum()))
    return df


# ── 2. Modelo ────────────────────────────────────────────────────────────────


def _generos_como_tokens(generos: list[str]) -> list[str]:
    """Cada género es una "palabra" para CountVectorizer.

    Va en minúsculas para que "Realismo mágico" y "Realismo Mágico" cuenten
    igual. Es una función normal (no lambda) porque joblib no puede guardar
    lambdas dentro del modelo.
    """
    return [g.strip().lower() for g in generos]


def construir_modelo(semilla: int = 42) -> Pipeline:
    """Pipeline = preprocesamiento + clasificador, como una sola pieza.

    - generos: una columna 0/1 por género (un libro puede tener varios)
    - editorial y nacionalidad: una columna 0/1 por valor (one-hot).
      handle_unknown="ignore": una editorial nunca vista no rompe la predicción
    - precio y año: pasan tal cual (los árboles no necesitan escalarlos)
    """
    preproceso = ColumnTransformer(
        [
            ("generos", CountVectorizer(analyzer=_generos_como_tokens, binary=True), GENEROS),
            ("categorias", OneHotEncoder(handle_unknown="ignore"), CATEGORICAS),
            ("numericas", "passthrough", NUMERICAS),
        ]
    )
    clasificador = RandomForestClassifier(
        n_estimators=200,
        min_samples_leaf=3,  # evita reglas basadas en uno o dos libros
        class_weight="balanced",  # hay ~4 "no" por cada "sí": que ambos pesen igual
        random_state=semilla,
    )
    return Pipeline([("preproceso", preproceso), ("clasificador", clasificador)])


def _validar(df: pd.DataFrame) -> None:
    if len(df) < MIN_LIBROS:
        raise DatosInsuficientesError(
            f"Se necesitan al menos {MIN_LIBROS} libros para entrenar (hay {len(df)})"
        )
    por_clase = df["best_seller"].value_counts()
    minimo = min(int(por_clase.get(0, 0)), int(por_clase.get(1, 0)))
    if minimo < MIN_POR_CLASE:
        raise DatosInsuficientesError(
            f"Se necesitan al menos {MIN_POR_CLASE} libros best seller y {MIN_POR_CLASE} "
            f"que no lo sean (hay {por_clase.to_dict()}). ¿La base tiene pedidos?"
        )


def entrenar(
    df: pd.DataFrame, semilla: int = 42, proporcion_prueba: float = 0.25
) -> ResultadoEntrenamiento:
    """Entrena, mide con libros que el modelo no vio y devuelve el modelo final.

    1. Separa los libros en entrenamiento y prueba. `stratify` conserva la
       misma proporción de best sellers en ambos grupos.
    2. Entrena con el grupo de entrenamiento y calcula métricas con el de prueba.
    3. Ya medido, vuelve a entrenar con TODOS los libros: el modelo que se
       guarda aprovecha todos los datos disponibles.
    """
    _validar(df)
    X = df[CARACTERISTICAS]
    y = df["best_seller"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=proporcion_prueba, stratify=y, random_state=semilla
    )
    modelo = construir_modelo(semilla)
    modelo.fit(X_train, y_train)

    y_pred = modelo.predict(X_test)
    probabilidades = modelo.predict_proba(X_test)[:, 1]
    metricas = Metricas(
        exactitud=float(accuracy_score(y_test, y_pred)),
        exactitud_base=float((y_test == 0).mean()),
        precision=float(precision_score(y_test, y_pred, zero_division=0)),
        recall=float(recall_score(y_test, y_pred)),
        f1=float(f1_score(y_test, y_pred)),
        roc_auc=float(roc_auc_score(y_test, probabilidades)),
        libros_entrenamiento=len(X_train),
        libros_prueba=len(X_test),
    )
    reporte = str(
        classification_report(
            y_test, y_pred, target_names=["no best seller", "best seller"], zero_division=0
        )
    )
    log.info("Modelo evaluado: %s", metricas)

    final = clone(modelo)  # misma configuración, sin lo aprendido
    final.fit(X, y)
    return ResultadoEntrenamiento(modelo=final, metricas=metricas, reporte=reporte)


def importancias(modelo: Pipeline, top: int | None = None) -> pd.Series:
    """Qué tanto usó el bosque cada característica para decidir (suman 1).

    Los nombres vienen del preprocesamiento: "generos__thriller",
    "categorias__editorial_Planeta", "numericas__precio", etc.
    """
    nombres = modelo.named_steps["preproceso"].get_feature_names_out()
    valores = modelo.named_steps["clasificador"].feature_importances_
    serie = pd.Series(valores, index=nombres).sort_values(ascending=False)
    return serie.head(top) if top is not None else serie


# ── 3. Guardar y cargar ──────────────────────────────────────────────────────


def guardar_modelo(modelo: Pipeline, ruta: str | Path = RUTA_MODELO) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, ruta)
    log.info("Modelo guardado en %s", ruta)
    return ruta


def cargar_modelo(ruta: str | Path = RUTA_MODELO) -> Pipeline:
    """Carga un modelo guardado con guardar_modelo().

    ⚠️ joblib usa pickle: cargar un archivo puede ejecutar código. Carga solo
    modelos que tú mismo entrenaste. Además, conviene usar la misma versión de
    scikit-learn con la que se guardó.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        raise ArchivoNoEncontradoError(
            f"No existe el modelo {ruta}. Entrénalo con: "
            "poetry run python -m libreria.prediccion entrenar"
        )
    modelo: Any = joblib.load(ruta)
    if not isinstance(modelo, Pipeline):
        raise LibreriaError(f"{ruta} no contiene un modelo de predicción válido")
    return modelo


# ── 4. Predecir ──────────────────────────────────────────────────────────────


def predecir(modelo: Pipeline, libros: Sequence[Libro]) -> pd.Series:
    """Probabilidad (0 a 1) de que cada libro sea best seller, indexada por ISBN."""
    if not libros:
        return pd.Series(dtype=float)
    X = a_dataframe(libros)
    columna_si = list(modelo.classes_).index(1)
    probabilidades = modelo.predict_proba(X[CARACTERISTICAS])[:, columna_si]
    return pd.Series(probabilidades, index=X.index, name="probabilidad")


def predecir_libro(modelo: Pipeline, libro: Libro) -> float:
    return float(predecir(modelo, [libro]).iloc[0])


# ---------------------------------------------------------------------------
# Uso directo: poetry run python -m libreria.prediccion {entrenar,predecir}
# ---------------------------------------------------------------------------
def _comando_entrenar(args: argparse.Namespace) -> None:
    motor = bd.crear_motor(f"sqlite:///{args.bd}")
    with Session(motor) as sesion:
        df = cargar_datos(sesion, args.percentil)
    motor.dispose()

    resultado = entrenar(df, semilla=args.semilla)
    m = resultado.metricas
    print(f"📚 {len(df)} libros, {int(df['best_seller'].sum())} best sellers\n")
    print(resultado.reporte)
    print(f"ROC AUC: {m.roc_auc:.3f}   (0.5 = azar, 1.0 = perfecto)")
    print(
        f"Exactitud: {m.exactitud:.1%}  vs. {m.exactitud_base:.1%} "
        "diciendo siempre 'no es best seller'\n"
    )
    print("Características más importantes:")
    for nombre, valor in importancias(resultado.modelo, top=10).items():
        print(f"   {valor:6.3f}  {nombre}")

    ruta = guardar_modelo(resultado.modelo, args.modelo)
    print(f"\n✅ Modelo guardado en {ruta}")


def _comando_predecir(args: argparse.Namespace) -> None:
    modelo = cargar_modelo(args.modelo)
    motor = bd.crear_motor(f"sqlite:///{args.bd}")
    with Session(motor) as sesion:
        libros = bd.listar_libros(sesion)
    motor.dispose()

    titulos = {libro.isbn: libro.titulo for libro in libros}
    probabilidades = predecir(modelo, libros).sort_values(ascending=False)
    print(f"Probabilidad de ser best seller ({len(libros)} libros):\n")
    for isbn, probabilidad in probabilidades.items():
        print(f"   {probabilidad:6.1%}  {titulos[str(isbn)]}  ({isbn})")


def main(argumentos: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Clasificador de best sellers.")
    comandos = parser.add_subparsers(dest="comando", required=True)

    p_entrenar = comandos.add_parser("entrenar", help="entrena y guarda el modelo")
    p_entrenar.add_argument("--bd", type=Path, default=Path("data/sintetico.db"))
    p_entrenar.add_argument("--modelo", type=Path, default=RUTA_MODELO)
    p_entrenar.add_argument("--percentil", type=float, default=0.8)
    p_entrenar.add_argument("--semilla", type=int, default=42)
    p_entrenar.set_defaults(funcion=_comando_entrenar)

    p_predecir = comandos.add_parser("predecir", help="predice para los libros de una base")
    p_predecir.add_argument("--bd", type=Path, default=Path("data/libreria.db"))
    p_predecir.add_argument("--modelo", type=Path, default=RUTA_MODELO)
    p_predecir.set_defaults(funcion=_comando_predecir)

    args = parser.parse_args(argumentos)
    if not args.bd.exists():
        parser.error(f"No existe la base {args.bd}")
    try:
        args.funcion(args)
    except LibreriaError as e:
        log.exception("Error en prediccion")
        print(f"❌ {e}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
