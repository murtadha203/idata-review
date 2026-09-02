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
import threading
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
  name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('uploader','reviewer')),
  gender TEXT NOT NULL DEFAULT 'm' CHECK (gender IN ('m','f')));

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
  resolved INTEGER NOT NULL DEFAULT 0, resolved_at TEXT, resolved_by INTEGER,
  edited_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (dashboard_id) REFERENCES dashboards(id),
  FOREIGN KEY (author_id) REFERENCES users(id));

CREATE INDEX IF NOT EXISTS ix_c_dash ON comments(dashboard_id);

-- تقييمُ المراجع للوحة كلّها: واحدٌ لكلّ مراجعٍ لكلّ لوحة، يُحدَّث ولا يُكرَّر.
-- ═══ المتابعة: هل فُتحت، وأين وصل، وكم بقي ═══════════════════════════════
-- **ثلاثةُ أسئلةٍ لا سؤالٌ واحد**، ولكلٍّ منها عمودُه:
--   `opens` · `first_at` · `last_at`   هل دخل أصلاً، ومتى، وكم مرّة
--   `section_views.seen_at`            أين وصل — أوّلُ ظهورٍ لكلِّ قسم
--   `section_views.ms`                 كم بقي في القسم، مجموعاً
--
-- **ولا يُحسب زمنٌ والصفحةُ خلف تبويب.** تبويبٌ متروكٌ مفتوحاً ليلةً يعطي
-- ثماني ساعاتٍ في قسمٍ لم يُقرأ، فيُفسد المقياسَ لا يزيّنه. فالعميلُ لا
-- يرسل إلّا زمنَ الظهور الفعليّ، والخادمُ يسقف الدفعةَ الواحدة.
CREATE TABLE IF NOT EXISTS views (
  id INTEGER PRIMARY KEY,
  dashboard_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  opens INTEGER NOT NULL DEFAULT 0,
  first_at TEXT NOT NULL, last_at TEXT NOT NULL,
  -- **والمُدخَلُ بيدٍ يُوسَم.** رقمٌ أدخلَه الرافعُ عن ظنٍّ صادقٍ يبقى ظنّاً،
  -- وخلطُه بالمقيس يُفقد الجدولَ معناه كلَّه. فيُفصل ويُعلَن في اللوح.
  manual INTEGER NOT NULL DEFAULT 0,
  UNIQUE (dashboard_id, user_id),
  FOREIGN KEY (dashboard_id) REFERENCES dashboards(id),
  FOREIGN KEY (user_id) REFERENCES users(id));

CREATE TABLE IF NOT EXISTS section_views (
  id INTEGER PRIMARY KEY,
  dashboard_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  sec_key TEXT NOT NULL, seen_at TEXT NOT NULL,
  ms INTEGER NOT NULL DEFAULT 0,
  manual INTEGER NOT NULL DEFAULT 0,
  UNIQUE (dashboard_id, user_id, sec_key),
  FOREIGN KEY (dashboard_id) REFERENCES dashboards(id),
  FOREIGN KEY (user_id) REFERENCES users(id));

CREATE INDEX IF NOT EXISTS ix_sv ON section_views(dashboard_id, user_id);

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
            return [(t["username"], t["name"], t["role"],
                     (t.get("email") or "").strip(),
                     "f" if (t.get("gender") or "m").lower().startswith("f")
                     else "m")
                    for t in json.loads(raw)]
        except (ValueError, KeyError) as e:
            sys.exit(f"سجلّ الفريق غير سليم: {e}")
    # سجلٌّ تجريبيّ — أسماؤه عشوائية، فلا يُستعمل بالخطأ في الإنتاج.
    print("⚠ لا سجلّ فريق — أُنشئ سجلٌّ تجريبيّ. أنشئ config/team.json.")
    return [(f"demo_{secrets.randbelow(9000) + 1000}", "مستخدم تجريبيّ",
             "uploader", "", "m")]


