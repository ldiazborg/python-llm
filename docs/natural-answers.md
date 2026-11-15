# Natural-language answers (`answers/`)

`AnswerComposer(writer).compose(question, sql, result) -> NaturalAnswer(text, source)`; `source` is `"llm"` or `"template"` and is returned to API clients as `answer_source`. It never raises `LLMError`: phrasing must never turn a successful query into an error.

## Decision flow (`answers/composer.py`)

no writer → template; writer raises `LLMError` → template; text empty or > 600 chars → template; check 1 fails → template; check 2 fails → template; else the LLM text. Every discard is logged at WARNING: `Discarded LLM answer (<reason>): '<text>'` — the main signal for tuning the answer prompt.

## Check 1 — every number exists (`grounding.find_unsupported_numbers`)

- `numbers_in_text` extracts digit runs with `.`/`,` separators; `interpretations()` returns **every** plausible value under Spanish and English notation (`"1.138"` → {1138, 1.138}); a token passes if any reading matches.
- `allowed_numbers(rows, question, sql, row_count)`: all cell values (digits inside strings and dates too), numbers in the question and the executed SQL (e.g. `month = 3`, `LIMIT 3`), `row_count` itself, and `0..min(row_count, MAX_ENUMERATION)` (small counts, list positions). The cap (20) matters: with plain `0..row_count`, a 300-row result accepted any invented figure up to 300.
- Matching tolerates roundings to 0, 1 and 2 decimals and ignores sign.

## Check 2 — numbers next to the right row (`grounding.find_misattributed_numbers`)

Added because the real model produced reversed rankings whose numbers all existed.

- Row "names" = text cells ≥ 3 chars, matched accent/case-insensitively (`etl.parsers.fold`), longest first, no overlaps; a name appearing in several rows carries the values and positions of all of them.
- A number binds to the closest name **before** it in the same sentence and must be one of that row's values or a question/SQL number.
- A list marker (`1.`/`2)` at line start) binds to the name **after** it and must be that row's own value (e.g. its rank) or its 1-based position in the result — question/SQL numbers do **not** count for markers ("top 3" must not excuse "3. <first customer>").
- Sentence breaks: newline, or `. ` not preceded by a digit (so `1. Albano` and `6.215,32` don't split).
- Numbers with no name to bind to are left to check 1. False positives only cost a template fallback, so the checks are deliberately conservative.

## Templates (`answers/templates.py`)

Deterministic Spanish text by result shape (0 rows / single value / single row / many rows, first 5 rows + "y N más"). Numbers in Spanish format (`format_number`: ints without decimals, everything else 2 decimals). Rows are labelled by their first **text** column so rankings read "Albano (customer_rank 1, ...)".

## Known gaps

The checks guarantee real, correctly attributed numbers, but not completeness (mentioning 1 of 6 countries passes) and not number formatting. List/ranking questions frequently fall back to the template with the 1.5B model.

Check 1 is "the number appears somewhere the answer may quote from", so these still pass (found in the audit, not fixed):

- numbers from the **question** or the **SQL** used as if they were results: the validator's own `LIMIT 1000` makes "1000" always allowed, and "¿vendimos más de 5000?" allows "5000";
- digits inside **text cells**: an `order_id` like `ORD-000250` allows "250";
- numbers written as **words** ("tres mil", "el doble") are not seen at all, and the sign is ignored (`-5` matches `5`).
