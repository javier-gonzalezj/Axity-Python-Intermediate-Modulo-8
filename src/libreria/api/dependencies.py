"""Dependencias de la API: lógica que FastAPI ejecuta ANTES de cada endpoint.

Cada función se declara una vez y los routers la piden con Depends(...).
Para no repetir `Annotated[..., Depends(...)]` en cada endpoint, al final de
cada bloque hay un alias (SesionDep, LibroDep, ...).

También es la RAÍZ DE COMPOSICIÓN de la API: el único lugar que conoce el
adaptador concreto (UnidadDeTrabajoSQL) y arma los casos de uso con él. Los
routers solo piden el caso de uso ya armado (CrearPedidoDep, ...).
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Path, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.api import seguridad
from libreria.casos_uso import AgregarLibro, CambiarEstatusPedido, CrearPedido
from libreria.modelos import Libro, Usuario
from libreria.pedidos import Pedido
from libreria.puertos import UnidadDeTrabajo
from libreria.repositorios_sql import UnidadDeTrabajoSQL

# Tipo del parámetro `responses` de los decoradores de FastAPI (documenta errores en /docs)
type Respuestas = dict[int | str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
@lru_cache
def obtener_motor() -> Engine:
    """Un solo motor para toda la app (lru_cache lo crea la primera vez).

    La URL se puede cambiar con la variable de entorno LIBRERIA_URL_BD.
    """
    return bd.crear_motor(os.environ.get("LIBRERIA_URL_BD", bd.URL_BD))


def obtener_sesion() -> Iterator[Session]:
    """Abre una sesión por petición y la cierra al terminar (dependencia con yield).

    En las pruebas se reemplaza con app.dependency_overrides para usar una base
    en memoria.
    """
    with Session(obtener_motor()) as sesion:
        yield sesion


SesionDep = Annotated[Session, Depends(obtener_sesion)]


# ---------------------------------------------------------------------------
# Casos de uso (wiring)
# ---------------------------------------------------------------------------
def obtener_uow(sesion: SesionDep) -> UnidadDeTrabajo:
    """Aquí se elige el adaptador. Los casos de uso solo ven el puerto."""
    return UnidadDeTrabajoSQL(sesion)


UowDep = Annotated[UnidadDeTrabajo, Depends(obtener_uow)]


def caso_crear_pedido(uow: UowDep) -> CrearPedido:
    return CrearPedido(uow)


def caso_cambiar_estatus(uow: UowDep) -> CambiarEstatusPedido:
    return CambiarEstatusPedido(uow)


def caso_agregar_libro(uow: UowDep) -> AgregarLibro:
    return AgregarLibro(uow)


CrearPedidoDep = Annotated[CrearPedido, Depends(caso_crear_pedido)]
CambiarEstatusDep = Annotated[CambiarEstatusPedido, Depends(caso_cambiar_estatus)]
AgregarLibroDep = Annotated[AgregarLibro, Depends(caso_agregar_libro)]


# ---------------------------------------------------------------------------
# "¿Existe el registro?" — se escribe una vez y la usan GET, PATCH y DELETE
# ---------------------------------------------------------------------------
IsbnPath = Annotated[
    str, Path(min_length=10, max_length=20, description="ISBN del libro, con o sin guiones")
]
IdPath = Annotated[int, Path(gt=0, description="Identificador numérico")]


def _o_404[T](valor: T | None, mensaje: str) -> T:
    if valor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=mensaje)
    return valor


def libro_existente(isbn: IsbnPath, sesion: SesionDep) -> Libro:
    return _o_404(bd.obtener_libro(sesion, isbn), f"No existe el libro {isbn}")


def usuario_existente(usuario_id: IdPath, sesion: SesionDep) -> Usuario:
    return _o_404(bd.obtener_usuario(sesion, usuario_id), f"No existe el usuario {usuario_id}")


def pedido_existente(pedido_id: IdPath, sesion: SesionDep) -> Pedido:
    return _o_404(bd.obtener_pedido(sesion, pedido_id), f"No existe el pedido {pedido_id}")


LibroDep = Annotated[Libro, Depends(libro_existente)]
UsuarioDep = Annotated[Usuario, Depends(usuario_existente)]
PedidoDep = Annotated[Pedido, Depends(pedido_existente)]


# ---------------------------------------------------------------------------
# Paginación: ?saltar=0&limite=20 en cualquier listado
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Paginacion:
    saltar: int
    limite: int

    def aplicar[T](self, elementos: list[T]) -> list[T]:
        return elementos[self.saltar : self.saltar + self.limite]


def paginacion(
    saltar: Annotated[int, Query(ge=0, description="Cuántos registros omitir")] = 0,
    limite: Annotated[int, Query(ge=1, le=100, description="Máximo de registros")] = 20,
) -> Paginacion:
    return Paginacion(saltar=saltar, limite=limite)


PaginacionDep = Annotated[Paginacion, Depends(paginacion)]


# ---------------------------------------------------------------------------
# Autenticación (¿quién eres?) y autorización (¿qué puedes hacer?)
# ---------------------------------------------------------------------------
# Lee el encabezado "Authorization: Bearer <token>" y responde 401 si falta.
# tokenUrl le dice a /docs dónde iniciar sesión (botón "Authorize").
esquema_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")


def _no_autenticado(mensaje: str) -> HTTPException:
    # El encabezado WWW-Authenticate es lo que el estándar pide junto con un 401
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=mensaje,
        headers={"WWW-Authenticate": "Bearer"},
    )


def usuario_actual(token: Annotated[str, Depends(esquema_oauth2)], sesion: SesionDep) -> Usuario:
    """El usuario dueño del token. 401 si el token no sirve o el usuario ya no existe."""
    try:
        usuario_id = seguridad.leer_token(token)
    except seguridad.TokenInvalidoError as e:
        raise _no_autenticado(str(e)) from None

    usuario = bd.obtener_usuario(sesion, usuario_id)
    if usuario is None:
        raise _no_autenticado("El usuario del token ya no existe")
    return usuario


UsuarioActualDep = Annotated[Usuario, Depends(usuario_actual)]


def _prohibido(mensaje: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=mensaje)


def requiere_admin(actual: UsuarioActualDep) -> Usuario:
    if actual.rol != "admin":
        raise _prohibido("Solo un administrador puede hacer esto")
    return actual


AdminDep = Annotated[Usuario, Depends(requiere_admin)]


# El usuario autenticado va PRIMERO: así, sin token se responde 401 antes de
# revelar si el registro existe (404).
def usuario_autorizado(actual: UsuarioActualDep, usuario: UsuarioDep) -> Usuario:
    """El usuario de la URL, si es uno mismo o si quien pregunta es admin."""
    if actual.rol != "admin" and actual.id != usuario.id:
        raise _prohibido("Solo puedes consultar o modificar tu propia cuenta")
    return usuario


def pedido_autorizado(actual: UsuarioActualDep, pedido: PedidoDep) -> Pedido:
    """El pedido de la URL, si es del usuario autenticado o si quien pregunta es admin."""
    if actual.rol != "admin" and actual.id != pedido.usuario_id:
        raise _prohibido("Este pedido no es tuyo")
    return pedido


UsuarioAutorizadoDep = Annotated[Usuario, Depends(usuario_autorizado)]
PedidoAutorizadoDep = Annotated[Pedido, Depends(pedido_autorizado)]
