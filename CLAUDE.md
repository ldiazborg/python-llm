# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python project for a client brief (in Spanish): generate a dirty orders CSV, clean it and load it into PostgreSQL with two SQL views, and expose a FastAPI endpoint that turns a natural-language question into SQL with a local LLM (vLLM), validates it (read-only, allowed tables only), runs it and returns the data plus a sentence whose numbers are checked against the data.

This file is an index. Each aspect of the code is described in its own document under [`docs/`](docs/); read the relevant one before changing that area, and keep it up to date when you change behaviour.

## Documentation index

| Doc | Read it for |
|---|---|
| [docs/commands.md](docs/commands.md) | Setup, Docker, data CLIs, running the API, running all/one/integration tests, lint status |
| [docs/architecture.md](docs/architecture.md) | ETL and question flows, layering rules, dependency injection seams, extension lists |
| [docs/domain-and-config.md](docs/domain-and-config.md) | `domain.py` vocabulary, `config.py` settings, what must change together |
| [docs/data-pipeline.md](docs/data-pipeline.md) | Faker generator and defects, cleaning steps and report, idempotent load |
| [docs/database.md](docs/database.md) | Schema and views, admin vs read-only role, init script, DB error translation |
| [docs/sql-validation.md](docs/sql-validation.md) | sqlglot validator: rules, LIMIT enforcement, gotchas, adding a rule |
| [docs/llm.md](docs/llm.md) | vLLM serving and GPU limits, OpenAI-compatible client, SQL and answer prompts, model behaviour |
| [docs/natural-answers.md](docs/natural-answers.md) | Answer composer, number grounding and attribution checks, templates |
| [docs/api.md](docs/api.md) | Endpoints, error mapping, dependency providers |
| [docs/testing.md](docs/testing.md) | Test layout, fakes and fixtures, mocking the OpenAI SDK, integration tests |
| [docs/conventions.md](docs/conventions.md) | Git workflow, ask-before-adding-tools, comment style, languages, Windows/ports specifics |

## Where to look when changing…

| Change | Docs |
|---|---|
| Statuses, countries, columns, allowed tables, settings | domain-and-config → database → llm |
| Generated or cleaned data | data-pipeline → testing |
| What SQL is accepted | sql-validation → testing |
| Prompts, model, LLM server | llm → natural-answers |
| `/ask` behaviour or error codes | api → architecture |
| Anything before committing | conventions |

The client-facing documentation is `README.md` (Spanish); it overlaps these docs but is written for the client, not for working on the code.
