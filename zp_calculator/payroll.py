"""Расчёт зарплаты: оклады по периодам + премии, помесячно, с НДФЛ.

Модель данных:
  * SalaryPeriod — период с постоянным окладом (с даты по дату включительно).
    Если указан только `start`, период считается открытым до конца расчётного
    диапазона. Оклады за неполный месяц пересчитываются пропорционально
    отработанным календарным дням месяца.
  * Bonus — премия в конкретном месяце; может быть фиксированной суммой или
    процентом от оклада этого месяца. Премия облагается НДФЛ на общих основаниях.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from zp_calculator.tax import DeductionProfile, MonthResult, YearTaxCalculator

MONTH_NAMES_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


@dataclass
class SalaryPeriod:
    """Период действия одного оклада."""

    salary: Decimal
    start: date
    end: date | None = None  # None = бессрочно (до конца расчётного периода)

    def __post_init__(self):
        if self.end is not None and self.end < self.start:
            raise ValueError(f"Дата окончания ({self.end}) раньше даты начала ({self.start})")

    def overlaps(self, month_start: date, month_end: date) -> bool:
        """Пересекается ли период с месяцем [month_start .. month_end]."""
        if self.start > month_end:
            return False
        if self.end is None:
            return True
        return self.end >= month_start


@dataclass
class Bonus:
    """Премия: фиксированная сумма или процент от оклада месяца."""

    amount: Decimal | None = None
    percent_of_salary: Decimal | None = None  # например 25 => 25% от оклада
    month: date | None = None                 # первый день месяца (2026-03-01)
    note: str = ""

    def resolve(self, month_salary: Decimal) -> Decimal:
        if self.amount is not None:
            return self.amount
        if self.percent_of_salary is not None:
            return month_salary * self.percent_of_salary / Decimal("100")
        return Decimal("0")


@dataclass
class MonthBreakdown:
    """Из чего сложился доход месяца."""

    salary_days: list[tuple[date, date, Decimal]] = field(default_factory=list)  # (от, до, часть оклада)
    salary_total: Decimal = Decimal("0")
    bonuses: list[tuple[str, Decimal]] = field(default_factory=list)  # (описание, сумма)
    bonus_total: Decimal = Decimal("0")


@dataclass
class MonthReport:
    year: int
    month: int  # 1..12
    breakdown: MonthBreakdown
    result: MonthResult

    @property
    def gross(self) -> Decimal:
        return self.result.gross

    @property
    def tax(self) -> Decimal:
        return self.result.tax

    @property
    def net(self) -> Decimal:
        return self.result.net

    @property
    def label(self) -> str:
        return f"{MONTH_NAMES_RU[self.month - 1]} {self.year}"


@dataclass
class PayrollSummary:
    months: list[MonthReport]
    total_gross: Decimal
    total_tax: Decimal
    total_net: Decimal
    total_salary: Decimal
    total_bonus: Decimal
    avg_effective_rate: Decimal


def month_range(start: date, end: date) -> Iterable[tuple[int, int]]:
    """Список (год, месяц) от start до end включительно."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            m, y = 1, y + 1


def _validate_periods(periods: list[SalaryPeriod]) -> None:
    sorted_p = sorted(periods, key=lambda p: p.start)
    for a, b in zip(sorted_p, sorted_p[1:]):
        if a.end is None:
            raise ValueError(f"Период с окладом {a.salary} открыт, но после него есть ещё периоды")
        if b.start <= a.end:
            raise ValueError(
                f"Перекрываются периоды окладов: ...{a.end} и {b.start}..."
            )


def calc_month_salary(year: int, mon: int, periods: list[SalaryPeriod]) -> tuple[Decimal, list]:
    """Оклад месяца с учётом смены оклада внутри месяца (пропорционально дням)."""
    first_day = date(year, mon, 1)
    last_day = date(year, mon, calendar.monthrange(year, mon)[1])
    days_in_month = (last_day - first_day).days + 1

    total = Decimal("0")
    breakdown: list[tuple[date, date, Decimal]] = []
    covered: set[int] = set()

    for p in sorted(periods, key=lambda x: x.start):
        seg_start = max(p.start, first_day)
        seg_end = min(p.end, last_day) if p.end else last_day
        if seg_start > seg_end:
            continue
        seg_days = (seg_end - seg_start).days + 1
        part = p.salary * Decimal(seg_days) / Decimal(days_in_month)
        total += part
        breakdown.append((seg_start, seg_end, part))
        covered.update(range((seg_start - first_day).days, (seg_end - first_day).days + 1))

    return total, breakdown


def uncovered_days(year: int, mon: int, periods: list[SalaryPeriod]) -> int:
    """Сколько календарных дней месяца не закрыто ни одним периодом оклада."""
    first_day = date(year, mon, 1)
    days_in_month = calendar.monthrange(year, mon)[1]
    last_day = date(year, mon, days_in_month)
    covered: set[int] = set()
    for p in periods:
        seg_start = max(p.start, first_day)
        seg_end = min(p.end, last_day) if p.end else last_day
        if seg_start > seg_end:
            continue
        covered.update(range((seg_start - first_day).days, (seg_end - first_day).days + 1))
    return days_in_month - len(covered)


def calculate_payroll(
    year: int,
    periods: list[SalaryPeriod],
    bonuses: list[Bonus] | None = None,
    *,
    start_month: int = 1,
    end_month: int = 12,
    deductions: DeductionProfile | None = None,
) -> list[MonthReport]:
    """Главная функция: помесячный расчёт дохода и НДФЛ за год (или его часть)."""
    _validate_periods(periods)
    bonuses = bonuses or []

    calc = YearTaxCalculator(year=year, deductions=deductions)
    reports: list[MonthReport] = []

    for mon in range(start_month, end_month + 1):
        salary, salary_breakdown = calc_month_salary(year, mon, periods)

        month_bonuses = [b for b in bonuses if b.month and b.month.year == year and b.month.month == mon]
        bonus_items: list[tuple[str, Decimal]] = []
        bonus_total = Decimal("0")
        for b in month_bonuses:
            value = b.resolve(salary)
            bonus_total += value
            if b.amount is not None:
                desc = f"{b.note + ': ' if b.note else ''}{value} ₽"
            else:
                desc = f"{b.note + ': ' if b.note else ''}{b.percent_of_salary}% от оклада = {value} ₽"
            bonus_items.append((desc, value))

        gross = salary + bonus_total
        result = calc.add_month(gross)

        breakdown = MonthBreakdown(
            salary_days=salary_breakdown,
            salary_total=salary,
            bonuses=bonus_items,
            bonus_total=bonus_total,
        )
        reports.append(MonthReport(year=year, month=mon, breakdown=breakdown, result=result))

    return reports


def summarize(reports: list[MonthReport]) -> PayrollSummary:
    total_gross = sum((r.gross for r in reports), Decimal("0"))
    total_tax = sum((r.tax for r in reports), Decimal("0"))
    total_salary = sum((r.breakdown.salary_total for r in reports), Decimal("0"))
    total_bonus = sum((r.breakdown.bonus_total for r in reports), Decimal("0"))
    avg_rate = (total_tax / total_gross) if total_gross else Decimal("0")
    return PayrollSummary(
        months=reports,
        total_gross=total_gross,
        total_tax=total_tax,
        total_net=total_gross - total_tax,
        total_salary=total_salary,
        total_bonus=total_bonus,
        avg_effective_rate=avg_rate,
    )
