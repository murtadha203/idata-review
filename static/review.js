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
  let all = [], filter = "all", pending = null;

  const $ = (s, r) => (r || document).querySelector(s);
  const el = (t, c, h) => { const e = document.createElement(t);
    if (c) e.className = c; if (h !== undefined) e.innerHTML = h; return e; };
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
  const hash = s => { let h = 0; for (let i = 0; i < s.length; i++)
    h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h).toString(36); };
  const norm = s => (s || "").replace(/\s+/g, " ").trim();

  const KINDS = { chart: "رسم", cards: "بطاقات", text: "نصّ",
                  divider: "فاصل", sources: "مصادر", table: "جدول",
                  title: "عنوان اللوحة", description: "وصف اللوحة" };

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
    /* والتحديثُ للرافع وحده — والمراجعُ يعلّق ولا يبدّل الصفحة. */
    const canPush = RV.me.role === "uploader";
    bar.innerHTML = `<a href="/">← اللوحات</a>` +
      `<span class="t">${esc(document.title.replace(/^معاينة — /, ""))}</span>` +
      `<span class="st st-${esc(RV.status)}" id="rv-st">` +
      `${esc(ST[RV.status] || RV.status)}</span>` +
      `<span class="avg" id="rv-avg"></span>` +
      (canRate ? `<button id="rv-rate" class="rate">تقييمي</button>` : "") +
      (canPush ? `<button id="rv-push" class="push">تحديث الملفّ</button>` +
                 `<input type="file" id="rv-file" accept=".html,.htm"` +
                 ` hidden>` +
                 `<button id="rv-prog" class="prog">المتابعة</button>` +
                 `<button id="rv-del" class="del" title="حذفٌ لا يُسترجَع">`
                 + `حذف</button>` : "") +
      /* **ولا يُقاس أحدٌ في الظلام.** المراجعُ يرى تقدّمَه كما يراه الرافع.
         وإخفاؤه يشتري رقماً صادقاً اليوم وثقةً مكسورةً حين يُكتشف. */
      (canRate ? `<span class="mine" id="rv-mine" title="يراه الرافعُ أيضاً">`
                 + `</span>` : "") +
      `<span class="me">${esc(RV.me.name)}</span>` +
      `<button id="rv-toggle">إخفاء اللوح</button>`;
    document.body.appendChild(bar);
    if (canPush) { wirePush(); wireDelete(); }

    const p = el("div"); p.id = "rv-panel";
    p.innerHTML = `<header><span class="n">التعليقات</span>
      <select class="flt" id="rv-filter">
        <option value="all">الكلّ</option>
        <option value="open">غير المعالَجة</option>
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

  // ═══ المتابعة: دخل · وصل · بقي ═══════════════════════════════════════
  /* **ثلاثةُ أسئلةٍ يقيسها هذا الجزء**: هل فُتحت اللوحةُ أصلاً، وأيُّ أقسامها
     بلغها القارئ، وكم بقي في كلٍّ منها.

     **والزمنُ لا يُحسب إلّا والصفحةُ منظورة.** تبويبٌ متروكٌ مفتوحاً ليلةً
     يعطي ثماني ساعاتٍ في قسمٍ لم يُقرأ. فيتوقّف العدّادُ عند `visibilitychange`
     وعند `blur`، ويستأنف عند العودة.

     **و«بلغ» غيرُ «قرأ».** القسمُ يُعدّ مبلوغاً إن ظهر في الشاشة ثانيةً
     كاملة — فالتمريرُ السريع لا يدّعي قراءةَ ما مرّ عليه. والزمنُ يُقاس
     مستقلّاً، فمن يمرّر يظهر بلوغاً عالياً وزمناً معدوماً، وذلك خبر. */
  /* **والحالةُ الابتدائيةُ تُقاس لا تُفترض.** كانت `on` تبدأ `true` دائماً،
     فلوحةٌ تُفتح في تبويبٍ خلفيٍّ تعدّ زمناً لم يُنظَر فيه — وهو العطبُ
     نفسُه الذي وُضع الحارسُ لمنعه. فتُشتقّ من حال الصفحة عند الإقلاع. */
  const isOn = () => document.visibilityState !== "hidden" && document.hasFocus();
  const TRACK = { secs: Object.create(null), vis: new Map(), on: false };
  const SEEN_MIN = 1000;      // ما دون الثانية مرورٌ لا بلوغ

  function trackTick() {
    const t = performance.now();
    TRACK.vis.forEach((since, key) => {
      if (since == null) return;
      const dt = t - since;
      if (dt <= 0) return;
      TRACK.secs[key] = (TRACK.secs[key] || 0) + dt;
      TRACK.vis.set(key, t);
    });
  }

  function trackPause() {
    trackTick();
    TRACK.vis.forEach((_, k) => TRACK.vis.set(k, null));
  }

  function trackResume() {
    const t = performance.now();
    TRACK.vis.forEach((v, k) => { if (v === null) TRACK.vis.set(k, t); });
  }

  /* يرسل ما تجمّع ويُفرغ المخزن. و`beacon` عند المغادرة لأنّ `fetch`
     يُلغى مع الصفحة، فيضيع آخرُ ما قُرئ — وهو غالباً أهمُّ ما قُرئ. */
  function trackFlush(final) {
    trackTick();
    const secs = {};
    let any = false;
    for (const k in TRACK.secs) {
      const ms = Math.round(TRACK.secs[k]);
      if (ms > 0) { secs[k] = ms; any = true; }
    }
    if (!any && !final) return;
    TRACK.secs = Object.create(null);
    const body = JSON.stringify({ slug: RV.slug, secs });
    if (final && navigator.sendBeacon) {
      navigator.sendBeacon("/api/seen",
        new Blob([body], { type: "application/json" }));
    } else {
      fetch("/api/seen", { method: "POST", keepalive: !!final,
        headers: { "Content-Type": "application/json" }, body }).catch(() => {});
    }
  }

  function startTracking() {
    TRACK.on = isOn();
    fetch("/api/seen", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: RV.slug, open: 1 }) }).catch(() => {});

    const io = new IntersectionObserver(ents => {
      const t = performance.now();
      ents.forEach(e => {
        const k = e.target.dataset.sec;
        if (!k) return;
        if (e.isIntersecting) {
          if (!TRACK.vis.has(k) || TRACK.vis.get(k) === null)
            TRACK.vis.set(k, TRACK.on ? t : null);
          /* البلوغُ يُسجَّل بعد ثانيةٍ من الظهور المتّصل، لا فورَه */
          setTimeout(() => {
            if (e.target.isConnected && TRACK.vis.get(k) != null
                && !TRACK.secs[k]) TRACK.secs[k] = TRACK.secs[k] || 1;
          }, SEEN_MIN);
        } else {
          const since = TRACK.vis.get(k);
          if (since != null) {
            TRACK.secs[k] = (TRACK.secs[k] || 0) + (t - since);
          }
          TRACK.vis.delete(k);
        }
      });
    }, { threshold: 0.25 });
    document.querySelectorAll("[data-sec]").forEach(n => io.observe(n));

    const idle = () => {
      const on = isOn();
      if (!on && TRACK.on) { TRACK.on = false; trackPause(); trackFlush(); }
      else if (on && !TRACK.on) { TRACK.on = true; trackResume(); }
    };
    document.addEventListener("visibilitychange", idle);
    addEventListener("blur", idle);
    addEventListener("focus", idle);
    setInterval(() => { if (TRACK.on) trackFlush(); }, 15000);
    addEventListener("pagehide", () => trackFlush(true));
  }

  // ═══ حذفُ الداشبورد ═══════════════════════════════════════════════════
  /* **ولا يُحذف بنقرةٍ وتأكيد.** «هل أنت متأكّد؟» تُنقر «نعم» بلا قراءة،
     لأنّها تُنقر عشرَ مرّاتٍ في اليوم لغير هذا. فيُكتب العنوانُ حرفاً بحرف:
     تأكيدٌ لا يقع سهواً، ويُجبر الحاذفَ على قراءة اسم ما يحذف.

     ويُقال له أوّلاً كم تعليقاً سيذهب معه — فالثمنُ يُعرَض قبل الدفع. */
  function wireDelete() {
    $("#rv-del").onclick = async () => {
      let n = 0;
      try {
        const cs = await (await fetch(
          `${API}?d=${encodeURIComponent(RV.slug)}`)).json();
        n = (cs || []).length;
      } catch (e) { /* العددُ تحسينٌ لا شرط */ }
      // العنوانُ من السجلّ، فهو ما يقارن به الخادم
      const title = (RV.title || "").trim();
      const warn = "حذفٌ لا يُسترجَع.\n\n"
        + (n ? `سيُحذف معه ${n} تعليقاً، ` : "سيُحذف معه ")
        + "وكلُّ التقييمات وبيانات المتابعة.\n\n"
        + `اكتب عنوان الداشبورد حرفاً بحرف للتأكيد:\n${title}`;
      const typed = prompt(warn, "");
      if (typed === null) return;
      if (typed.trim() !== title) {
        alert(`العنوانُ لا يطابق — لم يُحذف شيء.

