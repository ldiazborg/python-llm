"""Text-to-SQL prompt: schema description, rules and few-shot examples.

Built from `orders_nl.domain` so statuses/countries never drift from the data.
`build_messages` takes the pieces as parameters, so a different schema or
example set can be plugged in without editing this module.

Message layout sent to the model:

    system     rules + schema description
    user       example question 1        (few-shot)
    assistant  example SQL 1
    ...
    user       the real question

The prompt is written in English (models follow English instructions best),
while questions and examples are in Spanish, like real users' questions.
"""

import re
from collections.abc import Sequence

from orders_nl.domain import KNOWN_COUNTRIES, SALE_STATUSES, OrderStatus

# Sentinel the model must output when the data can't answer the question.
# The client turns it into UnanswerableQuestionError (HTTP 422).
CANNOT_ANSWER = "CANNOT_ANSWER"

# Pre-rendered lists for the prompt text, e.g. "'completed', 'pending', ...".
_statuses = ", ".join(f"'{s.value}'" for s in OrderStatus)
_sale_statuses = ", ".join(f"'{s.value}'" for s in sorted(SALE_STATUSES))
_countries = ", ".join(f"'{c}'" for c in KNOWN_COUNTRIES)

# Keep in sync with sql/schema.sql and sql/views.sql.
# Compact pseudo-DDL: enough for the model to know names and types without
# spending the small context window (2048 tokens) on full CREATE statements.
SCHEMA_DESCRIPTION = f"""\
orders(order_id text, order_date date, customer text, country text,
       product text, amount numeric, status text)
  -- one row per order; amount in EUR; status in ({_statuses})
v_sales_by_month_country(year int, month int, month_start date, country text,
                         orders bigint, total_amount numeric, avg_order_amount numeric)
  -- sales (status {_sale_statuses} only) aggregated per calendar month and country
v_top_customers(customer_rank int, customer text, main_country text,
                orders bigint, total_amount numeric)
  -- the 10 customers with the most sales, customer_rank 1 = best
Countries (exact spelling): {_countries}."""

# {placeholders} are filled by build_system_prompt (str.format).
SYSTEM_PROMPT_TEMPLATE = """\
You translate questions about an orders database into ONE PostgreSQL SELECT query.

Tables and views:
{schema}

Rules:
- Output only the SQL query: no explanation, no markdown, no trailing semicolon.
- Use only the tables and views listed above. Never modify data.
- "Ventas" / "vendimos" / sales means orders with status {sale_statuses}; prefer
  v_sales_by_month_country for sales by month or country.
- Months are numbers (enero = 1 ... diciembre = 12). If no year is given, do not
  filter by year.
- Give result columns short descriptive aliases.
- If the question cannot be answered with these tables, output exactly: {cannot_answer}
"""

# (question, sql) pairs. Small models follow examples far better than rules.
# Each example teaches one pattern: a view filter, the ranking view, a GROUP BY
# on the base table, a year filter, and refusing an off-topic question.
FEW_SHOT_EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        "¿Cuánto vendimos en España en marzo?",
        "SELECT SUM(total_amount) AS total_sales FROM v_sales_by_month_country "
        "WHERE country = 'España' AND month = 3",
    ),
    (
        "¿Quiénes son los 5 mejores clientes?",
        "SELECT customer_rank, customer, total_amount FROM v_top_customers "
        "ORDER BY customer_rank LIMIT 5",
    ),
    (
        "¿Cuántos pedidos cancelados hubo por país?",
        "SELECT country, COUNT(*) AS cancelled_orders FROM orders "
        "WHERE status = 'cancelled' GROUP BY country ORDER BY cancelled_orders DESC",
    ),
    (
        "¿Qué producto vendió más en 2025?",
        "SELECT product, SUM(amount) AS total_sales FROM orders "
        "WHERE status = 'completed' AND EXTRACT(YEAR FROM order_date) = 2025 "
        "GROUP BY product ORDER BY total_sales DESC LIMIT 1",
    ),
    ("¿Qué tiempo hace hoy en Madrid?", CANNOT_ANSWER),
)

# ```sql ... ``` or ``` ... ``` blocks that models add despite the instructions.
# DOTALL: '.' also matches newlines (multi-line SQL); IGNORECASE: ```SQL works too.
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_system_prompt(schema: str = SCHEMA_DESCRIPTION) -> str:
    """System message: rules plus the schema description.

    Args:
        schema: tables/views description; override to reuse the prompt elsewhere.
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        schema=schema,
        sale_statuses=_sale_statuses,
        cannot_answer=CANNOT_ANSWER,
    )


def build_messages(
    question: str,
    *,
    schema: str = SCHEMA_DESCRIPTION,
    examples: Sequence[tuple[str, str]] = FEW_SHOT_EXAMPLES,
) -> list[dict[str, str]]:
    """Chat messages: system rules, few-shot turns, then the user's question.

    Args:
        question: the user's natural-language question.
        schema: tables/views description.
        examples: (question, sql) pairs shown as previous conversation turns.

    Returns:
        A list in the OpenAI Chat Completions `messages` format.
    """
    messages = [{"role": "system", "content": build_system_prompt(schema)}]
    # Examples as real user/assistant turns: the model imitates its "own" answers.
    for example_question, example_sql in examples:
        messages.append({"role": "user", "content": example_question})
        messages.append({"role": "assistant", "content": example_sql})
    messages.append({"role": "user", "content": question})
    return messages


def extract_sql(content: str) -> str:
    """Pull the SQL out of a model reply: strip fences, whitespace and trailing ';'.

    Examples:
        "```sql\\nSELECT 1;\\n```"          -> "SELECT 1"
        "Here you go:\\n```SELECT 1```"    -> "SELECT 1"
        "  SELECT 1 ;  "                    -> "SELECT 1"
    """
    match = _FENCE_RE.search(content)
    # With a fence, keep only its content (drops chatter around it).
    sql = match.group(1) if match else content
    return sql.strip().rstrip(";").strip()
