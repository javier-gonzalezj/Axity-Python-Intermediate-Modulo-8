"""Crea el primer administrador, o le da rol de admin y contraseña a un usuario.

Resuelve el problema del "huevo y la gallina": para dar de alta admins por la
API hay que ser admin. Se corre desde la raíz del proyecto:

    poetry run python -m libreria.api.crear_admin

También sirve para asignar contraseña a los usuarios creados antes de la
migración 0003 (que quedaron sin contraseña).
"""

from getpass import getpass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.api.dependencies import obtener_motor
from libreria.api.schemas import UsuarioCreate
from libreria.api.seguridad import hashear_password


def _pedir_password() -> str | None:
    password = getpass("Contraseña (no se ve al escribir): ")
    if password != getpass("Repite la contraseña: "):
        print("❌ Las contraseñas no coinciden")
        return None
    return password


def main() -> None:
    email = input("Email del administrador: ").strip().lower()

    with Session(obtener_motor()) as sesion:
        existente = bd.buscar_usuario_por_email(sesion, email)
        nombre = existente.nombre if existente else input("Nombre: ").strip()

        password = _pedir_password()
        if password is None:
            return
        try:
            # Se reutiliza el esquema de la API: mismas reglas para email y contraseña
            datos = UsuarioCreate(nombre=nombre, email=email, password=password)
        except ValidationError as e:
            for error in e.errors():
                print(f"❌ {error['loc'][0]}: {error['msg']}")
            return

        password_hash = hashear_password(datos.password)
        if existente is not None and existente.id is not None:
            bd.cambiar_password_hash(sesion, existente.id, password_hash)
            bd.cambiar_rol(sesion, existente.id, "admin")
            print(f"✅ {existente.nombre} ahora es administrador y tiene contraseña nueva")
        else:
            bd.crear_usuario(
                sesion, datos.nombre, datos.email, password_hash=password_hash, rol="admin"
            )
            print(f"✅ Administrador {datos.nombre} creado")


if __name__ == "__main__":
    main()
