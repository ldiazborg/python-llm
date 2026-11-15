# Project conventions

## Git workflow

- One branch per feature/task, prefixed `feat/`, `fix/`, `docs/`, `chore/`. Never put unrelated work on one branch.
- Integrate into **`master`** (there is no `main`) with `git merge --no-ff <branch>` so each task stays visible in `git log --graph`.
- The remote `origin` is the public GitHub repo and mirrors every branch, not only `master`: when pushing, push the feature branch too (`git push origin --all`). Push only when the user asks.
- Commit messages: conventional prefix with scope, e.g. `feat(answers): ...`, `fix(validator): ...`, `docs(readme): ...`, plus a body explaining what/why.
- The user may have uncommitted edits in the working tree: never fold them into your commits; stage only your own hunks.

## Asking first

Ask the user before choosing or adding any framework, library or tool (including dev tools such as linters). The stack was chosen by the user: vLLM (local, Docker), OpenAI-compatible client, PostgreSQL + psycopg 3, pandas + sqlglot, uv, Faker.

## Code style

- Every module, class and function has a docstring; comments explain the *why* of non-obvious choices. Keep this standard in new code.
- Modular and extensible: prefer small composable units (step/rule/defect lists, protocols, dependency injection) over growing functions.
- Code, comments, logs and API error messages in English. README, generated answers, prompt examples and data values (countries, products) in Spanish.
- `README.md` (Spanish) documents setup, configuration, API, decisions and limitations for the client: update it when behaviour, settings or commands change. The "Cómo usé la IA" section is the user's own account — don't rewrite it without being asked.

## Local environment (Windows 11)

- Shells: Git Bash and PowerShell. `/tmp` in Git Bash is not the same path Windows Python sees — use explicit Windows paths for files shared between them.
- `.gitattributes` forces LF for `*.sh` and `*.sql`; `core.autocrlf=true` produces harmless "LF will be replaced by CRLF" warnings for other files.
- Port **8000** is usually taken by the developer's own `uvicorn --reload`. Start throwaway servers on another port (e.g. 8010) and only stop processes you started — identify them by PID/command line, never by "whatever listens on the port".
- Port 5432 may be a native Windows PostgreSQL; the project's container then runs on 5433 via `POSTGRES_PORT` in `.env`.
- GPU limits for vLLM: see [llm.md](llm.md).
