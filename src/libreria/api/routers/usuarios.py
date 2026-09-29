"""Endpoints de clientes: /usuarios.

Permisos:
    listar, crear y cambiar rol     solo admin
    ver, modificar, borrar, pedidos el propio usuario o un admin
Para registrarse sin ser admin está POST /auth/registro.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.api import seguridad
from libreria.api.dependencies import (
    AdminDep,
    PaginacionDep,
    Respuestas,
    SesionDep,
    UsuarioAutorizadoDep,
    UsuarioDep,
    requiere_admin,
)
from libreria.api.schemas import (
    PedidoRead,
    RolUpdate,
    UsuarioAdminCreate,
    UsuarioRead,
    UsuarioUpdate,
)
from libreria.modelos import Usuario
from libreria.pedidos import Pedido

router = APIRouter(
    prefix="/usuarios",
    tags=["usuarios"],
    responses={401: {"description": "No autenticado"}, 403: {"description": "Sin permiso"}},
)

_EMAIL_REPETIDO: Respuestas = {409: {"description": "Ya existe un usuario con ese email"}}
_NO_ENCONTRADO: Respuestas = {404: {"description": "Usuario no encontrado"}}


def _email_disponible(sesion: Session, email: str, excepto_id: int | None = None) -> None:
    otro = bd.buscar_usuario_por_email(sesion, email)
    if otro is not None and otro.id != excepto_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ya existe un usuario con email {email}")


@router.get(
    "/",
    response_model=list[UsuarioRead],
    summary="Listar usuarios",
    dependencies=[Depends(requiere_admin)],
)
def listar_usuarios(sesion: SesionDep, pagina: PaginacionDep) -> list[Usuario]:
    return pagina.aplicar(bd.listar_usuarios(sesion))


@router.post(
    "/",
    response_model=UsuarioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un usuario (con cualquier rol)",
    dependencies=[Depends(requiere_admin)],
    responses=_EMAIL_REPETIDO,
)
def crear_usuario(datos: UsuarioAdminCreate, sesion: SesionDep) -> Usuario:
    _email_disponible(sesion, datos.email)
    return bd.crear_usuario(
        sesion,
        datos.nombre,
        datos.email,
        datos.telefono,
        password_hash=seguridad.hashear_password(datos.password),
        rol=datos.rol,
    )


@router.get(
    "/{usuario_id}",
    response_model=UsuarioRead,
    summary="Obtener un usuario",
    responses=_NO_ENCONTRADO,
)
def obtener_usuario(usuario: UsuarioAutorizadoDep) -> Usuario:
    return usuario


@router.patch(
    "/{usuario_id}",
    response_model=UsuarioRead,
    summary="Modificar nombre, email o teléfono",
    responses={**_NO_ENCONTRADO, **_EMAIL_REPETIDO},
)
def actualizar_usuario(
    usuario: UsuarioAutorizadoDep, cambios: UsuarioUpdate, sesion: SesionDep
) -> Usuario:
    assert usuario.id is not None  # viene de la base, siempre tiene id
    if cambios.email is not None:
        _email_disponible(sesion, cambios.email, excepto_id=usuario.id)
    return bd.actualizar_usuario(sesion, usuario.id, **cambios.model_dump(exclude_none=True))


@router.put(
    "/{usuario_id}/rol",
    response_model=UsuarioRead,
    summary="Cambiar el rol de un usuario",
    responses=_NO_ENCONTRADO,
)
def cambiar_rol(
    admin: AdminDep, usuario: UsuarioDep, cambio: RolUpdate, sesion: SesionDep
) -> Usuario:
    assert usuario.id is not None
    if usuario.id == admin.id and cambio.rol != "admin":
        # Evita que el último (o único) admin se quite el permiso por error
        raise HTTPException(status.HTTP_409_CONFLICT, "No puedes quitarte tu propio rol de admin")
    return bd.cambiar_rol(sesion, usuario.id, cambio.rol)


@router.delete(
    "/{usuario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un usuario sin pedidos",
    responses={**_NO_ENCONTRADO, 409: {"description": "El usuario tiene pedidos"}},
)
def eliminar_usuario(usuario: UsuarioAutorizadoDep, sesion: SesionDep) -> None:
    assert usuario.id is not None
    bd.eliminar_usuario(sesion, usuario.id)


@router.get(
    "/{usuario_id}/pedidos",
    response_model=list[PedidoRead],
    summary="Pedidos de un usuario",
    responses=_NO_ENCONTRADO,
)
def pedidos_de_usuario(usuario: UsuarioAutorizadoDep, sesion: SesionDep) -> list[Pedido]:
    assert usuario.id is not None
    return bd.pedidos_de_usuario(sesion, usuario.id)
