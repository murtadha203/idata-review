/* تصفيةٌ وفرزٌ في المتصفّح.
 *
 * البطاقات كلّها مُصيَّرةٌ في الصفحة، والتصفية إخفاءٌ لا طلبٌ جديد — فاللوحات
 * عشراتٌ لا آلاف، والاستجابة فورية. ويُحفَظ الاختيار محلّياً فيبقى بين الزيارات.
 *
 * **بُعدان مستقلّان لا واحد.** كانت رقائق التصنيف ورقائق الحال تكتبان في
 * متغيّرٍ واحد، فضغطُ رقاقة حالٍ يمسح التصنيف المختار **ويبدو كأنّه لا يفعل
 * شيئاً**. فصار لكلّ بُعدٍ متغيّرُه، والشرطان يجتمعان.
 */
(function () {
  const grid = document.getElementById("grid");
  if (!grid) return;

  const cards = [...grid.querySelectorAll(".dash")];
  const q = document.getElementById("q");
  const sortSel = document.getElementById("sort");
  const none = document.getElementById("none");
  const catChips = [...document.querySelectorAll(".chip[data-cat]")];
  const stChips = [...document.querySelectorAll(".chip[data-st]")];
  const KEY = "rv-list-prefs";

  let cat = "", st = "", term = "", how = "fewest";
  try {
    const p = JSON.parse(localStorage.getItem(KEY) || "{}");
    cat = p.cat || ""; st = p.st || ""; how = p.how || "fewest";
  } catch (e) {}
  if (!catChips.some(c => c.dataset.cat === cat)) cat = "";
  if (!stChips.some(c => c.dataset.st === st)) st = "";
  sortSel.value = how;

  const num = (e, k) => parseFloat(e.dataset[k] || "0") || 0;
  const CMP = {
    fewest: (a, b) => num(a, "n") - num(b, "n")
      || (b.dataset.up || "").localeCompare(a.dataset.up || ""),
    most: (a, b) => num(b, "n") - num(a, "n"),
    open: (a, b) => num(b, "open") - num(a, "open") || num(b, "n") - num(a, "n"),
    recent: (a, b) => (b.dataset.up || "").localeCompare(a.dataset.up || ""),
    // «الأدنى تقييماً» يقدّم المُقيَّم المنخفض ويؤخّر ما لم يُقيَّم بعد —
    // فغيابُ التقييم ليس درجةً منخفضة.
    score: (a, b) => (num(a, "score") || 99) - (num(b, "score") || 99)
      || num(a, "n") - num(b, "n"),
    title: (a, b) => (a.dataset.t || "").localeCompare(b.dataset.t || "", "ar"),
  };

  function paintChips() {
    catChips.forEach(c => c.classList.toggle("on", c.dataset.cat === cat));
    stChips.forEach(c => c.classList.toggle("on", c.dataset.st === st));
  }

  function apply() {
    let shown = 0;
    cards.forEach(c => {
      const ok = (!cat || c.dataset.cat === cat)
        && (!st || c.dataset.st === st)
        && (!term || (c.dataset.t || "").toLowerCase().includes(term)
                  || (c.dataset.cat || "").toLowerCase().includes(term));
      c.hidden = !ok;
      if (ok) shown++;
    });
    [...cards].sort(CMP[how] || CMP.fewest).forEach(c => grid.appendChild(c));
    none.hidden = shown > 0;
    paintChips();
    try { localStorage.setItem(KEY, JSON.stringify({ cat, st, how })); }
    catch (e) {}
  }

  catChips.forEach(c => c.onclick = () => { cat = c.dataset.cat; apply(); });
  // رقاقةُ الحال تُطفأ بضغطها ثانيةً — فلا يحتاج المستخدم رقاقةَ «كلّ الحالات».
  stChips.forEach(c => c.onclick = () => {
    st = (st === c.dataset.st) ? "" : c.dataset.st; apply();
  });
  q.oninput = () => { term = q.value.trim().toLowerCase(); apply(); };
  sortSel.onchange = () => { how = sortSel.value; apply(); };
  apply();
})();
