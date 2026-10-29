"""Загрузка конфигурации расчёта из JSON/YAML-файла.

Пример config.yaml / config.json:

    year: 2026
    start_month: 1          # необязательно, по умолчанию 1
    end_month: 12           # необязательно, по умолчанию 12
    salary_periods:
      - salary: 150000
        start: 2026-01-01
        end: 2026-06-30     # необязательно; если нет — до конца года
      - salary: 180000
        start: 2026-07-01
    bonuses:
      - month: 2026-03-01
        amount: 50000
        note: "премия за квартал"
      - month: 2026-12-01
        percent_of_salary: 100
        note: "13-я зарплата"
    children:               # необязательно, стандартные вычеты ст. 218 НК РФ
      - name: "Анна"
      - name: "Борис"
        disabled: false
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from zp_calculator.payroll import Bonus, SalaryPeriod
from zp_calculator.tax import Child, DeductionProfile


@dataclass
class PayrollConfig:
    year: int
    periods: list[SalaryPeriod]
    bonuses: list[Bonus]
    deductions: DeductionProfile
    start_month: int = 1
    end_month: int = 12


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def parse_config(raw: dict) -> PayrollConfig:
    year = int(raw["year"])
    periods = [
        SalaryPeriod(
            salary=_dec(p["salary"]),
            start=_date(p["start"]),
            end=_date(p["end"]) if p.get("end") else None,
        )
        for p in raw.get("salary_periods", [])
    ]
    if not periods:
        raise ValueError("В конфигурации не указано ни одного периода оклада (salary_periods)")

    bonuses = [
        Bonus(
            amount=_dec(b["amount"]) if b.get("amount") is not None else None,
            percent_of_salary=_dec(b["percent_of_salary"]) if b.get("percent_of_salary") is not None else None,
            month=_date(b["month"]),
            note=b.get("note", ""),
        )
        for b in raw.get("bonuses", [])
    ]
    for b in bonuses:
        if b.amount is None and b.percent_of_salary is None:
            raise ValueError(f"Премия в {b.month}: укажите amount или percent_of_salary")

    children = [Child(name=c.get("name", ""), disabled=bool(c.get("disabled", False)))
                for c in raw.get("children", [])]
    deductions = DeductionProfile(children=children)

    return PayrollConfig(
        year=year,
        periods=periods,
        bonuses=bonuses,
        deductions=deductions,
        start_month=int(raw.get("start_month", 1)),
        end_month=int(raw.get("end_month", 12)),
    )


def load_config(path: str | Path) -> PayrollConfig:
    """Читает JSON всегда, YAML — если установлен pyyaml."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ValueError(
                "Файл похож на YAML, но pyyaml не установлен. "
                "Используйте JSON (`pip install pyyaml` для YAML)."
            ) from exc
        raw = yaml.safe_load(text)
    return parse_config(raw)
