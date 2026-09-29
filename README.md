# Proyecto: Librería

Programa de consola para administrar el catálogo de una librería: consultar, filtrar, agregar e importar libros, guardados en un archivo JSON.

Para los objetivos del nivel intermedio se toma el código base del último módulo del nivel básico.

Proyecto del curso **Axity Python Intermediate**.

## Créditos

- Autor: [Javier González](https://github.com/javier-gonzalezj)
- Datos de libros y portadas: [Open Library](https://openlibrary.org), un proyecto de Internet Archive.

## Características

- **Catálogo paginado** con los datos de la librería, el total de libros y el valor del inventario.
- **Filtros** por autor, género, disponibilidad, precio máximo y año mínimo, con opción de exportar el resultado a JSON.
- **Captura de libros por consola** con autocompletado por ISBN: el programa consulta [Open Library](https://openlibrary.org) y sugiere título, autor, géneros, año y editorial (Enter acepta la sugerencia).
- **Descarga de portadas** por streaming a `data/portadas/<isbn>.jpg`, sin dejar archivos a medias si la conexión se corta.
- **Descarga concurrente de portadas** con `asyncio`, `httpx.AsyncClient` y un semáforo: baja las portadas faltantes de todo el catálogo, o las de los libros recién importados, varias a la vez.
- **Importación desde CSV** que reporta las filas con errores sin detener la carga.
- **Validación de datos** con modelos de [pydantic](https://docs.pydantic.dev).
- **Guardado seguro**: el catálogo se escribe en un archivo temporal y solo reemplaza al original si todo salió bien.
- **Registro (logging)** detallado en `logs/libreria.log`.
- **Base de datos con SQLAlchemy y migraciones con Alembic**: el catálogo, los usuarios y los pedidos viven en una base de datos. Las tablas se versionan en `migraciones/` (0001 esquema inicial, 0002 teléfono de usuario y la nueva 0003 autenticación).
- **API web con FastAPI** en `src/libreria/api/`: endpoints para libros, usuarios, pedidos y reportes, con documentación interactiva en `/docs`.
- **Autenticación con JWT**: registro e inicio de sesión (`/auth`), roles `cliente` y `admin`, y contraseñas guardadas como hash Argon2 (pwdlib).
- **Script para crear administradores**: `poetry run python -m libreria.api.crear_admin`.
- **Predicción de best sellers** (`prediccion.py`): clasificador de scikit-learn que estima la probabilidad de que un libro se venda mucho, entrenado con ventas sintéticas (`datos_sinteticos.py`).
- **Pruebas**: nuevas pruebas de la API (`tests/test_api.py`) y de las migraciones.

Para ponerla en marcha:

```bash
poetry install
poetry run alembic upgrade head
poetry run python -m libreria.api.crear_admin
poetry run fastapi dev src/libreria/api/app.py
```

## Requisitos

- Python 3.13 o superior
- [Poetry](https://python-poetry.org) 2.x
- Conexión a internet (opcional: solo para el autocompletado por ISBN y las portadas; sin ella, la captura es manual)

## Instalación

```bash
git clone https://github.com/javier-gonzalezj/Axity-Python-Intermediate-Modulo-6
cd Axity-Python-Intermediate-Modulo-6
poetry install
```

Para activar las revisiones automáticas antes de cada commit:

```bash
poetry run pre-commit install
```

## Uso

```bash
poetry run libreria
```

Al iniciar se muestra el catálogo y después el menú:

```
═════════════ MENU ═════════════
  1. Filtrar libros
  2. Cargar archivo CSV
  3. Agregar un libro
  4. Ver catalogo completo
  5. Descargar portadas faltantes
  0. Salir
════════════════════════════════
```

### Agregar un libro (opción 3)

Al escribir el ISBN, el programa lo busca en Open Library y muestra los datos encontrados entre corchetes:

```
ISBN: 9780307474728
  🔎 Buscando el ISBN en Open Library...
  ✅ Encontrado: Cien años de soledad — Gabriel García Márquez
     Presiona Enter para aceptar el valor entre [corchetes] o escribe otro.
Título [Cien años de soledad]:
Nombre del autor [Gabriel García Márquez]:
Nacionalidad del autor: Colombiana
```

El precio, la cantidad disponible y la nacionalidad del autor siempre se capturan a mano. Si el libro no está en Open Library o no hay conexión, todos los campos se capturan manualmente.

> Open Library tiene menos libros en español, así que varios ISBN de editoriales mexicanas no aparecen. Los géneros que sugiere vienen en inglés.

### Base de datos (usuarios y pedidos)

Además del catálogo en JSON, el proyecto puede guardar libros, usuarios y pedidos en `data/libreria.db` (SQLite):

```
usuarios 1 ──< pedidos 1 ──< pedido_items >── 1 libros
```

Para crear la base y copiar el catálogo del JSON:

```bash
poetry run alembic upgrade head               # crea las tablas (aplica las migraciones)
poetry run python -m libreria.basedatos    # importa data/libreria.json a la base
```

Cada línea de pedido guarda el precio al momento de la compra, y crear un pedido es una sola transacción: si algún libro no tiene stock, no se guarda nada. Cancelar un pedido no lo borra: cambia su estatus y regresa los libros al inventario.

#### Operaciones (CRUD) de `basedatos.py`

| Entidad | Crear | Leer | Actualizar | Eliminar |
|---|---|---|---|---|
| Libro | `guardar_libro`, `importar_catalogo` | `obtener_libro`, `listar_libros` | `actualizar_libro` | `eliminar_libro` (solo si nunca se vendió) |
| Usuario | `crear_usuario` | `obtener_usuario`, `buscar_usuario_por_email`, `listar_usuarios` | `actualizar_usuario` | `eliminar_usuario` (solo si no tiene pedidos) |
| Pedido | `crear_pedido` | `obtener_pedido`, `pedidos_de_usuario`, `listar_pedidos` | `cambiar_estatus` | `cancelar_pedido` (no borra: cambia el estatus) |

Un pedido avanza `pendiente → pagado → enviado`, y solo se puede cancelar antes de enviarse.

#### Migraciones con Alembic

Las clases `*DB` de `basedatos.py` definen las tablas. Cuando cambies alguna, genera una migración, revísala y aplícala:

```bash
poetry run alembic revision --autogenerate --rev-id 0003 -m "descripcion del cambio"
poetry run alembic upgrade head
```

| Comando | Qué hace |
|---|---|
| `alembic current` | Versión actual de la base |
| `alembic history` | Lista de migraciones |
| `alembic downgrade -1` | Revierte la última migración |
| `alembic check` | Verifica que los modelos y las migraciones coincidan |

> `--autogenerate` no detecta que renombraste una columna: lo interpreta como borrar una y crear otra. Revisa siempre el archivo generado antes de aplicarlo.

### API web (FastAPI)

Las mismas operaciones de `basedatos.py` también están disponibles como API REST en `src/libreria/api/`. Para levantarla (después de `alembic upgrade head`):

```bash
poetry run fastapi dev src/libreria/api/app.py
```

y abre <http://127.0.0.1:8000/docs> para ver y probar todos los endpoints.

Antes de usarla por primera vez, crea un administrador (pide email, nombre y contraseña):

```bash
poetry run python -m libreria.api.crear_admin
```

El mismo comando sirve para darle contraseña a un usuario que ya existía antes de la migración 0003.

| Recurso | Endpoints | Quién puede |
|---|---|---|
| Autenticación | `POST /auth/registro`, `POST /auth/token` | Cualquiera |
| | `GET /auth/yo`, `PUT /auth/password` | Con sesión iniciada |
| Libros | `GET /libros/` (filtros `autor`, `genero`, `en_stock`, `precio_max`), `GET /libros/{isbn}` | Cualquiera |
| | `POST /libros/`, `PATCH/DELETE /libros/{isbn}` | Admin |
| Usuarios | `GET /usuarios/`, `POST /usuarios/`, `PUT /usuarios/{id}/rol` | Admin |
| | `GET/PATCH/DELETE /usuarios/{id}`, `GET /usuarios/{id}/pedidos` | El propio usuario o un admin |
| Pedidos | `POST /pedidos/` (queda a nombre de quien inició sesión) | Con sesión iniciada |
| | `GET /pedidos/{id}`, cancelar con `PATCH /pedidos/{id}/estatus` | El dueño o un admin |
| | `GET /pedidos/?estatus=`, marcar `pagado`/`enviado` | Admin |
| Reportes | `GET /reportes/usuarios`, `GET /reportes/mas-vendidos` | Admin |

Los listados aceptan `?saltar=` y `?limite=` (máximo 100).

#### Autenticación con JWT

1. `POST /auth/token` con un **formulario** (no JSON) con `username` (el email) y `password`. Responde un `access_token`.
2. Las demás peticiones mandan el encabezado `Authorization: Bearer <access_token>`.
3. En `/docs`, el botón **Authorize** hace estos dos pasos por ti.

El token solo guarda el id del usuario y su caducidad; el rol se lee de la base en cada petición, así que un cambio de rol surte efecto sin volver a iniciar sesión. Las contraseñas se guardan como hash Argon2, nunca en texto plano.

| Variable de entorno | Para qué | Por defecto |
|---|---|---|
| `LIBRERIA_JWT_SECRETO` | Clave para firmar los tokens (mínimo 32 caracteres) | Una al azar en cada arranque: los tokens dejan de servir al reiniciar |
| `LIBRERIA_JWT_MINUTOS` | Minutos de validez de cada token | 30 |

Para generar un secreto: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. No lo subas a git.

Cómo está organizada:

- **`routers/`**: un `APIRouter` por recurso, cada uno con su `prefix` y `tags`.
- **`dependencies.py`**: la sesión de base de datos (una por petición, con `yield`), la búsqueda de un libro/usuario/pedido que responde 404 si no existe, la paginación y la autenticación: `usuario_actual` (lee el token; 401), `requiere_admin` (403) y `usuario_autorizado`/`pedido_autorizado` (uno mismo o admin). Se aplican en tres niveles: como parámetro del endpoint, en el decorador (`dependencies=[...]`, libros) o en todo un router (pedidos y reportes).
- **`seguridad.py`**: hash de contraseñas (pwdlib + Argon2) y creación y lectura de tokens (PyJWT). No depende de FastAPI.
- **`schemas.py`**: esquemas pydantic de entrada (`Create`/`Update`) y salida (`Read`) con sus validaciones: formato de ISBN, año no futuro, géneros sin repetir, email en minúsculas, teléfono, al menos un campo en los `PATCH`, sin ISBN repetidos en un pedido, contraseñas de 8 a 128 caracteres con letras y números y sin espacios al inicio o al final.
- **`app.py`**: crea la app, incluye los routers y traduce las excepciones de `basedatos.py` a códigos HTTP (404 no existe, 409 conflicto, 422 datos inválidos).

### Importar desde CSV (opción 2)

El archivo debe estar en UTF-8 (en Excel: *Guardar como → CSV UTF-8*) y tener estas columnas:

| Columna | Obligatoria | Notas |
|---|---|---|
| `isbn` | Sí | No puede repetirse en el catálogo |
| `titulo` | Sí | |
| `autor` | Sí | Nombre del autor |
| `nacionalidad_autor` | No | Vacía por defecto |
| `genero` | Sí | Varios géneros separados por `;` |
| `año_publicacion` | Sí | Número entero |
| `precio` | Sí | Número, sin signo `$` |
| `cantidad_disponible` | Sí | Número entero |
| `editorial` | Sí | |
| `en_stock` | No | `sí`/`no`; si falta, se calcula con la cantidad |

Hay un ejemplo en [`data/ejemplos/libros_nuevos.csv`](data/ejemplos/libros_nuevos.csv).

Al terminar, el programa pregunta si quieres descargar de Open Library las portadas de los libros agregados (se descargan igual que en la opción 5).

### Descargar portadas faltantes (opción 5)

Busca los libros del catálogo que todavía no tienen su archivo en `data/portadas/` y descarga sus portadas **en paralelo**:

```
🖼️  Descargando 12 portada(s) (5 a la vez)... puede tardar un poco.

✅ 9 portada(s) descargada(s) en: data/portadas
ℹ️  2 libro(s) sin portada en Open Library:
   - 978-607-16-0001-1
   - 978-607-16-0002-8
⚠️  1 portada(s) no se pudieron descargar:
   - 978-0-14-044913-6: No se pudo descargar la portada: ...
```

Cómo funciona (`descargar_portadas()` en `buscador.py`):

- Cada ISBN es una corrutina que hace dos peticiones: la edición en `openlibrary.org` (para obtener el id de la portada) y la imagen en `covers.openlibrary.org`, por streaming y con archivo `.part` como en la opción 3.
- Todas las corrutinas comparten un `httpx.AsyncClient` por servidor, así que reutilizan las conexiones.
- Un `asyncio.Semaphore` deja pasar como máximo `MAX_DESCARGAS_SIMULTANEAS` (5) ISBN a la vez, para no saturar a Open Library.
- Se ejecutan con `asyncio.gather(..., return_exceptions=True)`: si un ISBN falla, los demás siguen y el error queda en el resumen. Un `TaskGroup` cancelaría todo al primer error.
- Los errores de red pasajeros se reintentan con `@reintentar_async` (en `utilidades.py`), que espera con `await asyncio.sleep()` para no congelar el event loop.
- El menú es síncrono, así que `main.py` lo ejecuta con `asyncio.run()`.

### Predicción de best sellers (machine learning)

Clasificador que estima la probabilidad de que un libro sea **best seller**. Como la base real todavía no tiene pedidos, se entrena con una base **aparte** de ventas sintéticas:

```bash
# 1. Genera data/sintetico.db: 300 libros, 100 usuarios y 3000 pedidos inventados
poetry run python -m libreria.datos_sinteticos

# 2. Entrena, muestra las métricas y guarda data/modelo_best_seller.joblib
poetry run python -m libreria.prediccion entrenar

# 3. Probabilidad de cada libro del catálogo real (data/libreria.db)
poetry run python -m libreria.prediccion predecir
```

Opciones útiles: `--libros`, `--pedidos`, `--semilla` y `--reemplazar` en el generador; `--bd`, `--modelo` y `--percentil` en `prediccion`. El generador se niega a escribir en `data/libreria.db`, así que los datos inventados nunca se mezclan con los reales.

Cómo funciona:

- **Etiqueta** (`etiquetar()`): un libro es best seller si está en el 20% superior de unidades vendidas, sin contar pedidos cancelados. Un libro sin ventas nunca lo es.
- **Características**: precio, año, géneros, editorial y nacionalidad del autor, es decir, solo datos conocidos *antes* de vender. `cantidad_disponible` no se usa, porque baja justamente cuando el libro se vende (sería fuga de datos).
- **Pipeline** (`construir_modelo()`): un `ColumnTransformer` convierte los géneros en una columna 0/1 por género (`CountVectorizer`, sin distinguir mayúsculas) y la editorial y la nacionalidad con `OneHotEncoder(handle_unknown="ignore")`; después, un `RandomForestClassifier` con `class_weight="balanced"`, porque hay unos 4 libros "no" por cada "sí".
- **Evaluación** (`entrenar()`): separa 25% de los libros para prueba con `stratify`, reporta precision, recall, F1 y ROC AUC, y compara la exactitud contra decir siempre "no es best seller". Después vuelve a entrenar con todos los libros y ese es el modelo que se guarda.
- **Guardado** con joblib. ⚠️ joblib usa pickle: carga solo modelos que tú mismo entrenaste.

La tendencia de las ventas sintéticas está escrita a mano en `datos_sinteticos.popularidad()` (thrillers, fantasía y novelas baratas se venden más; poesía y ensayo, menos). Un modelo entrenado con esos datos solo aprende esa regla: sirve para practicar el flujo, no para decidir qué libros comprar. `importancias()` muestra qué rasgos usó el modelo para compararlos con la regla.

## Cambios recientes: reglas de pedidos en el dominio (arquitectura hexagonal)

Las reglas de los pedidos vivían dentro de `basedatos.py`, mezcladas con SQLAlchemy. Ahora viven en el dominio, sin base de datos ni HTTP:

- **`pedidos.py`** (nuevo): las entidades `Pedido` y `PedidoItem`, el tipo `Estatus` y la tabla `TRANSICIONES` (pendiente → pagado → enviado, y cancelar desde pendiente o pagado). `crear_pedido()` valida todas las líneas antes de descontar stock (todo o nada), `Pedido.avanzar_a()` y `Pedido.cancelar()` cambian el estatus, y cancelar regresa los ejemplares al inventario.
- **`Libro.verificar_stock()`, `Libro.retirar()` y `Libro.reponer()`** (`modelos.py`): el inventario de un libro se modifica solo con estos métodos, que además mantienen `en_stock` sincronizado.
- **Excepciones de dominio** en `excepciones.py`: `PedidoInvalidoError`, `StockInsuficienteError` y `TransicionEstatusError`.
- **`basedatos.py` ahora es solo un adaptador**: carga las filas, las convierte en objetos del dominio, le deja las decisiones a `pedidos.py` y escribe el resultado en una transacción.
- **`tests/test_pedidos.py`** (nuevo): pruebas de dominio que corren en milisegundos, sin SQLite.

La API responde exactamente igual que antes: los mismos códigos HTTP y los mismos mensajes.

## Cambios anteriores: patrones de diseño

- **Strategy en los filtros** (`catalogo.py`): cada criterio es una función `Libro -> bool` (`por_autor`, `por_genero`, `por_stock`, `por_precio_max`, `por_año_min`). `construir_filtros()` arma la lista según los criterios recibidos y `aplicar_filtros()` deja los libros que pasan todos. La consola y el endpoint `GET /libros` usan ahora el mismo código, en lugar de repetir los filtros.
- **Decorador `@cache_temporal`** (`utilidades.py`): guarda el resultado de una función durante un tiempo, según sus argumentos, y descarta el que lleva más tiempo sin usarse cuando se llena. Los errores no se guardan.
- **Caché en `buscar_por_isbn`** (`buscador.py`): las búsquedas en Open Library se recuerdan 10 minutos (`SEGUNDOS_CACHE_ISBN`), así que repetir un ISBN no vuelve a usar la red. El ISBN se normaliza antes de consultar la caché (con o sin guiones es la misma entrada) y se devuelve una copia del resultado.
- **pre-commit**: `.pre-commit-config.yaml` se ajustó a que el repositorio git está en la carpeta `libreria`.

## Estructura del proyecto

```
libreria/
├── data/
│   ├── libreria.json          # catálogo principal
│   ├── libreria.db            # base SQLite (ignorada por git; se crea con alembic)
│   ├── sintetico.db           # ventas inventadas para entrenar (ignorada por git)
│   ├── modelo_best_seller.joblib  # modelo entrenado (ignorado por git)
│   ├── ejemplos/              # CSV de ejemplo para importar
│   ├── exportaciones/         # resultados de filtros exportados (ignorado por git)
│   └── portadas/              # portadas descargadas (ignorado por git)
├── logs/                      # archivo de log (ignorado por git)
├── migraciones/               # migraciones de Alembic
│   ├── env.py
│   └── versions/              # 0001_esquema_inicial, 0002_telefono_usuario, 0003_autenticacion
├── src/libreria/
│   ├── main.py                # punto de entrada y menú
│   ├── api/                   # API web (FastAPI)
│   │   ├── app.py             # crea la app, incluye routers, errores -> HTTP
│   │   ├── dependencies.py    # sesión de BD, búsqueda con 404, paginación, permisos
│   │   ├── schemas.py         # esquemas de entrada y salida con validaciones
│   │   ├── seguridad.py       # hash de contraseñas y tokens JWT
│   │   ├── crear_admin.py     # crea el primer administrador desde la consola
│   │   └── routers/           # auth.py, libros.py, usuarios.py, pedidos.py, reportes.py
│   ├── modelos.py             # modelos Libro y Autor (pydantic), con el inventario del libro
│   ├── pedidos.py             # dominio de pedidos: Pedido, estatus, stock y cancelación
│   ├── puertos.py             # puertos (Protocol) que usan los servicios
│   ├── servicios.py           # casos de uso del catálogo (ServicioCatalogo)
│   ├── almacenamiento.py      # lectura y escritura del catálogo JSON
│   ├── basedatos.py           # adaptador SQLAlchemy: tablas y traducción filas <-> dominio
│   ├── catalogo.py            # reglas del catálogo: agregar y filtrar (estrategias de filtrado)
│   ├── captura.py             # entrada de datos por consola
│   ├── vista.py               # salida por consola
│   ├── intercambio.py         # importar CSV y exportar JSON
│   ├── datos_sinteticos.py    # genera libros y pedidos inventados para practicar ML
│   ├── prediccion.py          # clasificador de best sellers (pandas + scikit-learn)
│   ├── buscador.py            # consultas a Open Library (con caché) y descarga de portadas (también en lote, async)
│   ├── excepciones.py         # jerarquía de errores del proyecto
│   ├── registro.py            # configuración del logging
│   └── utilidades.py          # reintentos (normal y async), caché temporal, escritura atómica, cronómetro
├── tests/
│   ├── conftest.py            # fixtures: base SQLite en memoria y caché de ISBN limpia por prueba
│   ├── test_buscador.py       # Open Library simulado y caché de búsquedas
│   ├── test_filtros.py        # estrategias de filtrado del catálogo
│   ├── test_pedidos.py        # reglas de pedidos e inventario, sin base de datos
│   ├── test_servicios.py      # ServicioCatalogo con repositorios falsos
│   ├── test_cache.py          # decorador @cache_temporal (con reloj falso)
│   ├── test_portadas_concurrentes.py  # descarga en lote (async), semáforo y @reintentar_async
│   ├── test_basedatos.py      # pedidos y reportes
│   ├── test_crud.py           # CRUD de usuarios, libros y estatus de pedidos
│   ├── test_api.py            # endpoints, login, tokens y permisos con TestClient
│   ├── test_migraciones.py    # upgrade/downgrade de Alembic en memoria
│   ├── test_datos_sinteticos.py  # generador reproducible y protección de la base real
│   └── test_prediccion.py     # etiqueta, entrenamiento, predicción y guardado del modelo
├── alembic.ini
└── pyproject.toml
```

## Desarrollo

### Pruebas

```bash
poetry run pytest
```

`test_pedidos.py` prueba las reglas de pedidos directamente sobre objetos del dominio: no necesita base de datos. Las pruebas de `basedatos.py` usan una base SQLite en memoria, así que no tocan `data/libreria.db`. `test_migraciones.py` aplica y revierte las migraciones de Alembic sobre una base en memoria y verifica que coincidan con los modelos (lo mismo que `alembic check`). `test_api.py` prueba los endpoints con `TestClient` y reemplaza la sesión de base de datos por una en memoria con `app.dependency_overrides`. Las pruebas de `buscador.py` usan `httpx.MockTransport` para simular las respuestas de Open Library, así que no necesitan conexión a internet. `test_portadas_concurrentes.py` usa el mismo `MockTransport` con `httpx.AsyncClient` y ejecuta cada corrutina con `asyncio.run()`, así que no necesita plugins extra de pytest. Una de sus pruebas simula una red lenta para comprobar que el semáforo nunca deja más descargas simultáneas de las permitidas. `test_cache.py` simula el paso del tiempo con un reloj falso, así las pruebas de caducidad no esperan de verdad; `conftest.py` limpia la caché de ISBN antes de cada prueba. `test_prediccion.py` genera ventas sintéticas con semilla fija y entrena el modelo una sola vez para todo el archivo (fixture con `scope="module"`); comprueba, entre otras cosas, que el modelo supera claramente al azar y que las existencias no cambian la predicción.

### Calidad del código

| Herramienta | Comando | Qué revisa |
|---|---|---|
| [ruff](https://docs.astral.sh/ruff/) | `poetry run ruff check` | Estilo, errores comunes y orden de imports |
| ruff format | `poetry run ruff format` | Formato del código |
| [mypy](https://mypy-lang.org) | `poetry run mypy` | Tipos, en modo estricto |

Las tres se ejecutan automáticamente en cada commit con [pre-commit](https://pre-commit.com). Para correrlas sobre todo el proyecto:

```bash
poetry run pre-commit run --all-files
```

## Dependencias principales

- [pydantic](https://docs.pydantic.dev): validación de los datos de cada libro.
- [httpx](https://www.python-httpx.org): consultas HTTP a Open Library y descarga de portadas.
- [SQLAlchemy](https://www.sqlalchemy.org): acceso a la base de datos SQLite (ORM).
- [Alembic](https://alembic.sqlalchemy.org): migraciones del esquema de la base.
- [FastAPI](https://fastapi.tiangolo.com): API web con documentación automática (OpenAPI).
- [PyJWT](https://pyjwt.readthedocs.io): creación y verificación de tokens JWT.
- [pwdlib](https://frankie567.github.io/pwdlib/): hash de contraseñas con Argon2.
- [pandas](https://pandas.pydata.org): tablas de datos para preparar el entrenamiento.
- [scikit-learn](https://scikit-learn.org): clasificador de best sellers.
- [joblib](https://joblib.readthedocs.io): guardar y cargar el modelo entrenado.

