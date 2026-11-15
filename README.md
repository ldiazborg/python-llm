# Pedidos + consultas en lenguaje natural

Proyecto en Python que:

1. **Carga**: genera un CSV de pedidos con errores intencionados (Faker), lo limpia y lo guarda en **PostgreSQL**.
2. **Modelos**: dos vistas SQL, ventas por mes y país, y los 10 clientes que más compran.
3. **API (FastAPI)**: recibe una pregunta en lenguaje natural, un **LLM local (vLLM en Docker)** la convierte en SQL, la consulta se **valida** (solo lectura, solo tablas permitidas) y se devuelve el resultado, **redactado también en lenguaje natural** (con cada cifra comprobada contra los datos), o un error claro.
4. **Tests** con pytest: 246 tests unitarios (sin Docker) y 13 de integración contra PostgreSQL real.

```text
"¿Cuánto vendimos en España en marzo?"
   → SELECT SUM(total_amount) AS total_sales FROM v_sales_by_month_country
     WHERE country = 'España' AND month = 3 LIMIT 1000
   → [{"total_sales": 6215.32}]
   → "Vendimos 6.215,32 euros en España en marzo."
```

## Índice

- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Puesta en marcha](#puesta-en-marcha)
- [Configuración](#configuración)
- [Datos: generación, limpieza y carga](#datos-generación-limpieza-y-carga)
- [Modelos (vistas SQL)](#modelos-vistas-sql)
- [API](#api)
- [Seguridad de las consultas](#seguridad-de-las-consultas)
- [Respuesta en lenguaje natural](#respuesta-en-lenguaje-natural)
- [Tests](#tests)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Cómo extenderlo](#cómo-extenderlo)
- [Decisiones](#decisiones)
- [Flujo de trabajo con git](#flujo-de-trabajo-con-git)
- [Solución de problemas](#solución-de-problemas)
- [Problemas conocidos y limitaciones](#problemas-conocidos-y-limitaciones)
- [Qué haría con más tiempo](#qué-haría-con-más-tiempo)
- [Cómo usé la IA](#cómo-usé-la-ia)

## Arquitectura

```text
                ┌──────────────── ETL (CLI) ────────────────┐
 orders-generate│  Faker + defectos  →  data/orders_raw.csv │
 orders-load    │  limpieza (pasos)  →  analytics.orders    │──┐ usuario admin
                │  schema.sql / views.sql → 2 vistas        │  │
                └───────────────────────────────────────────┘  ▼
                                                          ┌──────────────┐
 POST /ask ──► FastAPI ──► QueryService                   │  PostgreSQL  │
                              │                           │   (Docker)   │
                              │                           └──────────────┘
                              ├─1─► LLM: pregunta → SQL ─┐       ▲
                              │      (vLLM, Docker)      │       │ rol de solo
                              ├─2─► Validador (sqlglot) ◄┘       │ lectura
                              ├─3─► Ejecutor read-only ──────────┘
                              └─4─► Respuesta: LLM redacta → comprobación
                                    de cifras → (o plantilla)
```


| Capa       | Módulo         | Responsabilidad                                         |
| ---------- | -------------- | ------------------------------------------------------- |
| Dominio    | `domain.py`    | Columnas, estados y países: única fuente de verdad      |
| Generación | `generation/`  | Pedidos sintéticos reproducibles + defectos componibles |
| ETL        | `etl/`         | Parsers puros, limpieza por pasos, carga idempotente    |
| Modelos    | `sql/`         | Tabla `analytics.orders` y las dos vistas               |
| Validación | `validator.py` | Reglas sobre el AST de la SQL generada                  |
| LLM        | `llm/`         | Prompts y clientes OpenAI-compatibles (SQL y redacción) |
| Respuestas | `answers/`     | Comprobación de cifras, plantillas y selección          |
| Servicio   | `service.py`   | Orquesta los pasos 1–4, sin depender de HTTP            |
| API        | `api/`         | FastAPI: rutas, esquemas y errores → HTTP               |


Cada pieza del servicio (generador de SQL, validador, ejecutor, redactor) se **inyecta**, así que los tests sustituyen el LLM y la base de datos por versiones simuladas sin tocar el código.

## Requisitos


| Herramienta                      | Versión                        | Para qué                                           |
| -------------------------------- | ------------------------------ | -------------------------------------------------- |
| Python                           | ≥ 3.12 (desarrollado con 3.14) | la aplicación                                      |
| [uv](https://docs.astral.sh/uv/) | reciente                       | entorno y dependencias (`uv.lock`)                 |
| Docker                           | con Compose v2                 | PostgreSQL 17 y vLLM                               |
| GPU NVIDIA                       | ≥ 4 GB de VRAM                 | vLLM con `Qwen/Qwen2.5-Coder-1.5B-Instruct` (fp16) |


En **Windows** hace falta Docker Desktop con backend **WSL2** y un driver NVIDIA reciente para que el contenedor vea la GPU. Sin GPU se puede usar todo menos el LLM: los tests no la necesitan, y la API responde `502 llm_unavailable` de forma controlada.

## Puesta en marcha

```bash
# 1. Configuración (los secretos solo viven en .env, que git ignora)
cp .env.example .env            # cambia POSTGRES_PASSWORD, READER_PASSWORD y LLM_API_KEY

# 2. Dependencias
uv sync                         # crea .venv e instala todo, incluidas las de desarrollo

# 3. Infraestructura
docker compose up -d postgres vllm
docker compose ps               # espera a "healthy" en ambos servicios
```

El primer arranque de vLLM descarga el modelo (~3 GB) y lo carga en la GPU: unos minutos. Los siguientes arranques usan la caché del volumen `hf-cache`.

```bash
# 4. Datos
uv run orders-generate --rows 1200 --seed 42
#   Wrote 1236 rows to data/orders_raw.csv      (1.200 pedidos + 36 duplicados)

uv run orders-load --csv data/orders_raw.csv
#     rows_in                        1236
#     duplicates_removed             36
#     text_values_normalized         128
#     dates_repaired                 58
#     amounts_repaired               35
#     rows_dropped_bad_date          14
#     rows_dropped_bad_amount        48
#     rows_dropped_invalid_status    0
#     rows_out                       1138
#   Loaded 1138 rows into analytics.orders and rebuilt the views

# 5. API
uv run uvicorn orders_nl.api.main:app --reload  # http://localhost:8000/docs
```

En `http://localhost:8000/docs` está la documentación interactiva (OpenAPI) para probar `/ask` desde el navegador.

## Configuración

Todas las variables están documentadas en `.env.example`. Las lee `docker compose` (infraestructura) o la aplicación (`config.py`, con `pydantic-settings`); las variables de entorno reales tienen prioridad sobre `.env`.


| Variable                              | Por defecto                        | Usada por     | Descripción                                                                              |
| ------------------------------------- | ---------------------------------- | ------------- | ---------------------------------------------------------------------------------------- |
| `PG_HOST`                             | `localhost`                        | app           | Host de PostgreSQL                                                                       |
| `POSTGRES_PORT`                       | `5432`                             | compose + app | Puerto publicado (cámbialo si ya hay un Postgres local)                                  |
| `POSTGRES_DB`                         | `orders`                           | compose + app | Base de datos                                                                            |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `orders_admin` / —                 | compose + app | Usuario administrador: lo usa **solo** el ETL                                            |
| `READER_USER` / `READER_PASSWORD`     | `orders_reader` / —                | compose + app | Rol de **solo lectura**: lo usa la API                                                   |
| `VLLM_IMAGE_TAG`                      | `v0.10.2`                          | compose       | Versión de la imagen de vLLM                                                             |
| `VLLM_PORT`                           | `8001`                             | compose       | Puerto de vLLM en el host                                                                |
| `LLM_MODEL_ID`                        | `Qwen/Qwen2.5-Coder-1.5B-Instruct` | compose       | Modelo de Hugging Face                                                                   |
| `VLLM_GPU_MEM_UTIL`                   | `0.80`                             | compose       | Fracción de la VRAM **total** que reserva vLLM ([ver problemas](#solución-de-problemas)) |
| `VLLM_MAX_MODEL_LEN`                  | `2048`                             | compose       | Tokens máximos por petición (prompt + respuesta)                                         |
| `HF_TOKEN`                            | vacío                              | compose       | Solo para modelos con acceso restringido                                                 |
| `LLM_BASE_URL`                        | `http://localhost:8001/v1`         | app           | Cualquier servidor OpenAI-compatible                                                     |
| `LLM_MODEL`                           | `qwen-coder`                       | compose + app | Nombre con el que vLLM sirve el modelo                                                   |
| `LLM_API_KEY`                         | —                                  | compose + app | Secreto compartido entre vLLM y la API                                                   |
| `LLM_TIMEOUT_S`                       | `30`                               | app           | Tiempo máximo por llamada al LLM                                                         |
| `QUERY_ROW_LIMIT`                     | `1000`                             | app           | Filas máximas por consulta (se fuerza con `LIMIT`)                                       |
| `ANSWER_MAX_ROWS`                     | `20`                               | app           | Filas que se envían al LLM para redactar la respuesta                                    |


## Datos: generación, limpieza y carga

### Generación (`orders-generate`)

Genera pedidos válidos con **Faker** (nombres con el locale de cada país) y después les aplica **defectos intencionados**. Es reproducible: la misma semilla produce siempre el mismo CSV.


| Columna      | Ejemplo               | Notas                                                                                                    |
| ------------ | --------------------- | -------------------------------------------------------------------------------------------------------- |
| `order_id`   | `ORD-000532`          | Añadida al enunciado: identifica duplicados y es la clave primaria                                       |
| `order_date` | `2025-03-08`          | Uniforme en 2025                                                                                         |
| `customer`   | `Paco Rocamora Nieto` | 40 clientes por país; unos pocos compran mucho más (distribución tipo Zipf), así el top 10 tiene sentido |
| `country`    | `España`              | España 35 %, México 20 %, Argentina 15 %, Colombia 12 %, Chile 10 %, EE. UU. 8 %                         |
| `product`    | `Portátil`            | 10 productos con rango de precio realista                                                                |
| `amount`     | `2069.54`             | Precio unitario × cantidad (1–3), exacto al céntimo                                                      |
| `status`     | `completed`           | completed 75 %, pending 10 %, cancelled 10 %, refunded 5 %                                               |



| Defecto               | Tasa | Qué hace                                                                                                                                            |
| --------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MalformedDates`      | 6 %  | Reescribe la fecha en otro formato (`15/03/2025`, `20250315`, …); un 25 % de ellas se vuelven imposibles (`2025-13-45`, `31/02/2025`, `N/A`, vacía) |
| `MissingAmounts`      | 4 %  | Importe vacío o con espacios                                                                                                                        |
| `CommaDecimalAmounts` | 3 %  | Coma decimal (`12,50`)                                                                                                                              |
| `MessyText`           | 5 %  | País/cliente en mayúsculas, minúsculas, con espacios de más o sin tildes                                                                            |
| `DuplicateRows`       | 3 %  | Copias exactas de filas (se aplica el último)                                                                                                       |


Opciones: `--rows`, `--seed`, `--out` y `--clean` (sin defectos).

### Limpieza

Un *pipeline* de pasos independientes (`etl/clean.py`); cada uno corrige un tipo de problema y anota lo que hizo en un `CleaningReport`:


| Paso                    | Qué hace                                                                                                  |
| ----------------------- | --------------------------------------------------------------------------------------------------------- |
| `normalize_text`        | Recorta espacios, unifica países (`ESPANA`, `españa` → `España`) y pasa los estados a minúsculas          |
| `unify_customer_spellings` | Un cliente, una grafía: `ANA LÓPEZ` y `ana lopez` pasan a `Ana López` (la más frecuente en los datos)  |
| `drop_exact_duplicates` | Elimina filas idénticas (tras normalizar el texto)                                                        |
| `parse_dates`           | Acepta 8 formatos (día primero); descarta las fechas imposibles o vacías                                  |
| `parse_amounts`         | Acepta coma decimal, separadores de miles y símbolo €; descarta importes vacíos, no numéricos o negativos |
| `drop_invalid_status`   | Descarta estados desconocidos (la tabla tiene un `CHECK`)                                                 |
| `drop_duplicate_ids`    | Un `order_id` repetido se queda con la primera fila                                                       |


Cada fila de entrada queda contabilizada: `rows_out = rows_in − duplicados − descartadas`. Con los datos por defecto se conservan 1.138 de 1.236 filas (~92 %).

### Carga (`orders-load`)

1. Lee el CSV **como texto** (sin que pandas adivine tipos).
2. Lo limpia.
3. Crea la tabla si no existe (`sql/schema.sql`).
4. Sustituye su contenido con `TRUNCATE` + `INSERT` en **una sola transacción**: es idempotente y, si algo falla, se conservan los datos anteriores.
5. Recrea las vistas (`sql/views.sql`).

Tabla `analytics.orders`:


| Columna                          | Tipo            | Restricción                                         |
| -------------------------------- | --------------- | --------------------------------------------------- |
| `order_id`                       | `text`          | `PRIMARY KEY`                                       |
| `order_date`                     | `date`          | `NOT NULL`                                          |
| `customer`, `country`, `product` | `text`          | `NOT NULL`                                          |
| `amount`                         | `numeric(12,2)` | `NOT NULL`, `>= 0`                                  |
| `status`                         | `text`          | `IN ('completed','pending','cancelled','refunded')` |


Con índices en `order_date` y `country`, los filtros más habituales.

## Modelos (vistas SQL)

Regla de negocio común: **solo los pedidos `completed` cuentan como venta**.

**`v_sales_by_month_country`**: ventas por mes y país.


| Columna                                      | Descripción                                                                         |
| -------------------------------------------- | ----------------------------------------------------------------------------------- |
| `year`, `month`                              | Enteros, para que el LLM filtre "en marzo" con `month = 3` sin aritmética de fechas |
| `month_start`                                | Primer día del mes (`date`)                                                         |
| `country`                                    | País                                                                                |
| `orders`, `total_amount`, `avg_order_amount` | Nº de pedidos, total y ticket medio                                                 |


**`v_top_customers`**: los 10 clientes con más ventas.


| Columna                    | Descripción                                                                               |
| -------------------------- | ----------------------------------------------------------------------------------------- |
| `customer_rank`            | 1 = mejor cliente; empates resueltos por nombre, así que siempre hay exactamente 10 filas |
| `customer`, `main_country` | Cliente y el país desde el que más compra                                                 |
| `orders`, `total_amount`   | Nº de pedidos y total                                                                     |


```sql
SELECT * FROM analytics.v_top_customers LIMIT 3;
--  customer_rank |       customer        | main_country | orders | total_amount
--              1 | Albano Llopis Hierro  | España       |     79 |     38655.46
--              2 | Buenaventura de Bonet | España       |     34 |     14233.84
--              3 | Hugo Colón Ríos       | México       |     34 |     13126.62
```

## API


| Método | Ruta      | Descripción                                                               |
| ------ | --------- | ------------------------------------------------------------------------- |
| `GET`  | `/health` | Comprobación de vida (no llama al LLM ni a la BD)                         |
| `POST` | `/ask`    | Pregunta en lenguaje natural → SQL validada → datos + respuesta redactada |
| `GET`  | `/docs`   | Documentación interactiva (OpenAPI)                                       |


### Petición


| Campo            | Tipo           | Por defecto | Descripción                                                                      |
| ---------------- | -------------- | ----------- | -------------------------------------------------------------------------------- |
| `question`       | string (3–500) | —           | La pregunta                                                                      |
| `natural_answer` | bool           | `true`      | Redactar también la respuesta (una segunda llamada al LLM); `false` = solo datos |


### Respuesta


| Campo                          | Descripción                                                                |
| ------------------------------ | -------------------------------------------------------------------------- |
| `question`                     | La pregunta recibida                                                       |
| `answer`                       | La respuesta en lenguaje natural (`null` si `natural_answer=false`)        |
| `answer_source`                | `"llm"` (frase del modelo, verificada) o `"template"` (texto determinista) |
| `sql`                          | La SQL **validada que se ejecutó** (con `LIMIT`)                           |
| `columns`, `rows`, `row_count` | El resultado completo                                                      |

Cada fila es un objeto `{columna: valor}`. Si la consulta devuelve dos columnas con el mismo nombre (`SELECT COUNT(*), COUNT(DISTINCT customer)` → `count`, `count`), la segunda se renombra (`count_2`) para no perder ningún valor. Los `numeric` sin decimales (conteos, años) llegan como enteros y los importes como decimales.


### Ejemplos

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cuánto vendimos en España en marzo?"}'
```

PowerShell (envía el cuerpo en UTF-8 correctamente):

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/ask -ContentType "application/json; charset=utf-8" `
  -Body '{"question": "¿Cuánto vendimos en España en marzo?"}'
```

Python:

```python
import httpx

r = httpx.post("http://localhost:8000/ask",
               json={"question": "¿Cuánto vendimos en España en marzo?"}, timeout=60)
print(r.json()["answer"])
```

Respuesta:

```json
{
  "question": "¿Cuánto vendimos en España en marzo?",
  "answer": "Vendimos 6.215,32 euros en España en marzo.",
  "answer_source": "llm",
  "sql": "SELECT SUM(total_amount) AS total_sales FROM v_sales_by_month_country WHERE country = 'España' AND month = 3 LIMIT 1000",
  "columns": ["total_sales"],
  "rows": [{"total_sales": 6215.32}],
  "row_count": 1
}
```

Con la GPU de 4 GB, una pregunta tarda ~1–3 s (dos llamadas al LLM); con `natural_answer: false`, ~0,5–1 s.

### Errores

Los errores de la aplicación tienen todos la misma forma: `{"error": "<código>", "detail": "<explicación>", "sql": "<SQL rechazada o null>"}`.


| HTTP | `error`                 | Cuándo                                                                                                                                                                       |
| ---- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 502  | `llm_unavailable`       | El LLM no responde, da error o devuelve una respuesta vacía                                                                                                                  |
| 422  | `unanswerable_question` | El LLM indica que la pregunta no se puede responder con estos datos                                                                                                          |
| 422  | `invalid_query`         | La SQL no pasa la validación, o la base de datos la rechaza (columna inexistente…)                                                                                           |
| 504  | `query_timeout`         | La consulta supera el `statement_timeout` (5 s)                                                                                                                              |
| 500  | `query_failed`          | Fallo operativo de la base de datos (sin filtrar detalles internos)                                                                                                          |
| 422  | *(FastAPI)*             | Petición mal formada (p. ej. pregunta de menos de 3 caracteres). Este error lo genera FastAPI antes de llegar a la aplicación, con su formato estándar (`{"detail": [...]}`) |


Ejemplos reales con el modelo:

```jsonc
// "Borra todos los pedidos"
{"error": "invalid_query", "detail": "Only SELECT queries are allowed (got DELETE)",
 "sql": "DELETE FROM orders"}

// "¿Qué tiempo hace en Madrid?"
{"error": "invalid_query",
 "detail": "The query does not read any data table; it must use at least one of: orders, v_sales_by_month_country, v_top_customers",
 "sql": "SELECT NOW() AS current_time"}

// vLLM parado
{"error": "llm_unavailable", "detail": "The language model is unavailable (APIConnectionError)", "sql": null}
```

## Seguridad de las consultas

La SQL generada por el LLM se trata como **entrada no confiable**. Hay dos barreras independientes:

**1. Validador (`validator.py`)**: analiza la SQL con **sqlglot** y recorre el árbol sintáctico completo (no usa expresiones regulares, que se engañan con subconsultas, CTE o comentarios). Las reglas son funciones independientes:


| Regla                                          | Rechaza (ejemplo)                                                | Mensaje                                               |
| ---------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------- |
| Una sola sentencia                             | `SELECT 1; DROP TABLE orders`                                    | `Exactly one statement is allowed (got 2)`            |
| Raíz de lectura                                | `DELETE FROM orders`, `SET statement_timeout = 0`                | `Only SELECT queries are allowed (got DELETE)`        |
| Sin efectos secundarios en ningún nodo         | `WITH x AS (DELETE …) SELECT …`, `SELECT … INTO t`, `FOR UPDATE` | `Forbidden operation in query: DELETE`                |
| Solo tablas permitidas del esquema `analytics` | `pg_catalog.pg_user`, `public.orders`, `customers`               | `Table 'customers' is not allowed. Allowed: …`        |
| Leer al menos una tabla permitida              | `SELECT CURRENT_TIMESTAMP`                                       | `The query does not read any data table; …`           |
| Sin funciones de sistema                       | `pg_sleep(10)`, `pg_read_file(…)`, `set_config(…)`, `version()`  | `Function 'pg_sleep' is not allowed`                  |
| `LIMIT` máximo                                 | `SELECT * FROM orders`                                           | *(no rechaza: añade `LIMIT 1000` o reduce uno mayor)* |


Los 52 casos de ataque de `tests/test_validator.py` se rechazan cada uno por el motivo esperado. Tres detalles que una comprobación ingenua pasa por alto:

- **Un CTE no oculta una tabla real fuera de su ámbito.** `WITH pg_roles AS (SELECT * FROM pg_roles) SELECT … FROM pg_roles, orders` lee el catálogo dentro del cuerpo del CTE (un CTE no se ve a sí mismo salvo con `RECURSIVE`); el validador resuelve cada nombre con las reglas de visibilidad de PostgreSQL.
- **Los nombres se comparan como los lee PostgreSQL**: sin comillas se pasan a minúsculas (`FROM ORDERS` es la tabla permitida) y entre comillas se respetan (`"ORDERS"` es otra relación).
- **Funciones que reciben SQL o un nombre de tabla como texto** (`query_to_xml…`, `table_to_xml…`, `ts_stat`) se rechazan, porque leerían tablas que el validador no ve. Lo mismo `FETCH FIRST n ROWS WITH TIES`, que puede devolver más de `n` filas: se sustituye por `LIMIT 1000`.

**2. Base de datos**: la API se conecta con un rol que **solo** tiene `SELECT` sobre `analytics`, con `default_transaction_read_only = on`, `statement_timeout = 5s` y `search_path = analytics`. Además, cada sesión se abre en modo read-only. Aunque el validador tuviera un fallo, la API no podría escribir datos ni bloquear la base de datos.

## Respuesta en lenguaje natural

Tras ejecutar la consulta, el mismo LLM redacta la respuesta en una o dos frases a partir de la pregunta, la SQL y las filas (máximo `ANSWER_MAX_ROWS`). La frase **solo se devuelve si pasa dos comprobaciones**; si no, se usa una plantilla determinista:

1. **Cada número existe en los datos**: en las filas, en la pregunta o en la SQL. Se aceptan el formato español e inglés (`6.215,32` / `6,215.32`) y los redondeos (`unos 6.215 €`). Detecta cifras inventadas, alteradas (`6.512,32`) o calculadas (porcentajes, totales).
2. **Cada número está junto a la fila correcta**: una cifra escrita tras un nombre ("Chile … 8") debe pertenecer a la fila de ese nombre, y un marcador de lista ("1. Albano…") debe ser el ranking o la posición real de esa fila. Detecta rankings invertidos.


| Situación                         | `answer_source` | Ejemplo                                                                                              |
| --------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------- |
| Frase correcta                    | `llm`           | *"El producto más vendido en México fue el Portátil, con un total de ventas de 22.481,85 €."*        |
| Ranking desordenado por el modelo | `template`      | *"Se encontraron 3 resultados: Albano Llopis Hierro (customer\_rank 1, total\_amount 38.655,46); …"* |
| Cifra inventada                   | `template`      | *"El resultado es 6.215,32 (total\_sales)."*                                                         |
| El LLM falla al redactar          | `template`      | *(la petición devuelve igualmente 200 con los datos)*                                                |


Los motivos de cada frase descartada quedan en el log del servidor (`Discarded LLM answer (…)`), útil para ajustar el prompt.

## Tests

```bash
uv run pytest                                   # 246 tests unitarios: sin Docker; LLM y BD simulados

docker compose up -d postgres                   # integración: PostgreSQL real
RUN_INTEGRATION=1 uv run pytest -m integration  # 13 tests (vacían y recargan analytics.orders)
uv run orders-load                              # después, recargar el conjunto completo
```

En PowerShell: `$env:RUN_INTEGRATION = "1"; uv run pytest -m integration`. Los tests de integración se omiten salvo que `RUN_INTEGRATION=1`, porque modifican la base de datos.


| Archivo                        | Tests | Qué cubre                                                                          |
| ------------------------------ | ----- | ---------------------------------------------------------------------------------- |
| `test_generate.py`             | 6     | Tamaño, reproducibilidad, cada defecto aparece, defectos componibles               |
| `test_parsers.py`              | 32    | Formatos de fecha e importe aceptados y rechazados, normalización de texto         |
| `test_clean.py`                | 10    | Duplicados, fechas, importes, estados, grafías de clientes, generador → limpieza   |
| `test_validator.py`            | 93    | 19 consultas válidas, 52 ataques, límites, ámbito de los CTE, reglas personalizadas |
| `test_llm.py`                  | 15    | Clientes LLM con la capa HTTP simulada: errores, `CANNOT_ANSWER`, prompts          |
| `test_answers.py`              | 46    | Comprobación de cifras y de atribución (con salidas reales del modelo), plantillas |
| `test_db.py`                   | 25    | Conversión de valores a JSON, columnas repetidas, traducción de errores de la BD   |
| `test_api.py`                  | 19    | Endpoint con LLM y BD simulados: éxito, cada código de error, respuesta redactada  |
| `integration/test_postgres.py` | 13    | Carga real, idempotencia, vistas, permisos del rol, timeout, columnas repetidas    |


Los tres tests que pide el enunciado: limpieza (`test_clean.py`), validación de la consulta (`test_validator.py`) y endpoint con el LLM simulado (`test_api.py`).

## Estructura del proyecto

```text
docker-compose.yml               Postgres 17 + vLLM (API OpenAI-compatible en el puerto 8001)
docker/postgres/init/            esquema `analytics` + rol de solo lectura para la API
.env.example                     todas las variables, documentadas
src/orders_nl/
  domain.py                      columnas, estados, países: fuente única de verdad
  config.py                      configuración desde .env y tablas permitidas
  errors.py                      jerarquía de errores de la aplicación
  generation/                    generador con Faker
    catalog.py                     países (con locale de Faker), productos, pesos
    orders.py                      pedidos válidos y reproducibles (semilla)
    defects.py                     inyectores de errores componibles
  etl/
    generate.py                    CLI orders-generate
    parsers.py                     parsers puros: fechas, importes, normalización de texto
    clean.py                       pipeline de limpieza por pasos + informe
    load.py                        CLI orders-load: CSV -> limpieza -> Postgres -> vistas
  sql/schema.sql, sql/views.sql  tabla y modelos
  validator.py                   validación de SQL sobre el AST de sqlglot, con reglas enchufables
  llm/prompt.py, llm/client.py   prompt (esquema + few-shot) y cliente OpenAI-compatible
  llm/answer.py                  redacción de la respuesta en lenguaje natural (prompt + cliente)
  answers/                       respuesta fiable: comprobación de cifras, plantillas y selección
  db.py                          carga (admin) y ejecución de solo lectura (API)
  service.py                     pregunta -> SQL -> validación -> ejecución -> respuesta (sin HTTP)
  api/                           FastAPI: rutas, esquemas y mapeo de errores a HTTP
tests/                           unitarios + integration/ (opcionales)
```

Todo el código está comentado: cada módulo, clase y función tiene docstring, y los comentarios explican el *porqué* de las decisiones menos obvias.

## Cómo extenderlo

**Un nuevo tipo de defecto en los datos**: una clase con `name` y `apply(df, rng)`:

```python
@dataclass(frozen=True)
class NegativeAmounts:
    rate: float = 0.01
    name: str = "negative_amounts"

    def apply(self, df, rng):
        out = df.copy()
        for idx in _sample_index(out, self.rate, rng):
            out.at[idx, "amount"] = "-" + out.at[idx, "amount"]
        return out

generate_orders(GeneratorConfig(), defects=(*default_defects(), NegativeAmounts()))
```

**Un nuevo paso de limpieza**: una función `(df, report) -> df`:

```python
def drop_future_dates(df, report):
    return df.loc[df["order_date"] <= date.today()]

clean_orders(raw, steps=(*DEFAULT_STEPS, drop_future_dates))
```

**Una nueva regla de validación**: una función `(tree, policy) -> None` que lanza `InvalidQueryError`:

```python
def no_select_star(tree, policy):
    if tree.find(exp.Star):
        raise InvalidQueryError("SELECT * is not allowed")

SqlValidator(policy, rules=(*DEFAULT_RULES, no_select_star))
```

**Otros cambios**:

- **Nuevo país o producto**: añadirlo en `domain.KNOWN_COUNTRIES` y en `generation/catalog.py`.
- **Otro LLM**: cambiar `LLM_BASE_URL` / `LLM_MODEL` (Ollama, llama.cpp, un proveedor en la nube…) o implementar el protocolo `SQLGenerator`.
- **Otra forma de redactar respuestas**: implementar `AnswerWriter` (`write_answer(question, sql, result)`); `AnswerComposer` le aplica las mismas comprobaciones. Con `AnswerComposer(None)` solo se usan plantillas.
- **Nuevo error de la API**: excepción en `errors.py` + una entrada en `api/errors.ERROR_MAP`.
- **Nueva tabla o vista consultable**: crearla en `sql/`, añadirla a `config.ALLOWED_TABLES` y describirla en `llm/prompt.SCHEMA_DESCRIPTION`.

## Decisiones

**Datos y limpieza**

- `order_id` añadido a las columnas pedidas: permite detectar duplicados y es la clave primaria.
- Errores generados: duplicados exactos, fechas en varios formatos e imposibles, importes vacíos, importes con coma decimal y texto con mayúsculas/espacios/tildes inconsistentes.
- Las fechas numéricas se interpretan **día primero** (convención española); las ambiguas al estilo EE. UU. (`04/15/2025`) se descartan en vez de leerse mal.
- Los **importes vacíos se descartan, no se imputan**: inventar importes falsearía las ventas. Se informa de cuántas filas se pierden (~4 %).
- Los países se normalizan a su forma canónica ignorando mayúsculas y tildes; los desconocidos se conservan.
- Los **clientes** no tienen una lista cerrada ni un identificador: el nombre es su identidad. Los nombres que solo difieren en mayúsculas o tildes se consideran el mismo cliente y se escriben con la grafía más frecuente (en caso de empate, la que tiene mayúsculas y tildes correctas). Sin este paso, `v_top_customers` repartía las ventas de una persona entre varias grafías (17 clientes afectados con los datos por defecto). A cambio, dos personas distintas cuyo nombre solo difiera en eso se fusionarían.
- El CSV no se versiona: es reproducible con `--seed`.

**Modelos**

- Solo los pedidos `completed` cuentan como venta (pendientes, cancelados y reembolsados no).
- `v_sales_by_month_country` expone `year` y `month` como enteros para que un modelo pequeño pueda filtrar "en marzo" sin aritmética de fechas.
- `v_top_customers` desempata por nombre para devolver siempre exactamente 10 filas.

**Seguridad**

- Dos barreras independientes: validador sobre el AST + rol de solo lectura con timeout (ver [Seguridad de las consultas](#seguridad-de-las-consultas)).
- La API nunca usa las credenciales del administrador.

**LLM**

- vLLM con cliente OpenAI-compatible: la aplicación no depende del proveedor.
- `Qwen2.5-Coder-1.5B-Instruct` en fp16 para caber en 4 GB de VRAM (`--enforce-eager`, `--max-model-len 2048`). Al ser pequeño, el prompt lleva el esquema, las reglas de negocio y ejemplos few-shot, y `temperature=0`.
- El cliente OpenAI se inyecta, de modo que los tests simulan la capa HTTP sin servidor.
- **Respuesta en lenguaje natural híbrida**: el LLM redacta, pero solo se acepta si cada cifra existe en los datos y está junto a la fila correcta; si no, plantilla determinista. Un fallo al redactar nunca convierte una consulta correcta en un error. Se mantienen `sql` y `rows` en la respuesta para poder verificarla.

**Código**

- `QueryService` no depende de FastAPI; la API solo traduce HTTP y errores.
- Los endpoints son síncronos a propósito: los clientes de LLM y BD son bloqueantes y FastAPI los ejecuta en un pool de hilos.
- Uvicorn como servidor ASGI (el recomendado por FastAPI); la API corre en el host con `--reload` para iterar rápido, y solo la infraestructura va en Docker.

## Flujo de trabajo con git

Cada funcionalidad o tarea se desarrolló en su propia rama y se integró en `master` con `git merge --no-ff`, para que cada una quede visible en el historial (`git log --graph`):


| Rama                           | Contenido                                                      |
| ------------------------------ | -------------------------------------------------------------- |
| `chore/project-scaffolding`    | Estructura, Docker Compose, configuración, esqueleto de la API |
| `feat/data-generation`         | Generador con Faker y defectos                                 |
| `feat/data-cleaning`           | Parsers y pipeline de limpieza                                 |
| `feat/postgres-load-and-views` | Carga, tabla, vistas y tests de integración                    |
| `feat/sql-validator`           | Validación sobre el AST                                        |
| `feat/nl-query-endpoint`       | Servicio, prompt y endpoint `/ask`                             |
| `docs/readme`                  | Primera versión de este README                                 |
| `fix/require-table-reference`  | Regla "leer al menos una tabla"                                |
| `chore/vllm-memory-defaults`   | `VLLM_GPU_MEM_UTIL=0.80` para GPUs de 4 GB                     |
| `feat/natural-language-answer` | Respuesta redactada con comprobación de cifras                 |
| `docs/code-comments`           | Comentarios y docstrings en todo el código                     |
| `docs/readme-expanded`         | Esta versión ampliada del README                               |


## Solución de problemas


| Síntoma                                                                                    | Causa                                                         | Solución                                                                             |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `/ask` devuelve `502 llm_unavailable`                                                      | vLLM no está arrancado o todavía no está *healthy*            | `docker compose ps -a`; si está *Exited*, `docker compose logs vllm`                 |
| vLLM termina con *"Free memory on device (…) is less than desired GPU memory utilization"* | `VLLM_GPU_MEM_UTIL` pide más VRAM de la libre                 | Bájalo o cierra aplicaciones que usen la GPU (navegador, IDE, juegos)                |
| vLLM termina con *"No available memory for the cache blocks"*                              | No queda VRAM para la caché KV                                | Súbelo un poco o baja `VLLM_MAX_MODEL_LEN`                                           |
| El contenedor de Postgres no arranca porque el puerto 5432 está en uso                     | Ya hay un PostgreSQL local en ese puerto                      | `POSTGRES_PORT=5433` en `.env`                                                       |
| `curl` en Git Bash devuelve *"There was an error parsing the body"*                        | La consola de Windows no envía el cuerpo en UTF-8 (tildes, ¿) | Usa PowerShell (`Invoke-RestMethod`), `/docs`, o `curl --data-binary @pregunta.json` |
| Un cambio en `docker/postgres/init/` no tiene efecto                                       | Los scripts de inicio solo se ejecutan con el volumen vacío   | `docker compose down -v` (borra los datos) y vuelve a arrancar + `orders-load`       |
| Después de los tests de integración hay menos datos                                        | Esos tests recargan la tabla con su propio conjunto           | `uv run orders-load`                                                                 |


**Por qué 0.80 en una GPU de 4 GB**: `VLLM_GPU_MEM_UTIL` es la fracción de la VRAM *total* que reserva vLLM y no puede superar la memoria libre al arrancar. En Docker Desktop con WSL2 unos 0,8 GiB nunca están disponibles (WSL y el contexto CUDA): con una RTX 3050 de 4 GB solo quedan 3,21 GiB libres, así que 0.80 es el máximo. Con ese valor caben los pesos (2,89 GiB), las activaciones (0,13 GiB) y ~6.000 tokens de caché KV, suficiente para `max-model-len=2048`. El primer arranque descarga el modelo (~3 GB) y tarda varios minutos.

## Problemas conocidos y limitaciones

- Un modelo de 1.5B se equivoca en preguntas complejas. El validador y los errores claros evitan que eso llegue a ejecutarse o se devuelva como un resultado engañoso, pero algunas preguntas razonables acaban en error en vez de en respuesta.
- Respuestas en lenguaje natural: las comprobaciones garantizan que las cifras son reales y están bien atribuidas, pero **no que la frase sea completa** (con varias filas el modelo a veces menciona solo algunas; las filas completas siempre van en `rows`). Algunas cifras salen en formato inglés (`3386.21 €`). En listas y rankings el modelo de 1.5B suele desordenar los nombres, así que esas preguntas acaban a menudo en la plantilla.
- "Marzo" sin año filtra todos los marzos; con datos de un solo año no importa, pero con varios años habría que pedir el año o usar el más reciente.
- vLLM escribe `LLM_API_KEY` en su log de arranque (`non-default args`): no compartas esos logs.

## Qué haría con más tiempo

- Evaluación del text-to-SQL y de las respuestas redactadas: un conjunto de preguntas con su resultado esperado para medir la precisión del modelo, la tasa de respuestas descartadas y comparar prompts/modelos.
- Detectar respuestas **incompletas** (p. ej. mencionar solo 1 de 6 países) y normalizar el formato de los números a la convención española en la frase del LLM.
- Reintento guiado: si la SQL es rechazada, devolver el motivo al LLM y pedir una corrección (1 intento).
- Incluir en el prompt valores reales de la BD (rango de fechas, productos) y la fecha actual para resolver "el mes pasado".
- Pool de conexiones (`psycopg_pool`) y cliente LLM compartido en lugar de crearlos por petición.
- Autenticación y rate limiting en la API; métricas y trazas (preguntas, SQL, latencias, rechazos).
- Dockerfile para la API y CI (tests unitarios + integración con Postgres como servicio).
- Ruff + mypy en pre-commit.
- **Interfaz de chat**: una página web sencilla para hacer preguntas sin usar `curl` ni `/docs`, que muestre la respuesta redactada, la SQL ejecutada y las filas en una tabla, con el historial de la conversación. Como siguiente paso, preguntas de seguimiento que usen el contexto anterior ("¿y en abril?").

## Cómo usé la IA

- **Qué le pedí**: a Claude Code (asistente de programación) le pedí primero el andamiaje del proyecto a partir del enunciado, preguntándome antes de elegir cada herramienta (runtime del LLM, base de datos, librerías, gestor de dependencias). Después, la implementación completa con Faker para los datos, código comentado, diseño modular y una rama de git por funcionalidad. Más adelante, la respuesta en lenguaje natural (elegí la opción híbrida: LLM + comprobación de cifras + plantilla), comentar todo el código y ampliar este README.
- **Qué corregí / decidí yo**: la estructura por capas (generación → limpieza → carga → validación → servicio → API), la validación sobre el AST con sqlglot más el rol de solo lectura como segunda barrera, y el mapeo de errores a códigos HTTP. Elegí vLLM, PostgreSQL, pandas + sqlglot y uv; ajusté el modelo a mi GPU de 4 GB (Qwen2.5-Coder-1.5B en fp16 en lugar de uno de 7B); pedí Faker para generar los datos; elegí el enfoque híbrido para las respuestas redactadas. TODO: añadir lo que hayas revisado o cambiado tras leer el código.
- **Lo que se detectó y corrigió durante el desarrollo**:
  - Probando con el modelo real, respondía "¿Qué tiempo hace en Madrid?" con `SELECT CURRENT_TIMESTAMP` (se añadió la regla "al menos una tabla permitida") y redactaba rankings invertidos con cifras reales (se añadió la comprobación de atribución).
  - vLLM no arrancaba con `VLLM_GPU_MEM_UTIL=0.90`: en WSL2 solo hay 3,21 GiB libres de 4 (se midió y se fijó 0.80).
  - `text()` de SQLAlchemy interpretaba los casts `::date` como parámetros (se ejecutan los scripts con el cursor de psycopg).
  - `date.fromisoformat` acepta `20250315` en Python 3.11+ (el test de fechas ISO se hizo estricto).
  - Los mensajes de error de sqlglot incluían detalles internos (se limpian antes de devolverlos).