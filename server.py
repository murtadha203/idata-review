"""موقع مراجعة اللوحات — يعرض اللوحة **كما هي** ويضيف فوقها طبقة تعليق.

  python -X utf8 server.py            # http://127.0.0.1:8080

**الفكرة.** الملفّات المصدَّرة (PDF · Word) تنقل المضمون لا الصفحة، فيراجع
السينيور شيئاً غير الذي سيُنشر. وهذا الموقع يخدم **صفحة HTML كما هي** ولا يضيف
إليها إلّا طبقةً فوقها.

**ولا يعرف من أين جاءت.** يُولِّدها الرافع كيف شاء — `preview.py` أو غيره —
ويرفع الملفّ. فالموقع مستقلٌّ عن أدواتنا كما هو مستقلٌّ عن المنصّة.

**والمراسي.** كلّ قسمٍ في المخرَج يحمل `data-sec` مشتقّاً من نوعه وعنوانه لا من
ترتيبه، فتنجو التعليقات من إعادة التوليد. وإن تغيّر العنوان تغيّر المفتاح،
**فيظهر التعليق موسوماً «العنصر تغيّر» بدل أن ينتقل إلى غيره صامتاً**.

**والتخزين SQLite** — وهو لهجة D1 عند Cloudflare، فينتقل الجدول كما هو عند الرفع.
"""
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

# الاستضافة تُملي المنفذ والعنوان، والقرص الدائم يُركَّب على مسارٍ تختاره هي.
PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "127.0.0.1")
DATA = os.environ.get("DATA_DIR", "data")
DASH_DIR = os.path.join(DATA, "dashboards")
LIB_DIR = os.path.join(DATA, "lib")
DB_PATH = os.path.join(DATA, "review.db")
os.makedirs(DASH_DIR, exist_ok=True)
os.makedirs(LIB_DIR, exist_ok=True)

SCHEMA = """
-- `username` اسمُ دخولٍ يُكتب ويُتذكَّر، لا رمزٌ عشوائيّ. **ولا يظهر إلّا في
-- شاشة الدخول** — والتعامل في كلّ ما عداها بالاسم العربيّ حصراً.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('uploader','reviewer')));

CREATE TABLE IF NOT EXISTS dashboards (
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL, module TEXT, uploader_id INTEGER,
  cat_name TEXT,
  -- دورة الاعتماد: لا تُنشر لوحةٌ إلّا بعد `approved`
  status TEXT NOT NULL DEFAULT 'review',
  decided_by INTEGER, decided_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY (uploader_id) REFERENCES users(id));

-- المرساة ثلاثة حقول: القسم، ونوعه، وما حُدِّد داخله.
-- و`quote` نصُّ ما حُدِّد، ويُستعمل لإيجاد الموضع إن انزاح.
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY, dashboard_id INTEGER NOT NULL,
  sec_key TEXT NOT NULL, sec_kind TEXT, sec_label TEXT,
  -- `part_key` المرساة الدقيقة داخل القسم:
  --   ""              القسم كلّه
  --   "card:3"        بطاقةٌ بعينها
  --   "pt:سلسلة::فئة"  نقطةٌ في رسم
  --   "txt:<تجزئة>"    نصٌّ محدَّد، يُعاد إيجاده بـ`quote`
  part_key TEXT NOT NULL DEFAULT '', part_label TEXT,
  quote TEXT, body TEXT NOT NULL,
  author_id INTEGER NOT NULL, parent_id INTEGER,
  resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  FOREIGN KEY (dashboard_id) REFERENCES dashboards(id),
  FOREIGN KEY (author_id) REFERENCES users(id));

CREATE INDEX IF NOT EXISTS ix_c_dash ON comments(dashboard_id);

-- تقييمُ المراجع للوحة كلّها: واحدٌ لكلّ مراجعٍ لكلّ لوحة، يُحدَّث ولا يُكرَّر.
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY,
  dashboard_id INTEGER NOT NULL, reviewer_id INTEGER NOT NULL,
  score INTEGER CHECK (score IS NULL OR score BETWEEN 1 AND 10),
  note TEXT, passed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
  UNIQUE (dashboard_id, reviewer_id),
  FOREIGN KEY (dashboard_id) REFERENCES dashboards(id),
  FOREIGN KEY (reviewer_id) REFERENCES users(id));
"""

