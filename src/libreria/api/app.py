"""Aplicación FastAPI: arma la app, incluye los routers y traduce errores a HTTP.

Para correrla (desde la raíz del proyecto, después de `alembic upgrade head`):

    poetry run fastapi dev src/libreria/api/app.py

y abre http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from libreria.api.routers import auth, libros, pedidos, reportes, usuarios
from libreria.excepciones import (
    LibreriaError,
    LibroInvalidoError,
    LibroNoEncontradoError,
    PedidoNoEncontradoError,
    RegistroEnUsoError,
    StockInsuficienteError,
    TransicionEstatusError,
    UsuarioNoEncontradoError,
)

app = FastAPI(
    title="API de Librería",
    description="Catálogo, usuarios, pedidos y reportes de la Librería Libros Libres.",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(libros.router)
app.include_router(usuarios.router)
app.include_router(pedidos.router)
app.include_router(reportes.router)


# ---------------------------------------------------------------------------
# Excepciones del proyecto -> códigos HTTP
# ---------------------------------------------------------------------------
# El dominio y basedatos.py lanzan sus excepciones (no saben nada de HTTP). Aquí se
# traducen en un solo lugar. El orden importa: la primera clase que coincida
# gana, así que las más específicas van antes (LibroNoEncontradoError hereda
# de LibroInvalidoError).
CODIGOS_HTTP: list[tuple[type[LibreriaError], int]] = [
    (LibroNoEncontradoError, status.HTTP_404_NOT_FOUND),
    (UsuarioNoEncontradoError, status.HTTP_404_NOT_FOUND),
    (PedidoNoEncontradoError, status.HTTP_404_NOT_FOUND),
    (StockInsuficienteError, status.HTTP_409_CONFLICT),
    (RegistroEnUsoError, status.HTTP_409_CONFLICT),
    (TransicionEstatusError, status.HTTP_409_CONFLICT),
    (LibroInvalidoError, 422),  # datos que la base no puede procesar
]


@app.exception_handler(LibreriaError)
async def manejar_error_libreria(_peticion: Request, error: Exception) -> JSONResponse:
    codigo = next(
        (codigo for tipo, codigo in CODIGOS_HTTP if isinstance(error, tipo)),
        status.HTTP_400_BAD_REQUEST,  # cualquier otro LibreriaError
    )
    return JSONResponse(status_code=codigo, content={"detail": str(error)})


@app.get("/", include_in_schema=False)
def inicio() -> RedirectResponse:
    return RedirectResponse(url="/docs")
