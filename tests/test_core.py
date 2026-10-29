"""Тесты расчётного ядра (НДФЛ + периоды окладов + премии).

Запуск:  python -m pytest tests/  (или python tests/test_core.py)
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from zp_calculator.payroll import Bonus, SalaryPeriod, calculate_payroll, summarize
from zp_calculator.tax import (
    BRACKETS_2025,
    BRACKETS_2026,
    Child,
    DeductionProfile,
    YearTaxCalculator,
    brackets_for_year,
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
        calc = YearTaxCalculator(year=2025)
        total_tax = sum((calc.add_month(i).tax for i in incomes), Decimal(0))
        # 7.2 млн: 650к (13% с 5млн) + 15% с 2.2млн (330к) = 980к
        self.assertEqual(total_tax, D(980_000))

    def test_high_income_crosses_brackets_midyear(self):
        # 12 месяцев по 1 млн = 12 млн; налог = 650к + 15%*7млн = 1.7 млн
        calc = YearTaxCalculator(year=2025)
        taxes = [calc.add_month(D(1_000_000)).tax for _ in range(12)]
        self.assertEqual(sum(taxes), D(1_700_000))
        # в месяце перехода через 5 млн налог больше 13%
        self.assertGreater(taxes[5], D(130_000))


class TestScale2026(unittest.TestCase):
    """С 2026 года первая ступень 13% действует только до 2,4 млн накопленного дохода."""

    def test_2025_scale_still_13_until_5mln(self):
        self.assertEqual(tax_on_cumulative(D(2_400_000), BRACKETS_2025), D(312_000))
        self.assertEqual(tax_on_cumulative(D(5_000_000), BRACKETS_2025), D(650_000))

    def test_2026_scale_15_percent_above_2_4mln(self):
        # ровно 2,4 млн -> только 13%
        self.assertEqual(tax_on_cumulative(D(2_400_000), BRACKETS_2026), D(312_000))
        # 3 млн -> 312к + 15% с 600к (90к) = 402к
        self.assertEqual(tax_on_cumulative(D(3_000_000), BRACKETS_2026), D(402_000))
        # 5 млн -> 312к + 15% с 2,6 млн (390к) = 702к
        self.assertEqual(tax_on_cumulative(D(5_000_000), BRACKETS_2026), D(702_000))
        # 6 млн -> 702к + 18% с 1 млн = 882к
        self.assertEqual(tax_on_cumulative(D(6_000_000), BRACKETS_2026), D(882_000))
        # 25 млн -> 702к + 18%*15млн(2.7) + 20%*5млн(1) = 4.402 млн
        self.assertEqual(tax_on_cumulative(D(25_000_000), BRACKETS_2026), D(4_402_000))
        # 55 млн -> 702к + 18%*15млн(2.7) + 20%*5млн(1) + 22% с 5 млн (1.1) = 10.502 млн
        self.assertEqual(tax_on_cumulative(D(55_000_000), BRACKETS_2026), D(10_502_000))

    def test_brackets_for_year_selection(self):
        self.assertIs(brackets_for_year(2025), BRACKETS_2025)
        self.assertIs(brackets_for_year(2026), BRACKETS_2026)
        self.assertIs(brackets_for_year(2027), BRACKETS_2026)

    def test_monthly_crossing_2_4mln_in_2026(self):
        """Доход 300к/мес: превышение 2,4 млн происходит внутри августа —
        налог переходного месяца больше 13% (часть базы уже по ставке 15%)."""
        calc = YearTaxCalculator(year=2026)
        taxes = [calc.add_month(D(300_000)).tax for _ in range(12)]
        # январь–июль: накоплено до 2,1 млн — чистые 13%
        self.assertEqual(taxes[6], D(39_000))
        # август: база 2,1->2,4 млн — 13% (39 000), ровно на границе, без превышения
        self.assertEqual(taxes[7], D(39_000))
        # сентябрь: база 2,4->2,7 млн — вся выше границы -> ровно 15%
        self.assertEqual(taxes[8], D(45_000))
        # год: 312к (13% с 2,4 млн) + 15% с 1,2 млн = 492к
        self.assertEqual(sum(taxes), D(492_000))

    def test_month_crossing_midmonth_has_rate_above_13(self):
        """Если граница 2,4 млн проходит внутри месяца (доход 350к/мес),
        налог этого месяца эффективной ставкой больше 13%."""
        calc = YearTaxCalculator(year=2026)
        taxes = [calc.add_month(D(350_000)).tax for _ in range(12)]
        # июль: база 2,1->2,45 млн: 300к по 13% (39 000) + 50к по 15% (7 500)
        self.assertEqual(taxes[6], D(46_500))
        self.assertGreater(taxes[6] / D(350_000), D("0.13"))

    def test_monthly_crossing_2_4mln_midmonth_december(self):
        """Доход 200к/мес: ровно 2,4 млн накапливается к концу декабря,
        поэтому весь 2026 год идёт 13% (превышения нет)."""
        calc = YearTaxCalculator(year=2026)
        taxes = [calc.add_month(D(200_000)).tax for _ in range(12)]
        self.assertTrue(all(t == D(26_000) for t in taxes))
        self.assertEqual(sum(taxes), D(312_000))
        # а при доходе 210к/мес превышение происходит в декабре
        calc2 = YearTaxCalculator(year=2026)
        taxes2 = [calc2.add_month(D(210_000)).tax for _ in range(12)]
        self.assertGreater(taxes2[11], round_tax(D(210_000) * D("0.13")))

    def test_same_income_different_tax_by_year(self):
        """Один и тот же доход в 2025 и 2026 даёт разный налог после 2,4 млн."""
        c25 = YearTaxCalculator(year=2025)
        c26 = YearTaxCalculator(year=2026)
        t25 = sum((c25.add_month(D(300_000)).tax for _ in range(12)), Decimal(0))
        t26 = sum((c26.add_month(D(300_000)).tax for _ in range(12)), Decimal(0))
        # 3,6 млн: 2025 — всё 13% = 468 000; 2026 — 312к + 15% с 1,2 млн = 492 000
        self.assertEqual(t25, D(468_000))
        self.assertEqual(t26, D(492_000))


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


class TestDateValidation(unittest.TestCase):
    """Проверка существования дат (31.09, 30.02 и т.п.) при загрузке конфигурации."""

    @staticmethod
    def _raw(start="2026-01-01", end=None, bonus_month="2026-03-01"):
        p = {"salary": 150000, "start": start}
        if end:
            p["end"] = end
        return {
            "year": 2026,
            "salary_periods": [p],
            "bonuses": [{"month": bonus_month, "amount": 50000}],
        }

    def test_valid_config_passes(self):
        from zp_calculator.config import parse_config
        cfg = parse_config(self._raw(start="2026-01-01", end="2026-06-30"))
        self.assertEqual(cfg.periods[0].end, date(2026, 6, 30))

    def test_september_31_rejected(self):
        from zp_calculator.config import parse_config
        with self.assertRaises(ValueError) as ctx:
            parse_config(self._raw(end="2026-09-31"))
        self.assertIn("Дата окончания периода №1", str(ctx.exception))

    def test_start_date_31_september_rejected(self):
        from zp_calculator.config import parse_config
        with self.assertRaises(ValueError):
            parse_config(self._raw(start="2026-09-31"))

    def test_february_30_rejected_but_leap_day_ok(self):
        from zp_calculator.config import parse_config
        with self.assertRaises(ValueError):
            parse_config(self._raw(end="2026-02-30"))  # 2026 — невисокосный
        with self.assertRaises(ValueError):
            parse_config(self._raw(end="2026-02-29"))
        cfg = parse_config(self._raw(end="2028-02-29"))  # 2028 — високосный
        self.assertEqual(cfg.periods[0].end, date(2028, 2, 29))

    def test_month_13_and_bad_format_rejected(self):
        from zp_calculator.config import parse_config
        with self.assertRaises(ValueError):
            parse_config(self._raw(start="2026-13-01"))
        with self.assertRaises(ValueError):
            parse_config(self._raw(start="31.09.2026"))
        with self.assertRaises(ValueError):
            parse_config(self._raw(bonus_month="2026-13-01"))


if __name__ == "__main__":
    unittest.main(verbosity=2)