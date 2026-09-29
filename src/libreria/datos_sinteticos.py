"""Datos sintéticos para practicar machine learning: libros y ventas inventados.

La base real (data/libreria.db) tiene pocos libros y ningún pedido, así que no
alcanza para entrenar un clasificador. Este módulo llena una base APARTE con
cientos de libros y miles de pedidos generados al azar, pero con una tendencia
escondida: algunos rasgos (género, precio, editorial...) hacen que un libro se
venda más. El clasificador de prediccion.py tiene que descubrir esa tendencia
solo con los datos.

Importante: como la regla la inventamos nosotros (ver `popularidad`), un modelo
entrenado aquí solo aprende NUESTRA regla, no cómo se venden los libros en la
vida real. Sirve para aprender el flujo completo, no para tomar decisiones.

Uso:
    poetry run python -m libreria.datos_sinteticos
    poetry run python -m libreria.datos_sinteticos --libros 500 --pedidos 5000 --reemplazar
"""

import argparse
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.basedatos import PedidoDB, PedidoItemDB
from libreria.excepciones import LibreriaError
from libreria.modelos import Autor, Libreria, Libro

log = logging.getLogger(__name__)

RUTA_BD_SINTETICA: Final = Path("data/sintetico.db")
RUTA_BD_REAL: Final = Path("data/libreria.db")

# Catálogos de valores posibles. Incluyen los que ya usa la librería real para
# que, al predecir sobre los libros reales, el modelo reconozca sus categorías.
GENEROS: Final = (
    "Novela", "Ficción", "Realismo mágico", "Ensayo", "Poesía", "Ciencia ficción",
    "Distopía", "Romance", "Fantasía", "Historia", "Biografía", "Thriller", "Terror",
)  # fmt: skip
EDITORIALES: Final = (
    "Alfaguara", "Debolsillo", "Sudamericana", "Seix Barral", "TusQuets",
    "Fondo de Cultura Económica", "Anagrama", "Planeta", "Era", "Sexto Piso",
)  # fmt: skip
NACIONALIDADES: Final = (
    "Mexicana", "Argentina", "Colombiana", "Española", "Chilena", "Estadounidense", "Británica",
)  # fmt: skip
NOMBRES: Final = ("Ana", "Luis", "Marta", "Jorge", "Elena", "Pablo", "Sofía", "Diego", "Lucía")
APELLIDOS: Final = ("García", "López", "Hernández", "Torres", "Ramírez", "Flores", "Castro")

# ── La regla escondida ───────────────────────────────────────────────────────
# Cada rasgo suma o resta "puntos de popularidad". Son los números que el
# clasificador debería redescubrir (compáralos con prediccion.importancias()).
EFECTO_GENERO: Final[dict[str, float]] = {
    "Thriller": 0.8,
    "Fantasía": 0.7,
    "Romance": 0.6,
    "Novela": 0.5,
    "Realismo mágico": 0.4,
    "Ciencia ficción": 0.3,
    "Historia": -0.3,
    "Ensayo": -0.6,
    "Poesía": -0.8,
}
EFECTO_EDITORIAL: Final[dict[str, float]] = {"Planeta": 0.4, "Debolsillo": 0.3, "Era": -0.2}
EFECTO_NACIONALIDAD: Final[dict[str, float]] = {"Mexicana": 0.3}
PRECIO_REFERENCIA: Final = 300.0  # más barato que esto ayuda; más caro, perjudica
AÑO_RECIENTE: Final = 2015
PORCENTAJE_CANCELADOS: Final = 0.1


@dataclass(frozen=True)
class ResumenSintetico:
    """Cuántos registros se generaron."""

    libros: int
    usuarios: int
    pedidos: int
    unidades: int  # unidades vendidas, sin contar pedidos cancelados


def popularidad(libro: Libro) -> float:
    """Puntaje escondido de un libro: cuanto más alto, más se vende.

    Solo depende de rasgos que se conocen ANTES de vender (género, precio,
    editorial, nacionalidad del autor, año), igual que las características
    que usa el clasificador.
    """
    puntos = sum(EFECTO_GENERO.get(g, 0.0) for g in libro.genero)
    puntos += EFECTO_EDITORIAL.get(libro.editorial, 0.0)
    puntos += EFECTO_NACIONALIDAD.get(libro.autor.nacionalidad, 0.0)
    puntos -= (libro.precio - PRECIO_REFERENCIA) / 200
    if libro.año_publicacion >= AÑO_RECIENTE:
        puntos += 0.4
    return puntos


def generar_libros(cantidad: int, rng: random.Random) -> list[Libro]:
    """Crea `cantidad` libros al azar. Pasan por el modelo Libro, así que son válidos."""
    libros: list[Libro] = []
    for i in range(1, cantidad + 1):
        existencias = rng.randint(0, 30)
        libros.append(
            Libro(
                isbn=f"SINT-{i:05d}",
                titulo=f"Libro sintético {i}",
                autor=Autor(
                    nombre=f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}",
                    nacionalidad=rng.choice(NACIONALIDADES),
                ),
                genero=rng.sample(GENEROS, k=rng.choice((1, 1, 2, 2, 3))),
                año_publicacion=rng.randint(1900, 2026),
                precio=round(rng.uniform(99, 799), 2),
                en_stock=existencias > 0,
                cantidad_disponible=existencias,
                editorial=rng.choice(EDITORIALES),
            )
        )
    return libros


