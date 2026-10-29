"""Упаковка расчёта в Excel-файл (открытый, редактируемый пользователем).

Листы:
  * «Расчёт» — таблица месяцев с формулами по окладам/премиям и итогами.
  * «Оклады» — исходные периоды окладов.
  * «Премии» — исходные премии.
  * «НДФЛ»   — шкала налога и параметры вычетов.

Формулы считаются внутри Excel (чтобы пользователь мог менять данные), но
значения в тех же ячейках заранее проставлены расчётным ядром — файл можно
читать и без пересчёта формул.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from zp_calculator.payroll import Bonus, MonthReport, SalaryPeriod, uncovered_days
from zp_calculator.tax import (
    CHILD_DEDUCTION_AMOUNTS,
    CHILD_INCOME_LIMIT,
    brackets_for_year,
)

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
TOTAL_FILL = PatternFill("solid", fgColor="DDEBF7")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _style_header(cell):
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER


MONEY_FMT = "#,##0.00"
PCT_FMT = "0.00%"


def build_workbook(
    reports: list[MonthReport],
    periods: list[SalaryPeriod],
    bonuses: list[Bonus],
    *,
    children_count: int = 0,
) -> Workbook:
    wb = Workbook()

    # ------------------------------------------------------------------ Оклады
    ws_sal = wb.active
    ws_sal.title = "Оклады"
    ws_sal.append(["Название периода", "Дата с", "Дата по", "Оклад, руб"])
    for c in ws_sal[1]:
        _style_header(c)
    for p in periods:
        label = f"{p.start:%m.%Y} — {p.end:%m.%Y}" if p.end else f"с {p.start:%m.%Y}"
        ws_sal.append([label, p.start, p.end, float(p.salary)])
    for row in ws_sal.iter_rows(min_row=2):
        row[1].number_format = "yyyy-mm-dd"
        row[2].number_format = "yyyy-mm-dd"
        row[3].number_format = MONEY_FMT
        for cell in row:
            cell.border = BORDER
    for col, width in zip("ABCD", (26, 14, 14, 16)):
        ws_sal.column_dimensions[col].width = width

    # ------------------------------------------------------------------- Премии
    ws_bon = wb.create_sheet("Премии")
    ws_bon.append(["Месяц", "Комментарий", "Сумма, руб", "% от оклада"])
    for c in ws_bon[1]:
        _style_header(c)
    for b in bonuses:
        ws_bon.append([
            b.month.strftime("%Y-%m") if b.month else "",
            b.note,
            float(b.amount) if b.amount is not None else None,
            float(b.percent_of_salary) if b.percent_of_salary is not None else None,
        ])
    for row in ws_bon.iter_rows(min_row=2):
        row[2].number_format = MONEY_FMT
        row[3].number_format = PCT_FMT
        for cell in row:
            cell.border = BORDER
    for col, width in zip("ABCD", (12, 30, 16, 16)):
        ws_bon.column_dimensions[col].width = width

    # -------------------------------------------------------------------- НДФЛ
    tax_year = reports[0].year if reports else 2025
    ws_tax = wb.create_sheet("НДФЛ")
    ws_tax.append([f"Ставка НДФЛ, шкала {tax_year} (нарастающим итогом с начала года)"])
    ws_tax["A1"].font = Font(bold=True)
    ws_tax.append([])
    ws_tax.append(["Доход с начала года до, руб", "Ставка"])
    for c in ws_tax[3]:
        _style_header(c)
    prev = 0
    for threshold, rate in brackets_for_year(tax_year):
        limit = float(threshold) if threshold is not None else "∞"
        ws_tax.append([f"от {prev:,.0f} до {limit}" if threshold else f"свыше {prev:,.0f}", float(rate)])
        prev = int(threshold) if threshold else prev
    ws_tax.append([])
    ws_tax.append(["Стандартные вычеты на детей (ст. 218 НК РФ)"])
    ws_tax[f"A{ws_tax.max_row}"].font = Font(bold=True)
    ws_tax.append(["Первый ребёнок, руб/мес", float(CHILD_DEDUCTION_AMOUNTS[0])])
    ws_tax.append(["Второй ребёнок, руб/мес", float(CHILD_DEDUCTION_AMOUNTS[1])])
    ws_tax.append(["Третий и далее, руб/мес", float(CHILD_DEDUCTION_AMOUNTS[2])])
    ws_tax.append(["Лимит дохода для вычета, руб", float(CHILD_INCOME_LIMIT)])
    ws_tax.append(["Детей в расчёте", children_count])
    ws_tax.column_dimensions["A"].width = 42
    ws_tax.column_dimensions["B"].width = 16

    # ------------------------------------------------------------------ Расчёт
    ws = wb.create_sheet("Расчёт", 0)
    headers = [
        "Месяц", "Оклад начислено", "Премии", "Итого (грязными)",
        "Вычет", "База НДФЛ", "НДФЛ", "На руки", "Ставка НДФЛ",
        "Итог год начислено", "Итог год НДФЛ", "Итог год на руки",
    ]
    ws.append(headers)
    for c in ws[1]:
        _style_header(c)

    first_data_row = 2
    for i, r in enumerate(reports):
        row = first_data_row + i
        gross_formula = f"=B{row}+C{row}"
        base_formula = f"=MAX(D{row}-E{row},0)"
        net_formula = f"=D{row}-G{row}"
        ws.append([
            r.label,
            float(r.breakdown.salary_total),
            float(r.breakdown.bonus_total),
            gross_formula,
            float(r.result.deduction),
            base_formula,
            float(r.result.tax),
            net_formula,
            r.result.rate_label,  # фактическая ставка из шкалы: 13/15/18/20/22%
            float(r.result.cumulative_gross),
            float(r.result.cumulative_tax),
            float(r.result.cumulative_gross - r.result.cumulative_tax),
        ])

    # ---------------------------------------------------------------------------
    # НДФЛ считается формулой по годовой шкале нарастающим итогом:
    #   налог_накопленный(F_cum) - налог_накопленный(F_{row-1}),
    # где F — база месяца, а cumulative base = сумма F до текущей строки.
    # В ячейку G пишем формулу; Excel пересчитает её при изменении данных,
    # поэтому колонка E (вычеты) тоже участвует через F.
    # ---------------------------------------------------------------------------
    for i, r in enumerate(reports):
        row = first_data_row + i
        cum_base = f"SUM($F${first_data_row}:F{row})"
        prev_base = f"SUM($F${first_data_row}:F{row - 1})" if row > first_data_row else "0"
        tax_formula = (
            "=ROUND("
            f"IF({cum_base}<=5000000,{cum_base}*0.13,"
            f"IF({cum_base}<=20000000,650000+({cum_base}-5000000)*0.15,"
            f"IF({cum_base}<=50000000,2900000+({cum_base}-20000000)*0.18,"
            f"IF({cum_base}<=80000000,8300000+({cum_base}-50000000)*0.2,"
            f"14300000+({cum_base}-80000000)*0.22))))"
            f"-IF({prev_base}<=5000000,{prev_base}*0.13,"
            f"IF({prev_base}<=20000000,650000+({prev_base}-5000000)*0.15,"
            f"IF({prev_base}<=50000000,2900000+({prev_base}-20000000)*0.18,"
            f"IF({prev_base}<=80000000,8300000+({prev_base}-50000000)*0.2,"
            f"14300000+({prev_base}-80000000)*0.22))))"
            ",0)"
        )
        ws.cell(row=row, column=7).value = tax_formula
        # ставка из шкалы (текстом: 13% / 15% и т.д.) и накопленные итоги — формулы
        cum_tax = f"SUM($G${first_data_row}:G{row})"
        prev_tax = f"SUM($G${first_data_row}:G{row - 1})" if row > first_data_row else "0"
        rate_formula = (
            '=IF(D{r}=0,"",'
            'IF(ROUND({ct}-{pt},0)<=0,"",'
            'IF({cb}<=2400000,"13%",IF({cb}<=5000000,'
            'IF({pb}<2400000,"13 / 15%","15%"),'
            'IF({cb}<=20000000,IF({pb}<5000000,"15 / 18%","18%"),'
            'IF({cb}<=50000000,IF({pb}<20000000,"18 / 20%","20%"),"22%"))))))'.format(
                r=row, ct=cum_tax, pt=prev_tax, cb=cum_base, pb=prev_base)
        )
        ws.cell(row=row, column=9).value = rate_formula
        ws.cell(row=row, column=10).value = f"=SUM($D${first_data_row}:D{row})"
        ws.cell(row=row, column=11).value = f"=SUM($G${first_data_row}:G{row})"
        ws.cell(row=row, column=12).value = f"=J{row}-K{row}"

    total_row = first_data_row + len(reports)
    last_data_row = total_row - 1
    sums = ["ИТОГО"]
    for col_idx in range(2, 13):
        letter = get_column_letter(col_idx)
        if col_idx == 9:  # ставки, применявшиеся в периоде — список уникальных значений колонки I
            rng = f"I{first_data_row}:I{last_data_row}"
            sums.append(f'=TEXTJOIN(", ",TRUE,{rng})')
        elif col_idx in (10, 11, 12):  # накопительные итоги — берём последнее значение
            sums.append(f"={letter}{last_data_row}")
        else:
            sums.append(f"=SUM({letter}{first_data_row}:{letter}{last_data_row})")
    ws.append(sums)
    for cell in ws[total_row]:
        cell.font = Font(bold=True)
        cell.fill = TOTAL_FILL

    for row in ws.iter_rows(min_row=first_data_row, max_row=total_row):
        for cell in row:
            cell.border = BORDER
            if cell.column_letter == "I":
                cell.number_format = "General"  # ставка — текст из шкалы (13% / 15%)
            elif cell.column_letter in "BCDEFGHJKL":
                cell.number_format = MONEY_FMT

    widths = (16, 16, 14, 16, 10, 14, 12, 14, 12, 18, 16, 18)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "B2"

    # предупреждения о днях без оклада
    warnings = []
    for r in reports:
        miss = uncovered_days(r.year, r.month, periods)
        if miss:
            warnings.append(
                f"{r.label}: {miss} дн. месяца не покрыто ни одним периодом оклада "
                "(оклад за эти дни не начислен)"
            )
    if warnings:
        wrow = total_row + 2
        ws.cell(row=wrow, column=1, value="Внимание:").font = Font(bold=True, color="C00000")
        for j, text in enumerate(warnings, start=1):
            ws.cell(row=wrow + j, column=1, value=text)

    return wb
