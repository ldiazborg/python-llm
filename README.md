# Pedidos + consultas en lenguaje natural

Proyecto en Python que:

1. **Carga**: genera un CSV de pedidos con errores intencionados (Faker), lo limpia y lo guarda en **PostgreSQL**.
2. **Modelos**: dos vistas SQL, ventas por mes y país, y los 10 clientes que más compran.
3. **API (FastAPI)**: recibe una pregunta en lenguaje natural, un **LLM local (vLLM en Docker)** la convierte en SQL, la consulta se **valida** (solo lectura, solo tablas permitidas) y se devuelve el resultado, **redactado también en lenguaje natural** (con cada cifra comprobada contra los datos), o un error claro.
4. **Tests** con pytest: generación, limpieza, validación, cliente LLM, respuestas en lenguaje natural y endpoint con LLM simulado, más tests de integración opcionales contra Postgres.

## Requisitos

- Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker con soporte de GPU NVIDIA para vLLM (en Windows: Docker Desktop con backend WSL2 y driver NVIDIA reciente)
- GPU con ≥ 4 GB de VRAM para el modelo por defecto (`Qwen/Qwen2.5-Coder-1.5B-Instruct`, fp16)

## Cómo ejecutarlo

```bash
cp .env.example .env            # cambia contraseñas y LLM_API_KEY (.env no se sube a git)
uv sync                         # crea el entorno e instala dependencias

docker compose up -d postgres vllm
docker compose ps               # espera a "healthy"; vLLM tarda la primera vez (descarga el modelo)

uv run orders-generate --rows 1200 --seed 42    # -> data/orders_raw.csv (con errores)
uv run orders-load --csv data/orders_raw.csv    # limpia, carga y crea las vistas; imprime el informe

uv run uvicorn orders_nl.api.main:app --reload  # http://localhost:8000/docs
```

Si el puerto 5432 ya está ocupado (p. ej. un PostgreSQL local), cambia `POSTGRES_PORT` en `.env` (por ejemplo a 5433).

### Ejemplo

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

Para obtener solo los datos (sin la segunda llamada al LLM): `{"question": "...", "natural_answer": false}` → `answer` y `answer_source` vienen a `null`.

### Respuesta en lenguaje natural

Tras ejecutar la consulta, el mismo LLM redacta la respuesta en una o dos frases a partir de la pregunta, la SQL y las filas (máximo `ANSWER_MAX_ROWS`, 20 por defecto). Esa frase **solo se devuelve si pasa dos comprobaciones**; si no, se usa una plantilla determinista:

1. **Cada número existe en los datos** (en las filas, la pregunta o la SQL; se aceptan formato español/inglés y redondeos). Detecta cifras inventadas, alteradas o calculadas (porcentajes, totales).
2. **Cada número está junto a la fila correcta**: una cifra escrita tras un nombre ("Chile … 8") debe pertenecer a la fila de ese nombre, y un marcador de lista ("1. Albano…") debe ser el ranking o la posición real de esa fila. Detecta rankings invertidos, que el modelo de 1.5B produce con frecuencia.

`answer_source` indica el origen: `"llm"` (frase del modelo, verificada) o `"template"` (texto determinista, p. ej. *"Se encontraron 3 resultados: Albano Llopis Hierro (customer_rank 1, total_amount 38.655,46); …"*). Si el LLM falla al redactar, la petición **no** falla: se devuelven los datos con la plantilla.

### Errores

Todas las respuestas de error tienen la misma forma: `{"error": "<código>", "detail": "<explicación>", "sql": "<SQL rechazada o null>"}`.

| HTTP | `error`                 | Cuándo                                                                 |
|------|-------------------------|------------------------------------------------------------------------|
| 502  | `llm_unavailable`       | El LLM no responde, da error o devuelve una respuesta vacía            |
| 422  | `unanswerable_question` | El LLM indica que la pregunta no se puede responder con estos datos    |
| 422  | `invalid_query`         | La SQL no pasa la validación, o la base de datos la rechaza (columna inexistente…) |
| 504  | `query_timeout`         | La consulta supera el `statement_timeout` (5 s)                        |
| 500  | `query_failed`          | Fallo operativo de la base de datos (sin filtrar detalles internos)    |

### Tests

```bash
uv run pytest                                   # unitarios: sin Docker, LLM y BD simulados

docker compose up -d postgres                   # integración: Postgres real
RUN_INTEGRATION=1 uv run pytest -m integration  # (vacía y recarga analytics.orders)
```

## Estructura

