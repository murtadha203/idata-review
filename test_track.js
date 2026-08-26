/* اختبارُ حساب المتابعة — يُشغَّل بـ`node test_track.js`.
 *
 * **ولماذا بلا متصفّح.** المتصفّحُ في بيئتنا يعمل بتبويبٍ محجوب، فيُطفئ
 * العدّادَ بحقّ ولا يُظهر شيئاً. فتُبنى بيئةٌ مصطنعةٌ يُتحكَّم فيها بالزمن
 * وبالظهور، ويُشغَّل `review.js` نفسُه لا نسخةٌ منه.
 */
const fs = require("fs");
const path = require("path");

let NOW = 0;                       // زمنٌ مصطنعٌ نحرّكه بأيدينا
let VISIBLE = true, FOCUS = true;
const SENT = [];                   // ما أُرسل إلى الخادم
const timers = [];                 // مؤقّتاتٌ نُطلقها متى شئنا

const noop = () => {};
// **وقسمُ الاختبار عنصرٌ كامل.** الملفُّ يُشغَّل بتمامه لا جزءَ المتابعة
// وحدَه، فيعلّق على الأقسام شاراتٍ ويقرأ نصوصَها.
const stubEl = () => ({
  style: {}, dataset: {}, isConnected: true,
  classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  appendChild: noop, addEventListener: noop, removeEventListener: noop,
  setAttribute: noop, getAttribute: () => null, remove: noop,
  querySelector: () => null, querySelectorAll: () => [],
  closest: () => null, insertAdjacentElement: noop, insertBefore: noop,
  getBoundingClientRect: () => ({ top: 0, left: 0, width: 10, height: 10 }),
  get innerHTML() { return ""; }, set innerHTML(v) {},
  get textContent() { return ""; }, set textContent(v) {},
  focus: noop, click: noop, scrollIntoView: noop, children: [], childNodes: [],
});

function makeSection(key) {
  return Object.assign(stubEl(), { dataset: { sec: key, kind: "txt" } });
}

const SECTIONS = Array.from({ length: 10 }, (_, i) => makeSection("s" + i));
let observerCb = null;

function stubEnv() {
  global.performance = { now: () => NOW };
  global.IntersectionObserver = class {
    constructor(cb) { observerCb = cb; }
    observe() {} unobserve() {} disconnect() {}
  };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (url, blob) => { SENT.push({ url, blob }); return true; } },
  });
  global.Blob = class { constructor(parts) { this.text = parts.join(""); } };
  global.fetch = (url, opt) => {
    SENT.push({ url, body: opt && opt.body });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  global.setInterval = (fn, ms) => { timers.push({ fn, ms, kind: "i" }); return timers.length; };
  global.setTimeout = (fn, ms) => { timers.push({ fn, ms, kind: "t" }); return timers.length; };
  global.clearInterval = noop; global.clearTimeout = noop;
  const listeners = {};
  global.addEventListener = (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); };
  global.dispatch = ev => (listeners[ev] || []).forEach(f => f());
  global.document = {
    get visibilityState() { return VISIBLE ? "visible" : "hidden"; },
    get hidden() { return !VISIBLE; },
    hasFocus: () => FOCUS,
    readyState: "complete",
    title: "اختبار",
    body: stubEl(),
    createElement: stubEl,
    createTreeWalker: () => ({ nextNode: () => null }),
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    querySelector: sel => (sel === "[data-sec]" ? SECTIONS[0] : stubEl()),
    querySelectorAll: sel => (sel === "[data-sec]" ? SECTIONS : []),
  };
  global.window = { RV: { slug: "t", status: "review",
                          me: { id: 9, name: "عصام", role: "reviewer" } } };
  global.CSS = { escape: s => s };
  global.location = { reload: noop };
  global.alert = noop; global.confirm = () => true;
}

function fireTimers(kind) {
  timers.filter(t => t.kind === kind).forEach(t => t.fn());
}

