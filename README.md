# Pedidos + consultas en lenguaje natural

Pequeño proyecto en Python que:

1. Genera un CSV de pedidos con errores intencionados, lo limpia y lo carga en **PostgreSQL**.
2. Define dos vistas SQL: ventas por mes y país, y top 10 clientes.
3. Expone una API **FastAPI** que convierte preguntas en lenguaje natural a SQL con un **LLM local (vLLM en Docker)**, valida la consulta (solo lectura, solo tablas permitidas) y devuelve el resultado.

> Estado: *scaffolding*. Las piezas marcadas `TODO` / `NotImplementedError` están pendientes; los tests correspondientes están marcados `xfail` hasta implementarlas.

## Requisitos

- Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker con soporte de GPU NVIDIA (en Windows: Docker Desktop con backend WSL2 y driver NVIDIA reciente)
- GPU con ≥ 4 GB de VRAM para el modelo por defecto

## Cómo ejecutarlo

```bash
cp .env.example .env            # y cambia las contraseñas / LLM_API_KEY
uv sync                         # dependencias + entorno virtual

docker compose up -d postgres vllm
docker compose ps               # espera a que ambos estén "healthy" (vLLM tarda: descarga el modelo)

uv run orders-generate --rows 1200 --seed 42      # -> data/orders_raw.csv
uv run orders-load --csv data/orders_raw.csv      # limpia, carga y crea las vistas

uv run uvicorn orders_nl.api.main:app --reload    # http://localhost:8000/docs
```

Ejemplo:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cuánto vendimos en España en marzo?"}'
```

Tests (no necesitan Docker: el LLM y la base de datos están simulados):

```bash
uv run pytest
```

## Estructura

```
docker-compose.yml               Postgres 17 + vLLM (OpenAI-compatible, puerto 8001)
docker/postgres/init/            esquema `analytics` + rol de solo lectura para la API
src/orders_nl/
  config.py                      configuración (.env) y lista de tablas permitidas
  db.py                          motor de carga (admin) y ejecutor de solo lectura (API)
  etl/generate.py                genera el CSV con errores
  etl/clean.py                   limpieza (funciones puras)
  etl/load.py                    CSV -> limpieza -> Postgres -> vistas
  sql/schema.sql, sql/views.sql  tabla y modelos
  llm/                           prompt + cliente OpenAI-compatible
  validator.py                   validación de la SQL con sqlglot (AST)
  api/                           FastAPI: /health, /ask
tests/                           limpieza, validación y endpoint con LLM simulado
```

## Decisiones

- **PostgreSQL** en vez de SQLite/DuckDB: permite un **rol de solo lectura** (`default_transaction_read_only`, `statement_timeout`, `search_path` fijo) como segunda barrera detrás del validador. La API nunca usa el usuario administrador.
- **Validación con sqlglot (AST)**, no con expresiones regulares: se recorre el árbol para exigir una única sentencia de lectura, solo tablas permitidas (también dentro de CTEs y subconsultas), sin funciones peligrosas y con un `LIMIT` máximo.
- **vLLM + cliente OpenAI-compatible**: la API solo conoce `LLM_BASE_URL` / `LLM_MODEL`, así que se puede cambiar a Ollama, llama.cpp o un proveedor en la nube sin tocar código.
- **Modelo `Qwen2.5-Coder-1.5B-Instruct` (fp16)**: cabe en una GPU de 4 GB con `--enforce-eager`, `--max-model-len 2048` y `--gpu-memory-utilization` ajustable. Es pequeño: el validador es imprescindible.
- **El LLM se inyecta como dependencia** (`get_sql_generator`), por lo que los tests lo sustituyen por uno falso sin red.
- **Errores claros**: `502 llm_unavailable` si el LLM falla, `422 invalid_query` si la SQL no pasa la validación (se devuelve la SQL rechazada), `500 query_failed` si falla en la base de datos.
- **Datos generados reproducibles** con semilla; el CSV no se versiona (`data/*.csv` en `.gitignore`).
- **Secretos** solo en `.env` (ignorado por git); `.env.example` documenta las variables.
- TODO: decisiones de limpieza (formatos de fecha aceptados, descartar vs. imputar importes vacíos, qué estados cuentan como venta).

## Qué haría con más tiempo

- TODO

## Cómo usé la IA

- **Qué le pedí:** TODO
- **Qué acepté:** TODO
- **Qué corregí:** TODO

## Problemas conocidos

- vLLM con 4 GB de VRAM va justo: si falla con *"not enough free memory"* baja `VLLM_GPU_MEM_UTIL`; si falla por falta de espacio para la caché KV, súbelo o baja `VLLM_MAX_MODEL_LEN`. Cierra otras aplicaciones que usen la GPU.