`
          + `المطلوب: ${title}
وكتبتَ:  ${typed.trim()}`);
        return;
      }
      const b = $("#rv-del");
      b.disabled = true; b.textContent = "جارٍ الحذف…";
      try {
        const r = await fetch("/api/delete", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slug: RV.slug, confirm: typed.trim() }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) throw new Error(j.error || "تعذّر الحذف");
        alert(`حُذف «${j.title}».\n\n`
          + `تعليقات: ${j.comments} · تقييمات: ${j.verdicts} · `
          + `متابعة: ${j.views}`);
        location.href = "/";
      } catch (e) {
        alert("لم يُحذف: " + e.message);
        b.disabled = false; b.textContent = "حذف";
      }
    };
  }

  // ═══ عرضُ المتابعة ════════════════════════════════════════════════════
  const fmt = ms => {
    const s = Math.round(ms / 1000);
    if (s < 60) return s + " ثانية";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " دقيقة" + (s % 60 ? ` و${s % 60} ثانية` : "");
    return Math.floor(m / 60) + " ساعة" + (m % 60 ? ` و${m % 60} دقيقة` : "");
  };
  const when = t => (t || "").replace("T", " ").slice(0, 16) || "—";

  /* سطرُ المراجع عن نفسه: أين بلغ من هذه اللوحة. */
  async function showMine() {
    const box = $("#rv-mine"); if (!box) return;
    try {
      const j = await (await fetch(
        `/api/progress?d=${encodeURIComponent(RV.slug)}`)).json();
      const me = (j.who || []).find(w => w.id === RV.me.id);
      if (!me || !me.seen) { box.textContent = ""; return; }
      box.textContent = `بلغتَ ${me.seen} من ${j.total}`;
    } catch (e) { /* الصمتُ أهونُ من رقمٍ كاذب */ }
  }

  /* لوحُ الرافع: من فتح، وأين وصل، وكم بقي. */
  function wireProgress() {
    const btn = $("#rv-prog");
    let box = null;
    btn.onclick = async () => {
      if (box && box.style.display !== "none") {
        box.style.display = "none"; return;
      }
      if (!box) {
        box = el("div"); box.id = "rv-prog-box";
        document.body.appendChild(box);
      }
      box.style.display = "block";
      box.innerHTML = '<div class="ld">جارٍ التحميل…</div>';
      let j;
      try {
        j = await (await fetch(
          `/api/progress?d=${encodeURIComponent(RV.slug)}`)).json();
      } catch (e) { box.innerHTML = '<div class="ld">تعذّرت القراءة.</div>'; return; }

      const rows = (j.who || []).map(w => {
        if (!w.opens) {
          return `<tr class="none"><td>${esc(w.name)}</td>
            <td colspan="3">لم يفتحها بعد</td></tr>`;
        }
        const pct = j.total ? Math.round(w.seen / j.total * 100) : 0;
        return `<tr>
          <td>${esc(w.name)}</td>
          <td><b>${w.seen}</b> من ${j.total}
              <span class="pc">${pct}%</span>
              <div class="bar"><i style="width:${pct}%"></i></div></td>
          <td>${fmt(w.ms)}</td>
          <td class="dim">${esc(when(w.last_at))}<br>
              <span>${w.opens} مرّة</span></td></tr>`;
      }).join("");

      box.innerHTML = `<header>المتابعة
          <button class="x" id="rv-prog-x">×</button></header>
        <table><thead><tr><th>المراجع</th><th>أين بلغ</th>
          <th>كم بقي</th><th>آخر فتح</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="4">لا مراجعين.</td></tr>'}</tbody>
        </table>
        <p class="fine"><b>«بلغ» ليست «قرأ».</b> القسمُ يُعدّ مبلوغاً إن ظهر
          في الشاشة ثانيةً كاملة. والزمنُ لا يُحسب إلّا والصفحةُ منظورة، فلا
          يُحتسب تبويبٌ متروكٌ مفتوحاً. ومن يمرّر سريعاً يظهر بلوغاً عالياً
          وزمناً معدوماً — وذاك خبرٌ في نفسه.</p>
        <p class="fine dim">والمراجعون يرون تقدُّمَهم في شريطهم.</p>`;
      $("#rv-prog-x").onclick = () => { box.style.display = "none"; };
    };
  }

  // ═══ تحديثُ ملفّ اللوحة ═══════════════════════════════════════════════
  /* **يستبدل الصفحة ولا يُنشئ لوحة.** كان الرافع يعود إلى «رفع لوحة» ويكتب
     العنوان حرفاً بحرف ليصادف المعرّفَ نفسَه — وخطأُ حرفٍ يصنع لوحةً ثانية
     وتبقى التعليقاتُ على الأولى فتبدو ضائعة. وهنا يُرسل المعرّفُ صريحاً.

     **والتعليقاتُ تبقى، وما فقد مرساتَه يُقال عدده.** لا يُخفى ولا يُخمَّن
     له بديل: الصفحةُ الجديدة قد لا تحمل قسماً كان يحمل تعليقاً، فيُعلَن. */
  function wirePush() {
    const btn = $("#rv-push"), inp = $("#rv-file");
    btn.onclick = () => inp.click();
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      if (!confirm(`استبدالُ صفحة هذه اللوحة بـ«${f.name}»؟

