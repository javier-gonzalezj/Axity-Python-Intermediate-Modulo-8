"""Esquemas de la API: qué datos ENTRAN y qué datos SALEN de cada endpoint.

No son lo mismo que las tablas de basedatos.py (cómo se guardan los datos) ni
que el modelo Libro de modelos.py (cómo los usa el programa). Estos esquemas son
el "contrato" HTTP: FastAPI los usa para validar las peticiones, dar forma a las
respuestas y generar la documentación OpenAPI en /docs.

Convención de nombres:
    XxxCreate   lo que manda el cliente para crear
    XxxUpdate   lo que manda para modificar (todos los campos opcionales)
    XxxRead     lo que devuelve la API
"""

import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from libreria.basedatos import Rol
from libreria.modelos import Autor, Libro
from libreria.pedidos import Estatus

_PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PATRON_TELEFONO = re.compile(r"^\+?[\d\s\-()]{8,20}$")


class _Entrada(BaseModel):
    """Configuración común para todo lo que manda el cliente."""

    model_config = ConfigDict(
        extra="forbid",  # un campo desconocido (p. ej. "precoi") es error 422
        str_strip_whitespace=True,
    )


class _Salida(BaseModel):
    """Configuración común para las respuestas."""

    # Permite construir el esquema leyendo atributos de otro objeto
    # (los modelos Usuario/Pedido de basedatos.py), no solo de diccionarios.
    model_config = ConfigDict(from_attributes=True)


def _hay_cambios(modelo: BaseModel) -> None:
    """Para los esquemas Update: exige al menos un campo con valor."""
    if all(valor is None for valor in modelo.model_dump().values()):
        raise ValueError("Indica al menos un campo para modificar")


# ---------------------------------------------------------------------------
# Validadores reutilizables
# ---------------------------------------------------------------------------
def validar_isbn(valor: str) -> str:
    """Acepta ISBN-10 o ISBN-13, con o sin guiones. Lo devuelve tal como vino.

    No revisa el dígito verificador: los ISBN de ejemplo del catálogo son inventados.
    """
    digitos = valor.strip().replace("-", "").replace(" ", "")
    es_isbn13 = len(digitos) == 13 and digitos.isdigit()
    es_isbn10 = len(digitos) == 10 and digitos[:9].isdigit() and digitos[9] in "0123456789Xx"
    if not (es_isbn13 or es_isbn10):
        raise ValueError("El ISBN debe tener 10 o 13 dígitos (se permiten guiones)")
    return valor.strip()


def validar_email(valor: str) -> str:
    valor = valor.strip().lower()  # "Ana@Mail.com" y "ana@mail.com" son el mismo
    if not _PATRON_EMAIL.match(valor):
        raise ValueError("El email no tiene un formato válido (ejemplo: ana@mail.com)")
    return valor


def validar_telefono(valor: str | None) -> str | None:
    if valor is None or valor.strip() == "":
        return None
    valor = valor.strip()
    if not _PATRON_TELEFONO.match(valor) or sum(c.isdigit() for c in valor) < 8:
        raise ValueError("El teléfono debe tener al menos 8 dígitos (ejemplo: 55-1234-5678)")
    return valor


def validar_password(valor: object) -> object:
    """Se usa con mode="before": recibe el valor TAL COMO LLEGÓ, antes de que
    str_strip_whitespace le quite los espacios. Así "  secreto1" no se guarda
    silenciosamente como "secreto1" (y luego el login con espacios fallaría)."""
    if not isinstance(valor, str):
        return valor  # que la validación normal de tipos dé el error
    if valor != valor.strip():
        raise ValueError("La contraseña no puede empezar ni terminar con espacios")
    if not (any(c.isalpha() for c in valor) and any(c.isdigit() for c in valor)):
        raise ValueError("La contraseña debe tener al menos una letra y un número")
    return valor


# ---------------------------------------------------------------------------
# Libros
# ---------------------------------------------------------------------------
class LibroCreate(_Entrada):
    """Datos para dar de alta un libro. `en_stock` no se pide: se calcula."""

    isbn: str = Field(examples=["978-607-16-0001-1"])
    titulo: str = Field(min_length=1, max_length=200, examples=["Pedro Páramo"])
    autor: Autor  # reutiliza el modelo existente: nombre obligatorio, nacionalidad opcional
    genero: list[str] = Field(min_length=1, examples=[["Novela", "Realismo mágico"]])
    año_publicacion: int = Field(ge=1450, examples=[1955])  # la imprenta es de ~1450
    precio: float = Field(ge=0, le=100_000, examples=[189.0])
    cantidad_disponible: int = Field(ge=0, le=10_000, examples=[10])
    editorial: str = Field(min_length=1, max_length=100, examples=["Fondo de Cultura Económica"])

    @field_validator("isbn")
    @classmethod
    def _isbn_valido(cls, valor: str) -> str:
        return validar_isbn(valor)

    @field_validator("genero")
    @classmethod
    def _limpiar_generos(cls, valor: list[str]) -> list[str]:
        """Quita espacios, vacíos y repetidos (sin importar mayúsculas)."""
        limpios: list[str] = []
        for genero in valor:
            genero = genero.strip()
            if genero and genero.lower() not in {g.lower() for g in limpios}:
                limpios.append(genero)
        if not limpios:
            raise ValueError("Indica al menos un género")
        return limpios

    @field_validator("año_publicacion")
    @classmethod
    def _año_no_futuro(cls, valor: int) -> int:
        if valor > date.today().year:
            raise ValueError(f"El año no puede ser posterior a {date.today().year}")
        return valor

    def a_libro(self) -> Libro:
        """Convierte el esquema de entrada al modelo Libro que usa basedatos.py."""
        return Libro(**self.model_dump(), en_stock=self.cantidad_disponible > 0)


