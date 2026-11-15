# LLM integration (`llm/`, vLLM)

## Serving

- vLLM in Docker (`docker-compose.yml`), OpenAI-compatible API on host port **8001** (`LLM_BASE_URL=http://localhost:8001/v1`). The app only knows base URL + model name, so Ollama/llama.cpp/cloud work by changing env vars.
- Model `Qwen/Qwen2.5-Coder-1.5B-Instruct` in fp16, served as `qwen-coder` (`--served-model-name`), `--api-key` shared with the app (`LLM_API_KEY`).
- Hardware: the developer's GPU is a 4 GB RTX 3050 under Docker Desktop/WSL2, where only ~3.21 GiB is free inside the container. `VLLM_GPU_MEM_UTIL=0.80` is the measured maximum (0.85/0.90 crash with "Free memory on device ... is less than desired"). At 0.80: weights 2.89 GiB, activations 0.13 GiB, KV cache 0.16 GiB (~6k tokens). Don't propose models needing more VRAM without asking.
- `--max-model-len 2048` bounds prompt + reply; prompts must stay small (this is why `ANSWER_MAX_ROWS` exists).
- vLLM logs `LLM_API_KEY` in its startup line (`non-default args`).

## Client (`llm/client.py`)

- `make_openai_client(settings)` and `chat_completion(client, model, messages, max_tokens, temperature=0)` are shared by both LLM calls. `chat_completion` turns `openai.APIError` (connection, timeout, non-2xx) and empty replies into `LLMError`.
- `OpenAICompatibleSQLGenerator` takes an injected `openai.OpenAI` client (`from_settings` builds the real one); tests pass a client whose `http_client` uses `httpx.MockTransport`.

## SQL prompt (`llm/prompt.py`)

- Rules in English; questions and few-shot examples in Spanish (like real users).
- `SCHEMA_DESCRIPTION` is compact pseudo-DDL built from `domain` (statuses, countries) — keep it in sync with `sql/`.
- Few-shot examples are sent as real user/assistant turns; each teaches one pattern (view filter, ranking view, GROUP BY, year filter, refusal).
- Sentinel `CANNOT_ANSWER` → `UnanswerableQuestionError` (HTTP 422). The 1.5B model does not always obey it, which is why the validator has `require_data_source`.
- `extract_sql` strips ``` fences, chatter around them, whitespace and trailing `;`.

## Answer prompt (`llm/answer.py`)

- Written in Spanish (output must be Spanish). One worked example whose numbers are deliberately unrelated to real data, so copying them is caught by the grounding check.
- User message contains question, executed SQL, columns and the first `max_rows` rows as JSON, labelled "Filas (N de M)" when truncated.
- The writer's output is **not trusted**; see [natural-answers.md](natural-answers.md).

## Observed behaviour of the 1.5B model

In live tests, single-fact questions were answered correctly (~1–3 s including the answer step). It tends to: scramble rankings when listing names, mention only some rows of multi-row results, write some numbers in English format (`3386.21 €`), and occasionally answer off-topic questions with timestamp SQL instead of `CANNOT_ANSWER`. Regression tests in `tests/test_answers.py` and `tests/test_validator.py` contain real outputs.
