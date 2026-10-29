/* Калькулятор зарплаты и НДФЛ — клиентская логика (без зависимостей).
   Расчёт полностью повторяет Python-ядро из ../zp_calculator (tax.py, payroll.py),
   поэтому результаты в CLI, Excel и браузере совпадают. */

"use strict";

const MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

/* Две шкалы НДФЛ (ФЗ №176-ФЗ от 12.07.2024):
   - 2025 год: первая ступень 13% действует до накопленного дохода 5 млн руб;
   - с 2026 года верхняя граница первой ступени снижена до 2,4 млн руб,
     всё, что накоплено свыше 2,4 млн, облагается по следующей ставке 15%. */
const BRACKETS_MAP = {
  2025: [
    [5_000_000, 0.13],
    [20_000_000, 0.15],
    [50_000_000, 0.18],
    [80_000_000, 0.20],
    [Infinity, 0.22],
  ],
  default: [ // 2026 и далее
    [2_400_000, 0.13],
    [5_000_000, 0.15],
    [20_000_000, 0.18],
    [50_000_000, 0.20],
    [Infinity, 0.22],
  ],
};

let BRACKETS = BRACKETS_MAP.default; // устанавливается в recalc() по выбранному году

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

/** Выбор шкалы НДФЛ в зависимости от года (с 2026 граница 13% — 2,4 млн). */
function setBracketsForYear(year) {
  BRACKETS = year <= 2025 ? BRACKETS_MAP[2025] : BRACKETS_MAP.default;
}

function recalc() {
  const state = readState();
  setBracketsForYear(state.year);
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

// ---------------------------------------------------------------------------
// Шаблоны (localStorage + импорт/экспорт JSON в формате конфига ядра)
// ---------------------------------------------------------------------------

const TPL_KEY = "zp-calc-templates-v1";

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 2200);
}

function loadTemplates() {
  try {
    const obj = JSON.parse(localStorage.getItem(TPL_KEY) || "{}");
    return obj && typeof obj === "object" ? obj : {};
  } catch { return {}; }
}

function persistTemplates(obj) {
  localStorage.setItem(TPL_KEY, JSON.stringify(obj));
}

function refreshTplSelect(selectedName) {
  const sel = document.getElementById("tplSelect");
  sel.innerHTML = "";
  sel.add(new Option("— выберите шаблон —", ""));
  for (const name of Object.keys(loadTemplates()).sort((a, b) => a.localeCompare(b, "ru"))) {
    sel.add(new Option(name, name));
  }
  if (selectedName) sel.value = selectedName;
}

/** Текущий ввод страницы → объект шаблона. */
function collectTemplate() {
  const periods = [], bonuses = [];
  document.querySelectorAll("#salaryRows .grid").forEach((row) => {
    const salary = parseNum(row.querySelector("[data-f=salary]").value);
    const start = row.querySelector("[data-f=start]").value;
    const end = row.querySelector("[data-f=end]").value;
    if (Number.isNaN(salary) && !start && !end) return;
    periods.push({ salary: Number.isNaN(salary) ? null : salary, start, end });
  });
  document.querySelectorAll("#bonusRows .grid").forEach((row) => {
    const month = row.querySelector("[data-f=month]").value;
    const note = row.querySelector("[data-f=note]").value.trim();
    const type = row.querySelector("[data-f=type]").value;
    const value = parseNum(row.querySelector("[data-f=value]").value);
    if (!month && Number.isNaN(value)) return;
    bonuses.push({ month, note, type, value: Number.isNaN(value) ? null : value });
  });
  return {
    year: document.getElementById("year").value,
    fromMonth: document.getElementById("fromMonth").value,
    toMonth: document.getElementById("toMonth").value,
    children: document.getElementById("children").value,
    periods, bonuses,
  };
}

function applyTemplate(t, markAsLast = true) {
  document.getElementById("salaryRows").innerHTML = "";
  document.getElementById("bonusRows").innerHTML = "";
  document.getElementById("year").value = t.year ?? new Date().getFullYear();
  fillMonthSelects();
  document.getElementById("fromMonth").value = t.fromMonth ?? "1";
  document.getElementById("toMonth").value = t.toMonth ?? "12";
  document.getElementById("children").value = t.children ?? "0";
  (t.periods || []).forEach((p) => addSalaryRow(p.salary ?? "", p.start ?? "", p.end ?? ""));
  (t.bonuses || []).forEach((b) => addBonusRow(b.month ?? "", b.note ?? "", b.type ?? "fixed", b.value ?? ""));
  if (!(t.periods || []).length) addSalaryRow();
  recalc();
  if (markAsLast) {
    const name = document.getElementById("tplSelect").value;
    if (name) localStorage.setItem(TPL_KEY + ":last", name);
  }
}

