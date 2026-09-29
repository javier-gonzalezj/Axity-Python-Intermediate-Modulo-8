import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Final

from libreria.almacenamiento import CatalogoJSON
from libreria.buscador import (
    MAX_DESCARGAS_SIMULTANEAS,
    descargar_portada,
    descargar_portadas,
    nombre_archivo_portada,
)
from libreria.captura import capturar_filtros, capturar_libro
from libreria.excepciones import LibreriaError
from libreria.intercambio import exportar_json
from libreria.modelos import Libreria, Libro
from libreria.registro import configurar_logging
from libreria.servicios import ServicioCatalogo
from libreria.utilidades import cronometro
from libreria.vista import mostrar_libreria, mostrar_libros

log = logging.getLogger(__name__)

LIBROS_POR_PAGINA: Final = 3

MENU: Final = """
═════════════ MENU ═════════════
  1. Filtrar libros
  2. Cargar archivo CSV
  3. Agregar un libro
  4. Ver catalogo completo
  5. Descargar portadas faltantes
  0. Salir
════════════════════════════════"""


def _opcion_importar_csv(servicio: ServicioCatalogo, carpeta_portadas: Path) -> None:
    """2. Agrega al catálogo los libros de un archivo CSV y ofrece bajar sus portadas.

    El servicio se encarga de guardar y, si el guardado falla, de revertir.
    """
    # "Copiar como ruta" en Windows agrega comillas; se quitan
    texto_ruta = input("\nRuta del archivo CSV: ").strip().strip('"')

    try:
        resultado = servicio.importar_csv(texto_ruta)
    except LibreriaError as e:
        log.exception("Error al importar el archivo CSV")
        print(f"❌ No se pudo importar: {e}")
        return

    if resultado.rechazados:
        print(f"\n⚠️  {len(resultado.rechazados)} fila(s) rechazada(s):")
        for motivo in resultado.rechazados:
            print("   - " + motivo.replace("\n", "\n     "))

    if not resultado.agregados:
        print("\nNo se agregó ningún libro.")
        return
    print(f"\n✅ {len(resultado.agregados)} libro(s) agregado(s).")

    respuesta = input("¿Deseas descargar sus portadas de Open Library? (s/n): ").strip().lower()
    if respuesta == "s":
        _descargar_portadas_lote([libro.isbn for libro in resultado.agregados], carpeta_portadas)


def _opcion_filtrar(data: Libreria, carpeta_exportaciones: Path) -> None:
    """1. Filtra el catálogo y ofrece exportar el resultado a JSON."""
    resultados = capturar_filtros(data)
    print(f"\n🔍 {len(resultados)} resultado(s) encontrado(s):")
    mostrar_libros(resultados)

    if not resultados:
        return

    respuesta = input("\n¿Deseas exportar el resultado a JSON? (s/n): ").strip().lower()
    if respuesta != "s":
        return

    nombre_defecto = f"filtro_{datetime.now():%Y%m%d_%H%M%S}"
    nombre = input(f"Nombre del archivo [{nombre_defecto}]: ").strip()
    try:
        ruta_final = exportar_json(resultados, carpeta_exportaciones / (nombre or nombre_defecto))
    except LibreriaError as e:
        log.exception("Error al exportar el archivo JSON")
        print(f"❌ No se pudo exportar: {e}")
        return
    print(f"\n✅ Resultado exportado a: {ruta_final}")


def _opcion_agregar_libro(servicio: ServicioCatalogo, carpeta_portadas: Path) -> None:
    """3. Captura un libro por consola, lo agrega (el servicio guarda) y descarga su portada."""
    try:
        datos_libro, id_portada = capturar_libro(servicio.datos)
        nuevo_libro = Libro.desde_dict(datos_libro)
        servicio.agregar(nuevo_libro)
    except LibreriaError as e:
        log.exception("Error al agregar un libro")
        print(f"❌ No se pudo agregar el libro: {e}")
        return

    print(f"\n✅ '{nuevo_libro.titulo}' agregado correctamente.")

    if id_portada is not None:
        _descargar_portada(id_portada, carpeta_portadas / nombre_archivo_portada(nuevo_libro.isbn))