` +
                   `التعليقاتُ تبقى، وتعود اللوحةُ إلى «قيد المراجعة» ` +
                   `وتُمسح التقييمات.`)) { inp.value = ""; return; }
      const was = btn.textContent;
      btn.disabled = true; btn.textContent = "جارٍ الرفع…";
      try {
        const fd = new FormData();
        fd.append("slug", RV.slug);
        fd.append("file", f, f.name);
        const r = await fetch("/replace", { method: "POST", body: fd });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error) throw new Error(j.error || "تعذّر الرفع");
        alert(`حُدِّثت الصفحة.

` +
              `تعليقاتٌ بقيت في مواضعها: ${j.kept}
` +
              (j.lost ? `تعليقاتٌ فقدت موضعها: ${j.lost} — ` +
                        `تظهر في اللوح موسومةً «القسم لم يعد موجوداً»
` : "") +
              `
وعادت اللوحةُ إلى «قيد المراجعة».`);
        location.reload();
      } catch (e) {
        alert("لم يُحدَّث الملفّ: " + e.message);
        btn.disabled = false; btn.textContent = was; inp.value = "";
      }
    };
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
    : filter === "mine" ? c.author_id === RV.me.id : true);

  /* «2026-08-26T19:14:02+00:00» ← «08-26 · 19:14» */
  function stamp(t) {
    if (!t) return "";
    const v = String(t).replace("T", " ");
    return v.slice(5, 10) + " · " + v.slice(11, 16);
  }

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
      `<span class="dt">${esc(stamp(c.created_at))}</span>` +
      (c.resolved ? `<span class="ok">عولج</span>` : "") + `</div>` +
      (isRep ? "" : `<div class="loc">${where}</div>`) +
      (lost ? `<div class="warn">⚠ ${esc(loc.why)} — التعليق محفوظ` +
        (c.sec_label ? `<br>كان في: <b>${esc(c.sec_label)}</b>` : "") +
        `</div>` : "") +
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

  function start() {
    chrome(); wire(); load(); loadVerdicts(); reflow();
    startTracking();
    if (RV.me.role === "uploader") wireProgress();
    else setInterval(showMine, 20000), showMine();
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
})();
