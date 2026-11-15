"""Natural-language answers: number grounding, templates and the composer."""

from datetime import date
from decimal import Decimal

import pytest

from orders_nl.answers import AnswerComposer, find_unsupported_numbers, template_answer
from orders_nl.answers.grounding import allowed_numbers, interpretations
from orders_nl.answers.templates import format_number
from orders_nl.db import QueryResult
from orders_nl.errors import LLMError

# --- number parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("6.215,32", {Decimal("6215.32")}),  # Spanish
        ("6,215.32", {Decimal("6215.32")}),  # English
        ("6215,32", {Decimal("6215.32")}),
        ("6215.32", {Decimal("6215.32")}),
        ("1.138", {Decimal("1138"), Decimal("1.138")}),  # ambiguous: both readings
        ("1,138", {Decimal("1138"), Decimal("1.138")}),
        ("2025", {Decimal("2025")}),
        ("1.2.3", set()),  # not a number
    ],
)
def test_interpretations(token: str, expected: set[Decimal]) -> None:
    """Numeric tokens are read under both Spanish and English notation."""
    assert interpretations(token) == expected


# --- grounding -------------------------------------------------------------------------

ROWS = [
    {"country": "España", "orders": 35, "total_amount": 6215.32, "month_start": date(2025, 3, 1)},
    {"country": "Chile", "orders": 8, "total_amount": 1138.0, "month_start": date(2025, 3, 1)},
]
ALLOWED = allowed_numbers(ROWS, "¿Top 2 países en marzo?", "SELECT ... WHERE month = 3", row_count=len(ROWS))


@pytest.mark.parametrize(
    "answer",
    [
        "España vendió 6.215,32 € y Chile 1.138,00 €.",  # Spanish format
        "España vendió 6,215.32 € (35 pedidos).",  # English format
        "España vendió unos 6.215 € y 6.215,3 €.",  # rounded
        "Los 2 países con más ventas en marzo de 2025.",  # question, row count, date
        "1. España; 2. Chile.",  # enumeration up to row_count
        "En el mes 3 hubo ventas.",  # value from the SQL
        "No hay números en esta frase.",
    ],
)
def test_grounded_answers_pass(answer: str) -> None:
    """Sentences whose numbers all come from rows/question/SQL are accepted."""
    assert find_unsupported_numbers(answer, ALLOWED) == []


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("España vendió 6.512,32 €.", ["6.512,32"]),  # swapped digits
        ("España tiene el 85% de las ventas.", ["85"]),  # computed percentage
        ("Entre ambos suman 7.353,32 €.", ["7.353,32"]),  # computed total
        ("Hubo 4 países.", ["4"]),  # > row_count and not in data/question/SQL
    ],
)
def test_invented_numbers_are_reported(answer: str, bad: list[str]) -> None:
    """Altered, computed or made-up numbers are reported, exactly as written."""
    assert find_unsupported_numbers(answer, ALLOWED) == bad


# --- templates -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1138, "1.138"), (6215.32, "6.215,32"), (Decimal("0.5"), "0,50"), (1234567.891, "1.234.567,89")],
)
def test_format_number_spanish(value, expected: str) -> None:
    """Template numbers use '.' for thousands and ',' for decimals."""
    assert format_number(value) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (QueryResult(["total"], []), "No se encontraron resultados para esta pregunta."),
        (QueryResult(["total"], [{"total": None}]), "No hay datos para esta pregunta."),
        (QueryResult(["total"], [{"total": 6215.32}]), "El resultado es 6.215,32 (total)."),
        (
            QueryResult(["product", "total"], [{"product": "Portátil", "total": 22481.85}]),
            "Resultado: product: Portátil, total: 22.481,85.",
        ),
        (
            QueryResult(["country", "orders"], [{"country": "Chile", "orders": 8}, {"country": "España", "orders": 35}]),
            "Se encontraron 2 resultados: Chile (orders 8); España (orders 35).",
        ),
    ],
)
def test_template_answer(result: QueryResult, expected: str) -> None:
    """Each result shape (empty, null, single value, single row, many rows) has its sentence."""
    assert template_answer(result) == expected


def test_template_truncates_long_results() -> None:
    """Long results list `max_items` rows and summarize the rest."""
    rows = [{"n": i} for i in range(8)]
    assert template_answer(QueryResult(["n"], rows), max_items=3) == "Se encontraron 8 resultados: 0; 1; 2; y 5 más."


# --- composer ------------------------------------------------------------------------

RESULT = QueryResult(columns=["total_sales"], rows=[{"total_sales": 6215.32}])
QUESTION = "¿Cuánto vendimos en España en marzo?"
SQL = "SELECT SUM(total_amount) AS total_sales FROM v_sales_by_month_country WHERE month = 3"


class _Writer:
    """Minimal AnswerWriter stub for composer tests."""
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        """Configure the canned sentence or error."""
        self.text, self.error = text, error

    def write_answer(self, question: str, sql: str, result: QueryResult) -> str:
        """Raise the canned error, or return the canned sentence."""
        if self.error:
            raise self.error
        return self.text


def test_composer_uses_grounded_llm_text() -> None:
    """A grounded sentence is returned (whitespace-stripped) with source 'llm'."""
    answer = AnswerComposer(_Writer("  En marzo se vendieron 6.215,32 € en España. ")).compose(QUESTION, SQL, RESULT)
    assert (answer.text, answer.source) == ("En marzo se vendieron 6.215,32 € en España.", "llm")


