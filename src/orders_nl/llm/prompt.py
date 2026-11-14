"""Prompt for text-to-SQL. Starting point: iterate on it against the real model."""

import re

# TODO: keep in sync with sql/schema.sql and sql/views.sql once the views are written.
SCHEMA_DESCRIPTION = """\
orders(order_id text, order_date date, customer text, country text,
       product text, amount numeric, status text)
v_sales_by_month_country(...)  -- TODO: columns
v_top_customers(...)           -- TODO: columns
"""

SYSTEM_PROMPT = f"""\
You translate questions about an orders database into ONE PostgreSQL SELECT query.
Rules:
- Output only the SQL query, no explanation, no markdown.
- Use only these tables/views:
{SCHEMA_DESCRIPTION}
- Never modify data. Never use tables not listed above.
- Country names are stored in Spanish (e.g. 'España', 'México').
- If the question cannot be answered with these tables, output exactly: CANNOT_ANSWER
"""

# TODO: add a few question -> SQL examples (few-shot); small models benefit a lot.
FEW_SHOT: list[tuple[str, str]] = []

CANNOT_ANSWER = "CANNOT_ANSWER"

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_messages(question: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for q, sql in FEW_SHOT:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": sql}]
    messages.append({"role": "user", "content": question})
    return messages


def extract_sql(content: str) -> str:
    """Strip markdown fences / whitespace that models add despite instructions."""
    match = _FENCE_RE.search(content)
    sql = match.group(1) if match else content
    return sql.strip().rstrip(";").strip()
