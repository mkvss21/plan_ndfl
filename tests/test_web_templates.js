/* Тест логики шаблонов web-калькулятора (web/app.js) в Node.js с DOM-заглушкой.
   Провер cycle: заполнение формы → сохранение шаблона → «перезагрузка страницы» →
   автовосстановление последнего шаблона; а также импорт/экспорт JSON-конфига ядра.

   Запуск:  node tests/test_web_templates.js */
"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

// ----------------------------- DOM-заглушка --------------------------------

class ClassList {
  constructor() { this.s = new Set(); }
  add(c) { this.s.add(c); }
  remove(c) { this.s.delete(c); }
}

class El {
  constructor(id) {
    this.id = id || "";
    this.children = [];
    this._value = "";
    this.textContent = "";
    this.classList = new ClassList();
    const el = this;
    Object.defineProperty(this, "value", {
      get() { return el._value; },
      set(v) { el._value = String(v); },
    });
  }
  addEventListener() {}
  click() {}
  get innerHTML() { return ""; }
  set innerHTML(v) {
    if (v === "") { this.children = []; return; }
    // минимальный парсер плоской разметки: <tag attr="val" ...>text</tag>,
    // одиночные теги <input ...> (без закрывающего) и self-closing <tag .../>
    const re = /<(\w+)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>(?:(\s*[\s\S]*?<\/\1>)|\s)/g;
    let m;
    while ((m = re.exec(v)) !== null) {
      const child = new El(m[1]);
      const are = /([\w-]+)="([^"]*)"/g;
      let a;
      while ((a = are.exec(m[2])) !== null) {
        if (a[1] === "data-f") child.dataset = { f: a[2] };
        else if (a[1] === "value") child.value = a[2];
      }
      child.textContent = m[4] || "";
      this.appendChild(child);
    }
    // в реальных строках ввода элементы .grid не вкладываются друг в друга,
    // но в разметке app.js есть вложенные блоки (.grid > select > option, button > ×) —
    // переносим детей из «контейнерных» элементов наверх, чтобы остались только input/select/button
    for (const c of [...this.children]) {
      if (c.tagName !== "input" && c.tagName !== "select" && c.tagName !== "button" && c.children.length) {
        this.children.splice(this.children.indexOf(c), 1, ...c.children);
      }
    }
  }
  appendChild(el) { this.children.push(el); return el; }
  add(opt) { this.children.push(opt); } // Option для <select>
  remove() { /* noop */ }
  querySelector(sel) {
    const m = sel.match(/\[data-f=(\w+)\]/);
    if (m) return this.children.find((c) => c.dataset && c.dataset.f === m[1]);
    // классы (.del и т.п.) — заглушка, чтобы app.js мог назначить onclick
    return new El(sel);
  }
  querySelectorAll(sel) {
    if (sel === ".grid") return this.children.filter((c) => c.classList.s.has("grid"));
    const m = sel.match(/\[data-f=(\w+)\]/);
    if (m) return this.children.filter((c) => c.dataset && c.dataset.f === m[1]);
    // простые селекторы тегов/классов ("input", "input,select", ".del") — фильтр по тегу
    return this.children.filter((c) => sel.split(",").some((s) => s.trim().startsWith(c.tagName)));
  }
}

const byId = {};
function makeEl(id) {
  if (byId[id]) return byId[id];
  const el = new El(id);
  byId[id] = el;
  return el;
}
makeEl("salaryRows"); makeEl("bonusRows"); makeEl("toast"); makeEl("tplSelect");
makeEl("tplName"); makeEl("importFile");
["year", "fromMonth", "toMonth", "children"].forEach(makeEl);
byId.year.value = "2026";
byId.children.value = "0";