def _elegir_distintos(
    isbns: list[str], pesos: list[float], cuantos: int, rng: random.Random
) -> set[str]:
    """Elige `cuantos` ISBN distintos, favoreciendo los de mayor peso.

    Un libro solo puede aparecer una vez por pedido (UniqueConstraint en
    pedido_items), por eso se repite el sorteo hasta tener libros diferentes.
    """
    elegidos: set[str] = set()
    while len(elegidos) < cuantos:
        elegidos.add(rng.choices(isbns, weights=pesos)[0])
    return elegidos


def poblar(
    sesion: Session,
    libros: int = 300,
    pedidos: int = 3000,
    usuarios: int = 100,
    semilla: int = 42,
    dias: int = 365,
) -> ResumenSintetico:
    """Llena la base con libros, usuarios y pedidos sintéticos.

    Con la misma `semilla` siempre se generan exactamente los mismos datos, lo
    que hace reproducibles los experimentos y las pruebas.

    Los pedidos se insertan directamente como historial: NO usan
    basedatos.crear_pedido(), porque ese valida y descuenta existencias y pone
    la fecha de hoy, y aquí queremos ventas repartidas en el pasado.
    """
    if libros < 1 or pedidos < 1 or usuarios < 1:
        raise LibreriaError("Se necesita al menos un libro, un pedido y un usuario")

    rng = random.Random(semilla)
    catalogo = generar_libros(libros, rng)
    datos: Libreria = {
        "nombre": "Librería sintética",
        "direccion": {"calle": "", "colonia": "", "ciudad": "", "cp": ""},
        "telefono": "",
        "horario": "",
        "libros": catalogo,
    }
    bd.importar_catalogo(sesion, datos)

    ids_usuarios: list[int] = []
    for i in range(1, usuarios + 1):
        nombre = f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}"
        usuario = bd.crear_usuario(sesion, nombre, f"cliente{i}@sintetico.test")
        assert usuario.id is not None
        ids_usuarios.append(usuario.id)

    # Probabilidad de que un libro entre en un pedido: exp() convierte el
    # puntaje (que puede ser negativo) en un peso positivo. Se suma un poco de
    # ruido para que la regla no sea perfecta, como en la vida real.
    isbns = [libro.isbn for libro in catalogo]
    pesos = [math.exp(popularidad(libro) + rng.gauss(0, 0.5)) for libro in catalogo]
    precios = {libro.isbn: libro.precio for libro in catalogo}

    ahora = datetime.now().replace(microsecond=0)
    unidades = 0
    for _ in range(pedidos):
        estatus = (
            "cancelado"
            if rng.random() < PORCENTAJE_CANCELADOS
            else rng.choice(("pendiente", "pagado", "enviado"))
        )
        pedido = PedidoDB(
            usuario_id=rng.choice(ids_usuarios),
            fecha=ahora - timedelta(days=rng.randint(0, dias), minutes=rng.randint(0, 1439)),
            estatus=estatus,
        )
        for isbn in _elegir_distintos(isbns, pesos, rng.choice((1, 1, 1, 2, 3)), rng):
            cantidad = rng.choice((1, 1, 1, 2, 3))
            pedido.items.append(
                PedidoItemDB(isbn=isbn, cantidad=cantidad, precio_unitario=precios[isbn])
            )
            if estatus != "cancelado":
                unidades += cantidad
        sesion.add(pedido)
    sesion.commit()

    resumen = ResumenSintetico(libros=libros, usuarios=usuarios, pedidos=pedidos, unidades=unidades)
    log.info("Datos sintéticos generados: %s", resumen)
    return resumen


# ---------------------------------------------------------------------------
# Uso directo: poetry run python -m libreria.datos_sinteticos
# ---------------------------------------------------------------------------
def main(argumentos: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Genera una base con ventas sintéticas.")
    parser.add_argument("--bd", type=Path, default=RUTA_BD_SINTETICA, help="archivo SQLite")
    parser.add_argument("--libros", type=int, default=300)
    parser.add_argument("--pedidos", type=int, default=3000)
    parser.add_argument("--usuarios", type=int, default=100)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument(
        "--reemplazar", action="store_true", help="borra la base sintética si ya existe"
    )
    args = parser.parse_args(argumentos)
    ruta: Path = args.bd

    # Protección: nunca mezclar datos inventados con la base real
    if ruta.resolve() == RUTA_BD_REAL.resolve():
        parser.error(f"{RUTA_BD_REAL} es la base real; usa otro archivo para datos sintéticos")
    if ruta.exists():
        if not args.reemplazar:
            parser.error(f"{ruta} ya existe. Usa --reemplazar para generarla de nuevo")
        ruta.unlink()

    ruta.parent.mkdir(parents=True, exist_ok=True)
    motor = bd.crear_motor(f"sqlite:///{ruta}")
    # Base desechable: se crea con create_all en lugar de Alembic (igual que en las pruebas)
    bd.crear_esquema(motor)
    with Session(motor) as sesion:
        resumen = poblar(
            sesion,
            libros=args.libros,
            pedidos=args.pedidos,
            usuarios=args.usuarios,
            semilla=args.semilla,
        )
    motor.dispose()

    print(f"✅ Base sintética creada en {ruta}")
    print(
        f"   {resumen.libros} libros, {resumen.usuarios} usuarios, "
        f"{resumen.pedidos} pedidos, {resumen.unidades} unidades vendidas"
    )


if __name__ == "__main__":
    main()