function saveTemplate() {
  let name = document.getElementById("tplName").value.trim();
  const sel = document.getElementById("tplSelect");
  if (!name) name = sel.value || prompt("Название шаблона:");
  if (!name) return;
  const all = loadTemplates();
  all[name] = collectTemplate();
  persistTemplates(all);
  refreshTplSelect(name);
  localStorage.setItem(TPL_KEY + ":last", name);
  document.getElementById("tplName").value = "";
  toast(`Шаблон «${name}» сохранён`);
}

function applySelectedTemplate() {
  const name = document.getElementById("tplSelect").value;
  if (!name) { toast("Сначала выберите шаблон из списка"); return; }
  const t = loadTemplates()[name];
  if (!t) { toast("Шаблон не найден"); return; }
  applyTemplate(t);
  toast(`Шаблон «${name}» загружен`);
}

function deleteTemplate() {
  const name = document.getElementById("tplSelect").value;
  if (!name) { toast("Сначала выберите шаблон из списка"); return; }
  if (!confirm(`Удалить шаблон «${name}»?`)) return;
  const all = loadTemplates();
  delete all[name];
  persistTemplates(all);
  if (localStorage.getItem(TPL_KEY + ":last") === name) localStorage.removeItem(TPL_KEY + ":last");
  refreshTplSelect();
  toast(`Шаблон «${name}» удалён`);
}

