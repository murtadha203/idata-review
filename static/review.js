/* طبقة التعليق.
 *
 * تُحقن فوق مخرَج `preview.py` ولا تعدّل منه شيئاً — فما يراه المراجع هو ما
 * سيُنشر حرفاً.
 *
 * ── المرساة على مستويين ────────────────────────────────────────────────────
 * `sec_key`  هويةُ القسم — مشتقّةٌ من نوعه وعنوانه لا من ترتيبه، فتنجو من
 *            إعادة التوليد.
 * `part_key` الموضعُ داخله:
 *              ""             القسم كلّه
 *              "card:3"       بطاقةٌ بعينها
 *              "pt:سلسلة::فئة" عمودٌ أو نقطةٌ في رسم
 *              "txt:<تجزئة>"   نصٌّ محدَّد — يُعاد إيجاده بنصّه لا بموضعه
 *
 * **ولماذا لا يُخزَّن موضعُ الحرف.** تحرير الفقرة يزحزح كلّ ما بعدها، فتنتقل
 * التعليقات صامتةً. أمّا البحث بالنصّ فإمّا يجده أو يعلن أنّه لم يجده.
 */
(function () {
  const RV = window.RV, API = "/api/comments";
  let all = [], filter = "open", pending = null;

  const $ = (s, r) => (r || document).querySelector(s);
  const el = (t, c, h) => { const e = document.createElement(t);
    if (c) e.className = c; if (h !== undefined) e.innerHTML = h; return e; };
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
  const hash = s => { let h = 0; for (let i = 0; i < s.length; i++)
    h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h).toString(36); };
  const norm = s => (s || "").replace(/\s+/g, " ").trim();

  const KINDS = { chart: "رسم", cards: "بطاقات", text: "نصّ", divider: "فاصل" };

  /* نصُّ العنصر بلا ما حقنّاه فيه — فزرّ «+» والشارة يدخلان في `textContent`
     ويتسرّبان إلى وسم التعليق المحفوظ. تُنسخ العقدة ويُنزع منها ما لنا. */
  function ownText(node) {
    const c = node.cloneNode(true);
    c.querySelectorAll(".rv-add, .rv-badge, script, style").forEach(e => e.remove());
    return norm(c.textContent);
  }

  function secLabel(sec) {
    const t = ownText(sec.querySelector("h4") || sec).slice(0, 80);
    return (KINDS[sec.dataset.kind] || "قسم") + (t ? " · " + t : "");
  }

  // ═══ الهيكل ══════════════════════════════════════════════════════════════
  function chrome() {
    const bar = el("div"); bar.id = "rv-bar";
    const ST = { review: "قيد المراجعة", approved: "جاهزة للنشر",
                 changes: "تحتاج تعديلاً" };
    /* التقييم للمراجع وحده — والرافع يرى النتيجة ولا يضعها. */
    const canRate = RV.me.role === "reviewer";
    bar.innerHTML = `<a href="/">← اللوحات</a>` +
      `<span class="t">${esc(document.title.replace(/^معاينة — /, ""))}</span>` +
      `<span class="st st-${esc(RV.status)}" id="rv-st">` +
      `${esc(ST[RV.status] || RV.status)}</span>` +
      `<span class="avg" id="rv-avg"></span>` +
      (canRate ? `<button id="rv-rate" class="rate">تقييمي</button>` : "") +
      `<span class="me">${esc(RV.me.name)}</span>` +
      `<button id="rv-toggle">إخفاء اللوح</button>`;
    document.body.appendChild(bar);

    const p = el("div"); p.id = "rv-panel";
    p.innerHTML = `<header><span class="n">التعليقات</span>
      <select class="flt" id="rv-filter">
        <option value="open">المفتوحة</option><option value="all">الكلّ</option>
        <option value="mine">تعليقاتي</option></select></header>
      <div id="rv-editor"></div><div id="rv-list"></div>`;
    document.body.appendChild(p);
    $("#rv-editor").innerHTML = `<div class="loc"></div><div class="q"></div>
      <textarea placeholder="اكتب تعليقك…  (Ctrl+Enter للحفظ)"></textarea>
      <div class="row"><button class="ok">حفظ</button>
      <button class="g cancel">إلغاء</button></div>`;


    /* لوحُ التقييم: عشرة أزرارٍ تُضغط، ورأيٌ اختياريّ، ومفتاح إجازة.
       والإجازة مستقلّةٌ عن التعليقات المفتوحة — قد يرى المراجع ملاحظاتٍ
       ويرى اللوحة صالحةً بحالها. */
    const rt = el("div"); rt.id = "rv-rate-box";
    rt.innerHTML = `
      <div class="hd">تقييمك للوحة</div>
      <div class="scale" id="rv-scale">${[...Array(10)].map((_, i) =>
        `<button data-s="${i + 1}">${i + 1}</button>`).join("")}</div>
      <div class="lbl"><span>ضعيفة</span><span>ممتازة</span></div>
      <textarea id="rv-note" placeholder="رأيك في اللوحة كاملةً (اختياريّ)…"
        ></textarea>
      <label class="pass"><input type="checkbox" id="rv-pass">
        <span><b>جاهزة للنشر</b> — حتى لو بقيت ملاحظات</span></label>
      <div class="row"><button class="ok" id="rv-save">حفظ التقييم</button>
        <button class="g" id="rv-close">إغلاق</button></div>
      <div class="others" id="rv-others"></div>`;
    document.body.appendChild(rt);

    $("#rv-toggle").onclick = () => {
      document.body.classList.toggle("rv-collapsed");
      $("#rv-toggle").textContent = document.body.classList.contains("rv-collapsed")
        ? "إظهار اللوح" : "إخفاء اللوح";
      reflow();
    };
    if (canRate) {
      $("#rv-rate").onclick = () => {
        rt.classList.toggle("on");
        if (rt.classList.contains("on")) loadVerdicts();
      };
      $("#rv-close").onclick = () => rt.classList.remove("on");
      $("#rv-scale").onclick = ev => {
        const b = ev.target.closest("button[data-s]");
        if (!b) return;
        myScore = +b.dataset.s;
        [...$("#rv-scale").children].forEach(x =>
          x.classList.toggle("on", +x.dataset.s <= myScore));
      };
      $("#rv-save").onclick = saveVerdict;
    }
    $("#rv-filter").onchange = e => { filter = e.target.value; render(); };
    $("#rv-editor .cancel").onclick = closeEditor;
    $("#rv-editor .ok").onclick = save;
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") closeEditor();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)
          && $("#rv-editor").classList.contains("on")) save();
    });
  }

  function reflow() {
    setTimeout(() => { if (window.Highcharts)
      Highcharts.charts.forEach(c => { if (c) try { c.reflow(); } catch (e) {} });
    }, 60);
  }

  /* ── مراسٍ لصفحةٍ لم تُولَّد من `preview.py` ────────────────────────────
     الملفّ المرفوع من الخارج لا يحمل `data-sec`. فتُشتقّ له مراسٍ من **نصّ
     الكتلة نفسها** — وهو المبدأ ذاته: الهوية لا الترتيب. فتنجو تعليقاته من
     رفع نسخةٍ محرَّرةٍ ما دامت الكتلة نفسها لم تتغيّر. */
  function autoAnchor() {
    if (document.querySelector("[data-sec]")) return;
    const sel = "section, article, figure, table, h1, h2, h3, " +
                "div.card, div.chart, .highcharts-container";
    const seen = {};
    [...document.querySelectorAll(sel)].forEach(n => {
      if (n.closest("[data-sec]") || !ownText(n)) return;
      const kind = /^H[123]$/.test(n.tagName) ? "divider"
        : n.querySelector(".highcharts-container, svg, canvas") ? "chart"
        : n.tagName === "TABLE" ? "table" : "text";
      const base = kind + "|" + ownText(n).slice(0, 80);
      const i = seen[base] = (seen[base] || 0) + 1;
      n.dataset.sec = hash(base) + (i > 1 ? "-" + (i - 1) : "");
      n.dataset.kind = kind;
    });
  }

  // ═══ التقاط الهدف — أربعة مستويات ════════════════════════════════════════
  function wire() {
    autoAnchor();
    document.querySelectorAll("[data-sec]").forEach(sec => {
      const b = el("button", "rv-add", "+");
      b.title = "تعليق على القسم كلّه";
      b.onclick = ev => { ev.stopPropagation();
        openEditor({ sec, part: "", partLabel: "القسم كلّه" }); };
      sec.appendChild(b);
      sec.addEventListener("mouseenter", () => sec.classList.add("rv-hot"));
      sec.addEventListener("mouseleave", () => sec.classList.remove("rv-hot"));
    });

    // ── بطاقةٌ بعينها ─────────────────────────────────────────────────────
    document.querySelectorAll("[data-card]").forEach(card => {
      card.addEventListener("click", ev => {
        if (ev.target.closest(".rv-badge")) return;
        ev.stopPropagation();
        const sec = card.closest("[data-sec]");
        const v = norm((($(".v", card) || {}).textContent) || "");
        const l = norm((($(".l", card) || {}).textContent) || "");
        openEditor({ sec, part: "card:" + card.dataset.card,
                     partLabel: "بطاقة · " + (l || v),
                     quote: (v + " — " + l).trim() });
      });
    });

    // ── نقطةٌ أو عمودٌ في رسم ──────────────────────────────────────────────
    // لا يُمسّ إعداد الرسم: Highcharts يضع النقطة تحت المؤشّر في `hoverPoint`،
    // فتُقرأ عند النقر. وهذا يبقي المعاينة مطابقةً للمنشور.
    document.addEventListener("click", ev => {
      const cont = ev.target.closest(".highcharts-container");
      if (!cont || !window.Highcharts) return;
      const chart = Highcharts.charts.find(c => c && c.container === cont);
      const pt = chart && chart.hoverPoint;
      if (!pt) return;
      const sec = cont.closest("[data-sec]");
      if (!sec) return;
      ev.stopPropagation();
      const s = pt.series.name || "", cat = pt.category != null ? pt.category : pt.x;
      openEditor({ sec, part: `pt:${s}::${cat}`,
                   partLabel: `نقطة · ${cat}` + (chart.series.length > 1 ? ` (${s})` : ""),
                   quote: `${cat} = ${pt.y}` });
    }, true);

    /* ── نصٌّ محدَّد ────────────────────────────────────────────────────────
       **لا زرَّ عائماً.** كان زرّ «علّق على المحدَّد» يظهر عند التحديد فيصطدم
       بقائمة التحديد التي يعرضها المتصفّح نفسه في الموضع ذاته — وهي خارج
       سلطتنا فلا تُطفأ. فالتحديدُ نفسه يفتح المحرّر، **والمحرّر مرسوٌّ في
       اللوح الجانبيّ** بعيداً عن موضع التحديد، فلا تزاحم. */
    document.addEventListener("mouseup", () => setTimeout(fromSelection, 10));
  }

  function fromSelection() {
    const s = window.getSelection();
    if (!s || s.isCollapsed) return;
    const q = norm(s.toString());
    // حدٌّ أدنى: نقرةٌ مزدوجةٌ عابرة أو تحديدٌ بالخطأ لا يفتح محرّراً.
    if (q.length < 6) return;
    if (document.activeElement && /INPUT|TEXTAREA/.test(document.activeElement.tagName))
      return;
    const n = s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement;
    const sec = n && n.closest && n.closest("[data-sec]");
    if (!sec) return;
    openEditor({ sec, part: "txt:" + hash(q.slice(0, 300)),
                 partLabel: "نصّ محدَّد", quote: q.slice(0, 300) });
  }

  // ═══ المحرّر ═════════════════════════════════════════════════════════════
  function openEditor(t) {
    if (!t.sec) return;
    pending = { sec_key: t.sec.dataset.sec, sec_kind: t.sec.dataset.kind,
                sec_label: secLabel(t.sec), part_key: t.part || "",
                part_label: t.partLabel || "", quote: t.quote || "",
                parent_id: t.parent || null };
    const ed = $("#rv-editor");
    $(".loc", ed).textContent = pending.sec_label +
      (t.partLabel && t.part ? "  ›  " + t.partLabel : "");
    const q = $(".q", ed);
    q.style.display = t.quote ? "block" : "none";
    q.textContent = t.quote || "";
    ed.scrollIntoView({ block: "nearest" });
    const ta = $("textarea", ed); ta.value = "";
    /* مرسوٌّ في اللوح — لا يطفو فوق النصّ ولا يزاحم قائمة المتصفّح. */
    ed.classList.add("on");
    document.body.classList.remove("rv-collapsed");
    t.sec.classList.add("rv-flash");
    setTimeout(() => t.sec.classList.remove("rv-flash"), 1500);
    ta.focus();
  }

  function closeEditor() {
    $("#rv-editor").classList.remove("on");
    const s = window.getSelection();
    if (s) s.removeAllRanges();
    pending = null;
  }

  async function save() {
    const body = $("#rv-editor textarea").value.trim();
    if (!body || !pending) return;
    const r = await fetch(API, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ slug: RV.slug, body }, pending)) });
    if (!r.ok) return alert("تعذّر الحفظ");
    all.push(await r.json());
    closeEditor(); render();
  }

  // ═══ إيجاد الهدف من المرساة ══════════════════════════════════════════════
  /* يعيد العنصر الذي يشير إليه التعليق، أو null إن لم يعد موجوداً.
     ولا يُخمَّن بديلٌ عند الفقد — الصمت هنا أسوأ من الإعلان. */
  function locate(c) {
    const sec = document.querySelector(`[data-sec="${CSS.escape(c.sec_key)}"]`);
    if (!sec) return { sec: null, node: null, why: "القسم لم يعد موجوداً" };
    const p = c.part_key || "";
    if (!p) return { sec, node: sec };
    if (p.startsWith("card:")) {
      const n = sec.querySelector(`[data-card="${CSS.escape(p.slice(5))}"]`);
      return { sec, node: n, why: n ? null : "البطاقة لم تعد موجودة" };
    }
    if (p.startsWith("txt:")) {
      const n = findText(sec, c.quote);
      return { sec, node: n, why: n ? null : "النصّ تغيّر" };
    }
    if (p.startsWith("pt:")) return { sec, node: sec, point: p.slice(3) };
    return { sec, node: sec };
  }

  /* يبحث عن النصّ المقتبَس داخل القسم ويغلّفه بعلامة.
     والبحث بالنصّ لا بالموضع، فتحريرُ ما قبله لا يزحزحه. */
  function findText(sec, quote) {
    const q = norm(quote);
    if (!q) return null;
    const existing = [...sec.querySelectorAll("mark.rv-mark")]
      .find(m => norm(m.textContent) === q);
    if (existing) return existing;
    const w = document.createTreeWalker(sec, NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.parentElement.closest(".rv-badge, .rv-add, script, style")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    let n;
    while ((n = w.nextNode())) {
      const i = norm(n.nodeValue).indexOf(q.slice(0, 60));
      if (i < 0) continue;
      const raw = n.nodeValue.indexOf(q.slice(0, 30).trim());
      if (raw < 0) continue;
      const len = Math.min(q.length, n.nodeValue.length - raw);
      const range = document.createRange();
      range.setStart(n, raw); range.setEnd(n, raw + len);
      const mk = el("mark", "rv-mark");
      try { range.surroundContents(mk); } catch (e) { return null; }
      return mk;
    }
    return null;
  }

  // ═══ العرض ══════════════════════════════════════════════════════════════
  const visible = () => all.filter(c => filter === "all" ? true
    : filter === "mine" ? c.author_id === RV.me.id : !c.resolved);

  function card(c, isRep) {
    const loc = locate(c);
    const lost = !isRep && !loc.node;
    const d = el("div", "rv-c" + (c.resolved ? " done" : "")
      + (isRep ? " rv-rep" : "") + (lost ? " lost" : ""));
    const where = c.part_label
      ? `${esc(c.sec_label)} <b>›</b> ${esc(c.part_label)}`
      : esc(c.sec_label || c.sec_key);
    d.innerHTML =
      `<div class="h"><span class="au">${esc(c.author)}</span>` +
      `<span class="dt">${esc((c.created_at || "").slice(5, 10))}</span></div>` +
      (isRep ? "" : `<div class="loc">${where}</div>`) +
      (lost ? `<div class="warn">⚠ ${esc(loc.why)} — التعليق محفوظ</div>` : "") +
      (c.quote ? `<div class="q">${esc(c.quote)}</div>` : "") +
      `<div class="b">${esc(c.body)}</div>` +
      `<div class="acts"><button data-a="reply">ردّ</button>` +
      `<button data-a="resolve">${c.resolved ? "إعادة فتح" : "عولج"}</button></div>`;
    d.onclick = ev => {
      const a = ev.target.dataset ? ev.target.dataset.a : null;
      if (a === "reply") { ev.stopPropagation();
        return openEditor({ sec: loc.sec, part: c.part_key,
                            partLabel: c.part_label, parent: c.id }); }
      if (a === "resolve") { ev.stopPropagation(); return toggle(c); }
      jump(loc);
    };
    return d;
  }

  function jump(loc) {
    const n = loc.node || loc.sec;
    if (!n) return alert("العنصر لم يعد موجوداً في اللوحة.");
    n.scrollIntoView({ behavior: "smooth", block: "center" });
    n.classList.add("rv-flash");
    setTimeout(() => n.classList.remove("rv-flash"), 1600);
    if (loc.point && window.Highcharts) {
      const cont = loc.sec.querySelector(".highcharts-container");
      const ch = Highcharts.charts.find(c => c && c.container === cont);
      const [s, cat] = loc.point.split("::");
      if (ch) ch.series.forEach(se => se.points.forEach(p => {
        if (String(p.category != null ? p.category : p.x) === cat
            && (ch.series.length < 2 || se.name === s)) {
          p.select(true, false); setTimeout(() => p.select(false), 2200); }
      }));
    }
  }

  async function toggle(c) {
    const r = await fetch(`${API}/${c.id}/resolve`, { method: "POST" });
    c.resolved = (await r.json()).resolved;
    render();
  }

  function render() {
    document.querySelectorAll(".rv-badge").forEach(b => b.remove());
    document.querySelectorAll("mark.rv-mark").forEach(m => m.classList.remove("on"));

    const list = $("#rv-list"); list.innerHTML = "";
    const vis = visible(), roots = vis.filter(c => !c.parent_id);
    if (!roots.length) list.appendChild(el("div", "rv-empty",
      filter === "open" ? "لا تعليقات مفتوحة." : "لا تعليقات بعد."));
    roots.forEach(c => {
      list.appendChild(card(c, false));
      all.filter(r => r.parent_id === c.id).forEach(r =>
        list.appendChild(card(r, true)));
    });
    $("#rv-panel .n").textContent = `التعليقات (${roots.length})`;

    /* الشارة تُعلَّق على **الهدف نفسه** — البطاقة أو العلامة أو القسم — لا على
       القسم دائماً. فيرى المراجع أين وقع التعليق بالضبط. */
    const by = new Map();
    all.filter(c => !c.parent_id).forEach(c => {
      const k = c.sec_key + "|" + (c.part_key || "");
      (by.get(k) || by.set(k, []).get(k)).push(c);
    });
    by.forEach(cs => {
      const loc = locate(cs[0]);
      const host = loc.node || loc.sec;
      if (!host) return;
      const open = cs.filter(c => !c.resolved).length;
      if (host.tagName === "MARK") {
        host.classList.add("on", open ? "open" : "done");
        host.title = cs.length + " تعليقاً";
        host.onclick = ev => { ev.stopPropagation(); focusOn(cs[0]); };
        return;
      }
      const b = el("button", "rv-badge" + (open ? "" : " done"),
                   open ? String(open) : "✓");
      b.title = cs.map(c => c.author + ": " + c.body.slice(0, 60)).join("\n");
      b.onclick = ev => { ev.stopPropagation(); focusOn(cs[0]); };
      if (getComputedStyle(host).position === "static") host.style.position = "relative";
      host.appendChild(b);
    });
  }

  function focusOn(c) {
    filter = "all"; $("#rv-filter").value = "all"; render();
    const cards = [...$("#rv-list").children];
    const i = visible().filter(x => !x.parent_id).findIndex(x => x.id === c.id);
    const t = cards[i] || cards[0];
    if (t) { t.scrollIntoView({ block: "center" }); t.classList.add("hit");
             setTimeout(() => t.classList.remove("hit"), 1400); }
  }

  // ═══ التقييم ════════════════════════════════════════════════════════════
  let myScore = 0;

  function paintVerdicts(st) {
    const e = $("#rv-st");
    if (e) { e.textContent = { review: "قيد المراجعة", approved: "جاهزة للنشر",
      changes: "تحتاج تعديلاً" }[st.status]; e.className = "st st-" + st.status; }
    const a = $("#rv-avg");
    if (a) a.textContent = st.avg
      ? `${st.avg}/10 · ${st.passed} من ${st.n} أجاز` : "";
    const box = $("#rv-others");
    if (!box) return;
    box.innerHTML = st.verdicts.length
      ? `<div class="hd2">تقييمات الفريق</div>` + st.verdicts.map(v =>
          `<div class="v"><span class="nm">${esc(v.reviewer)}</span>
           <span class="sc">${v.score}/10</span>
           <span class="pz ${v.passed ? "y" : "n"}">
             ${v.passed ? "أجازها" : "لم يُجزها"}</span>
           ${v.note ? `<div class="nt">${esc(v.note)}</div>` : ""}</div>`).join("")
      : `<div class="hd2">لم يُقيِّمها أحدٌ بعد.</div>`;
    // تقييمي أنا — يُعرض ليُعدَّل لا ليُكرَّر
    const mine = st.verdicts.find(v => v.reviewer_id === RV.me.id);
    if (mine) {
      myScore = mine.score || 0;
      const sc = $("#rv-scale");
      if (sc) [...sc.children].forEach(x =>
        x.classList.toggle("on", +x.dataset.s <= myScore));
      const nt = $("#rv-note"), ps = $("#rv-pass");
      if (nt && !nt.value) nt.value = mine.note || "";
      if (ps) ps.checked = !!mine.passed;
    }
  }

  async function loadVerdicts() {
    paintVerdicts(await (await fetch(
      `/api/verdicts?d=${encodeURIComponent(RV.slug)}`)).json());
  }

  async function saveVerdict() {
    if (!myScore) return alert("اختر درجةً من 1 إلى 10.");
    const r = await fetch("/api/verdict", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: RV.slug, score: myScore,
        note: $("#rv-note").value, passed: $("#rv-pass").checked }) });
    if (!r.ok) return alert("تعذّر الحفظ.");
    paintVerdicts(await r.json());
    $("#rv-rate-box").classList.remove("on");
  }


  async function load() {
    all = await (await fetch(`${API}?d=${encodeURIComponent(RV.slug)}`)).json();
    render();
  }

  function start() { chrome(); wire(); load(); loadVerdicts(); reflow(); }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
})();
