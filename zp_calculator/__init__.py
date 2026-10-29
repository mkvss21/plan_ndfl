"""Калькулятор зарплаты и НДФЛ (РФ, шкала 2025 года).

Модули:
    tax       — расчёт НДФЛ нарастающим итогом + стандартные вычеты;
    payroll   — периоды окладов, премии, помесячная сборка дохода;
    config    — загрузка входных данных из JSON/YAML;
    excel     — выгрузка в редактируемый XLSX с формулами;
    cli       — консольный интерфейс (`python -m zp_calculator`).
"""

from zp_calculator.payroll import Bonus, SalaryPeriod, calculate_payroll, summarize
from zp_calculator.tax import Child, DeductionProfile, YearTaxCalculator

__all__ = [
    "Bonus",
    "SalaryPeriod",
    "calculate_payroll",
    "summarize",
    "Child",
    "DeductionProfile",
    "YearTaxCalculator",
]