TEAM = load_team()
EMAILS = {t[1]: t[3] for t in TEAM if len(t) > 3 and t[3]}


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

    # وقواعدُ أُنشئت قبل عمود `manual` تُكمَّل بلا فقدِ صفّ
    kcols = {r["name"] for r in c.execute("PRAGMA table_info(comments)")}
    if kcols and "resolved_at" not in kcols:
        c.execute("ALTER TABLE comments ADD COLUMN resolved_at TEXT")
    if kcols and "edited_at" not in kcols:
        c.execute("ALTER TABLE comments ADD COLUMN edited_at TEXT")
    if kcols and "resolved_by" not in kcols:
        c.execute("ALTER TABLE comments ADD COLUMN resolved_by INTEGER")

    ucols2 = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
    if ucols2 and "gender" not in ucols2:
        c.execute("ALTER TABLE users ADD COLUMN gender TEXT NOT NULL "
                  "DEFAULT 'm'")

    for t in ("views", "section_views"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        if cols and "manual" not in cols:
            c.execute(f"ALTER TABLE {t} ADD COLUMN manual "
                      f"INTEGER NOT NULL DEFAULT 0")

    have = {(r["username"] or "").lower(): r["id"]
            for r in c.execute("SELECT id, username FROM users")}
    want = {t[0].lower() for t in TEAM}
    for un, name, role, _mail, gen in TEAM:
        k = un.lower()
        if k in have:
            c.execute("UPDATE users SET username=?, name=?, role=?, gender=? "
                      "WHERE id=?", (un, name, role, gen, have[k]))
        else:
            c.execute("INSERT INTO users (username, name, role, gender) "
                      "VALUES (?,?,?,?)", (un, name, role, gen))
    # من خرج من السجلّ: يُحذف هو وما كتبه — ولا يُنسب عملُه إلى غيره.
    # **ويُعرف بخروج اسم دخوله**، فتغييرُ الاسم المعروض لا يمسّ شيئاً.
    for k, uid in have.items():
        if k not in want:
            for t in ("section_views", "views", "verdicts"):
                c.execute(f"DELETE FROM {t} WHERE "
                          f"{'reviewer_id' if t == 'verdicts' else 'user_id'}=?",
                          (uid,))
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


# ── وسمُ المراجع على كلِّ داشبورد ─────────────────────────────────────────
# **وسمٌ واحدٌ لا أربعة.** أربعةُ أوسمةٍ على بطاقةٍ واحدةٍ تصير زينةً لا
# إشارة، فيُعرض أعلاها أولويّةً وحدَه.
#
# **والترتيبُ ترتيبُ إلحاح**: من ينتظرك ردُّه أولى ممّن لم تبدأه، ومن لم
# تبدأه أولى ممّن بدأتَه. والأخيرُ «لم تُكمله» — وهو أهونُها.
TAGS = [("reply", "ردودٌ لك"), ("none", "لم تفتحه"),
        ("skim", "مررتَ سريعاً"), ("part", "لم تُكمله")]


def owns(conn, uid, slug=None, did=None):
    """أصاحبُ هذه اللوحة هو؟ — لا كلُّ من له دورُ رفع."""
    r = (conn.execute("SELECT uploader_id FROM dashboards WHERE slug=?",
                      (slug,)).fetchone() if slug else
         conn.execute("SELECT uploader_id FROM dashboards WHERE id=?",
                      (did,)).fetchone())
    return bool(r) and r["uploader_id"] == uid


def reviewer_tags(uid):
    """يردّ {رقم الداشبورد: (المفتاح، النصّ)} — أو لا شيءَ لمن أتمّه."""
    import digest
    c = db()
    out = {}
    for d in c.execute("SELECT id, slug FROM dashboards WHERE status='review'"):
        try:
            with open(os.path.join(DASH_DIR, d["slug"] + ".html"),
                      encoding="utf-8") as fh:
                total = len(set(re.findall(r'data-sec="([^"]+)"', fh.read())))
        except OSError:
            total = 0
        v = c.execute("""SELECT opens, last_at FROM views
                         WHERE dashboard_id=? AND user_id=?""",
                      (d["id"], uid)).fetchone()
        if not v or not v["opens"]:
            out[d["id"]] = TAGS[1]
            continue
        # ردودٌ ومعالجاتٌ بعد آخر زيارة
        nr = c.execute("""
          SELECT (SELECT COUNT(*) FROM comments k JOIN comments p
                    ON p.id=k.parent_id
                  WHERE k.dashboard_id=? AND p.author_id=? AND k.author_id<>?
                    AND k.created_at > ?)
               + (SELECT COUNT(*) FROM comments
                  WHERE dashboard_id=? AND author_id=? AND resolved=1
                    AND resolved_at IS NOT NULL AND resolved_at > ?
                    AND resolved_by IS NOT NULL AND resolved_by <> ?) n""",
          (d["id"], uid, uid, v["last_at"], d["id"], uid,
           v["last_at"], uid)).fetchone()["n"]
        if nr:
            out[d["id"]] = TAGS[0]
            continue
        g = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(ms),0) ms
                         FROM section_views
                         WHERE dashboard_id=? AND user_id=?""",
                      (d["id"], uid)).fetchone()
        if g["ms"] < digest.min_ms(total):
            out[d["id"]] = TAGS[2]
        elif total and g["n"] < total:
            out[d["id"]] = TAGS[3]
    c.close()
    return out


# ── الملاحظةُ المفتوحة ───────────────────────────────────────────────────
# **سلسلةٌ لا سطر.** كان العدُّ يحسب كلَّ صفٍّ `resolved=0`، فتعليقُ المراجع
# واحدةٌ وردُّ صاحبِ اللوحةِ عليه ثانية — فيصحّح الناشرُ فيرتفع العدّاد.
#
# **وتُغلَق بصاحبِ اللوحةِ وحدَه**: ردُّه أو زرُّ «عولج». وردُّ مراجعٍ آخر
# ليس إغلاقاً، وإن رُدَّ بعده فُتحت من جديد — فالعبرةُ بآخرِ ردّ.
LAST_AUTHOR = """COALESCE((SELECT r.author_id FROM comments r
                            WHERE r.parent_id=k.id
                            ORDER BY r.created_at DESC, r.id DESC LIMIT 1),
                          k.author_id)"""

OPEN_ONE = f"""k.parent_id IS NULL AND k.resolved=0
               AND {LAST_AUTHOR} <> {{owner}}"""


def open_sql(owner="d.uploader_id"):
    """شرطُ «مفتوحة» لصفٍّ اسمُه `k`، ومالكُ اللوحةِ `owner`."""
    return OPEN_ONE.format(owner=owner)


def list_page(user, rows, tags=None):
    tags = tags or {}
    can_add = user["role"] == "uploader"

    cats = {}
    for r in rows:
        k = r["cat_name"] or "بلا تصنيف"
        cats[k] = cats.get(k, 0) + 1
    sts = {}
    for r in rows:
        k = derive(r["n_v"], r["n_pass"])
        sts[k] = sts.get(k, 0) + 1
    # **والناشرُ بُعدٌ ثالث.** يُحصى بالاسمِ لا بالرقم، فالرقاقةُ تُقرأ.
    ups = {}
    for r in rows:
        k = r["uploader"] or "—"
        ups[k] = ups.get(k, 0) + 1

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
        owner = r["uploader_id"] == user["id"]
        badge = (f'<span class="pill open">{notes(op)} مفتوحة</span>'
                 if op and owner
                 else '<span class="pill done">عولجت الملاحظات</span>'
                 if n and not op and not owner
                 else '')
        cat = r["cat_name"] or "بلا تصنيف"
        st = derive(r["n_v"], r["n_pass"])
        st_txt, st_cls = STATUS[st]
        sc = (f'<span class="score">{r["avg_score"]}<i>/10</i></span>'
              if r["avg_score"] else '')
        vote = (f'<span class="vote">{r["n_pass"]} من {r["n_v"]} أجاز</span>'
                if r["n_v"] else '<span class="vote dim">بانتظار التقييم</span>')
        tg = tags.get(r["id"])
        tag = (f'<span class="rtag rt-{tg[0]}">{esc(tg[1])}</span>'
               if tg else "")
        cards.append(f"""<a class="dash {st_cls}" href="/d/{esc(r['slug'])}"
     data-cat="{esc(cat)}" data-n="{n}" data-open="{op}" data-st="{st}"
     data-score="{r['avg_score'] or 0}"
     data-up="{esc(r['updated_at'] or '')}" data-t="{esc(r['title'])}"
     data-uploader="{esc(r['uploader'] or '—')}">
  <div class="dash-h"><span class="cat">{esc(cat)}</span>{tag}{badge}</div>
  <div class="row2"><span class="st {st_cls}">{esc(st_txt)}</span>
     {sc}</div>
  <div class="dash-t">{esc(r['title'])}</div>
  <div class="dash-m">رفعها {esc(r['uploader'] or '—')} ·
     {esc((r['updated_at'] or '')[:10])}</div>
  <div class="dash-b"><span class="cnt">{notes(n)} · {vote}</span>
     <span class="go">فتح ←</span></div></a>""")

    add = ('<a class="addbtn" href="/new">+ رفع لوحة للمراجعة</a>'
           if can_add else "")

    # **ولا رقاقةَ ناشرٍ إن كان واحداً**، فهي حينئذٍ لا تفرّق شيئاً.
    up_chips = ""
    if len(ups) > 1:
        mine = user["name"]
        up_chips = ('<span class="sep"></span>' + "".join(
            f'<button class="chip up-chip" data-uploader="{esc(k)}">'
            f'{esc("لوحاتي" if k == mine else k)} <i>{v}</i></button>'
            for k, v in sorted(ups.items(), key=lambda t: -t[1])))

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
               for k, v in sorted(sts.items()))}
      {up_chips}</div>
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
                (SELECT COUNT(*) FROM comments k WHERE k.dashboard_id=d.id
                   AND k.parent_id IS NULL) n_comments,
                (SELECT COUNT(*) FROM comments k WHERE k.dashboard_id=d.id
                   AND """ + open_sql() + """) n_open,
                (SELECT COUNT(*) FROM verdicts v WHERE v.dashboard_id=d.id) n_v,
                (SELECT COUNT(*) FROM verdicts v WHERE v.dashboard_id=d.id
                   AND v.passed=1) n_pass,
                (SELECT ROUND(AVG(v.score),1) FROM verdicts v
                   WHERE v.dashboard_id=d.id AND v.score IS NOT NULL) avg_score
              FROM dashboards d LEFT JOIN users us ON us.id=d.uploader_id
              ORDER BY n_comments ASC, d.updated_at DESC""").fetchall()
            c.close()
            tags = (reviewer_tags(u["id"]) if u["role"] == "reviewer"
                    else {})
            return self.send(list_page(u, rows, tags))

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
            ctx = {"slug": slug, "title": d["title"],
                   "status": d["status"] or "review",
                   "me": {"id": u["id"], "name": u["name"], "role": u["role"],
                          "owner": d["uploader_id"] == u["id"]}}
            inject = (f'<link rel="stylesheet" href="/static/review.css">'
                      f'<script>window.RV={json.dumps(ctx, ensure_ascii=False)};'
                      f'</script>'
                      f'<script src="/static/review.js" defer></script>')
            page = page.replace("</head>", inject + "</head>", 1)
            return self.send(page)

        # **ويُقرأ التقدّمُ بالأقسام لا بالنِّسَب وحدَها.** «بلغ 58 من 72»
        # يدعوك تسأل، و«80%» يوهمك أنّك عرفت. فتُرسل الأعدادُ خاماً وتُعرض
        # كما هي، والنسبةُ معها لا بدلاً منها.
        if path == "/api/progress":
            slug = q.get("d", [""])[0]
            c = db()
            d = c.execute("SELECT id, slug FROM dashboards WHERE slug=?",
                          (slug,)).fetchone()
            if not d:
                c.close()
                return self.json({"error": "no dashboard"}, 404)
            try:
                with open(os.path.join(DASH_DIR, slug + ".html"),
                          encoding="utf-8") as fh:
                    total = len(set(re.findall(r'data-sec="([^"]+)"',
                                               fh.read())))
            except OSError:
                total = 0
            # **وإخفاءُ الزرّ ليس منعاً.** كان هذا المسارُ بلا فحصِ دورٍ
            # إطلاقاً، فأيُّ مراجعٍ يعرفه يقرأ به زمنَ زملائه وأينَ بلغوا.
            # **وذاك غيرُ ما طُلب**: الرافعُ يتابع المراجعين، والمراجعُ لا
            # يتابع أقرانه.
            #
            # فالرافعُ يرى الجميع، وكلُّ من عداه يرى صفَّه وحدَه — فيبقى
            # سطرُ المراجع عن نفسه عاملاً بلا أن ينكشف غيرُه.
            mine_only = not owns(c, u["id"], did=d["id"])
            rows = c.execute("""
              SELECT us.id, us.name, us.role,
                     v.opens, v.first_at, v.last_at, v.manual,
                     (SELECT COUNT(*) FROM section_views sv
                       WHERE sv.dashboard_id=? AND sv.user_id=us.id) AS seen,
                     (SELECT COALESCE(SUM(ms),0) FROM section_views sv
                       WHERE sv.dashboard_id=? AND sv.user_id=us.id) AS ms
              FROM users us
              LEFT JOIN views v ON v.dashboard_id=? AND v.user_id=us.id
              WHERE us.role='reviewer' AND (?=0 OR us.id=?)
              ORDER BY us.name""",
                            (d["id"], d["id"], d["id"],
                             1 if mine_only else 0, u["id"])).fetchall()
            # وتفصيلُ الأقسام لمن طلبه
            det = {}
            if q.get("who") and not mine_only:
                for r in c.execute("""SELECT sec_key, ms, seen_at
                                      FROM section_views
                                      WHERE dashboard_id=? AND user_id=?""",
                                   (d["id"], int(q["who"][0]))):
                    det[r["sec_key"]] = {"ms": r["ms"], "at": r["seen_at"]}
            c.close()
            return self.json({"total": total, "who": [dict(r) for r in rows],
                              "detail": det})

        # **ولا تُرسَل رسالةٌ لم تُقرأ.** هذا المسارُ يُظهر نصَّ ما سيصل
        # كلَّ مراجعٍ الأحدَ القادم، بلا إرسال — فيُقرأ قبل أن يُفعَّل.
        if path == "/digest":
            if u["role"] != "uploader":
                return self.send("403", 403)
            import digest
            emails = EMAILS
            base = os.environ.get("BASE_URL", "")
            rows, msgs, skipped, _ = digest.run_once(
                DB_PATH, DASH_DIR, emails, base or "/", do_send=False)
            out = [f"<h2>معاينةُ الرسالة الأسبوعية</h2>"
                   f"<p>الموعد القادم: <b>{digest.next_run():%Y-%m-%d %H:%M}</b> "
                   f"بتوقيت بغداد · الإرسال "
                   f"<b>{'مفعَّل' if digest.ENABLED else 'معطَّل'}</b> · "
                   f"المفتاح "
                   f"<b>{'موجود' if os.environ.get('RESEND_API_KEY') else 'غائب'}"
                   f"</b></p>"]
            if skipped:
                out.append("<p><b>بلا بريدٍ في السجلّ فلن تصلهم:</b> "
                           + esc(" · ".join(skipped)) + "</p>")
            for w in rows:
                out.append(f"<hr><p><b>إلى:</b> {esc(w['name'])} "
                           f"&lt;{esc(emails.get(w['name'], 'لا بريد'))}&gt;<br>"
                           f"<b>الموضوع:</b> {esc(digest.subject(w))}</p>"
                           f"<pre style='white-space:pre-wrap;font:14px/1.9 "
                           f'"Segoe UI",sans-serif'
                           f"'>{esc(digest.body(w, base or '/'))}</pre>")
            if not rows:
                out.append("<p>لا شيء يُرسَل — كلُّ مراجعٍ أنهى ما عليه.</p>")
            return self.send(SHELL.format(title="معاينة الرسالة",
                                          body="<div class=\"wrap\">"
                                               + "".join(out) + "</div>"))

        if path == "/api/verdicts":
            return self.json(verdict_state(q.get("d", [""])[0]))

        if path == "/api/comments":
            slug = q.get("d", [""])[0]
            c = db()
            # **والحكمُ يُحسب هنا لا في المتصفّح**، فلا يفترق تعريفان.
            rows = c.execute("""
              SELECT k.*, us.name AS author, us.role AS author_role,
                     CASE WHEN """ + open_sql() + """ THEN 1 ELSE 0 END
                       AS is_open
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

        # ── تحديثُ لوحةٍ قائمة ───────────────────────────────────────────
        # **ولماذا مسارٌ غيرُ `/add`.** كان التحديثُ يمرّ بنموذج الرفع: يعود
        # الرافعُ إلى `/new` ويكتب العنوانَ **حرفاً بحرف** ليخرج الـslug
        # نفسُه، ويختار التصنيفَ من جديد. وخطأُ حرفٍ واحدٍ في العنوان يصنع
        # لوحةً ثانيةً بدل أن يحدّث الأولى، **وتبقى التعليقاتُ على الأولى**
        # فتبدو ضائعة. فالتحديثُ يأخذ الـslug صريحاً ولا يشتقّه من عنوان.
        #
        # **والتعليقاتُ لا تُمسّ.** هي مرتبطةٌ بـ`dashboard_id` والصفُّ يُحدَّث
        # ولا يُستبدل، فتبقى كما هي. وما قد يفقد موضعَه هو مرساتُها داخل
        # الصفحة (`sec_key` مشتقٌّ من نوع القسم وعنوانه)، وذلك يعرضه
        # `review.js` صراحةً «القسم لم يعد موجوداً» ولا يخمّن بديلاً.
        if path == "/replace":
            if u["role"] != "uploader":
                return self.json({"error": "forbidden"}, 403)
            ctype = self.headers.get("Content-Type") or ""
            n = int(self.headers.get("Content-Length") or 0)
            if "multipart/form-data" not in ctype:
                return self.json({"error": "أرسل الملفّ من النموذج."}, 400)
            bnd = re.search(r"boundary=([^;]+)", ctype)
            raw = parse_multipart(self.rfile.read(n),
                                  bnd.group(1).strip('"').encode())
            fields = {k: (v[1].decode("utf-8", "replace") if v[0] is None else v)
                      for k, v in raw.items()}
            slug = (fields.get("slug") or "").strip()
            up = fields.get("file")
            c = db()
            d = c.execute("SELECT * FROM dashboards WHERE slug=?",
                          (slug,)).fetchone()
            if d and d["uploader_id"] != u["id"]:
                c.close()
                return self.json({"error": "اللوحةُ ليست لك"}, 403)
            try:
                if not d:
                    raise RuntimeError("لا لوحةَ بهذا المعرّف.")
                if not (isinstance(up, tuple) and up[0] and up[1]):
                    raise RuntimeError("لم يصل ملفّ.")
                if b"<" not in up[1][:2000]:
                    raise RuntimeError("الملفّ لا يبدو صفحة HTML.")
                page, moved = split_libs(up[1].decode("utf-8", "replace"))
            except Exception as e:
                c.close()
                return self.json({"error": str(e)[-300:]}, 400)

            # **ولا يُكتب فوق الملفّ إلّا بعد أن يُقرأ الجديدُ كلُّه.** كتابةٌ
            # تفشل في منتصفها تترك اللوحةَ بلا صفحة، والتعليقاتُ عليها.
            with open(os.path.join(DASH_DIR, slug + ".html"), "w",
                      encoding="utf-8") as f:
                f.write(page)

            # الحالةُ تعود إلى المراجعة والتقييماتُ تُمسح: **المُجاز هو النسخة
            # التي رآها المراجع لا أيّ نسخةٍ بعدها** — وهي قاعدةُ `/add` نفسُها.
            c.execute("""UPDATE dashboards SET status='review', updated_at=?,
                          decided_by=NULL, decided_at=NULL WHERE id=?""",
                      (now(), d["id"]))
            c.execute("DELETE FROM verdicts WHERE dashboard_id=?", (d["id"],))

            # وكم تعليقاً فقد مرساتَه؟ يُحسب بمقارنة `sec_key` بما في الصفحة
            # الجديدة، **ويُقال للرافع صراحةً** بدل أن يكتشفه بالتصفّح.
            keys = set(re.findall(r'data-sec="([^"]+)"', page))
            rows = c.execute("""SELECT sec_key, COUNT(*) n FROM comments
                                WHERE dashboard_id=? GROUP BY sec_key""",
                             (d["id"],)).fetchall()
            kept = sum(r["n"] for r in rows if r["sec_key"] in keys)
            lost = sum(r["n"] for r in rows if r["sec_key"] not in keys)
            c.commit()
            c.close()
            return self.json({"ok": True, "slug": slug,
                              "kept": kept, "lost": lost, "libs": moved})

        # ── المتابعة: نبضةٌ من الصفحة ────────────────────────────────────
        # **دفعاتٌ لا نبضةٌ لكلّ ثانية.** يجمع العميلُ ما ظهر وما بقي ثمّ
        # يرسل كلَّ خمسَ عشرةَ ثانيةً وعند مغادرة الصفحة، فلا يُثقل الخادمَ
        # ولا يُفقد ما جُمع إن أُغلق التبويبُ فجأة.
        if path == "/api/seen":
            b = self.body_json() or {}
            slug = (b.get("slug") or "").strip()
            c = db()
            d = c.execute("SELECT id FROM dashboards WHERE slug=?",
                          (slug,)).fetchone()
            if not d:
                c.close()
                return self.json({"error": "no dashboard"}, 404)
            did, uid, t = d["id"], u["id"], now()

            # `open=1` مرّةً واحدةً عند تحميل الصفحة لا مع كلّ دفعة
            if b.get("open"):
                c.execute("""INSERT INTO views (dashboard_id,user_id,opens,
                              first_at,last_at) VALUES (?,?,1,?,?)
                             ON CONFLICT(dashboard_id,user_id) DO UPDATE SET
                              opens=opens+1, last_at=excluded.last_at""",
                          (did, uid, t, t))
            else:
                c.execute("""UPDATE views SET last_at=? WHERE dashboard_id=?
                             AND user_id=?""", (t, did, uid))

            # **والسقفُ حارسٌ لا تجميل.** ساعةٌ في قسمٍ واحدٍ في دفعةٍ واحدة
            # يعني ساعةً لم تُقرأ فيها الصفحة — أو عميلاً عابثاً. فيُقصّ.
            CAP = 5 * 60 * 1000
            for k, ms in (b.get("secs") or {}).items():
                if not isinstance(k, str) or not isinstance(ms, (int, float)):
                    continue
                ms = max(0, min(int(ms), CAP))
                c.execute("""INSERT INTO section_views (dashboard_id,user_id,
                              sec_key,seen_at,ms) VALUES (?,?,?,?,?)
                             ON CONFLICT(dashboard_id,user_id,sec_key)
                             DO UPDATE SET ms = ms + excluded.ms""",
                          (did, uid, k[:80], t, ms))
            c.commit()
            c.close()
            return self.json({"ok": True})

        # ── جسرُ المراسي بعد تغيير طريقة التوليد ────────────────────────
        # **ولا يُنقل تعليقٌ إلّا بمقابلٍ محسوب.** الجسرُ يأتي من الباني:
        # لكلِّ قسمٍ قائمٍ مفتاحُه القديمُ ومعرِّفُه الجديد، فالنقلُ يقينٌ.
        # وما لا مقابلَ له يبقى يتيماً معلَناً — والرميُ على قسمٍ يشبهه
        # أسوأُ من الفقد، لأنّه ينسب كلاماً إلى غير موضعه بلا أن يظهر.
        if path == "/api/remap":
            if u["role"] != "uploader":
                return self.json({"error": "forbidden"}, 403)
            body = self.body_json() or {}
            slug = (body.get("slug") or "").strip()
            mp = body.get("map")
            if not isinstance(mp, dict) or not mp or not slug:
                return self.json({"error": "جسرٌ فارغٌ أو غيرُ سليم"}, 400)
            c = db()
            d = c.execute("SELECT id, uploader_id FROM dashboards "
                          "WHERE slug=?", (slug,)).fetchone()
            if not d:
                c.close()
                return self.json({"error": "لا لوحةَ بهذا المعرّف."}, 404)
            if d["uploader_id"] != u["id"]:
                c.close()
                return self.json({"error": "اللوحةُ ليست لك"}, 403)
            did = d["id"]
            moved, untouched = 0, 0
            for old, new in mp.items():
                if not (isinstance(old, str) and isinstance(new, str)):
                    continue
                r = c.execute("""UPDATE comments SET sec_key=?
                                 WHERE dashboard_id=? AND sec_key=?""",
                              (new, did, old))
                moved += r.rowcount
            # واليتيمُ من لا مِقبضَ له في الصفحة — لا من ليس في هذا الجسر
            try:
                with open(os.path.join(DASH_DIR, slug + ".html"),
                          encoding="utf-8") as fh:
                    page_keys = set(re.findall(r'data-sec="([^"]+)"', fh.read()))
            except OSError:
                page_keys = set()
            for r in c.execute("""SELECT sec_key, COUNT(*) n FROM comments
                                  WHERE dashboard_id=? GROUP BY sec_key""",
                               (did,)):
                if page_keys and r["sec_key"] not in page_keys:
                    untouched += r["n"]
            c.commit()
            c.close()
            return self.json({"ok": True, "moved": moved,
                              "orphans": untouched})

        if path == "/api/delete":
            if u["role"] != "uploader":
                return self.json({"error": "forbidden"}, 403)
            b = self.body_json() or {}
            slug = (b.get("slug") or "").strip()
            c = db()
            d = c.execute("SELECT * FROM dashboards WHERE slug=?",
                          (slug,)).fetchone()
            if not d:
                c.close()
                return self.json({"error": "لا لوحةَ بهذا المعرّف."}, 404)
            if d["uploader_id"] != u["id"]:
                c.close()
                return self.json({"error": "اللوحةُ ليست لك"}, 403)
            if (b.get("confirm") or "").strip() != (d["title"] or "").strip():
                c.close()
                return self.json({"error": "العنوانُ لا يطابق."}, 400)

            did = d["id"]
            n = {t: c.execute(f"SELECT COUNT(*) n FROM {t} WHERE dashboard_id=?",
                              (did,)).fetchone()["n"]
                 for t in ("comments", "verdicts", "views", "section_views")}
            for t in ("section_views", "views", "verdicts", "comments"):
                c.execute(f"DELETE FROM {t} WHERE dashboard_id=?", (did,))
            c.execute("DELETE FROM dashboards WHERE id=?", (did,))
            c.commit()
            c.close()
            # **والملفُّ يُحذف بعد السجلّ.** لو سقط الحذفُ بينهما بقي ملفٌّ
            # يتيمٌ لا يضرّ، وأمّا العكسُ فصفحةٌ في القائمة بلا ملفّ.
            try:
                os.remove(os.path.join(DASH_DIR, slug + ".html"))
            except OSError:
                pass
            return self.json({"ok": True, "title": d["title"], **n})

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

        # ── تحريرُ تعليقٍ وحذفُه ─────────────────────────────────────────
        # **وصاحبُ الكلام وحدَه يملكه.** لا يُعدّل تعليقُ أحدٍ ولا يُحذف إلّا
        # بيده — ولو كان الرافعُ صاحبَ اللوحة. وحذفُ اعتراضٍ لا يعجبك أسوأُ
        # من الاعتراض نفسِه.
        if re.fullmatch(r"/api/comments/\d+", path):
            cid = int(path.rsplit("/", 1)[1])
            b = self.body_json() or {}
            c = db()
            row = c.execute("SELECT * FROM comments WHERE id=?",
                            (cid,)).fetchone()
            if not row:
                c.close()
                return self.json({"error": "لا تعليقَ بهذا الرقم"}, 404)
            if row["author_id"] != u["id"]:
                c.close()
                return self.json({"error": "التعليقُ ليس لك"}, 403)

            if b.get("delete"):
                # **والردودُ تذهب معه.** ردٌّ بلا ما يردّ عليه لغزٌ لا كلام،
                # فيُقال عددُها قبل الحذف ويُحذف الجميعُ معاً.
                kids = c.execute("SELECT COUNT(*) n FROM comments "
                                 "WHERE parent_id=?", (cid,)).fetchone()["n"]
                c.execute("DELETE FROM comments WHERE parent_id=?", (cid,))
                c.execute("DELETE FROM comments WHERE id=?", (cid,))
                c.commit()
                c.close()
                return self.json({"ok": True, "deleted": 1 + kids})

            body = (b.get("body") or "").strip()
            if not body:
                c.close()
                return self.json({"error": "النصُّ فارغ"}, 400)
            c.execute("UPDATE comments SET body=?, edited_at=? WHERE id=?",
                      (body[:4000], now(), cid))
            c.commit()
            c.close()
            return self.json({"ok": True, "body": body[:4000]})

        if path.startswith("/api/comments/") and path.endswith("/resolve"):
            cid = path.split("/")[3]
            c = db()
            _d = c.execute("""SELECT d.uploader_id FROM dashboards d
                              JOIN comments k ON k.dashboard_id=d.id
                              WHERE k.id=?""", (cid,)).fetchone()
            if not _d or _d["uploader_id"] != u["id"]:
                c.close()
                return self.json({"error": "اللوحةُ ليست لك"}, 403)
            c.execute("""UPDATE comments
                         SET resolved=1-resolved,
                             resolved_at=CASE WHEN resolved=0 THEN ? END,
                             resolved_by=CASE WHEN resolved=0 THEN ? END
                         WHERE id=?""", (now(), u["id"], cid))
            c.commit()
            r = c.execute("SELECT resolved FROM comments WHERE id=?", (cid,)).fetchone()
            c.close()
            return self.json({"resolved": r["resolved"] if r else 0})

        self.json({"error": "404"}, 404)


def start_digest():
    """يشغّل جدولَ الرسالة الأسبوعية في خيطٍ خلفيّ.

    **ولا يقوم إن كان الإرسالُ معطَّلاً**، فلا خيطَ يدور بلا عمل. وتشغيلُه
    مشروطٌ بمفتاحٍ صريح: نشرُ الخادم لا يبدأ مراسلةَ أحد.
    """
    import digest
    if not digest.ENABLED:
        print("الرسالةُ الأسبوعية: معطَّلة (MAIL_ENABLED ليست 1)")
        return
    if not os.environ.get("RESEND_API_KEY"):
        print("الرسالةُ الأسبوعية: مفعَّلةٌ بلا RESEND_API_KEY — لن تُرسَل.")
        return
    base = os.environ.get("BASE_URL", "")
    t = threading.Thread(target=digest.loop, daemon=True,
                         args=(DB_PATH, DASH_DIR, lambda: EMAILS, base))
    t.start()
    print(f"الرسالةُ الأسبوعية: القادمة {digest.next_run():%Y-%m-%d %H:%M} "
          f"بتوقيت بغداد · {len(EMAILS)} عنواناً في السجلّ")


if __name__ == "__main__":
    init()
    start_digest()
    print(f"يعمل على {HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
