# Commands

All Python commands go through `uv` (the environment lives in `.venv`, versions pinned in `uv.lock`).

## Setup

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD, READER_PASSWORD, LLM_API_KEY
uv sync                   # installs runtime + dev group (pytest, httpx)
```

Dependencies: `uv add <pkg>` (runtime) or `uv add --group dev <pkg>`. Ask the user before adding any new library or tool (see [conventions.md](conventions.md)).

## Infrastructure (Docker)

```bash
docker compose up -d postgres vllm   # vLLM's first start downloads ~3 GB and takes minutes
docker compose ps -a                 # wait for "healthy"; "Exited" = check logs
docker compose logs vllm             # startup failures are almost always GPU memory (see llm.md)
docker compose stop vllm             # frees the GPU (~3.2 GiB) without losing the model cache
docker compose down -v               # DELETES the data volume; needed to re-run docker/postgres/init/
```

## Data

```bash
uv run orders-generate --rows 1200 --seed 42 [--out data/orders_raw.csv] [--clean]
uv run orders-load --csv data/orders_raw.csv     # cleans, (re)creates table, loads, rebuilds views
```

`--seed 42` with defaults yields 1236 raw rows → 1138 clean rows. Re-run `orders-load` after the integration tests (they replace the table contents).

## API

```bash
uv run uvicorn orders_nl.api.main:app --reload   # http://localhost:8000 , docs at /docs
```

Calling `/ask` from Windows: Git Bash `curl -d '...'` mangles non-ASCII (¿, ñ) and FastAPI answers "There was an error parsing the body". Use PowerShell `Invoke-RestMethod ... -ContentType "application/json; charset=utf-8"`, `/docs`, or `curl --data-binary @file.json`.

## Tests

```bash
uv run pytest                                           # unit tests: no Docker, LLM and DB faked
uv run pytest tests/test_validator.py                   # one file
uv run pytest "tests/test_validator.py::test_adds_limit_when_missing"   # one test
uv run pytest -k attribution                            # by name substring
RUN_INTEGRATION=1 uv run pytest -m integration          # real Postgres (Git Bash syntax)
```

PowerShell: `$env:RUN_INTEGRATION = "1"; uv run pytest -m integration`. Integration tests are skipped unless `RUN_INTEGRATION=1` because they TRUNCATE and reload `analytics.orders`. See [testing.md](testing.md).

## Lint / format / type-check

None configured (no ruff/mypy in `pyproject.toml`; listed as future work in the README). Validate compose changes with `docker compose --env-file .env.example config --quiet` and shell scripts with `bash -n <file>`.
