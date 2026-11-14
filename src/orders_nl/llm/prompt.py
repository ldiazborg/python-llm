"""Text-to-SQL prompt: schema description, rules and few-shot examples.

Built from `orders_nl.domain` so statuses/countries never drift from the data.
`build_messages` takes the pieces as parameters, so a different schema or
example set can be plugged in without editing this module.
"""

import re
from collections.abc import Sequence

from orders_nl.domain import KNOWN_COUNTRIES, SALE_STATUSES, OrderStatus

CANNOT_ANSWER = "CANNOT_ANSWER"

_statuses = ", ".join(f"'{s.value}'" for s in OrderStatus)
_sale_statuses = ", ".join(f"'{s.value}'" for s in sorted(SALE_STATUSES))
_countries = ", ".join(f"'{c}'" for c in KNOWN_COUNTRIES)

# Keep in sync with sql/schema.sql and sql/views.sql.
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
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_system_prompt(schema: str = SCHEMA_DESCRIPTION) -> str:
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
    """Chat messages: system rules, few-shot turns, then the user's question."""
    messages = [{"role": "system", "content": build_system_prompt(schema)}]
    for example_question, example_sql in examples:
        messages.append({"role": "user", "content": example_question})
        messages.append({"role": "assistant", "content": example_sql})
    messages.append({"role": "user", "content": question})
    return messages


def extract_sql(content: str) -> str:
    """Pull the SQL out of a model reply: strip fences, whitespace and trailing ';'."""
    match = _FENCE_RE.search(content)
    sql = match.group(1) if match else content
    return sql.strip().rstrip(";").strip()
