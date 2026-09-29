"""Pruebas de la API web (FastAPI) con TestClient y una base SQLite en memoria.

La clave es `app.dependency_overrides`: la dependencia obtener_sesion (que
abriría data/libreria.db) se reemplaza por una que usa la base de prueba.

Cada prueba arranca con dos usuarios: un admin y una clienta (Ana). Las
fixtures `admin` y `ana` devuelven los encabezados con su token.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from libreria import basedatos as bd
from libreria.almacenamiento import cargar_datos
from libreria.api import seguridad
from libreria.api.app import app
from libreria.api.dependencies import obtener_sesion

# Catálogo fijo, ver conftest.py
CATALOGO = Path(__file__).parent / "datos" / "catalogo_prueba.json"
CIEN_AÑOS = "978-607-07-1234-5"  # 12 ejemplares, $349.90
RAYUELA = "978-84-9793-563-2"  # 5 ejemplares, $399.50
ORWELL_1984 = "978-607-11-9876-3"  # 0 ejemplares

PASSWORD_ADMIN = "admin12345"
PASSWORD_ANA = "libros2026"
# Argon2 es lento a propósito: se calcula una vez para todo el archivo
HASH_ADMIN = seguridad.hashear_password(PASSWORD_ADMIN)
HASH_ANA = seguridad.hashear_password(PASSWORD_ANA)

Headers = dict[str, str]

LIBRO_NUEVO: dict[str, Any] = {
    "isbn": "978-607-16-0001-1",
    "titulo": "Pedro Páramo",
    "autor": {"nombre": "Juan Rulfo", "nacionalidad": "Mexicana"},
    "genero": ["Novela", "Realismo mágico"],
    "año_publicacion": 1955,
    "precio": 189.0,
    "cantidad_disponible": 10,
    "editorial": "Fondo de Cultura Económica",
}


# ---------------------------------------------------------------------------
# Fixtures y ayudantes
# ---------------------------------------------------------------------------
@pytest.fixture
def motor() -> Iterator[Engine]:
    # StaticPool + check_same_thread=False: todas las peticiones comparten la
    # MISMA conexión en memoria, aunque FastAPI las atienda en otros hilos.
    motor = bd.crear_motor(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    bd.crear_esquema(motor)
    with Session(motor) as s:
        bd.importar_catalogo(s, cargar_datos(CATALOGO))
        bd.crear_usuario(s, "Admin", "admin@libreria.mx", password_hash=HASH_ADMIN, rol="admin")
        bd.crear_usuario(s, "Ana", "ana@mail.com", password_hash=HASH_ANA)
    yield motor
    motor.dispose()


@pytest.fixture
def cliente(motor: Engine) -> Iterator[TestClient]:
    def sesion_de_prueba() -> Iterator[Session]:
        with Session(motor) as s:
            yield s

    app.dependency_overrides[obtener_sesion] = sesion_de_prueba
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def iniciar_sesion(cliente: TestClient, email: str, password: str) -> Headers:
    # OAuth2 pide un FORMULARIO (data=), no JSON, y el email va en "username"
    r = cliente.post("/auth/token", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def registrar(cliente: TestClient, email: str, nombre: str = "Beto") -> tuple[int, Headers]:
    """Registra un cliente nuevo y devuelve su id y sus encabezados con token."""
    r = cliente.post(
        "/auth/registro", json={"nombre": nombre, "email": email, "password": "clave1234"}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"]), iniciar_sesion(cliente, email, "clave1234")


def token_de(usuario_id: int, **cambios: Any) -> Headers:
    """Encabezado con un token fabricado a mano (para probar tokens inválidos)."""
    return {"Authorization": f"Bearer {seguridad.crear_token(usuario_id, **cambios)}"}


@pytest.fixture
def admin(cliente: TestClient) -> Headers:
    return iniciar_sesion(cliente, "admin@libreria.mx", PASSWORD_ADMIN)


@pytest.fixture
def ana(cliente: TestClient) -> Headers:
    return iniciar_sesion(cliente, "ana@mail.com", PASSWORD_ANA)


def id_de(cliente: TestClient, headers: Headers) -> int:
    return int(cliente.get("/auth/yo", headers=headers).json()["id"])


def pedir(cliente: TestClient, headers: Headers, isbn: str, cantidad: int) -> dict[str, Any]:
    r = cliente.post(
        "/pedidos/", json={"items": [{"isbn": isbn, "cantidad": cantidad}]}, headers=headers
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


# ---------------------------------------------------------------------------
# Registro, login y tokens
# ---------------------------------------------------------------------------
class TestAutenticacion:
    def test_registro(self, cliente: TestClient) -> None:
        r = cliente.post(
            "/auth/registro",
            json={"nombre": "Beto", "email": " Beto@Mail.com ", "password": "clave1234"},
        )
        assert r.status_code == 201
        datos = r.json()
        assert (datos["email"], datos["rol"]) == ("beto@mail.com", "cliente")
        assert "password" not in datos and "password_hash" not in datos

    def test_registro_no_permite_elegir_rol(self, cliente: TestClient) -> None:
        cuerpo = {"nombre": "X", "email": "x@mail.com", "password": "clave1234", "rol": "admin"}
        assert cliente.post("/auth/registro", json=cuerpo).status_code == 422

    @pytest.mark.parametrize(
        "password",
        ["corta1", "solo-letras", "12345678", " conespacio1", "conespacio1 ", "x1" * 70],
    )
    def test_password_debil(self, cliente: TestClient, password: str) -> None:
        cuerpo = {"nombre": "X", "email": "x@mail.com", "password": password}
        assert cliente.post("/auth/registro", json=cuerpo).status_code == 422

    def test_registro_email_repetido(self, cliente: TestClient) -> None:
        cuerpo = {"nombre": "Otra", "email": "ANA@mail.com", "password": "clave1234"}
        assert cliente.post("/auth/registro", json=cuerpo).status_code == 409

    def test_login(self, cliente: TestClient) -> None:
        r = cliente.post("/auth/token", data={"username": "ana@mail.com", "password": PASSWORD_ANA})
        assert r.status_code == 200
        assert r.json()["token_type"] == "bearer"
        assert r.json()["expira_en"] == seguridad.MINUTOS_VALIDEZ * 60

    @pytest.mark.parametrize(
        ("email", "password"),
        [("ana@mail.com", "incorrecta1"), ("nadie@mail.com", PASSWORD_ANA)],
    )
    def test_login_fallido(self, cliente: TestClient, email: str, password: str) -> None:
        r = cliente.post("/auth/token", data={"username": email, "password": password})
        assert r.status_code == 401
        # mismo mensaje en los dos casos: no revela si el email existe
        assert r.json()["detail"] == "Email o contraseña incorrectos"

    def test_usuario_sin_password_no_puede_entrar(self, cliente: TestClient, motor: Engine) -> None:
        # como los usuarios que ya existían antes de la migración 0003
        with Session(motor) as s:
            bd.crear_usuario(s, "Viejo", "viejo@mail.com")
        datos = {"username": "viejo@mail.com", "password": "cualquiera1"}
        assert cliente.post("/auth/token", data=datos).status_code == 401

    def test_yo(self, cliente: TestClient, ana: Headers) -> None:
        r = cliente.get("/auth/yo", headers=ana)
        assert (r.json()["nombre"], r.json()["rol"]) == ("Ana", "cliente")

    def test_sin_token(self, cliente: TestClient) -> None:
        r = cliente.get("/auth/yo")
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"] == "Bearer"

    def test_token_caducado(self, cliente: TestClient, ana: Headers) -> None:
        r = cliente.get("/auth/yo", headers=token_de(id_de(cliente, ana), minutos=-1))
        assert r.status_code == 401
        assert "caducó" in r.json()["detail"]

    def test_token_alterado(self, cliente: TestClient, ana: Headers) -> None:
        cabecera, payload, firma = ana["Authorization"].removeprefix("Bearer ").split(".")
        otra_firma = ("A" if firma[0] != "A" else "B") + firma[1:]
        falso = {"Authorization": f"Bearer {cabecera}.{payload}.{otra_firma}"}
        assert cliente.get("/auth/yo", headers=falso).status_code == 401

    def test_token_firmado_con_otro_secreto(self, cliente: TestClient) -> None:
        # alguien intenta fabricar un token del admin (id 1) sin conocer el secreto
        falso = jwt.encode({"sub": "1", "exp": 9999999999}, "x" * 40, algorithm="HS256")
        r = cliente.get("/auth/yo", headers={"Authorization": f"Bearer {falso}"})
        assert r.status_code == 401

    def test_token_de_usuario_borrado(self, cliente: TestClient) -> None:
        beto_id, beto = registrar(cliente, "beto@mail.com")
        assert cliente.delete(f"/usuarios/{beto_id}", headers=beto).status_code == 204
        assert cliente.get("/auth/yo", headers=beto).status_code == 401

    def test_cambiar_password(self, cliente: TestClient, ana: Headers) -> None:
        incorrecta = {"actual": "no-es-esta1", "nueva": "nueva12345"}
        assert cliente.put("/auth/password", json=incorrecta, headers=ana).status_code == 400

        cambio = {"actual": PASSWORD_ANA, "nueva": "nueva12345"}
        assert cliente.put("/auth/password", json=cambio, headers=ana).status_code == 204

        viejo = {"username": "ana@mail.com", "password": PASSWORD_ANA}
        assert cliente.post("/auth/token", data=viejo).status_code == 401
        iniciar_sesion(cliente, "ana@mail.com", "nueva12345")

    def test_password_nueva_igual_a_la_actual(self, cliente: TestClient, ana: Headers) -> None:
        cambio = {"actual": PASSWORD_ANA, "nueva": PASSWORD_ANA}
        assert cliente.put("/auth/password", json=cambio, headers=ana).status_code == 422


# ---------------------------------------------------------------------------
# Libros
# ---------------------------------------------------------------------------
class TestLibros:
    def test_consultar_es_publico(self, cliente: TestClient) -> None:
        assert cliente.get("/libros/").status_code == 200
        assert cliente.get(f"/libros/{RAYUELA}").json()["titulo"] == "Rayuela"

    def test_modificar_requiere_admin(self, cliente: TestClient, ana: Headers) -> None:
        assert cliente.post("/libros/", json=LIBRO_NUEVO).status_code == 401
        assert cliente.post("/libros/", json=LIBRO_NUEVO, headers=ana).status_code == 403
        r = cliente.patch(f"/libros/{RAYUELA}", json={"precio": 1}, headers=ana)
        assert r.status_code == 403
        assert cliente.delete(f"/libros/{RAYUELA}", headers=ana).status_code == 403

    def test_listar_con_paginacion(self, cliente: TestClient) -> None:
        todos = cliente.get("/libros/").json()
        pagina = cliente.get("/libros/", params={"saltar": 1, "limite": 3}).json()
        assert len(pagina) == 3
        assert pagina == todos[1:4]

    @pytest.mark.parametrize("params", [{"limite": 0}, {"limite": 101}, {"saltar": -1}])
    def test_paginacion_invalida(self, cliente: TestClient, params: dict[str, int]) -> None:
        assert cliente.get("/libros/", params=params).status_code == 422

    def test_filtros(self, cliente: TestClient) -> None:
        r = cliente.get("/libros/", params={"autor": "cortázar"})
        assert [lb["isbn"] for lb in r.json()] == [RAYUELA]

        sin_stock = cliente.get("/libros/", params={"en_stock": False}).json()
        assert ORWELL_1984 in {lb["isbn"] for lb in sin_stock}
        assert all(not lb["en_stock"] for lb in sin_stock)

    def test_obtener_inexistente(self, cliente: TestClient) -> None:
        r = cliente.get("/libros/978-000-00-0000-0")
        assert r.status_code == 404
        assert "No existe" in r.json()["detail"]

    def test_crear(self, cliente: TestClient, admin: Headers) -> None:
        r = cliente.post("/libros/", json=LIBRO_NUEVO, headers=admin)
        assert r.status_code == 201, r.text
        assert r.json()["en_stock"] is True  # se calculó a partir de la cantidad
        assert cliente.get(f"/libros/{LIBRO_NUEVO['isbn']}").status_code == 200

    def test_crear_repetido(self, cliente: TestClient, admin: Headers) -> None:
        r = cliente.post("/libros/", json={**LIBRO_NUEVO, "isbn": RAYUELA}, headers=admin)
        assert r.status_code == 409

    @pytest.mark.parametrize(
        "cambio",
        [
            {"isbn": "123"},  # formato de ISBN
            {"año_publicacion": 3000},  # año futuro (field_validator)
            {"genero": ["  "]},  # sin géneros útiles
            {"precio": -5},  # Field(ge=0)
            {"titulo": ""},  # Field(min_length=1)
            {"precoi": 10},  # campo desconocido (extra="forbid")
        ],
    )
    def test_crear_invalido(
        self, cliente: TestClient, admin: Headers, cambio: dict[str, Any]
    ) -> None:
        r = cliente.post("/libros/", json={**LIBRO_NUEVO, **cambio}, headers=admin)
        assert r.status_code == 422
        assert cliente.get(f"/libros/{LIBRO_NUEVO['isbn']}").status_code == 404

    def test_actualizar(self, cliente: TestClient, admin: Headers) -> None:
        cambios = {"precio": 420, "cantidad_disponible": 0}
        r = cliente.patch(f"/libros/{RAYUELA}", json=cambios, headers=admin)
        assert r.status_code == 200
        assert (r.json()["precio"], r.json()["en_stock"]) == (420, False)
        assert r.json()["titulo"] == "Rayuela"  # no cambió

    @pytest.mark.parametrize("cuerpo", [{}, {"precio": -1}, {"isbn": "otro"}])
    def test_actualizar_invalido(
        self, cliente: TestClient, admin: Headers, cuerpo: dict[str, Any]
    ) -> None:
        assert cliente.patch(f"/libros/{RAYUELA}", json=cuerpo, headers=admin).status_code == 422

    def test_eliminar(self, cliente: TestClient, admin: Headers) -> None:
        cliente.post("/libros/", json=LIBRO_NUEVO, headers=admin)
        assert cliente.delete(f"/libros/{LIBRO_NUEVO['isbn']}", headers=admin).status_code == 204
        assert cliente.get(f"/libros/{LIBRO_NUEVO['isbn']}").status_code == 404

    def test_no_se_elimina_libro_vendido(
        self, cliente: TestClient, admin: Headers, ana: Headers
    ) -> None:
        pedir(cliente, ana, RAYUELA, 1)
        assert cliente.delete(f"/libros/{RAYUELA}", headers=admin).status_code == 409


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
class TestUsuarios:
    def test_listar_solo_admin(self, cliente: TestClient, admin: Headers, ana: Headers) -> None:
        assert cliente.get("/usuarios/").status_code == 401
        assert cliente.get("/usuarios/", headers=ana).status_code == 403
        nombres = [u["nombre"] for u in cliente.get("/usuarios/", headers=admin).json()]
        assert nombres == ["Admin", "Ana"]

    def test_admin_crea_usuario_con_rol(self, cliente: TestClient, admin: Headers) -> None:
        cuerpo = {"nombre": "Eva", "email": "eva@mail.com", "password": "clave1234"}
        r = cliente.post("/usuarios/", json={**cuerpo, "rol": "admin"}, headers=admin)
        assert r.status_code == 201
        assert r.json()["rol"] == "admin"
        iniciar_sesion(cliente, "eva@mail.com", "clave1234")

    def test_cliente_no_crea_usuarios(self, cliente: TestClient, ana: Headers) -> None:
        cuerpo = {"nombre": "Eva", "email": "eva@mail.com", "password": "clave1234"}
        assert cliente.post("/usuarios/", json=cuerpo, headers=ana).status_code == 403

    @pytest.mark.parametrize(
        "cuerpo",
        [
            {"nombre": "X", "email": "sin-arroba", "password": "clave1234"},
            {"nombre": "", "email": "x@mail.com", "password": "clave1234"},
            {"nombre": "X", "email": "x@mail.com", "password": "clave1234", "telefono": "12"},
            {"nombre": "X", "email": "x@mail.com"},  # falta la contraseña
        ],
    )
    def test_crear_invalido(
        self, cliente: TestClient, admin: Headers, cuerpo: dict[str, Any]
    ) -> None:
        assert cliente.post("/usuarios/", json=cuerpo, headers=admin).status_code == 422

    def test_ver_y_modificar_la_cuenta_propia(self, cliente: TestClient, ana: Headers) -> None:
        ana_id = id_de(cliente, ana)
        assert cliente.get(f"/usuarios/{ana_id}", headers=ana).status_code == 200

        r = cliente.patch(f"/usuarios/{ana_id}", json={"telefono": "55-1234-5678"}, headers=ana)
        assert r.status_code == 200
        assert (r.json()["telefono"], r.json()["rol"]) == ("55-1234-5678", "cliente")

    def test_no_ve_ni_modifica_cuentas_ajenas(self, cliente: TestClient, ana: Headers) -> None:
        beto_id, _ = registrar(cliente, "beto@mail.com")
        assert cliente.get(f"/usuarios/{beto_id}", headers=ana).status_code == 403
        r = cliente.patch(f"/usuarios/{beto_id}", json={"nombre": "Hackeado"}, headers=ana)
        assert r.status_code == 403
        assert cliente.delete(f"/usuarios/{beto_id}", headers=ana).status_code == 403
        assert cliente.get(f"/usuarios/{beto_id}/pedidos", headers=ana).status_code == 403

    def test_admin_ve_cualquier_cuenta(
        self, cliente: TestClient, admin: Headers, ana: Headers
    ) -> None:
        assert cliente.get(f"/usuarios/{id_de(cliente, ana)}", headers=admin).status_code == 200

    def test_email_de_otro(self, cliente: TestClient, ana: Headers) -> None:
        cambio = {"email": "admin@libreria.mx"}
        r = cliente.patch(f"/usuarios/{id_de(cliente, ana)}", json=cambio, headers=ana)
        assert r.status_code == 409

    def test_id_invalido_e_inexistente(self, cliente: TestClient, admin: Headers) -> None:
        assert cliente.get("/usuarios/0", headers=admin).status_code == 422  # Path(gt=0)
        assert cliente.get("/usuarios/999", headers=admin).status_code == 404

    def test_sin_token_401_antes_que_404(self, cliente: TestClient) -> None:
        # usuario_autorizado pide primero el token: sin él no se revela si el id existe
        assert cliente.get("/usuarios/999").status_code == 401

    def test_cambiar_rol(self, cliente: TestClient, admin: Headers, ana: Headers) -> None:
        ana_id = id_de(cliente, ana)
        url = f"/usuarios/{ana_id}/rol"
        assert cliente.put(url, json={"rol": "admin"}, headers=ana).status_code == 403

        r = cliente.put(url, json={"rol": "admin"}, headers=admin)
        assert r.json()["rol"] == "admin"
        # El MISMO token de Ana ya sirve como admin: el rol se lee de la base
        assert cliente.get("/usuarios/", headers=ana).status_code == 200

    def test_admin_no_se_quita_su_rol(self, cliente: TestClient, admin: Headers) -> None:
        admin_id = id_de(cliente, admin)
        r = cliente.put(f"/usuarios/{admin_id}/rol", json={"rol": "cliente"}, headers=admin)
        assert r.status_code == 409

    def test_no_se_elimina_usuario_con_pedidos(self, cliente: TestClient, ana: Headers) -> None:
        pedir(cliente, ana, RAYUELA, 1)
        assert cliente.delete(f"/usuarios/{id_de(cliente, ana)}", headers=ana).status_code == 409


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------
class TestPedidos:
    def test_requiere_sesion(self, cliente: TestClient) -> None:
        linea = {"items": [{"isbn": RAYUELA, "cantidad": 1}]}
        assert cliente.post("/pedidos/", json=linea).status_code == 401

    def test_crear_a_nombre_propio(self, cliente: TestClient, ana: Headers) -> None:
        items = [{"isbn": CIEN_AÑOS, "cantidad": 2}, {"isbn": RAYUELA, "cantidad": 1}]
        r = cliente.post("/pedidos/", json={"items": items}, headers=ana)
        assert r.status_code == 201, r.text
        pedido = r.json()
        assert pedido["usuario_id"] == id_de(cliente, ana)  # salió del token
        assert pedido["estatus"] == "pendiente"
        assert pedido["total"] == round(2 * 349.90 + 399.50, 2)  # campo calculado
        assert cliente.get(f"/libros/{RAYUELA}").json()["cantidad_disponible"] == 4

    def test_no_acepta_usuario_id_en_el_cuerpo(self, cliente: TestClient, ana: Headers) -> None:
        cuerpo = {"usuario_id": 1, "items": [{"isbn": RAYUELA, "cantidad": 1}]}
        assert cliente.post("/pedidos/", json=cuerpo, headers=ana).status_code == 422

    def test_libro_repetido(self, cliente: TestClient, ana: Headers) -> None:
        items = [{"isbn": RAYUELA, "cantidad": 1}, {"isbn": RAYUELA, "cantidad": 2}]
        r = cliente.post("/pedidos/", json={"items": items}, headers=ana)
        assert r.status_code == 422  # model_validator

    def test_sin_stock_no_cambia_nada(
        self, cliente: TestClient, admin: Headers, ana: Headers
    ) -> None:
        items = [{"isbn": CIEN_AÑOS, "cantidad": 1}, {"isbn": RAYUELA, "cantidad": 50}]
        assert cliente.post("/pedidos/", json={"items": items}, headers=ana).status_code == 409
        assert cliente.get(f"/libros/{CIEN_AÑOS}").json()["cantidad_disponible"] == 12
        assert cliente.get("/pedidos/", headers=admin).json() == []

    def test_libro_inexistente(self, cliente: TestClient, ana: Headers) -> None:
        items = [{"isbn": "978-000-00-0000-0", "cantidad": 1}]
        assert cliente.post("/pedidos/", json={"items": items}, headers=ana).status_code == 422

    def test_ver_pedido(self, cliente: TestClient, admin: Headers, ana: Headers) -> None:
        pedido = pedir(cliente, ana, RAYUELA, 1)
        _, beto = registrar(cliente, "beto@mail.com")
        url = f"/pedidos/{pedido['id']}"

        assert cliente.get(url, headers=ana).status_code == 200  # dueña
        assert cliente.get(url, headers=admin).status_code == 200  # admin
        assert cliente.get(url, headers=beto).status_code == 403  # otro cliente

    def test_listar_todos_solo_admin(
        self, cliente: TestClient, admin: Headers, ana: Headers
    ) -> None:
        pedir(cliente, ana, RAYUELA, 1)
        assert cliente.get("/pedidos/", headers=ana).status_code == 403
        assert len(cliente.get("/pedidos/", headers=admin).json()) == 1
        mis_pedidos = cliente.get(f"/usuarios/{id_de(cliente, ana)}/pedidos", headers=ana)
        assert len(mis_pedidos.json()) == 1

    def test_estatus_por_rol(self, cliente: TestClient, admin: Headers, ana: Headers) -> None:
        pedido = pedir(cliente, ana, RAYUELA, 2)
        url = f"/pedidos/{pedido['id']}/estatus"

        # la clienta no puede marcar su pedido como pagado...
        assert cliente.patch(url, json={"estatus": "pagado"}, headers=ana).status_code == 403
        # ...el admin sí
        assert cliente.patch(url, json={"estatus": "enviado"}, headers=admin).status_code == 409
        assert cliente.patch(url, json={"estatus": "pendiente"}, headers=admin).status_code == 422
        r = cliente.patch(url, json={"estatus": "pagado"}, headers=admin)
        assert r.json()["estatus"] == "pagado"

        # y la clienta sí puede cancelar lo suyo
        r = cliente.patch(url, json={"estatus": "cancelado"}, headers=ana)
        assert r.json()["estatus"] == "cancelado"
        assert cliente.get(f"/libros/{RAYUELA}").json()["cantidad_disponible"] == 5

    def test_filtrar_por_estatus(self, cliente: TestClient, admin: Headers) -> None:
        for estatus, codigo in [("pagado", 200), ("perdido", 422)]:
            r = cliente.get("/pedidos/", params={"estatus": estatus}, headers=admin)
            assert r.status_code == codigo


# ---------------------------------------------------------------------------
# Reportes (dependencia a nivel router)
# ---------------------------------------------------------------------------
class TestReportes:
    def test_requiere_admin(self, cliente: TestClient, ana: Headers) -> None:
        assert cliente.get("/reportes/usuarios").status_code == 401
        assert cliente.get("/reportes/usuarios", headers=ana).status_code == 403

    def test_con_admin(self, cliente: TestClient, admin: Headers, ana: Headers) -> None:
        pedir(cliente, ana, RAYUELA, 2)

        usuarios = cliente.get("/reportes/usuarios", headers=admin).json()
        assert usuarios == [{"nombre": "Ana", "pedidos": 1, "total": 799.0}]

        vendidos = cliente.get("/reportes/mas-vendidos", headers=admin, params={"limite": 1})
        assert vendidos.json() == [{"titulo": "Rayuela", "cantidad": 2}]


def test_openapi_documenta_todo(cliente: TestClient) -> None:
    esquema = cliente.get("/openapi.json").json()
    rutas = set(esquema["paths"])
    assert {"/auth/token", "/libros/", "/usuarios/{usuario_id}", "/pedidos/"} <= rutas
    # el esquema de seguridad que usa el botón "Authorize" de /docs
    assert "OAuth2PasswordBearer" in esquema["components"]["securitySchemes"]