/** Экспорт текущего ввода в формат config.json расчётного ядра. */
function exportTemplateJson() {
  const s = readStateForExport();
  const cfg = {
    year: s.year,
    start_month: s.fromMonth,
    end_month: s.toMonth,
    salary_periods: s.periods.map((p) => ({
      salary: p.salary,
      start: isoFromParts(p.start),
      ...(p.end ? { end: isoFromParts(p.end) } : {}),
    })),
    bonuses: s.bonuses.map((b) => ({
      month: `${b.year}-${String(b.month).padStart(2, "0")}-01`,
      ...(b.type === "percent" ? { percent_of_salary: b.value } : { amount: b.value }),
      ...(b.note ? { note: b.note } : {}),
    })),
    children: Array.from({ length: s.children }, (_, i) => ({ name: `Ребёнок ${i + 1}` })),
  };
  const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `zp-template-${cfg.year}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Файл шаблона скачан");
}

const isoFromParts = (d) =>
  d ? `${String(d.y).padStart(4, "0")}-${String(d.m).padStart(2, "0")}-${String(d.d).padStart(2, "0")}` : "";

/** Облегчённый парсер для экспорта: без проверок на пересечения и ошибок. */
function readStateForExport() {
  const state = { year: parseNum(document.getElementById("year").value) || new Date().getFullYear(),
                  fromMonth: Number(document.getElementById("fromMonth").value),
                  toMonth: Number(document.getElementById("toMonth").value),
                  children: Math.max(0, parseNum(document.getElementById("children").value) || 0),
                  periods: [], bonuses: [] };
  document.querySelectorAll("#salaryRows .grid").forEach((row) => {
    const salary = parseNum(row.querySelector("[data-f=salary]").value);
    const start = parseDateParts(row.querySelector("[data-f=start]").value);
    const end = parseDateParts(row.querySelector("[data-f=end]").value);
    if ((Number.isNaN(salary) && !start) || salary < 0 || !start) return;
    state.periods.push({ salary, start, end });
  });
  document.querySelectorAll("#bonusRows .grid").forEach((row) => {
    const monthVal = row.querySelector("[data-f=month]").value;
    const note = row.querySelector("[data-f=note]").value.trim();
    const type = row.querySelector("[data-f=type]").value;
    const value = parseNum(row.querySelector("[data-f=value]").value);
    if (!monthVal || Number.isNaN(value) || value < 0) return;
    const [y, m] = monthVal.split("-").map(Number);
    state.bonuses.push({ year: y, month: m, note, type, value });
  });
  return state;
}

/** Импорт из JSON: поддерживается формат конфига ядра и формат самого шаблона. */
function importTemplateJson(text, fileName) {
  let data;
  try { data = JSON.parse(text); } catch { toast("Не удалось прочитать JSON-файл"); return; }
  let tpl;
  if (Array.isArray(data.salary_periods)) {
    // формат examples/config.json (конфиг Python-ядра)
    tpl = {
      year: String(data.year ?? new Date().getFullYear()),
      fromMonth: String(data.start_month ?? 1),
      toMonth: String(data.end_month ?? 12),
      children: Array.isArray(data.children) ? String(data.children.length) : "0",
      periods: data.salary_periods.map((p) => ({
        salary: p.salary,
        start: String(p.start ?? "").slice(0, 10),
        end: p.end ? String(p.end).slice(0, 10) : "",
      })),
      bonuses: (data.bonuses || []).map((b) => ({
        month: String(b.month ?? "").slice(0, 7),
        note: b.note ?? "",
        type: b.percent_of_salary != null ? "percent" : "fixed",
        value: b.percent_of_salary != null ? b.percent_of_salary : b.amount,
      })),
    };
  } else if (Array.isArray(data.periods) || Array.isArray(data.bonuses)) {
    tpl = { periods: [], bonuses: [], ...data }; // собственный формат шаблона
  } else {
    toast("В файле нет ни salary_periods, ни periods — неизвестный формат");
    return;
  }
  const name = ((fileName || "").replace(/\.json$/i, "") || `Импорт ${new Date().toLocaleDateString("ru-RU")}`).trim();
  applyTemplate(tpl, false);
  const all = loadTemplates();
  all[name] = collectTemplate();
  persistTemplates(all);
  refreshTplSelect(name);
  localStorage.setItem(TPL_KEY + ":last", name);
  toast(`Шаблон «${name}» загружен из файла и сохранён`);
}

// ---------------------------------------------------------------------------
// Инфраструктура страницы (DOM, localStorage, обработчики) — только в браузере.
// В Node.js app.js загружается без этой части для тестирования логики шаблонов.
// ---------------------------------------------------------------------------
if (typeof document !== "undefined") {

document.getElementById("addSalary").onclick = () => { addSalaryRow(); recalc(); };
document.getElementById("addBonus").onclick = () => { addBonusRow(); recalc(); };
document.getElementById("loadDemo").onclick = demo;
document.getElementById("clearAll").onclick = clearAll;
document.getElementById("csvBtn").onclick = exportCsv;
document.getElementById("copyBtn").onclick = copyTable;
["year", "fromMonth", "toMonth", "children"].forEach((id) =>
  document.getElementById(id).addEventListener("input", recalc));

document.getElementById("saveTpl").onclick = saveTemplate;
document.getElementById("applyTpl").onclick = applySelectedTemplate;
document.getElementById("deleteTpl").onclick = deleteTemplate;
document.getElementById("exportTpl").onclick = exportTemplateJson;
document.getElementById("importTpl").onclick = () => document.getElementById("importFile").click();
document.getElementById("importFile").onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => importTemplateJson(String(reader.result), file.name);
  reader.readAsText(file);
  e.target.value = "";
};
document.getElementById("tplSelect").onchange = (e) => { if (e.target.value) applySelectedTemplate(); };

fillMonthSelects();
refreshTplSelect();
const lastTplName = localStorage.getItem(TPL_KEY + ":last") || "";
const lastTpl = loadTemplates()[lastTplName];
if (lastTpl) {
  document.getElementById("tplSelect").value = lastTplName;
  applyTemplate(lastTpl, false); // при повторном открытии — сразу последний использованный шаблон
} else {
  demo();
}

} // конец браузерной части (typeof document !== "undefined")

// ---------------------------------------------------------------------------
// Экспорт функций для тестов в Node.js (см. tests/test_web_templates.js)
// ---------------------------------------------------------------------------
if (typeof module !== "undefined" && module.exports) {
  module.exports = { parseNum, parseDateParts, taxOnCumulative, setBracketsForYear, roundTax,
                     collectTemplate, applyTemplate, importTemplateJson, loadTemplates, persistTemplates };
}