def _descargar_portada(id_portada: int, destino: Path) -> None:
    """Descarga la portada. Si falla solo avisa: el libro ya quedó guardado."""
    print("🖼️  Descargando portada...")
    try:
        with cronometro("Descargar portada"):
            ruta = descargar_portada(id_portada, destino)
    except LibreriaError as e:
        log.exception("Error al descargar la portada")
        print(f"⚠️  El libro se guardó, pero no su portada: {e}")
        return
    print(f"✅ Portada guardada en: {ruta}")


def _isbns_sin_portada(libros: list[Libro], carpeta_portadas: Path) -> list[str]:
    """ISBN de los libros que todavía no tienen su portada en `carpeta_portadas`."""
    return [
        libro.isbn
        for libro in libros
        if not (carpeta_portadas / nombre_archivo_portada(libro.isbn)).exists()
    ]


def _opcion_portadas_faltantes(data: Libreria, carpeta_portadas: Path) -> None:
    """5. Descarga las portadas de todos los libros del catálogo que no la tienen."""
    faltantes = _isbns_sin_portada(data["libros"], carpeta_portadas)
    if not faltantes:
        print("\n✅ Todos los libros ya tienen su portada.")
        return
    _descargar_portadas_lote(faltantes, carpeta_portadas)


def _descargar_portadas_lote(isbns: list[str], carpeta_portadas: Path) -> None:
    """Descarga varias portadas a la vez y muestra el resumen.

    El menú es síncrono; asyncio.run() crea el event loop, ejecuta la
    corrutina hasta que terminan todas las descargas y cierra el loop.
    """
    print(
        f"\n🖼️  Descargando {len(isbns)} portada(s) "
        f"({MAX_DESCARGAS_SIMULTANEAS} a la vez)... puede tardar un poco."
    )
    with cronometro("Descargar portadas en lote"):
        resultado = asyncio.run(descargar_portadas(isbns, carpeta_portadas))

    print(f"\n✅ {len(resultado.descargadas)} portada(s) descargada(s) en: {carpeta_portadas}")
    if resultado.sin_portada:
        print(f"ℹ️  {len(resultado.sin_portada)} libro(s) sin portada en Open Library:")
        for isbn in resultado.sin_portada:
            print(f"   - {isbn}")
    if resultado.errores:
        print(f"⚠️  {len(resultado.errores)} portada(s) no se pudieron descargar:")
        for isbn, motivo in resultado.errores.items():
            print(f"   - {isbn}: {motivo}")


def main() -> None:
    os.system("cls" if os.name == "nt" else "clear")

    raiz_proyecto = Path(__file__).parent.parent.parent
    ruta_json = raiz_proyecto / "data" / "libreria.json"
    carpeta_exportaciones = ruta_json.parent / "exportaciones"
    carpeta_portadas = ruta_json.parent / "portadas"

    configurar_logging(raiz_proyecto / "logs")
    log.info("Inicio del programa")

    print("\nSCRIPT DE MANEJO DE CATALOGO DE LIBROS (INTERMEDIATE)\n")

    try:
        with cronometro("Cargar catálogo"):
            # Composition root: aquí se elige el adaptador. Para usar otro
            # almacenamiento solo cambia esta línea; ServicioCatalogo no se toca.
            servicio = ServicioCatalogo(CatalogoJSON(ruta_json))
    except LibreriaError as e:
        log.exception("Error al cargar el catálogo")
        print(f"❌ Error al cargar la librería: {e}")
        return

    while True:
        print(MENU)
        opcion = input("Elige una opción: ").strip()
        log.debug("Opción elegida: %r", opcion)

        match opcion:
            case "1":
                _opcion_filtrar(servicio.datos, carpeta_exportaciones)
            case "2":
                _opcion_importar_csv(servicio, carpeta_portadas)
            case "3":
                _opcion_agregar_libro(servicio, carpeta_portadas)
            case "4":
                mostrar_libreria(servicio.datos, por_pagina=LIBROS_POR_PAGINA)
            case "5":
                _opcion_portadas_faltantes(servicio.datos, carpeta_portadas)
            case "0":
                break
            case _:
                print("⚠️  Opción no válida, elige un número del menú.")

    log.info("Fin del programa")
    print("\n¡HASTA LUEGO!\n")


if __name__ == "__main__":
    main()