# ── السجلّ ──────────────────────────────────────────────────────────────────
# **أسماء الدخول لا تدخل الشيفرة.** هي كلمات المرور الوحيدة في هذا الموقع،
# ووضعُها في ملفٍّ يُرفع إلى مستودعٍ يجعلها مقروءةً لمن يصل إليه.
# فتُقرأ من `config/team.json` (خارج المستودع)، أو من متغيّر البيئة `TEAM_JSON`
# عند الاستضافة. وإن غاب الاثنان يُنشأ سجلٌّ تجريبيّ بأسماءٍ عشوائية.
TEAM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "config", "team.json")


def load_team():
    raw = os.environ.get("TEAM_JSON")
    if not raw and os.path.exists(TEAM_FILE):
        with open(TEAM_FILE, encoding="utf-8") as f:
            raw = f.read()
    if raw:
        try:
            return [(t["username"], t["name"], t["role"]) for t in json.loads(raw)]
        except (ValueError, KeyError) as e:
            sys.exit(f"سجلّ الفريق غير سليم: {e}")
    # سجلٌّ تجريبيّ — أسماؤه عشوائية، فلا يُستعمل بالخطأ في الإنتاج.
    print("⚠ لا سجلّ فريق — أُنشئ سجلٌّ تجريبيّ. أنشئ config/team.json.")
    return [(f"demo_{secrets.randbelow(9000) + 1000}", "مستخدم تجريبيّ", "uploader")]


