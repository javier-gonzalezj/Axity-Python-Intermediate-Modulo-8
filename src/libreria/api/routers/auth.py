"""Registro, inicio de sesión y cuenta propia: /auth."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from libreria import basedatos as bd
from libreria.api import seguridad
from libreria.api.dependencies import SesionDep, UsuarioActualDep
from libreria.api.schemas import PasswordUpdate, Token, UsuarioCreate, UsuarioRead
from libreria.modelos import Usuario

router = APIRouter(prefix="/auth", tags=["autenticación"])


@router.post(
    "/registro",
    response_model=UsuarioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una cuenta de cliente",
    responses={409: {"description": "Ya existe un usuario con ese email"}},
)
def registrarse(datos: UsuarioCreate, sesion: SesionDep) -> Usuario:
    if bd.buscar_usuario_por_email(sesion, datos.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un usuario con ese email")
    return bd.crear_usuario(
        sesion,
        datos.nombre,
        datos.email,
        datos.telefono,
        password_hash=seguridad.hashear_password(datos.password),
        rol="cliente",  # registrarse nunca da permisos de admin
    )


@router.post(
    "/token",
    response_model=Token,
    summary="Iniciar sesión",
    responses={401: {"description": "Email o contraseña incorrectos"}},
)
def iniciar_sesion(
    formulario: Annotated[OAuth2PasswordRequestForm, Depends()], sesion: SesionDep
) -> Token:
    """Recibe un formulario (no JSON) con `username` y `password`, como pide OAuth2.

    En `username` va el email. Esto es lo que usa el botón "Authorize" de /docs.
    """
    usuario = bd.buscar_usuario_por_email(sesion, formulario.username.strip().lower())
    password_hash = bd.obtener_password_hash(sesion, usuario.id) if usuario and usuario.id else None

    # Se verifica SIEMPRE (con un hash falso si el email no existe) y se responde
    # el mismo mensaje en ambos casos: así no se revela qué emails están registrados
    valida = seguridad.verificar_password(formulario.password, password_hash)
    if not valida or usuario is None or usuario.id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(
        access_token=seguridad.crear_token(usuario.id),
        expira_en=seguridad.MINUTOS_VALIDEZ * 60,
    )


@router.get("/yo", response_model=UsuarioRead, summary="Mi cuenta")
def mi_cuenta(actual: UsuarioActualDep) -> Usuario:
    return actual


@router.put(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cambiar mi contraseña",
    responses={400: {"description": "La contraseña actual no es correcta"}},
)
def cambiar_password(cambio: PasswordUpdate, actual: UsuarioActualDep, sesion: SesionDep) -> None:
    assert actual.id is not None
    if not seguridad.verificar_password(cambio.actual, bd.obtener_password_hash(sesion, actual.id)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta")
    bd.cambiar_password_hash(sesion, actual.id, seguridad.hashear_password(cambio.nueva))
