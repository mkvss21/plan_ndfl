/* Калькулятор зарплаты и НДФЛ — клиентская логика (без зависимостей).
   Расчёт полностью повторяет Python-ядро из ../zp_calculator (tax.py, payroll.py),
   поэтому результаты в CLI, Excel и браузере совпадают. */

"use strict";

const MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

// Шкала НДФЛ 2025: [верхняя граница накопленного дохода или Infinity, ставка]
const BRACKETS = [
  [5_000_000, 0.13],
  [20_000_000, 0.15],
  [50_000_000, 0.18],
  [80_000_000, 0.20],
  [Infinity, 0.22],
];

const CHILD_AMOUNTS = [1400, 2800, 6000, 6000, 6000, 6000, 6000, 6000, 6000, 6000, 6000];
const CHILD_LIMIT = 450_000;

const daysInMonth = (y, m) => new Date(y, m, 0).getDate(); // m = 1..12

/** Налог с накопленного дохода по годовой шкале. */
function taxOnCumulative(income) {
  income = Math.max(income, 0);
  let remaining = income, prev = 0, tax = 0;
  for (const [limit, rate] of BRACKETS) {
    const band = Math.min(remaining, limit - prev);
    tax += band * rate;
    remaining -= band;
    prev = limit;
    if (remaining <= 0) break;
  }
  return tax;
}

/** НДФЛ округляется до целых рублей (ст. 225 п.6 НК РФ), как Decimal ROUND_HALF_UP. */
const roundTax = (v) => Math.floor(Math.abs(v) + 0.5) * (v >= 0 ? 1 : -1);

// ---------------------------------------------------------------------------
// Парсинг ввода
// ---------------------------------------------------------------------------

function parseNum(text) {
  if (!text) return NaN;
  const cleaned = String(text).replace(/\s|\u00a0/g, "").replace(",", ".");
  return Number(cleaned);
}

function parseDateParts(value) {
  // value из <input type="date"> — "YYYY-MM-DD" или ""
  if (!value) return null;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return null;
  return { y, m, d, time: new Date(y, m - 1, d).getTime(), label: `${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${y}` };
}

function readState() {
  const state = { year: parseNum(document.getElementById("year").value) || new Date().getFullYear(),
                  fromMonth: Number(document.getElementById("fromMonth").value),
                  toMonth: Number(document.getElementById("toMonth").value),
                  children: Math.max(0, parseNum(document.getElementById("children").value) || 0),
                  periods: [], bonuses: [], errors: [] };

  document.querySelectorAll("#salaryRows .grid").forEach((row, idx) => {
    const salary = parseNum(row.querySelector("[data-f=salary]").value);
    const start = parseDateParts(row.querySelector("[data-f=start]").value);
    const end = parseDateParts(row.querySelector("[data-f=end]").value);
    const hasData = !Number.isNaN(salary) || start || end;
    if (!hasData) return;
    if (Number.isNaN(salary) || salary < 0) { state.errors.push(`Период №${idx + 1}: укажите корректный оклад`); return; }
    if (!start) { state.errors.push(`Период №${idx + 1}: укажите дату «с»`); return; }
    if (end && end.time < start.time) { state.errors.push(`Период №${idx + 1}: дата «по» раньше даты «с»`); return; }
    state.periods.push({ salary, start, end });
  });

  document.querySelectorAll("#bonusRows .grid").forEach((row, idx) => {
    const monthVal = row.querySelector("[data-f=month]").value; // <input type="month"> "YYYY-MM"
    const note = row.querySelector("[data-f=note]").value.trim();
    const type = row.querySelector("[data-f=type]").value;
    const raw = row.querySelector("[data-f=value]").value;
    const value = parseNum(raw);
    if (!monthVal && !raw) return;
    if (!monthVal) { state.errors.push(`Премия №${idx + 1}: укажите месяц`); return; }
    if (Number.isNaN(value) || value < 0) { state.errors.push(`Премия №{idx + 1}: некорректное значение`.replace("{idx + 1}", idx + 1)); return; }
    const [y, m] = monthVal.split("-").map(Number);
    state.bonuses.push({ year: y, month: m, note, type, value });
  });

  if (!state.periods.length && !state.bonuses.length) {
    state.errors.push("Добавьте хотя бы один период оклада или премию");
  }
  // проверка пересечений окладов
  const sorted = [...state.periods].sort((a, b) => a.start.time - b.start.time);
  for (let i = 0; i + 1 < sorted.length; i++) {
    const a = sorted[i], b = sorted[i + 1];
    if (a.end === null) { state.errors.push(`Период ${a.start.label} открыт, но после него есть ещё периоды`); break; }
    if (b.start.time <= a.end.time) { state.errors.push(`Периоды окладов пересекаются: ${a.start.label}—${a.end.label} и ${b.start.label}`); break; }
  }
  if (state.fromMonth > state.toMonth) state.errors.push("Начальный месяц позже конечного");
  return state;
}

