"""CLI калькулятора зарплаты: `python -m zp_calculator --config config.json`."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from decimal import Decimal, InvalidOperation

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zp_calculator.config import PayrollConfig, load_config, parse_config
from zp_calculator.excel import build_workbook
from zp_calculator.payroll import Bonus, SalaryPeriod, calculate_payroll, summarize
from zp_calculator.tax import CHILD_DEDUCTION_AMOUNTS


def fmt(value: Decimal) -> str:
    """12 345.67 по-русски, без лишних нулей после запятой."""
    q = value.quantize(Decimal("0.01"))
    s = f"{int(q):,}" if q == q.to_integral_value() else f"{q:,}"
    return s.replace(",", " ")


def fmt_pct(value: Decimal) -> str:
    return f"{value * 100:.2f}".replace(".", ",") + "%"


def render(reports, summary, config: PayrollConfig, console: Console) -> None:
    # --- исходные данные -----------------------------------------------------
    src = Table(title="Исходные данные", title_style="bold", show_edge=False, pad_edge=False)
    src.add_column("Периоды оклада", style="cyan")
    src.add_column("Премии", style="green")
    src.add_column("Вычеты на детей", style="magenta")
    sal_lines = [
        f"{p.start.strftime('%d.%m.%Y')} — "
        f"{p.end.strftime('%d.%m.%Y') if p.end else 'бессрочно'}: {fmt(p.salary)} ₽"
        for p in sorted(config.periods, key=lambda x: x.start)
    ]
    bon_lines = []
    for b in sorted(config.bonuses, key=lambda x: x.month or date.min):
        what = f"{fmt(b.amount)} ₽" if b.amount is not None else f"{b.percent_of_salary}% от оклада"
        note = f" ({b.note})" if b.note else ""
        bon_lines.append(f"{b.month.strftime('%m.%Y')}: {what}{note}")
    ded_lines = []
    for i, ch in enumerate(config.deductions.children):
        amount = CHILD_DEDUCTION_AMOUNTS[i] if i < len(CHILD_DEDUCTION_AMOUNTS) else Decimal(0)
        ded_lines.append(f"{i + 1}-й ребёнок{' (инвалид)' if ch.disabled else ''}: {fmt(amount)} ₽/мес")
    src.add_row("\n".join(sal_lines) or "—", "\n".join(bon_lines) or "—", "\n".join(ded_lines) or "—")
    console.print(src)

    # --- помесячная таблица ---------------------------------------------------
    table = Table(
        title=f"Расчёт за {config.year} год",
        title_style="bold",
        header_style="bold white on dark_blue",
    )
    table.add_column("Месяц", justify="left", style="bold")
    table.add_column("Оклад", justify="right")
    table.add_column("Премии", justify="right")
    table.add_column("Начислено", justify="right", style="cyan")
    table.add_column("НДФЛ", justify="right", style="red")
    table.add_column("На руки", justify="right", style="green")
    table.add_column("Ставка", justify="right")

    for r in reports:
        table.add_row(
            r.label,
            fmt(r.breakdown.salary_total),
            fmt(r.breakdown.bonus_total) if r.breakdown.bonus_total else "",
            fmt(r.gross),
            fmt(r.tax),
            fmt(r.net),
            r.result.rate_label,  # ставка из шкалы: 13/15/18/20/22%
        )
    table.add_section()
    used_rates = []
    for r in reports:
        for lab in r.result.rate_label.split(", "):
            if lab and lab not in used_rates:
                used_rates.append(lab)
    table.add_row(
        "ИТОГО",
        fmt(summary.total_salary),
        fmt(summary.total_bonus),
        fmt(summary.total_gross),
        fmt(summary.total_tax),
        fmt(summary.total_net),
        ", ".join(used_rates),
        style="bold",
    )
    console.print(table)

    console.print(Panel.fit(
        f"[bold]Заработано:[/bold] {fmt(summary.total_gross)} ₽ (грязными)\n"
        f"[bold]НДФЛ:[/bold]      {fmt(summary.total_tax)} ₽\n"
        f"[bold]На руки:[/bold]   {fmt(summary.total_net)} ₽",
        title=f"Итог {config.year}",
        border_style="blue",
    ))


def interactive(console: Console) -> PayrollConfig:
    """Пошаговый ввод данных без файла конфигурации."""
    year = int(input("Год расчёта [2026]: ").strip() or "2026")

    periods: list[SalaryPeriod] = []
    print("Периоды оклада (пустой оклад — закончить ввод):")
    while True:
        salary = input(f"  Оклад №{len(periods) + 1}, ₽: ").strip()
        if not salary:
            break
        start_raw = input("    Дата с (ГГГГ-ММ-ДД): ").strip()
        end_raw = input("    Дата по (ГГГГ-ММ-ДД, Enter — бессрочно): ").strip()
        periods.append(SalaryPeriod(
            salary=Decimal(salary),
            start=date.fromisoformat(start_raw),
            end=date.fromisoformat(end_raw) if end_raw else None,
        ))

    bonuses: list[Bonus] = []
    print("Премии (пустой месяц — закончить ввод):")
    while True:
        month = input("  Месяц ГГГГ-ММ: ").strip()
        if not month:
            break
        kind = input("  [f] фиксированная сумма / [p] % от оклада [f]: ").strip().lower() or "f"
        value = input("  Значение: ").strip()
        note = input("  Комментарий: ").strip()
        d = date.fromisoformat(month + "-01")
        if kind.startswith("p"):
            bonuses.append(Bonus(percent_of_salary=Decimal(value), month=d, note=note))
        else:
            bonuses.append(Bonus(amount=Decimal(value), month=d, note=note))

    children_n = int(input("Сколько детей для стандартных вычетов (0 — нет): ").strip() or "0")

    raw = {
        "year": year,
        "salary_periods": [
            {
                "salary": str(p.salary),
                "start": p.start.isoformat(),
                **({"end": p.end.isoformat()} if p.end else {}),
            }
            for p in periods
        ],
        "bonuses": [
            {
                "month": b.month.isoformat(),
                "amount": str(b.amount) if b.amount is not None else None,
                "percent_of_salary": str(b.percent_of_salary) if b.percent_of_salary is not None else None,
                "note": b.note,
            }
            for b in bonuses
        ],
        "children": [{"name": f"ребёнок {i + 1}"} for i in range(children_n)],
    }
    return parse_config(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zp-calculator",
        description="Калькулятор зарплаты и НДФЛ: периоды окладов, премии, помесячный расчёт.",
    )
    parser.add_argument("-c", "--config", help="JSON/YAML с периодами окладов и премиями")
    parser.add_argument("--xlsx", metavar="FILE", help="сохранить результат в Excel")
    parser.add_argument("--csv", metavar="FILE", help="сохранить результат в CSV")
    args = parser.parse_args(argv)

    console = Console()
    try:
        config = load_config(args.config) if args.config else interactive(console)
        reports = calculate_payroll(
            year=config.year,
            periods=config.periods,
            bonuses=config.bonuses,
            start_month=config.start_month,
            end_month=config.end_month,
            deductions=config.deductions,
        )
    except (ValueError, KeyError, InvalidOperation) as exc:
        console.print(f"[bold red]Ошибка ввода:[/] {exc}")
        return 2

    summary = summarize(reports)
    render(reports, summary, config, console)

    if args.xlsx:
        wb = build_workbook(
            reports, config.periods, config.bonuses,
            children_count=len(config.deductions.children),
        )
        wb.save(args.xlsx)
        console.print(f"[dim]Сохранено: {args.xlsx}[/dim]")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["Месяц", "Оклад", "Премии", "Начислено", "НДФЛ", "На руки"])
            for r in reports:
                writer.writerow([
                    r.label, r.breakdown.salary_total, r.breakdown.bonus_total,
                    r.gross, r.tax, r.net,
                ])
            writer.writerow([
                "ИТОГО", summary.total_salary, summary.total_bonus,
                summary.total_gross, summary.total_tax, summary.total_net,
            ])
        console.print(f"[dim]Сохранено: {args.csv}[/dim]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