TEAM = load_team()


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init():
    fresh = not os.path.exists(DB_PATH)
    c = db()
    c.executescript(SCHEMA)

    # هجرةُ قاعدةٍ أُنشئت بعمود `code`: يُعاد تسميته، ثمّ يُطابَق السجلّ
    # بالأسماء — فمن بقي في السجلّ يحتفظ بتعليقاته وتقييماته.
    ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
    if "code" in ucols and "username" not in ucols:
        c.execute("ALTER TABLE users RENAME COLUMN code TO username")

    have = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM users")}
    want = {name: (un, role) for un, name, role in TEAM}
    for un, name, role in TEAM:
        if name in have:
            c.execute("UPDATE users SET username=?, role=? WHERE id=?",
                      (un, role, have[name]))
        else:
            c.execute("INSERT INTO users (username, name, role) VALUES (?,?,?)",
                      (un, name, role))
    # من خرج من السجلّ: يُحذف هو وما كتبه — ولا يُنسب عملُه إلى غيره.
    for name, uid in have.items():
        if name not in want:
            c.execute("DELETE FROM verdicts WHERE reviewer_id=?", (uid,))
            c.execute("DELETE FROM comments WHERE author_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))
    c.commit()

    if fresh:
        print("أُنشئت قاعدة البيانات.")
    print("أسماء الدخول:")
    for u in c.execute("SELECT * FROM users ORDER BY role, id"):
        print(f"  {u['name']:<12} {'مراجع' if u['role'] == 'reviewer' else 'رافع':<7}"
              f" {u['username']}")
    c.close()


def plural(n, one, two, few, many):
    """جمعُ العربية خمس صيغ لا صيغتان — و«7 ملاحظة» خطأ يراه كلّ قارئ."""
    if n == 0:
        return None
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n % 100 <= 10:
        return f"{n} {few}"
    return f"{n} {many}"


def notes(n):
    return plural(n, "ملاحظة واحدة", "ملاحظتان", "ملاحظات", "ملاحظة") or "بلا ملاحظات"


def esc(s):
    return html.escape(str(s or ""))


# ── التصنيف والحال ──────────────────────────────────────────────────────────
# **قائمةٌ نملكها، لا فئاتُ المنصّة** — فهذا الموقع سابقٌ عليها لا تابعٌ لها:
# يُراجَع هنا، ولا يُنشر هناك إلّا بعد الإجازة. ويختار الرافعُ التصنيف.
CATEGORIES = ["الاقتصاد والمالية", "البيئة والمناخ", "الطاقة", "الزراعة والغذاء",
              "الصحّة", "التعليم", "الصناعة والتجارة", "النقل والاتصالات",
              "السكّان والعمل", "أخرى"]

STATUS = {"review": ("قيد المراجعة", "st-review"),
          "approved": ("جاهزة للنشر", "st-ok"),
          "changes": ("تحتاج تعديلاً", "st-fix")}


def derive(n_verdicts, n_passed):
    """حالُ اللوحة من التقييمات — لا تُضبط يدوياً فلا تفترق عن سجلّها."""
    if not n_verdicts:
        return "review"
    return "approved" if n_passed == n_verdicts else "changes"


def verdict_state(slug):
    """تقييماتُ لوحةٍ ومعدّلُها وحالُها المشتقّة — مصدرٌ واحد للواجهتين."""
    c = db()
    rows = c.execute("""SELECT v.*, us.name AS reviewer FROM verdicts v
                        JOIN users us ON us.id=v.reviewer_id
                        JOIN dashboards d ON d.id=v.dashboard_id
                        WHERE d.slug=? ORDER BY v.updated_at DESC""",
                     (slug,)).fetchall()
    n = len(rows)
    passed = sum(r["passed"] for r in rows)
    scores = [r["score"] for r in rows if r["score"]]
    st = derive(n, passed)
    c.execute("UPDATE dashboards SET status=? WHERE slug=?", (st, slug))
    c.commit()
    c.close()
    return {"verdicts": [dict(r) for r in rows], "n": n, "passed": passed,
            "avg": round(sum(scores) / len(scores), 1) if scores else None,
            "status": st}


# ── فصلُ المكتبات عن الصفحة ─────────────────────────────────────────────────
# لوحاتُنا تُضمّن محرّك الرسم داخلها — نحو 800 كيلو في **كلّ** ملفّ. فتُنقل
# الكتلة الكبيرة إلى ملفٍّ مستقلٍّ يُسمّى ببصمتها، ويُستبدل مكانُها وسمُ نصّ.
#
# **والفائدة أنّ البصمة واحدة لكلّ اللوحات** ما دامت المكتبة نفسها — فيُحمَّل
# الملفّ مرّةً في المتصفّح، وتفتح اللوحة الثانية والعاشرة بلا تحميلٍ جديد.
# ويُخدَم بترويسة تخزينٍ سنوية، فهو لا يتغيّر أبداً: اسمه بصمتُه.
LIB_MIN = 40_000          # ما دون هذا يبقى في مكانه — النقل لا يستحقّ طلباً


def split_libs(html):
    """يُخرج كتل السكربت الكبيرة إلى ملفّات، ويردّ الصفحة مخفَّفة."""
    out, i, moved = [], 0, 0
    while True:
        a = html.find("<script>", i)
        if a < 0:
            break
        b = html.find("</script>", a)
        if b < 0:
            break
        body = html[a + 8:b]
        if len(body) >= LIB_MIN:
            h = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
            path = os.path.join(LIB_DIR, h + ".js")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
            out.append(html[i:a])
            out.append(f'<script src="/lib/{h}.js"></script>')
            moved += 1
        else:
            out.append(html[i:b + 9])
        i = b + 9
    out.append(html[i:])
    return "".join(out), moved


def parse_multipart(body, boundary):
    """قارئُ نماذجَ مبسَّط: يردّ {اسم الحقل: (اسم الملفّ, البايتات)}.

    **ولماذا يدوياً**: `cgi.FieldStorage` أُزيل في بايثون 3.13، ولا نريد
    اعتماداً خارجياً على موقعٍ يُفترض أن يعمل بالمكتبة القياسية وحدها.
    """
    sep = b"--" + boundary
    out = {}
    for part in body.split(sep):
        if not part.strip() or part.strip() == b"--":
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if not _:
            continue
        head = head.decode("utf-8", "replace")
        m = re.search(r'name="([^"]*)"', head)
        if not m:
            continue
        fn = re.search(r'filename="([^"]*)"', head)
        out[m.group(1)] = (fn.group(1) if fn else None,
                           data.rstrip(b"\r\n"))
    return out


def slugify(name):
    """اسمٌ آمنٌ للمسار من عنوانٍ عربيّ أو لاتينيّ."""
    s = re.sub(r"[^\w\u0600-\u06FF]+", "-", (name or "").strip())
    return (s.strip("-").lower() or "dash")[:60]


# ═══ الواجهة ════════════════════════════════════════════════════════════════
SHELL = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="/static/app.css"></head>
<body>{body}</body></html>"""


def login_page(bad=False):
    return SHELL.format(title="مراجعة اللوحات", body=f"""
<div class="mid"><div class="login">
  <div class="mark">iData</div>
  <h1>مراجعة اللوحات</h1>
  <p class="sub">قبل النشر — يراجع الفريق كلّ لوحةٍ ويعلّق على مواضعها.</p>
  {'<div class="bad">اسم الدخول غير معروف.</div>' if bad else ''}
  <form method="get" action="/">
    <input name="u" placeholder="اسم الدخول" autocomplete="username" required
           dir="ltr" spellcheck="false" autocapitalize="off">
    <button>دخول</button></form>
  <p class="fine">الاسم الذي وصلك في الرابط. وتبقى الجلسة سنة.
    <br>ولا يظهر اسم الدخول في أيّ مكانٍ بعدها؛ التعامل بالأسماء.</p>
</div></div>""")


def list_page(user, rows):
    can_add = user["role"] == "uploader"

    cats = {}
    for r in rows:
        k = r["cat_name"] or "بلا تصنيف"
        cats[k] = cats.get(k, 0) + 1
    sts = {}
    for r in rows:
        k = derive(r["n_v"], r["n_pass"])
        sts[k] = sts.get(k, 0) + 1

    chips = [f'<button class="chip" data-cat="">كلّ التصنيفات '
             f'<i>{len(rows)}</i></button>']
    for k, n in sorted(cats.items(), key=lambda t: -t[1]):
        chips.append(f'<button class="chip" data-cat="{esc(k)}">{esc(k)} '
                     f'<i>{n}</i></button>')

    cards = []
    for r in rows:
        n, op = r["n_comments"], r["n_open"]
        # **شارةُ الملاحظات لا شارةُ المراجعة.** كانت تقول «لم تُراجَع بعد»
        # حين لا تعليقات — فتناقض «جاهزة للنشر» على البطاقة نفسها، والحالان
        # يجتمعان: لوحةٌ أُجيزت بلا ملاحظةٍ واحدة. ولا تُعرض عند الصفر، فالسطر
        # الأسفل يقول «0 تعليقاً» أصلاً.
        badge = (f'<span class="pill open">{notes(op)} مفتوحة</span>' if op
                 else '<span class="pill done">عولجت الملاحظات</span>' if n
                 else '')
        cat = r["cat_name"] or "بلا تصنيف"
        st = derive(r["n_v"], r["n_pass"])
        st_txt, st_cls = STATUS[st]
        sc = (f'<span class="score">{r["avg_score"]}<i>/10</i></span>'
              if r["avg_score"] else '')
        vote = (f'<span class="vote">{r["n_pass"]} من {r["n_v"]} أجاز</span>'
                if r["n_v"] else '<span class="vote dim">بانتظار التقييم</span>')
        cards.append(f"""<a class="dash {st_cls}" href="/d/{esc(r['slug'])}"
     data-cat="{esc(cat)}" data-n="{n}" data-open="{op}" data-st="{st}"
     data-score="{r['avg_score'] or 0}"
     data-up="{esc(r['updated_at'] or '')}" data-t="{esc(r['title'])}">
  <div class="dash-h"><span class="cat">{esc(cat)}</span>{badge}</div>
  <div class="row2"><span class="st {st_cls}">{esc(st_txt)}</span>
     {sc}</div>
  <div class="dash-t">{esc(r['title'])}</div>
  <div class="dash-m">رفعها {esc(r['uploader'] or '—')} ·
     {esc((r['updated_at'] or '')[:10])}</div>
  <div class="dash-b"><span class="cnt">{notes(n)} · {vote}</span>
     <span class="go">فتح ←</span></div></a>""")

    add = ('<a class="addbtn" href="/new">+ رفع لوحة للمراجعة</a>'
           if can_add else "")

    boards = plural(len(rows), "لوحة واحدة", "لوحتان", "لوحات", "لوحة") or "لا لوحات"
    tot = sum(r["n_comments"] for r in rows)
    op_tot = sum(r["n_open"] for r in rows)
    return SHELL.format(title="اللوحات", body=f"""
<header class="top"><div class="brand">مراجعة اللوحات</div>
  <div class="who"><span>{esc(user['name'])}</span>
    <span class="role">{'رفع' if can_add else 'مراجعة'}</span>
    <a href="/export" class="lnk">تصدير</a>
    <a href="/out" class="lnk">خروج</a></div></header>
<main class="wrap">
  <div class="stats">
    <b>{boards}</b> · <b class="ok">{sts.get("approved", 0)}</b> جاهزة للنشر · <b>{notes(tot)}</b>
    {"· <b class=\"hot\">" + str(op_tot) + " مفتوحة</b>" if op_tot else ""}</div>
  {add}
  <div class="bar">
    <div class="chips">{''.join(chips)}
      <span class="sep"></span>
      {''.join(f'<button class="chip st-chip" data-st="{k}">'
               f'{STATUS[k][0].split(" — ")[0]} <i>{v}</i></button>'
               for k, v in sorted(sts.items()))}</div>
    <div class="tools">
      <input id="q" placeholder="بحث…" autocomplete="off">
      <select id="sort">
        <option value="fewest">الأقلّ تعليقاً</option>
        <option value="most">الأكثر تعليقاً</option>
        <option value="open">الأكثر مفتوحاً</option>
        <option value="recent">الأحدث</option>
        <option value="score">الأدنى تقييماً</option>
        <option value="title">أبجدياً</option>
      </select></div></div>
  <div class="grid" id="grid">{''.join(cards)
      or '<p class="empty">لا لوحات بعد.</p>'}</div>
  <p class="empty" id="none" hidden>لا نتائج.</p>
</main>
<script src="/static/list.js" defer></script>""")



def new_page(err=""):
    cops = "".join(f'<option value="{esc(x)}">{esc(x)}</option>'
                   for x in CATEGORIES)
    return SHELL.format(title="رفع لوحة", body=f"""
<header class="top"><div class="brand">
  <a href="/">مراجعة اللوحات</a></div>
  <div class="who"><a href="/" class="lnk">← رجوع</a></div></header>
<main class="wrap narrow">
  <h1 class="pg">رفع لوحة للمراجعة</h1>
  <p class="pg-sub">تُرفع هنا فقط — <b>ولا تُنشر على المنصّة</b> إلّا بعد
    أن يُجيزها المراجعون.</p>
  {f'<div class="bad">{esc(err)}</div>' if err else ''}

  <form class="panel" method="post" action="/add" enctype="multipart/form-data">
    <label>ملفّ اللوحة
      <input type="file" name="file" accept=".html,.htm" required></label>
    <label>العنوان
      <input name="title" placeholder="اسم اللوحة كما يراه الفريق" required></label>
    <label>التصنيف<select name="category">{cops}</select></label>
    <button class="submit">رفع للمراجعة</button>
  </form>

  <p class="tip">صفحة HTML واحدة. تُعرض كما هي بلا مساس، وتُضاف عليها طبقة
    التعليق. ورفعُ ملفٍّ بالعنوان نفسه يستبدله ويُرجع اللوحة إلى المراجعة.</p>
</main>""")

# ═══ الخادم ═════════════════════════════════════════════════════════════════
class H(BaseHTTPRequestHandler):
    server_version = "review/1.0"

    def log_message(self, *a):
        pass

    # ── مساعدات ─────────────────────────────────────────────────────────────
    def user(self):
        ck = self.headers.get("Cookie") or ""
        m = re.search(r"rv=([A-Za-z0-9_.-]+)", ck)
        if not m:
            return None
        c = db()
        u = c.execute("SELECT * FROM users WHERE lower(username)=lower(?)",
                      (m.group(1),)).fetchone()
        c.close()
        return u

    def send(self, body, code=200, ctype="text/html; charset=utf-8", extra=()):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(json.dumps(obj, ensure_ascii=False), code,
                  "application/json; charset=utf-8")

    def redirect(self, to, extra=()):
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def body_json(self):
        """يقرأ الجسم، ويردّ None عند تشوّهه.

        **ولا يُترك ليرمي**: استثناءٌ داخل المعالج يقطع الاتّصال فيرى العميل
        ردّاً فارغاً لا رمزَ خطأ — فيبدو كأنّ الطلب نجح ولم يُحفَظ. وقد وقع
        هذا في الاختبار حين أرسل الشلّ بايتاً غير UTF-8.
        """
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None

    def body_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"))

    # ── GET ─────────────────────────────────────────────────────────────────
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        path = urllib.parse.unquote(p.path)

        if path.startswith("/lib/"):
            f = os.path.join(LIB_DIR, os.path.basename(path))
            if not os.path.exists(f):
                return self.send("404", 404)
            with open(f, "rb") as fh:
                # الاسم بصمةُ المحتوى، فالملفّ لا يتغيّر أبداً — يُخزَّن سنة.
                return self.send(fh.read(), 200,
                                 "application/javascript; charset=utf-8",
                                 [("Cache-Control",
                                   "public, max-age=31536000, immutable")])

        if path.startswith("/static/"):
            f = os.path.join("static", os.path.basename(path))
            if not os.path.exists(f):
                return self.send("404", 404)
            ct = ("text/css; charset=utf-8" if f.endswith(".css")
                  else "application/javascript; charset=utf-8")
            with open(f, "rb") as fh:
                return self.send(fh.read(), 200, ct)

        # الدخول بالرمز: يُثبَّت في كعكة ويُنظَّف من الرابط
        if "u" in q or "code" in q:
            name = (q.get("u") or q.get("code"))[0].strip()
            c = db()
            row = c.execute("SELECT username FROM users "
                            "WHERE lower(username)=lower(?)", (name,)).fetchone()
            c.close()
            if row:
                # يُخزَّن كما هو في السجلّ، لا كما كُتب — فالمطابقة بلا حساسية
                # لحالة الأحرف، والكعكة يجب أن تطابق قراءتها لاحقاً.
                return self.redirect("/", [("Set-Cookie",
                                            f"rv={row['username']}; Path=/; "
                                            f"Max-Age=31536000; SameSite=Lax")])
            return self.send(login_page(bad=True), 401)

        if path == "/out":
            return self.redirect("/", [("Set-Cookie", "rv=; Path=/; Max-Age=0")])

        u = self.user()
        if not u:
            return self.send(login_page(), 200)

        if path == "/new":
            if u["role"] != "uploader":
                return self.redirect("/")
            return self.send(new_page())

        if path == "/":
            c = db()
            rows = c.execute("""
              SELECT d.*, us.name AS uploader,
                (SELECT COUNT(*) FROM comments k WHERE k.dashboard_id=d.id) n_comments,
                (SELECT COUNT(*) FROM comments k WHERE k.dashboard_id=d.id
                   AND k.resolved=0) n_open,
                (SELECT COUNT(*) FROM verdicts v WHERE v.dashboard_id=d.id) n_v,
                (SELECT COUNT(*) FROM verdicts v WHERE v.dashboard_id=d.id
                   AND v.passed=1) n_pass,
                (SELECT ROUND(AVG(v.score),1) FROM verdicts v
                   WHERE v.dashboard_id=d.id AND v.score IS NOT NULL) avg_score
              FROM dashboards d LEFT JOIN users us ON us.id=d.uploader_id
              ORDER BY n_comments ASC, d.updated_at DESC""").fetchall()
            c.close()
            return self.send(list_page(u, rows))

        if path.startswith("/d/"):
            slug = path[3:]
            c = db()
            d = c.execute("SELECT * FROM dashboards WHERE slug=?", (slug,)).fetchone()
            c.close()
            if not d:
                return self.send("404", 404)
            f = os.path.join(DASH_DIR, slug + ".html")
            with open(f, encoding="utf-8") as fh:
                page = fh.read()
            # طبقة التعليق تُحقن، ولا يُمسّ شيءٌ من الصفحة نفسها
            ctx = {"slug": slug, "status": d["status"] or "review",
                   "me": {"id": u["id"], "name": u["name"], "role": u["role"]}}
            inject = (f'<link rel="stylesheet" href="/static/review.css">'
                      f'<script>window.RV={json.dumps(ctx, ensure_ascii=False)};'
                      f'</script>'
                      f'<script src="/static/review.js" defer></script>')
            page = page.replace("</head>", inject + "</head>", 1)
            return self.send(page)

        if path == "/api/verdicts":
            return self.json(verdict_state(q.get("d", [""])[0]))

        if path == "/api/comments":
            slug = q.get("d", [""])[0]
            c = db()
            rows = c.execute("""
              SELECT k.*, us.name AS author, us.role AS author_role
              FROM comments k JOIN users us ON us.id=k.author_id
              JOIN dashboards d ON d.id=k.dashboard_id
              WHERE d.slug=? ORDER BY k.created_at""", (slug,)).fetchall()
            c.close()
            return self.json([dict(r) for r in rows])

        if path == "/export":
            c = db()
            rows = c.execute("""
              SELECT d.slug, d.title, k.sec_key, k.sec_kind, k.sec_label,
                     k.part_key, k.part_label,
                     k.quote, k.body, us.name AS author, k.resolved, k.created_at
              FROM comments k JOIN users us ON us.id=k.author_id
              JOIN dashboards d ON d.id=k.dashboard_id
              ORDER BY d.slug, k.created_at""").fetchall()
            c.close()
            return self.send(
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1),
                200, "application/json; charset=utf-8",
                [("Content-Disposition", 'attachment; filename="comments.json"')])

        self.send("404", 404)

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self):
        u = self.user()
        if not u:
            return self.json({"error": "auth"}, 401)
        path = urllib.parse.urlparse(self.path).path

        if path == "/add":
            if u["role"] != "uploader":
                return self.json({"error": "forbidden"}, 403)
            ctype = self.headers.get("Content-Type") or ""
            n = int(self.headers.get("Content-Length") or 0)
            if "multipart/form-data" not in ctype:
                return self.send(new_page("أرسل الملفّ من النموذج."), 400)
            bnd = re.search(r"boundary=([^;]+)", ctype)
            raw = parse_multipart(self.rfile.read(n),
                                  bnd.group(1).strip('"').encode())
            fields = {k: (v[1].decode("utf-8", "replace") if v[0] is None else v)
                      for k, v in raw.items()}

            cat = fields.get("category")
            if cat not in CATEGORIES:
                cat = CATEGORIES[-1]
            up = fields.get("file")
            title = (fields.get("title") or "").strip()[:160]
            try:
                if not (isinstance(up, tuple) and up[0] and up[1]):
                    raise RuntimeError("لم يصل ملفّ.")
                if not title:
                    raise RuntimeError("اكتب عنوان اللوحة.")
                if b"<" not in up[1][:2000]:
                    raise RuntimeError("الملفّ لا يبدو صفحة HTML.")
                slug = slugify(title)
                page, moved = split_libs(up[1].decode("utf-8", "replace"))
                with open(os.path.join(DASH_DIR, slug + ".html"), "w",
                          encoding="utf-8") as f:
                    f.write(page)
            except Exception as e:
                return self.send(new_page(str(e)[-300:]), 400)

            c = db()
            # إعادةُ الرفع تُرجع اللوحة إلى المراجعة وتمسح التقييمات — فالمُجاز
            # هو النسخة التي رآها المراجع، لا أيّ نسخةٍ بعدها.
            c.execute("""INSERT INTO dashboards (slug,title,uploader_id,
                          cat_name,status,created_at,updated_at)
                         VALUES (?,?,?,?,'review',?,?)
                         ON CONFLICT(slug) DO UPDATE SET
                          title=excluded.title, cat_name=excluded.cat_name,
                          status='review', updated_at=excluded.updated_at""",
                      (slug, title, u["id"], cat, now(), now()))
            row = c.execute("SELECT id FROM dashboards WHERE slug=?",
                            (slug,)).fetchone()
            c.execute("DELETE FROM verdicts WHERE dashboard_id=?", (row["id"],))
            c.commit()
            c.close()
            return self.redirect(f"/d/{urllib.parse.quote(slug)}")

        if path == "/api/comments":
            b = self.body_json()
            if b is None:
                return self.json({"error": "جسم الطلب مشوَّه"}, 400)
            c = db()
            d = c.execute("SELECT id FROM dashboards WHERE slug=?",
                          (b.get("slug"),)).fetchone()
            if not d or not (b.get("body") or "").strip():
                c.close()
                return self.json({"error": "bad"}, 400)
            cur = c.execute("""INSERT INTO comments (dashboard_id,sec_key,sec_kind,
                    sec_label,part_key,part_label,quote,body,author_id,
                    parent_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (d["id"], b.get("sec_key") or "", b.get("sec_kind"),
                             (b.get("sec_label") or "")[:160],
                             (b.get("part_key") or "")[:120],
                             (b.get("part_label") or "")[:160],
                             (b.get("quote") or "")[:400], b["body"].strip(),
                             u["id"], b.get("parent_id"), now()))
            c.commit()
            r = c.execute("""SELECT k.*, us.name AS author, us.role AS author_role
                             FROM comments k JOIN users us ON us.id=k.author_id
                             WHERE k.id=?""", (cur.lastrowid,)).fetchone()
            c.close()
            return self.json(dict(r), 201)

        if path == "/api/verdict":
            if u["role"] != "reviewer":
                return self.json({"error": "reviewers only"}, 403)
            b = self.body_json()
            if b is None:
                return self.json({"error": "جسم الطلب مشوَّه"}, 400)
            try:
                score = int(b.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            if not 1 <= score <= 10:
                return self.json({"error": "score 1..10"}, 400)
            c = db()
            row = c.execute("SELECT id FROM dashboards WHERE slug=?",
                            (b.get("slug"),)).fetchone()
            if not row:
                c.close()
                return self.json({"error": "dashboard"}, 404)
            c.execute("""INSERT INTO verdicts (dashboard_id,reviewer_id,score,
                          note,passed,updated_at) VALUES (?,?,?,?,?,?)
                         ON CONFLICT(dashboard_id,reviewer_id) DO UPDATE SET
                          score=excluded.score, note=excluded.note,
                          passed=excluded.passed,
                          updated_at=excluded.updated_at""",
                      (row["id"], u["id"], score, (b.get("note") or "").strip(),
                       1 if b.get("passed") else 0, now()))
            c.commit()
            c.close()
            return self.json(verdict_state(b.get("slug")))

        if path == "/api/decide":
            # الاعتماد قرارُ مراجعةٍ لا رفع — فالرافع لا يعتمد عمل نفسه.
            if u["role"] != "reviewer":
                return self.json({"error": "reviewers only"}, 403)
            b = self.body_json()
            if b is None:
                return self.json({"error": "جسم الطلب مشوَّه"}, 400)
            st = b.get("status")
            if st not in STATUS:
                return self.json({"error": "status"}, 400)
            c = db()
            c.execute("""UPDATE dashboards SET status=?, decided_by=?, decided_at=?
                         WHERE slug=?""", (st, u["id"], now(), b.get("slug")))
            c.commit()
            c.close()
            return self.json({"status": st, "by": u["name"]})

        if path.startswith("/api/comments/") and path.endswith("/resolve"):
            cid = path.split("/")[3]
            c = db()
            c.execute("UPDATE comments SET resolved=1-resolved WHERE id=?", (cid,))
            c.commit()
            r = c.execute("SELECT resolved FROM comments WHERE id=?", (cid,)).fetchone()
            c.close()
            return self.json({"resolved": r["resolved"] if r else 0})

        self.json({"error": "404"}, 404)


if __name__ == "__main__":
    init()
    print(f"يعمل على {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