```
docker-compose.yml               Postgres 17 + vLLM (API OpenAI-compatible en el puerto 8001)
docker/postgres/init/            esquema `analytics` + rol de solo lectura para la API
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

### Cómo extenderlo

- **Nuevo país o producto**: añadirlo en `domain.KNOWN_COUNTRIES` y en `generation/catalog.py`.
- **Nuevo tipo de error en los datos**: una clase con `name` y `apply(df, rng)` en `generation/defects.py`.
- **Nuevo paso de limpieza**: una función `(df, report) -> df` añadida a `clean.DEFAULT_STEPS`.
- **Nueva regla de validación**: una función `(tree, policy) -> None` pasada en `SqlValidator(rules=...)`.
- **Otro LLM**: cambiar `LLM_BASE_URL` / `LLM_MODEL` (cualquier servidor OpenAI-compatible) o implementar el protocolo `SQLGenerator`.
- **Nuevo error de la API**: excepción en `errors.py` + una entrada en `api/errors.ERROR_MAP`.
- **Otra forma de redactar respuestas**: implementar el protocolo `AnswerWriter` (`write_answer(question, sql, result)`); `AnswerComposer` le aplica las mismas comprobaciones. Con `AnswerComposer(None)` solo se usan plantillas.

## Decisiones

**Datos y limpieza**
- `order_id` añadido a las columnas pedidas: permite detectar duplicados y es la clave primaria.
- Errores generados: duplicados exactos, fechas en varios formatos e imposibles, importes vacíos, importes con coma decimal y texto con mayúsculas/espacios/tildes inconsistentes.
- Las fechas numéricas se interpretan **día primero** (convención española); las ambiguas al estilo EE. UU. (`04/15/2025`) se descartan en vez de leerse mal.
- Los **importes vacíos se descartan, no se imputan**: inventar importes falsearía las ventas. Se informa de cuántas filas se pierden (~4 %).
- Los países se normalizan a su forma canónica ignorando mayúsculas y tildes; los desconocidos se conservan.
- El CSV no se versiona: es reproducible con `--seed`.

**Modelos**
- Solo los pedidos `completed` cuentan como venta (pendientes, cancelados y reembolsados no).
- `v_sales_by_month_country` expone `year` y `month` como enteros para que un modelo pequeño pueda filtrar "en marzo" sin aritmética de fechas.
- `v_top_customers` desempata por nombre para devolver siempre exactamente 10 filas.

**Seguridad de las consultas (dos barreras)**
1. **Validador** sobre el AST de sqlglot (no expresiones regulares): una sola sentencia; raíz `SELECT`/`UNION`; ningún nodo de escritura en todo el árbol (también `DELETE` dentro de CTE, `SELECT INTO`, `FOR UPDATE`); solo las tablas permitidas y del esquema `analytics`; al menos una de ellas debe leerse (rechaza respuestas fuera de tema como `SELECT CURRENT_TIMESTAMP` a "¿Qué tiempo hace?"); sin funciones de sistema (`pg_*`, `set_config`…); `LIMIT` máximo forzado.
2. **Base de datos**: la API usa un rol de **solo lectura** (`default_transaction_read_only`, solo `SELECT` en `analytics`, `statement_timeout` de 5 s) y además abre la sesión en modo read-only. Un fallo del validador no puede escribir datos.

**LLM**
- vLLM con cliente OpenAI-compatible: la aplicación no depende del proveedor.
- `Qwen2.5-Coder-1.5B-Instruct` en fp16 para caber en 4 GB de VRAM (`--enforce-eager`, `--max-model-len 2048`). Al ser pequeño, el prompt lleva el esquema, las reglas de negocio y ejemplos few-shot, y `temperature=0`.
- El cliente OpenAI se inyecta, de modo que los tests simulan la capa HTTP sin servidor.
- **Respuesta en lenguaje natural híbrida**: el LLM redacta, pero solo se acepta si cada cifra existe en los datos y está junto a la fila correcta; si no, plantilla determinista. Un fallo al redactar nunca convierte una consulta correcta en un error. Se mantienen `sql` y `rows` en la respuesta para poder verificarla.

**Código**
- `QueryService` no depende de FastAPI; la API solo traduce HTTP y errores.
- Los endpoints son síncronos a propósito: los clientes de LLM y BD son bloqueantes y FastAPI los ejecuta en un pool de hilos.

## Qué haría con más tiempo

- Evaluación del text-to-SQL y de las respuestas redactadas: un conjunto de preguntas con su resultado esperado para medir la precisión del modelo, la tasa de respuestas descartadas y comparar prompts/modelos.
- Detectar respuestas **incompletas** (p. ej. mencionar solo 1 de 6 países) y normalizar el formato de los números a la convención española en la frase del LLM.
- Reintento guiado: si la SQL es rechazada, devolver el motivo al LLM y pedir una corrección (1 intento).
- Incluir en el prompt valores reales de la BD (rango de fechas, productos) y la fecha actual para resolver "el mes pasado".
- Pool de conexiones (`psycopg_pool`) y cliente LLM compartido en lugar de crearlos por petición.
- Autenticación y rate limiting en la API; métricas y trazas (preguntas, SQL, latencias, rechazos).
- Dockerfile para la API y CI (tests unitarios + integración con Postgres como servicio).
- Ruff + mypy en pre-commit.

## Cómo usé la IA

> ✏️ **Borrador: revisar y completar con tu experiencia.**

- **Qué le pedí**: a Claude Code (asistente de programación) le pedí primero el andamiaje del proyecto a partir del enunciado, preguntándome antes de elegir cada herramienta (runtime del LLM, base de datos, librerías, gestor de dependencias). Después, la implementación completa con Faker para los datos, código comentado, diseño modular y una rama de git por funcionalidad.
- **Qué acepté**: la estructura por capas (generación → limpieza → carga → validación → servicio → API), la validación sobre el AST con sqlglot más el rol de solo lectura como segunda barrera, y el mapeo de errores a códigos HTTP.
- **Qué corregí / decidí yo**: elegí vLLM, PostgreSQL, pandas + sqlglot y uv; ajusté el modelo a mi GPU de 4 GB (Qwen2.5-Coder-1.5B en fp16 en lugar de uno de 7B); pedí Faker para generar los datos. TODO: añadir lo que hayas revisado o cambiado tras leer el código.
- **Lo que se detectó y corrigió durante el desarrollo**: probando con el modelo real, respondía "¿Qué tiempo hace en Madrid?" con `SELECT CURRENT_TIMESTAMP` (se añadió la regla "al menos una tabla permitida") y redactaba rankings invertidos con cifras reales (se añadió la comprobación de atribución); `text()` de SQLAlchemy interpretaba los casts `::date` como parámetros (se ejecutan los scripts con el cursor de psycopg); `date.fromisoformat` acepta `20250315` en Python 3.11+ (el test de fechas ISO se hizo estricto); los mensajes de error de sqlglot incluían detalles internos (se limpian antes de devolverlos).

## Problemas conocidos

- **vLLM con 4 GB de VRAM va justo.** `VLLM_GPU_MEM_UTIL` es la fracción de la VRAM *total* que reserva vLLM y no puede superar la memoria libre al arrancar. En Docker Desktop con WSL2 unos 0,8 GiB nunca están disponibles (WSL y el contexto CUDA): con una RTX 3050 de 4 GB solo quedan 3,21 GiB libres, así que **0.80 es el máximo**. Con ese valor caben los pesos (2,89 GiB), las activaciones (0,13 GiB) y ~6.000 tokens de caché KV, suficiente para `max-model-len=2048`.
  - Si el contenedor termina con *"Free memory on device (…) is less than desired GPU memory utilization"*: baja `VLLM_GPU_MEM_UTIL` o cierra aplicaciones que usen la GPU (navegador, IDE, juegos).
  - Si termina con *"No available memory for the cache blocks"*: súbelo un poco o baja `VLLM_MAX_MODEL_LEN`.
  - Diagnóstico: `docker compose ps -a` (estado *Exited*) y `docker compose logs vllm`. Mientras vLLM no esté *healthy*, la API responde `502 llm_unavailable`.
  - El primer arranque descarga el modelo (~3 GB) y tarda varios minutos; los siguientes usan la caché del volumen `hf-cache`.
- vLLM escribe `LLM_API_KEY` en su log de arranque (`non-default args`): no compartas esos logs.
- Un modelo de 1.5B se equivoca en preguntas complejas; el validador y los errores claros evitan que eso llegue a ejecutarse o se devuelva como un resultado engañoso.
- Respuestas en lenguaje natural: las comprobaciones garantizan que las cifras son reales y están bien atribuidas, pero **no que la frase sea completa** (con resultados de varias filas el modelo a veces menciona solo algunas; las filas completas siempre van en `rows`). Algunas cifras salen en formato inglés (`3386.21 €`). En listas y rankings el modelo de 1.5B suele desordenar los nombres, así que esas preguntas acaban a menudo en la plantilla.
