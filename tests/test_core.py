"""Тесты расчётного ядра (НДФЛ + периоды окладов + премии).

Запуск:  python -m pytest tests/  (или python tests/test_core.py)
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from zp_calculator.payroll import Bonus, SalaryPeriod, calculate_payroll, summarize
from zp_calculator.tax import (
    Child,
    DeductionProfile,
    YearTaxCalculator,
    round_tax,
    tax_on_cumulative,
)


def D(x) -> Decimal:
    return Decimal(str(x))


class TestTaxScale(unittest.TestCase):
    """Шкала НДФЛ 2025: 13/15/18/20/22% нарастающим итогом."""

    def test_flat_13_percent(self):
        self.assertEqual(tax_on_cumulative(D(1_000_000)), D(130_000))

    def test_bracket_2_boundary_and_value(self):
        # ровно 5 млн -> только первая ставка
        self.assertEqual(tax_on_cumulative(D(5_000_000)), D(650_000))
        # 7 млн -> 650к + 15% с 2 млн = 950к
        self.assertEqual(tax_on_cumulative(D(7_000_000)), D(950_000))

    def test_bracket_3(self):
        # 20 млн: 650к + 15%*15млн(2.25млн) = 2.9 млн
        self.assertEqual(tax_on_cumulative(D(20_000_000)), D(2_900_000))
        # 25 млн: + 18% * 5 млн = 900к -> 3.8 млн
        self.assertEqual(tax_on_cumulative(D(25_000_000)), D(3_800_000))

    def test_bracket_4_and_5(self):
        # 50 млн: 2.9 + 18%*30млн(5.4) = 8.3 млн
        self.assertEqual(tax_on_cumulative(D(50_000_000)), D(8_300_000))
        # 80 млн: 8.3 + 20%*30млн(6) = 14.3 млн
        self.assertEqual(tax_on_cumulative(D(80_000_000)), D(14_300_000))
        # 100 млн: 14.3 + 22%*20млн(4.4) = 18.7 млн
        self.assertEqual(tax_on_cumulative(D(100_000_000)), D(18_700_000))

    def test_monthly_split_sums_to_year_tax(self):
        """Сумма месячных налогов равна годовому налогу (нарастающий итог)."""
        incomes = [D(600_000)] * 12
        calc = YearTaxCalculator(year=2026)
        total_tax = sum((calc.add_month(i).tax for i in incomes), Decimal(0))
        # 7.2 млн: 650к (13% с 5млн) + 15% с 2.2млн (330к) = 980к
        self.assertEqual(total_tax, D(980_000))

    def test_high_income_crosses_brackets_midyear(self):
        # 12 месяцев по 1 млн = 12 млн; налог = 650к + 15%*7млн = 1.7 млн
        calc = YearTaxCalculator(year=2026)
        taxes = [calc.add_month(D(1_000_000)).tax for _ in range(12)]
        self.assertEqual(sum(taxes), D(1_700_000))
        # в месяце перехода через 5 млн налог больше 13%
        self.assertGreater(taxes[5], D(130_000))


class TestChildDeductions(unittest.TestCase):
    def test_deductions_stop_after_limit(self):
        # один ребёнок: вычет 1400 руб пока доход < 450 000
        profile = DeductionProfile(children=[Child("A")])
        calc = YearTaxCalculator(2026, deductions=profile)
        jan = calc.add_month(D(100_000))
        feb = calc.add_month(D(100_000))
        mar = calc.add_month(D(100_000))  # накопленный доход 300к — ещё < 450к
        apr = calc.add_month(D(200_000))  # 500к — лимит превышен, вычета нет
        self.assertEqual(jan.deduction, D(1400))
        self.assertEqual(mar.deduction, D(1400))
        self.assertEqual(apr.deduction, D(0))

    def test_two_children_amounts(self):
        profile = DeductionProfile(children=[Child("A"), Child("B")])
        self.assertEqual(profile.monthly_amount(D(100_000)), D(4200))

    def test_disabled_child_lower_limit(self):
        profile = DeductionProfile(children=[Child("A", disabled=True)])
        self.assertEqual(profile.monthly_amount(D(340_000)), D(1400))
        self.assertEqual(profile.monthly_amount(D(360_000)), D(0))


class TestSalaryPeriods(unittest.TestCase):
    def test_raise_in_middle_of_month_prorated_by_days(self):
        periods = [
            SalaryPeriod(D(100_000), date(2026, 1, 1), date(2026, 1, 15)),
            SalaryPeriod(D(200_000), date(2026, 1, 16)),
        ]
        reports = calculate_payroll(2026, periods, start_month=1, end_month=1)
        jan = reports[0]
        # январь: 15 дней из 31 по 100к + 16 дней из 31 по 200к
        expected = D(100_000) * 15 / 31 + D(200_000) * 16 / 31
        self.assertAlmostEqual(float(jan.breakdown.salary_total), float(expected), places=2)

    def test_bonus_fixed_and_percent(self):
        periods = [SalaryPeriod(D(100_000), date(2026, 1, 1))]
        bonuses = [
            Bonus(amount=D(30_000), month=date(2026, 2, 1)),
            Bonus(percent_of_salary=D(50), month=date(2026, 3, 1)),
        ]
        reports = calculate_payroll(2026, periods, bonuses, start_month=1, end_month=3)
        self.assertEqual(reports[0].gross, D(100_000))
        self.assertEqual(reports[1].gross, D(130_000))
        self.assertEqual(reports[2].gross, D(150_000))

    def test_overlapping_periods_rejected(self):
        periods = [
            SalaryPeriod(D(100_000), date(2026, 1, 1), date(2026, 6, 30)),
            SalaryPeriod(D(120_000), date(2026, 6, 1)),
        ]
        with self.assertRaises(ValueError):
            calculate_payroll(2026, periods)

    def test_gap_month_zero_income(self):
        periods = [
            SalaryPeriod(D(100_000), date(2026, 1, 1), date(2026, 1, 31)),
            SalaryPeriod(D(100_000), date(2026, 3, 1)),
        ]
        reports = calculate_payroll(2026, periods, start_month=1, end_month=3)
        self.assertEqual(reports[1].gross, D(0))
        self.assertEqual(reports[1].tax, D(0))


class TestEndToEnd(unittest.TestCase):
    def test_net_equals_gross_minus_tax_everywhere(self):
        cfg_periods = [
            SalaryPeriod(D(150_000), date(2026, 1, 1), date(2026, 6, 30)),
            SalaryPeriod(D(180_000), date(2026, 7, 1)),
        ]
        bonuses = [Bonus(amount=D(50_000), month=date(2026, 3, 1))]
        reports = calculate_payroll(2026, cfg_periods, bonuses)
        s = summarize(reports)
        for r in reports:
            self.assertEqual(r.net, r.gross - r.tax)
        self.assertEqual(s.total_net, s.total_gross - s.total_tax)
        # годовой налог = годовая шкала от дохода (всё ниже 5 млн -> ровно 13%)
        self.assertEqual(s.total_gross, D(2_030_000))
        self.assertEqual(s.total_tax, D(263_900))

    def test_child_deductions_reduce_early_months_only(self):
        """Два ребёнка: вычет 4200 ₽ действует, пока доход с начала года < 450 000."""
        periods = [SalaryPeriod(D(150_000), date(2026, 1, 1), date(2026, 6, 30)),
                   SalaryPeriod(D(180_000), date(2026, 7, 1))]
        deductions = DeductionProfile([Child("Анна"), Child("Борис")])
        reports = calculate_payroll(2026, periods, [], deductions=deductions)
        jan, feb, mar = reports[0], reports[1], reports[2]
        self.assertEqual(jan.result.deduction, D(4200))
        self.assertEqual(jan.tax, round_tax((D(150_000) - D(4200)) * D("0.13")))  # 18 954
        self.assertEqual(feb.result.deduction, D(4200))   # накопленный доход 300к < 450к
        self.assertEqual(mar.result.deduction, D(0))      # 450к превышен в марте
        self.assertLess(summarize(reports).total_tax, D(263_900))

    def test_simple_13_percent_year(self):
        """Оклад 100к без детей: 13% каждый месяц ровно."""
        reports = calculate_payroll(2026, [SalaryPeriod(D(100_000), date(2026, 1, 1))])
        for r in reports:
            self.assertEqual(r.tax, D(13_000))
            self.assertEqual(r.net, D(87_000))


if __name__ == "__main__":
    unittest.main(verbosity=2)