@pytest.mark.parametrize(
    "writer",
    [
        _Writer("Se vendieron 9.999 €."),  # invented number
        _Writer("   "),  # empty
        _Writer("x" * 700),  # too long
        _Writer(error=LLMError("down")),  # LLM failure
    ],
)
def test_composer_falls_back_to_template(writer: _Writer) -> None:
    """Invented numbers, empty/too-long text and LLM errors all fall back to the template."""
    answer = AnswerComposer(writer).compose(QUESTION, SQL, RESULT)
    assert (answer.text, answer.source) == ("El resultado es 6.215,32 (total_sales).", "template")


def test_composer_without_writer_uses_template() -> None:
    """With no writer configured, only templates are produced."""
    assert AnswerComposer(None).compose(QUESTION, SQL, RESULT).source == "template"


# --- attribution (numbers next to the right row) ----------------------------------------

from orders_nl.answers import find_misattributed_numbers  # noqa: E402

TOP3 = [
    {"customer_rank": 1, "customer": "Albano Llopis Hierro", "total_amount": 38655.46},
    {"customer_rank": 2, "customer": "Buenaventura de Bonet", "total_amount": 14180.59},
    {"customer_rank": 3, "customer": "Hugo Colón Ríos", "total_amount": 13126.62},
]
TOP3_Q = "¿Quiénes son los 3 mejores clientes?"
TOP3_SQL = "SELECT customer_rank, customer, total_amount FROM v_top_customers ORDER BY customer_rank LIMIT 3"


def test_reversed_ranking_is_detected() -> None:
    """Regression: real model output. Every number exists, but ranks are paired wrongly."""
    answer = (
        "Los 3 mejores clientes son:\n\n"
        "1. Hugo Colón Ríos con un total de 13.127 €.\n"
        "2. Buenaventura de Bonet con un total de 14.181 €.\n"
        "3. Albano Llopis Hierro con un total de 38.655 €."
    )
    assert find_unsupported_numbers(answer, allowed_numbers(TOP3, TOP3_Q, TOP3_SQL, row_count=3)) == []
    assert find_misattributed_numbers(answer, TOP3, TOP3_Q, TOP3_SQL) == ["1", "3"]


@pytest.mark.parametrize(
    "answer",
    [
        "Los 3 mejores clientes son:\n1. Albano Llopis Hierro con 38.655 €.\n"
        "2. Buenaventura de Bonet con 14.181 €.\n3. Hugo Colón Ríos con 13.127 €.",
        "El mejor cliente es Albano Llopis Hierro (38.655,46 €), seguido de Buenaventura de Bonet "
        "con 14.180,59 € y Hugo Colon Rios con 13.126,62 €.",  # accents dropped still matches
        "Los 3 mejores clientes suman más que el resto.",  # numbers from the question only
    ],
)
def test_correct_attribution_passes(answer: str) -> None:
    """Correct pairings pass, including accent-less names and question-only numbers."""
    assert find_misattributed_numbers(answer, TOP3, TOP3_Q, TOP3_SQL) == []


def test_number_after_wrong_name_is_detected() -> None:
    """Swapping two countries' figures is detected."""
    rows = [{"country": "Chile", "orders": 8}, {"country": "España", "orders": 35}]
    assert find_misattributed_numbers("En Chile hubo 8 pedidos y en España 35.", rows) == []
    assert find_misattributed_numbers("En Chile hubo 35 pedidos y en España 8.", rows) == ["35", "8"]


def test_name_in_many_rows_allows_any_of_its_values() -> None:
    """'España' appears once per month: any of its monthly figures is fine after it."""
    rows = [{"month": 1, "country": "España", "total": 100.0}, {"month": 2, "country": "España", "total": 200.0}]
    assert find_misattributed_numbers("España vendió 100 € en enero y 200 € en febrero.", rows) == []


def test_composer_rejects_misattributed_llm_answer() -> None:
    """A misattributed sentence never reaches the client: template instead."""
    writer = _Writer("1. Hugo Colón Ríos con 13.127 €.\n2. Albano Llopis Hierro con 38.655 €.")
    answer = AnswerComposer(writer).compose(TOP3_Q, TOP3_SQL, QueryResult(list(TOP3[0]), TOP3))
    assert answer.source == "template"


def test_list_markers_follow_result_order_without_rank_column() -> None:
    """No rank column: markers must match the rows' order in the result."""
    rows = [{"country": "Chile", "orders": 8}, {"country": "España", "orders": 35}]
    assert find_misattributed_numbers("1. Chile: 8 pedidos.\n2. España: 35 pedidos.", rows) == []
    assert find_misattributed_numbers("1. España: 35 pedidos.\n2. Chile: 8 pedidos.", rows) == ["1", "2"]


def test_template_labels_rows_by_name_not_rank() -> None:
    """Ranked rows are labelled by the customer name, not the rank number."""
    result = QueryResult(["customer_rank", "customer", "total_amount"], TOP3[:2])
    assert template_answer(result) == (
        "Se encontraron 2 resultados: "
        "Albano Llopis Hierro (customer_rank 1, total_amount 38.655,46); "
        "Buenaventura de Bonet (customer_rank 2, total_amount 14.180,59)."
    )
