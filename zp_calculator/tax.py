"""НДФЛ (РФ): прогрессивная шкала 2025/2026 годов + стандартные вычеты на детей.

Шкала до 2026 года включительно переходного периода (ФЗ №176-ФЗ от 12.07.2024,
действует с 01.01.2025), считается нарастающим итогом с начала года по
совокупному доходу:

    2025 год:
        до 5    млн руб  -> 13%
        св. 5   до 20 млн -> 13% с 5 млн + 15% с превышения
        св. 20  до 50 млн -> ... + 18% с превышения
        св. 50  до 80 млн -> ... + 20% с превышения
        св. 80  млн       -> ... + 22% с превышения

    С 2026 года верхняя граница первой ступени (13%) снижена до 2,4 млн руб:
        до 2,4  млн руб  -> 13%
        св. 2,4 до 5 млн  -> 15% с превышения
        св. 5   до 20 млн -> 18% с превышения
        св. 20  до 50 млн -> 20% с превышения
        св. 50  млн       -> 22% с превышения

Стандартные вычеты (ст. 218 НК РФ) уменьшают налоговую базу каждый месяц,
пока совокупный доход с начала года не превысит лимит:
    первый ребёнок  — 1400 руб/мес
    второй ребёнок  — 2800 руб/мес
    третий и далее — 6000 руб/мес
    лимит дохода   — 450 000 руб (для детей-инвалидов — 350 000 руб)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# Шкалы НДФЛ (ФЗ №176-ФЗ от 12.07.2024)
# ---------------------------------------------------------------------------


def _d(x: str) -> Decimal:
    return Decimal(x)


BRACKETS_2025 = [
    (_d("5000000"), _d("0.13")),
    (_d("20000000"), _d("0.15")),
    (_d("50000000"), _d("0.18")),
    (_d("80000000"), _d("0.20")),
    (None, _d("0.22")),
]

# С 2026 года первая ступень 13% действует только до 2,4 млн накопленного дохода
BRACKETS_2026 = [
    (_d("2400000"), _d("0.13")),
    (_d("5000000"), _d("0.15")),
    (_d("20000000"), _d("0.18")),
    (_d("50000000"), _d("0.20")),
    (None, _d("0.22")),
]

# Совместимость: BRACKETS — шкала 2025 года
BRACKETS = BRACKETS_2025


def brackets_for_year(year: int) -> list[tuple[Decimal | None, Decimal]]:
    """Возвращает шкалу НДФЛ для налогового периода (года)."""
    return BRACKETS_2025 if year <= 2025 else BRACKETS_2026

CHILD_DEDUCTION_AMOUNTS = [Decimal("1400"), Decimal("2800")] + [Decimal("6000")] * 20
CHILD_INCOME_LIMIT = Decimal("450000")
DISABLED_CHILD_INCOME_LIMIT = Decimal("350000")


def round_tax(value: Decimal) -> Decimal:
    """НДФЛ округляется до целых рублей (ст. 225 п. 6 НК РФ)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tax_on_cumulative(income: Decimal, brackets: list | None = None) -> Decimal:
    """Налог с накопленного дохода по годовой шкале (без учёта вычетов).

    `brackets` — шкала НДФЛ; по умолчанию используется шкала 2025 года.
    Для расчёта по году передавайте `brackets_for_year(year)`.
    """
    if brackets is None:
        brackets = BRACKETS_2025
    income = max(income, Decimal("0"))
    remaining = income
    prev_threshold = Decimal("0")
    tax = Decimal("0")
    for threshold, rate in brackets:
        if threshold is None:
            tax += remaining * rate
            break
        band = min(remaining, threshold - prev_threshold)
        tax += band * rate
        remaining -= band
        prev_threshold = threshold
        if remaining <= 0:
            break
    return tax


@dataclass
class Child:
    """Ребёнок для стандартного вычета."""

    name: str = ""
    disabled: bool = False

    @property
    def limit(self) -> Decimal:
        return DISABLED_CHILD_INCOME_LIMIT if self.disabled else CHILD_INCOME_LIMIT


@dataclass
class DeductionProfile:
    """Стандартные вычеты на детей (важен порядок рождения)."""

    children: list[Child] = field(default_factory=list)

    def monthly_amount(self, cumulative_income: Decimal) -> Decimal:
        """Сумма вычета в месяце при доходе `cumulative_income` с начала года."""
        total = Decimal("0")
        for i, child in enumerate(self.children):
            amount = CHILD_DEDUCTION_AMOUNTS[i] if i < len(CHILD_DEDUCTION_AMOUNTS) else Decimal("0")
            if cumulative_income < child.limit:
                total += amount
        return total


@dataclass
class MonthResult:
    gross: Decimal           # начислено (грязными) за месяц
    deduction: Decimal       # применённый стандартный вычет
    taxable: Decimal         # налогооблагаемая база месяца
    tax: Decimal             # НДФЛ за месяц
    net: Decimal             # на руки
    effective_rate: Decimal  # эффективная ставка НДФЛ за месяц
    cumulative_gross: Decimal
    cumulative_tax: Decimal


class YearTaxCalculator:
    """Считает НДФЛ месяцами нарастающим итогом с начала года.

    Использование::

        calc = YearTaxCalculator(year=2026, deductions=deductions)
        for month_income in incomes:
            res = calc.add_month(month_income)
            res.tax   # НДФЛ за месяц
            res.net   # на руки
    """

    def __init__(self, year: int, deductions: DeductionProfile | None = None):
        self.year = year
        self.brackets = brackets_for_year(year)
        self.deductions = deductions or DeductionProfile()
        self.cumulative_gross = Decimal("0")
        self.cumulative_deductions = Decimal("0")
        self.cumulative_tax = Decimal("0")

    def add_month(self, gross: Decimal) -> MonthResult:
        gross = money(gross)
        base_before = self.cumulative_gross - self.cumulative_deductions
        self.cumulative_gross += gross

        deduction = self.deductions.monthly_amount(self.cumulative_gross)
        self.cumulative_deductions += deduction
        base_after = self.cumulative_gross - self.cumulative_deductions

        month_tax = round_tax(
            tax_on_cumulative(base_after, self.brackets)
            - tax_on_cumulative(base_before, self.brackets)
        )
        if month_tax < 0:  # налог не может быть отрицательным
            month_tax = Decimal("0")

        self.cumulative_tax += month_tax
        effective_rate = (month_tax / gross) if gross else Decimal("0")

        return MonthResult(
            gross=gross,
            deduction=deduction,
            taxable=max(gross - deduction, Decimal("0")),
            tax=month_tax,
            net=money(gross - month_tax),
            effective_rate=effective_rate,
            cumulative_gross=self.cumulative_gross,
            cumulative_tax=self.cumulative_tax,
        )