global.document = {
  getElementById: (id) => makeEl(id),
  createElement: (tag) => new El(tag),
  querySelectorAll: (sel) => {
    // полные селекторы вида "#salaryRows .grid"
    const m = sel.match(/^#(\w+)\s+\.(\w+)$/);
    if (m && byId[m[1]]) return byId[m[1]].children.filter((c) => c.classList.s.has(m[2]));
    return [];
  },
};
global.Option = function Option(text, value) { return { text, value }; };

let storage = {};
global.localStorage = {
  getItem: (k) => (k in storage ? storage[k] : null),
  setItem: (k, v) => { storage[k] = String(v); },
  removeItem: (k) => { delete storage[k]; },
};

global.Blob = class { constructor() {} };
global.URL = { createObjectURL: () => "blob:x", revokeObjectURL: () => {} };
global.FileReader = class { readAsText() {} };
global.navigator = {};

// ------------------------- загрузка app.js (1-й «открытие») ----------------

const APP_PATH = path.join(__dirname, "..", "web", "app.js");
delete require.cache[require.resolve(APP_PATH)];
const M = require(APP_PATH); // exports первого экземпляра: collectTemplate и др. работают с текущим DOM

// demo() уже выполнен при загрузке — добавляем свои данные вручную
byId.year.value = "2026";
byId.children.value = "1";
byId.salaryRows.innerHTML = "";
byId.bonusRows.innerHTML = "";

function addRow(containerId, fields) {
  const row = new El("div");
  row.classList.add("grid"); // app.js ищет строки как "#salaryRows .grid"
  for (const [f, v] of Object.entries(fields)) {
    const inp = new El("input");
    inp.dataset = { f };
    inp.value = v;
    row.appendChild(inp);
  }
  byId[containerId].appendChild(row);
}

addRow("salaryRows", { salary: "200000", start: "2026-01-01", end: "" });
addRow("bonusRows", { month: "2026-05", note: "премия", type: "fixed", value: "70000" });

// Сохраняем шаблон под именем «Мой 2026» (M — exports первого require-экземпляра app.js)
storage["zp-calc-templates-v1"] = JSON.stringify({ "Мой 2026": M.collectTemplate() });
storage["zp-calc-templates-v1:last"] = "Мой 2026";

// Проверка roundtrip внутри одного «открытия»: collectTemplate сохраняет всё нужное
const tpl = M.collectTemplate();
assert.strictEqual(tpl.year, "2026");
assert.strictEqual(tpl.children, "1");
assert.strictEqual(tpl.periods.length, 1);
assert.strictEqual(tpl.periods[0].salary, 200000);
assert.strictEqual(tpl.periods[0].start, "2026-01-01");
assert.strictEqual(tpl.bonuses.length, 1);
assert.strictEqual(tpl.bonuses[0].month, "2026-05");
assert.strictEqual(tpl.bonuses[0].value, 70000);

// ------------------- «Перезагрузка страницы»: новый require ----------------

for (const id of ["salaryRows", "bonusRows"]) byId[id].innerHTML = "";
byId.year.value = "2025"; byId.children.value = "0";
delete require.cache[require.resolve(APP_PATH)];
const M2 = require(APP_PATH); // инициализация должна подхватить последний шаблон из localStorage

assert.strictEqual(byId.year.value, "2026", "год восстановлен из шаблона");
assert.strictEqual(byId.children.value, "1", "вычеты восстановлены");
assert.strictEqual(byId.salaryRows.children.length, 1, "оклад восстановлен");
assert.strictEqual(byId.salaryRows.children[0].querySelector("[data-f=salary]")._value, "200000");
assert.strictEqual(byId.bonusRows.children.length, 1, "премия восстановлена");
assert.strictEqual(byId.bonusRows.children[0].querySelector("[data-f=value]")._value, "70000");

// ------------------------------ импорт JSON --------------------------------

const cfgJson = fs.readFileSync(path.join(__dirname, "..", "examples", "config.json"), "utf8");
for (const id of ["salaryRows", "bonusRows"]) byId[id].innerHTML = "";
M2.importTemplateJson(cfgJson, "config.json");

assert.strictEqual(byId.year.value, "2026");
assert.strictEqual(byId.children.value, "2", "детей из конфига ядра");
assert.strictEqual(byId.salaryRows.children.length, 2);
assert.strictEqual(byId.salaryRows.children[1].querySelector("[data-f=start]")._value, "2026-07-01");
assert.strictEqual(byId.bonusRows.children.length, 3);
assert.strictEqual(byId.bonusRows.children[1].querySelector("[data-f=type]")._value, "percent");
assert.strictEqual(byId.bonusRows.children[1].querySelector("[data-f=value]")._value, "100");
const saved = JSON.parse(storage["zp-calc-templates-v1"]);
assert.ok(saved["config"], "импортированный шаблон сохранён в списке");

console.log("OK: все проверки шаблонов пройдены (roundtrip, автовосстановление, импорт config.json)");
