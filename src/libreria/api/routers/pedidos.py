"""Endpoints de pedidos: /pedidos. Todos requieren iniciar sesión.

Permisos:
    listar todos                    solo admin
    crear                           cualquier usuario (el pedido queda a su nombre)
    ver                             el dueño del pedido o un admin
    cambiar estatus                 admin; el dueño solo puede cancelar
"""

from fastapi import APIRouter, Depends, HTTPException, status

from libreria import basedatos as bd
from libreria.api.dependencies import (
    PaginacionDep,
    PedidoAutorizadoDep,
    Respuestas,
    SesionDep,
    UsuarioActualDep,
    requiere_admin,
    usuario_actual,
)
from libreria.api.schemas import EstatusUpdate, PedidoCreate, PedidoRead
from libreria.pedidos import Estatus, Pedido

router = APIRouter(
    prefix="/pedidos",
    tags=["pedidos"],
    # Nivel router: ningún endpoint de pedidos es público. Los que además
    # necesitan saber QUIÉN es el usuario lo piden como parámetro; FastAPI
    # reutiliza el resultado y no decodifica el token dos veces.
    dependencies=[Depends(usuario_actual)],
    responses={401: {"description": "No autenticado"}, 403: {"description": "Sin permiso"}},
)

_NO_ENCONTRADO: Respuestas = {404: {"description": "Pedido no encontrado"}}


@router.get(
    "/",
    response_model=list[PedidoRead],
    summary="Listar todos los pedidos",
    dependencies=[Depends(requiere_admin)],
)
def listar_pedidos(
    sesion: SesionDep, pagina: PaginacionDep, estatus: Estatus | None = None
) -> list[Pedido]:
    # Al declarar `estatus` como Literal, FastAPI rechaza ?estatus=perdido con 422
    return pagina.aplicar(bd.listar_pedidos(sesion, estatus))


@router.post(
    "/",
    response_model=PedidoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un pedido a mi nombre",
    responses={
        409: {"description": "No hay stock suficiente"},
        422: {"description": "Datos inválidos o algún libro no existe"},
    },
)
def crear_pedido(datos: PedidoCreate, actual: UsuarioActualDep, sesion: SesionDep) -> Pedido:
    assert actual.id is not None
    # El usuario sale del token, no del cuerpo de la petición
    return bd.crear_pedido(sesion, actual.id, datos.como_lineas())


@router.get(
    "/{pedido_id}", response_model=PedidoRead, summary="Obtener un pedido", responses=_NO_ENCONTRADO
)
def obtener_pedido(pedido: PedidoAutorizadoDep) -> Pedido:
    return pedido


@router.patch(
    "/{pedido_id}/estatus",
    response_model=PedidoRead,
    summary="Avanzar o cancelar un pedido",
    responses={**_NO_ENCONTRADO, 409: {"description": "Transición de estatus no permitida"}},
)
def cambiar_estatus(
    pedido: PedidoAutorizadoDep,
    cambio: EstatusUpdate,
    actual: UsuarioActualDep,
    sesion: SesionDep,
) -> Pedido:
    """pendiente → pagado → enviado. Cancelar regresa los libros al inventario."""
    if actual.rol != "admin" and cambio.estatus != "cancelado":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo un administrador puede marcar pagos o envíos"
        )
    assert pedido.id is not None  # viene de la base, siempre tiene id
    return bd.cambiar_estatus(sesion, pedido.id, cambio.estatus)
