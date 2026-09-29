"""Reportes de ventas: /reportes. Solo para administradores."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from libreria import basedatos as bd
from libreria.api.dependencies import SesionDep, requiere_admin
from libreria.api.schemas import LibroVendidoRead, TotalUsuarioRead

router = APIRouter(
    prefix="/reportes",
    tags=["reportes"],
    # Dependencia a nivel router: se ejecuta antes de CADA endpoint de este archivo
    dependencies=[Depends(requiere_admin)],
    responses={401: {"description": "No autenticado"}, 403: {"description": "Solo admin"}},
)


@router.get("/usuarios", response_model=list[TotalUsuarioRead], summary="Gasto por usuario")
def total_por_usuario(sesion: SesionDep) -> list[TotalUsuarioRead]:
    return [
        TotalUsuarioRead(nombre=nombre, pedidos=pedidos, total=total)
        for nombre, pedidos, total in bd.total_por_usuario(sesion)
    ]


@router.get("/mas-vendidos", response_model=list[LibroVendidoRead], summary="Libros más vendidos")
def libros_mas_vendidos(
    sesion: SesionDep, limite: Annotated[int, Query(ge=1, le=50)] = 5
) -> list[LibroVendidoRead]:
    return [
        LibroVendidoRead(titulo=titulo, cantidad=cantidad)
        for titulo, cantidad in bd.libros_mas_vendidos(sesion, limite)
    ]