// ---------------------------------------------------------------------------
// Расчёт
// ---------------------------------------------------------------------------

function monthSalary(year, month, periods) {
  const first = new Date(year, month - 1, 1).getTime();
  const dim = daysInMonth(year, month);
  const last = new Date(year, month - 1, dim).getTime();
  let total = 0;
  for (const p of periods) {
    const segStart = Math.max(p.start.time, first);
    const segEnd = p.end ? Math.min(p.end.time, last) : last;
    if (segStart > segEnd) continue;
    const days = Math.round((segEnd - segStart) / 86_400_000) + 1;
    total += (p.salary * days) / dim;
  }
  return total;
}

function childDeduction(childrenCount, cumulativeIncome) {
  let sum = 0;
  for (let i = 0; i < childrenCount; i++) {
    if (cumulativeIncome < CHILD_LIMIT) sum += CHILD_AMOUNTS[i] ?? 0;
  }
  return sum;
}

function calculate(state) {
  const rows = [];
  let cumGross = 0, cumDeduct = 0, cumTax = 0;

  for (let month = state.fromMonth; month <= state.toMonth; month++) {
    const salary = monthSalary(state.year, month, state.periods);
    let bonusTotal = 0;
    const bonusNotes = [];
    for (const b of state.bonuses) {
      if (b.year !== state.year || b.month !== month) continue;
      const v = b.type === "percent" ? (salary * b.value) / 100 : b.value;
      bonusTotal += v;
      bonusNotes.push(`${b.type === "percent" ? b.value + "% от оклада" : "" + v}${b.note ? " (" + b.note + ")" : ""}`);
    }
    const gross = Math.round((salary + bonusTotal) * 100) / 100;

    const baseBefore = cumGross - cumDeduct;
    cumGross += gross;
    const deduction = childDeduction(state.children, cumGross);
    cumDeduct += deduction;
    const baseAfter = cumGross - cumDeduct;

    let tax = roundTax(taxOnCumulative(baseAfter) - taxOnCumulative(baseBefore));
    if (tax < 0) tax = 0;
    cumTax += tax;

    rows.push({
      label: `${MONTHS_RU[month - 1]} ${state.year}`,
      salary, bonus: bonusTotal, bonusNotes, gross,
      deduction, taxable: Math.max(gross - deduction, 0),
      tax, net: gross - tax,
      rate: gross ? tax / gross : 0,
      cumGross, cumTax,
    });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Форматирование и вывод
// ---------------------------------------------------------------------------

const nf = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const money = (v) => nf.format(Math.round(v * 100) / 100);
const pct = (v) => nf.format(Math.round(v * 10_000) / 100) + "%";

let lastRows = [];

function render(rows, errors) {
  const errBox = document.getElementById("error");
  errBox.textContent = errors.join(" · ");
  const body = document.getElementById("resultBody");
  body.innerHTML = "";

  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td title="${r.bonusNotes.join("; ") || ""}">${r.label}</td>
      <td>${money(r.salary)}</td>
      <td>${r.bonus ? money(r.bonus) : ""}</td>
      <td>${money(r.gross)}</td>
      <td>${r.deduction ? money(r.deduction) : ""}</td>
      <td class="err-cell">${money(r.tax)}</td>
      <td>${money(r.net)}</td>
      <td>${pct(r.rate)}</td>
      <td class="hide-sm">${money(r.cumGross)}</td>
      <td class="hide-sm">${money(r.cumTax)}</td>`;
    body.appendChild(tr);
  }

  const totals = rows.reduce((a, r) => ({
    salary: a.salary + r.salary, bonus: a.bonus + r.bonus, gross: a.gross + r.gross,
    tax: a.tax + r.tax, net: a.net + r.net,
  }), { salary: 0, bonus: 0, gross: 0, tax: 0, net: 0 });

  if (rows.length) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `<td>ИТОГО</td><td>${money(totals.salary)}</td><td>${money(totals.bonus)}</td>
      <td>${money(totals.gross)}</td><td></td><td>${money(totals.tax)}</td><td>${money(totals.net)}</td>
      <td>${totals.gross ? pct(totals.tax / totals.gross) : "0%"}</td><td class="hide-sm"></td><td class="hide-sm"></td>`;
    body.appendChild(tr);
  }

  document.getElementById("tGross").textContent = money(totals.gross) + " ₽";
  document.getElementById("tTax").textContent = money(totals.tax) + " ₽";
  document.getElementById("tNet").textContent = money(totals.net) + " ₽";
  document.getElementById("tRate").textContent = totals.gross ? pct(totals.tax / totals.gross) : "—";
  lastRows = rows;
}

function recalc() {
  const state = readState();
  render(state.errors.length ? [] : calculate(state), state.errors);
}

// ---------------------------------------------------------------------------
// Динамические строки ввода
// ---------------------------------------------------------------------------

function addSalaryRow(salary = "", start = "", end = "") {
  const div = document.createElement("div");
  div.className = "grid g4";
  div.innerHTML = `
    <input data-f="salary" type="text" inputmode="decimal" placeholder="150 000" value="${salary}">
    <input data-f="start" type="date" value="${start}">
    <input data-f="end" type="date" value="${end}">
    <button class="del" title="Удалить">×</button>`;
  div.querySelector(".del").onclick = () => { div.remove(); recalc(); };
  div.querySelectorAll("input").forEach((i) => i.addEventListener("input", recalc));
  document.getElementById("salaryRows").appendChild(div);
}

function addBonusRow(month = "", note = "", type = "fixed", value = "") {
  const div = document.createElement("div");
  div.className = "grid g4b";
  div.innerHTML = `
    <input data-f="month" type="month" value="${month}">
    <input data-f="note" type="text" placeholder="напр. премия за квартал" value="${note}">
    <select data-f="type">
      <option value="fixed"${type === "fixed" ? " selected" : ""}>Сумма, ₽</option>
      <option value="percent"${type === "percent" ? " selected" : ""}>% от оклада</option>
    </select>
    <input data-f="value" type="text" inputmode="decimal" placeholder="50 000" value="${value}">
    <button class="del" title="Удалить">×</button>`;
  div.querySelector(".del").onclick = () => { div.remove(); recalc(); };
  div.querySelectorAll("input,select").forEach((i) => i.addEventListener("input", recalc));
  document.getElementById("bonusRows").appendChild(div);
}

// ---------------------------------------------------------------------------
// Инициализация
// ---------------------------------------------------------------------------

function fillMonthSelects() {
  const from = document.getElementById("fromMonth"), to = document.getElementById("toMonth");
  MONTHS_RU.forEach((name, i) => {
    from.add(new Option(name, i + 1));
    to.add(new Option(name, i + 1));
  });
  from.value = "1";
  to.value = "12";
}

function demo() {
  document.getElementById("salaryRows").innerHTML = "";
  document.getElementById("bonusRows").innerHTML = "";
  addSalaryRow("150000", "2026-01-01", "2026-06-30");
  addSalaryRow("180000", "2026-07-01", "");
  addBonusRow("2026-03", "премия за квартал", "fixed", "50000");
  addBonusRow("2026-06", "летняя премия", "percent", "100");
  addBonusRow("2026-12", "годовая премия", "percent", "150");
  document.getElementById("children").value = "2";
  document.getElementById("year").value = "2026";
  recalc();
}

function clearAll() {
  document.getElementById("salaryRows").innerHTML = "";
  document.getElementById("bonusRows").innerHTML = "";
  document.getElementById("children").value = "0";
  addSalaryRow();
  recalc();
}

function exportCsv() {
  if (!lastRows.length) return;
  const head = ["Месяц", "Оклад", "Премии", "Начислено", "Вычет", "НДФЛ", "На руки", "Ставка"];
  const lines = [head.join(";")];
  for (const r of lastRows) {
    lines.push([r.label, r.salary.toFixed(2), r.bonus.toFixed(2), r.gross.toFixed(2),
                r.deduction.toFixed(2), r.tax.toFixed(0), r.net.toFixed(2),
                (r.rate * 100).toFixed(2).replace(".", ",") + "%"].join(";"));
  }
  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "raschet-zp.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function copyTable() {
  if (!lastRows.length) return;
  const text = lastRows.map((r) =>
    [r.label, money(r.gross), money(r.tax), money(r.net)].join("\t")).join("\n");
  navigator.clipboard?.writeText(text);
}

document.getElementById("addSalary").onclick = () => { addSalaryRow(); recalc(); };
document.getElementById("addBonus").onclick = () => { addBonusRow(); recalc(); };
document.getElementById("loadDemo").onclick = demo;
document.getElementById("clearAll").onclick = clearAll;
document.getElementById("csvBtn").onclick = exportCsv;
document.getElementById("copyBtn").onclick = copyTable;
["year", "fromMonth", "toMonth", "children"].forEach((id) =>
  document.getElementById(id).addEventListener("input", recalc));

fillMonthSelects();
demo();