function parseSent() {
  const out = {};
  SENT.forEach(m => {
    const raw = m.body || (m.blob && m.blob.text);
    if (!raw) return;
    let j; try { j = JSON.parse(raw); } catch (e) { return; }
    if (j.open) out.opened = (out.opened || 0) + 1;
    Object.entries(j.secs || {}).forEach(([k, v]) => {
      out[k] = (out[k] || 0) + v;
    });
  });
  return out;
}

// ── التشغيل ────────────────────────────────────────────────────────────────
stubEnv();
const src = fs.readFileSync(path.join(__dirname, "static", "review.js"), "utf8");
new Function(src)();

let fails = 0;
const ok = (cond, label, detail) => {
  console.log(`  ${cond ? "✔" : "✘"} ${label}${detail ? "  — " + detail : ""}`);
  if (!cond) fails++;
};

console.log("\n=== ١ · فتحةٌ تُسجَّل مرّةً واحدة ===");
ok(parseSent().opened === 1, "أُرسلت فتحةٌ واحدة", `opened=${parseSent().opened}`);

console.log("\n=== ٢ · قسمٌ ظهر عشرَ ثوانٍ والصفحةُ منظورة ===");
observerCb([{ target: SECTIONS[0], isIntersecting: true }]);
NOW += 10000;
fireTimers("i");                       // الدفعةُ الدورية
let got = parseSent();
ok(got.s0 >= 9900 && got.s0 <= 10100, "سُجِّلت عشرُ ثوانٍ", `${got.s0}ms`);

console.log("\n=== ٣ · ولا يُحسَب زمنٌ والتبويبُ خلفٌ ===");
const before = parseSent().s0;
VISIBLE = false; FOCUS = false;
dispatch("blur");                      // يُطفئ العدّاد
NOW += 3600000;                        // ساعةٌ كاملة
fireTimers("i");
got = parseSent();
ok((got.s0 - before) < 500, "لم يُضَف زمنٌ للساعة المحجوبة",
   `أُضيف ${got.s0 - before}ms من 3,600,000`);

console.log("\n=== ٤ · ويستأنف عند العودة ===");
VISIBLE = true; FOCUS = true;
dispatch("focus");
NOW += 5000;
fireTimers("i");
got = parseSent();
ok(got.s0 - before >= 4800 && got.s0 - before <= 5600,
   "عاد العدّ بعد الرجوع", `+${got.s0 - before}ms`);

console.log("\n=== ٥ · قسمٌ خرج من الشاشة يتوقّف ===");
observerCb([{ target: SECTIONS[0], isIntersecting: false }]);
const atLeave = parseSent().s0;
NOW += 20000;
fireTimers("i");
ok(Math.abs(parseSent().s0 - atLeave) < 500, "لا يُزاد بعد الخروج",
   `${parseSent().s0 - atLeave}ms`);

console.log("\n=== ٦ · أقسامٌ متعدّدةٌ تُحسَب مستقلّة ===");
observerCb([{ target: SECTIONS[3], isIntersecting: true },
            { target: SECTIONS[4], isIntersecting: true }]);
NOW += 4000;
fireTimers("i");
got = parseSent();
ok(got.s3 >= 3800 && got.s4 >= 3800 && !got.s7,
   "كلُّ قسمٍ بزمنِه",
   `s3=${got.s3} s4=${got.s4} s7=${got.s7 || 0}`);

console.log("\n=== ٧ · المغادرةُ ترسل الباقيَ بـbeacon ===");
const nBefore = SENT.length;
NOW += 2000;
dispatch("pagehide");
const beacons = SENT.slice(nBefore).filter(m => m.blob);
ok(beacons.length === 1, "أُرسلت دفعةٌ أخيرةٌ بـbeacon",
   `${beacons.length}`);

console.log(`\n${fails ? "✘ إخفاقات: " + fails : "✔ لا إخفاق"}\n`);
process.exit(fails ? 1 : 0);