class LibroUpdate(_Entrada):
    """Campos que se pueden modificar (los mismos que acepta bd.actualizar_libro)."""

    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    precio: float | None = Field(default=None, ge=0, le=100_000)
    cantidad_disponible: int | None = Field(default=None, ge=0, le=10_000)
    editorial: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _al_menos_un_campo(self) -> Self:
        _hay_cambios(self)
        return self


# Para responder se usa directamente el modelo Libro de modelos.py:
# ya tiene exactamente los campos que queremos mostrar.
LibroRead = Libro


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
class UsuarioCreate(_Entrada):
    """Registro de un cliente (POST /auth/registro)."""

    nombre: str = Field(min_length=1, max_length=100, examples=["Ana López"])
    email: str = Field(max_length=255, examples=["ana@mail.com"])
    telefono: str | None = Field(default=None, examples=["55-1234-5678"])
    # min 8: contraseñas cortas se adivinan rápido. max 128: evita que alguien
    # mande megas de texto para hacer trabajar de más al hash (que es lento).
    password: str = Field(min_length=8, max_length=128, examples=["libros2026"])

    @field_validator("password", mode="before")
    @classmethod
    def _password_valida(cls, valor: object) -> object:
        return validar_password(valor)

    @field_validator("email")
    @classmethod
    def _email_valido(cls, valor: str) -> str:
        return validar_email(valor)

    @field_validator("telefono")
    @classmethod
    def _telefono_valido(cls, valor: str | None) -> str | None:
        return validar_telefono(valor)


class UsuarioAdminCreate(UsuarioCreate):
    """Alta hecha por un administrador (POST /usuarios/): puede elegir el rol."""

    rol: Rol = "cliente"


class UsuarioUpdate(_Entrada):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = None

    @field_validator("email")
    @classmethod
    def _email_valido(cls, valor: str | None) -> str | None:
        return None if valor is None else validar_email(valor)

    @field_validator("telefono")
    @classmethod
    def _telefono_valido(cls, valor: str | None) -> str | None:
        return validar_telefono(valor)

    @model_validator(mode="after")
    def _al_menos_un_campo(self) -> Self:
        _hay_cambios(self)
        return self


class UsuarioRead(_Salida):
    # Sin password ni password_hash: la respuesta nunca los incluye
    id: int
    nombre: str
    email: str
    telefono: str | None
    rol: Rol


class RolUpdate(_Entrada):
    rol: Rol


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class PasswordUpdate(BaseModel):
    # Hereda de BaseModel y no de _Entrada: las contraseñas no se recortan
    model_config = ConfigDict(extra="forbid")

    actual: str = Field(max_length=128)
    nueva: str = Field(min_length=8, max_length=128)

    @field_validator("nueva", mode="before")
    @classmethod
    def _nueva_valida(cls, valor: object) -> object:
        return validar_password(valor)

    @model_validator(mode="after")
    def _distinta(self) -> Self:
        if self.nueva == self.actual:
            raise ValueError("La contraseña nueva debe ser distinta de la actual")
        return self


class Token(BaseModel):
    """Respuesta de POST /auth/token. Los nombres los fija el estándar OAuth2."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expira_en: int = Field(description="Segundos de validez del token")


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------
class LineaPedidoCreate(_Entrada):
    isbn: str
    cantidad: int = Field(gt=0, le=50, examples=[2])

    @field_validator("isbn")
    @classmethod
    def _isbn_valido(cls, valor: str) -> str:
        return validar_isbn(valor)


class PedidoCreate(_Entrada):
    # Ya no hay usuario_id: el pedido es de quien está autenticado. Si viniera en
    # el cuerpo, cualquiera podría hacer pedidos a nombre de otro.
    items: list[LineaPedidoCreate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _sin_libros_repetidos(self) -> Self:
        """Validación entre elementos: un ISBN solo puede aparecer una vez."""
        vistos: set[str] = set()
        for item in self.items:
            if item.isbn in vistos:
                raise ValueError(f"El ISBN {item.isbn} aparece más de una vez; suma las cantidades")
            vistos.add(item.isbn)
        return self

    def como_lineas(self) -> dict[str, int]:
        """Formato que espera bd.crear_pedido: {isbn: cantidad}."""
        return {item.isbn: item.cantidad for item in self.items}


class EstatusUpdate(_Entrada):
    # "pendiente" no está: un pedido nunca regresa a pendiente
    estatus: Literal["pagado", "enviado", "cancelado"]


class PedidoItemRead(_Salida):
    isbn: str
    titulo: str
    cantidad: int
    precio_unitario: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def subtotal(self) -> float:
        return round(self.cantidad * self.precio_unitario, 2)


class PedidoRead(_Salida):
    id: int
    usuario_id: int
    fecha: datetime
    estatus: Estatus
    items: list[PedidoItemRead]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return round(sum(item.subtotal for item in self.items), 2)


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
class TotalUsuarioRead(BaseModel):
    nombre: str
    pedidos: int
    total: float


class LibroVendidoRead(BaseModel):
    titulo: str
    cantidad: int
