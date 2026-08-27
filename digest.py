"""الرسالة الأسبوعية للمراجعين — أحدَ كلِّ أسبوعٍ عاشرةً بتوقيت بغداد.

**وثلاثةُ أخبارٍ لا واحد**: ما لم يُفتح أصلاً، وما مُرَّ عليه سريعاً، وما
بُلغ بعضُه ولم يُكمَل. والفرقُ بينها هو الرسالةُ كلُّها: من لم يفتح لم يبدأ،
ومن مرّ سريعاً بدأ ولم يقرأ، ومن بلغ نصفَها انقطع في الطريق.

**ولا تُرسل ما لم تُفتَح صراحةً.** `MAIL_ENABLED` مطفأةٌ افتراضاً، فنشرُ
الخادم لا يبدأ مراسلةَ ستّةِ أشخاصٍ قبل أن يُقرأ نصُّ الرسالة.

  python -X utf8 digest.py            # يطبع ما كان سيُرسل، ولا يرسل
  python -X utf8 digest.py --send     # يرسل الآن (يحتاج MAIL_ENABLED=1)
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# **والمفتاحُ يُقرأ من ملفٍّ لا يُرفع.** `.env` في `.gitignore`، فيبقى
# المفتاحُ على القرص ولا يدخل المستودعَ ولا محادثةً ولا سجلّاً.
def _load_env():
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(f):
        return
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

BAGHDAD = timezone(timedelta(hours=3))     # بلا توقيتٍ صيفيّ منذ 2015

# ── العتبات ────────────────────────────────────────────────────────────────
# **والعتبةُ تتبع طولَ الداشبورد لا تكون رقماً واحداً للجميع.** دقيقةٌ على
# عشرين قسماً غيرُ دقيقةٍ على اثنين وسبعين. فتُحسب: ثلاثُ ثوانٍ لكلِّ قسم،
# وهي أدنى ما يُلمَح فيه رسمٌ أو بطاقةٌ لا ما يُقرأ فيه.
#
# **وأرضيّةٌ تحت الحساب** كي لا يمرّ داشبوردٌ قصيرٌ بلمحةٍ: خمسُ ثوانٍ
# لثلاثة أقسامٍ ليست قراءةً مهما قصُر.
SEC_MS = int(os.environ.get("DIGEST_MS_PER_SECTION", 3_000))
FLOOR_MS = int(os.environ.get("DIGEST_FLOOR_MS", 30_000))


def min_ms(total):
    """أدنى زمنٍ يُعدّ قراءةً لداشبوردٍ فيه `total` قسماً."""
    return max(FLOOR_MS, SEC_MS * (total or 0))
SEND_HOUR = int(os.environ.get("DIGEST_HOUR", 10))
SEND_DOW = int(os.environ.get("DIGEST_DOW", 6))     # 6 = الأحد
ENABLED = os.environ.get("MAIL_ENABLED", "") == "1"


def _sections(dash_dir, slug):
    import re
    try:
        with open(os.path.join(dash_dir, slug + ".html"), encoding="utf-8") as f:
            return len(set(re.findall(r'data-sec="([^"]+)"', f.read())))
    except OSError:
        return 0


def collect(db_path, dash_dir):
    """يردّ لكلِّ مراجعٍ ثلاثَ قوائمَ متمايزة، ومن لا شيءَ عليه لا يُدرَج.

    **والقوائمُ لا تتقاطع.** لوحةٌ واحدةٌ تقع في خانةٍ واحدة، بترتيب:
    لم تُفتح ← مُرَّ عليها سريعاً ← بُلغ بعضُها. فمن أعطاها عشرين ثانيةً
    وبلغ آخرَها لا يُقال له «أكمِلها» بل «لم تقرأها».
    """
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    dashes = c.execute("""SELECT id, slug, title FROM dashboards
                          WHERE status='review' ORDER BY updated_at""").fetchall()
    revs = c.execute("""SELECT id, name,
                          COALESCE(gender,'m') AS gender FROM users
                        WHERE role='reviewer' ORDER BY name""").fetchall()
    total = {d["id"]: _sections(dash_dir, d["slug"]) for d in dashes}

    out = []
    for r in revs:
        never, quick, partial = [], [], []
        for d in dashes:
            v = c.execute("""SELECT opens FROM views
                             WHERE dashboard_id=? AND user_id=?""",
                          (d["id"], r["id"])).fetchone()
            if not v or not v["opens"]:
                never.append({"title": d["title"], "slug": d["slug"]})
                continue
            s = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(ms),0) ms
                             FROM section_views
                             WHERE dashboard_id=? AND user_id=?""",
                          (d["id"], r["id"])).fetchone()
            seen, ms, tot = s["n"], s["ms"], total[d["id"]]
            item = {"title": d["title"], "slug": d["slug"],
                    "seen": seen, "total": tot, "ms": ms}
            if ms < min_ms(tot):
                quick.append(item)
            elif tot and seen < tot:
                partial.append(item)
        # ردودٌ على تعليقاته جاءت بعد آخر مرّةٍ فتح فيها الداشبورد
        unseen = []
        for d in dashes:
            v = c.execute("""SELECT last_at FROM views
                             WHERE dashboard_id=? AND user_id=?""",
                          (d["id"], r["id"])).fetchone()
            if not v or not v["last_at"]:
                continue
            n_new = c.execute("""
              SELECT COUNT(*) n FROM comments k
              JOIN comments p ON p.id = k.parent_id
              WHERE k.dashboard_id=? AND p.author_id=? AND k.author_id<>?
                AND k.created_at > ?""",
              (d["id"], r["id"], r["id"], v["last_at"])).fetchone()["n"]
            n_res = c.execute("""
              SELECT COUNT(*) n FROM comments
              WHERE dashboard_id=? AND author_id=? AND resolved=1
                AND resolved_at IS NOT NULL AND resolved_at > ?
                AND resolved_by IS NOT NULL AND resolved_by <> ?""",
              (d["id"], r["id"], v["last_at"], r["id"])).fetchone()["n"]
            if n_new + n_res:
                unseen.append({"title": d["title"], "slug": d["slug"],
                               "n": n_new + n_res})

        if never or quick or partial or unseen:
            out.append({"id": r["id"], "name": r["name"],
                        "fem": r["gender"] == "f", "never": never,
                        "quick": quick, "partial": partial,
                        "unseen": unseen})
    c.close()
    return out


def _mins(ms):
    s = round(ms / 1000)
    if s < 60:
        return f"{s} ثانية"
    m = s // 60
    return f"{m} دقيقة" + (f" و{s % 60} ثانية" if s % 60 else "")


def _count(n):
    """صيغةُ العدد العربيّة لكلمةٍ مذكّرة. **و«2 داشبورد» ليست عربية.**"""
    if n == 1:
        return "داشبورد واحد"
    if n == 2:
        return "داشبوردان"
    return f"{n} داشبوردات" if 3 <= n <= 10 else f"{n} داشبورداً"


def _pro(n, one, two, many):
    """الضميرُ العائد: ـه · ـهما · ـها."""
    return one if n == 1 else two if n == 2 else many


def _v(fem, m, f):
    """صيغةُ الفعل بحسب المخاطَب."""
    return f if fem else m


def _heads(who):
    """عناوينُ الكتل، مصرَّفةً للمخاطَب وللعدد."""
    fem = who.get("fem")
    out = {}
    if who.get("unseen"):
        k = sum(d["n"] for d in who["unseen"])
        out["unseen"] = (f"{k} ردٌّ لم {_v(fem, 'ترَه', 'ترَيه')}"
                         if k == 1 else
                         f"{k} ردّاً لم {_v(fem, 'ترَها', 'ترَيها')}")
        out["unseen"] += " على تعليقاتك:"
    if who["never"]:
        n = len(who["never"])
        out["never"] = (f"{_count(n)} لم "
                        f"{_v(fem, 'تفتح', 'تفتحي')}"
                        f"{_pro(n, 'ه', 'هما', 'ها')} بعد:")
    if who["quick"]:
        n = len(who["quick"])
        out["quick"] = (f"{_count(n)} لم {_v(fem, 'تمكث', 'تمكثي')} "
                        f"في{_pro(n, 'ه', 'هما', 'ها')} إلّا قليلاً:")
    if who["partial"]:
        n = len(who["partial"])
        p = _pro(n, "ه", "هما", "ها")
        out["partial"] = (f"{_count(n)} {_v(fem, 'بلغتَ', 'بلغتِ')} "
                          f"بعض{p} ولم {_v(fem, 'تُكمل', 'تُكملي')}{p}:")
    return out


def body(who, base_url):
    """نصُّ الرسالة. **بلا لومٍ وبلا تلطيفٍ يُخفي الرقم.**"""
    L = [f"أهلاً {who['name']}،", "",
         "هذه رسالة تلقائية بشأن مراجعة لوحات اي داتا.", ""]
    H = _heads(who)
    if who.get("unseen"):
        L += [H["unseen"]] + [f"  · {d['title']} — {d['n']}"
                              for d in who["unseen"]] + [""]
    if who["never"]:
        L += [H["never"]] + [f"  · {d['title']}" for d in who["never"]] + [""]
    if who["quick"]:
        L += [H["quick"]] + [f"  · {d['title']} — {_mins(d['ms'])}"
                             for d in who["quick"]] + [""]
    if who["partial"]:
        L += [H["partial"]] + [f"  · {d['title']} — {d['seen']} قسماً "
                               f"من {d['total']}" for d in who["partial"]] + [""]
    L += [f"الداشبوردات هنا: {base_url}"]
    return "\n".join(L)


# ── نسخةُ HTML: العناوينُ روابطُ تُنقر ────────────────────────────────────
# **وتُرسَل النسختان معاً.** النصّيّةُ تبقى لمن يعطّل الـHTML أو يقرأ في
# عميلٍ لا يعرضه، فلا يصل أحداً بريدٌ فارغ. و`text` هي نفسُها بلا نقصان.
def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _link(d, base):
    """رابطُ الداشبورد. **والمعرّفُ عربيٌّ فيُرمَّز**، وإلّا كُسر الرابط."""
    href = f"{base.rstrip('/')}/d/{urllib.parse.quote(d.get('slug') or '')}"
    return f'<a href="{_esc(href)}" style="color:#0e7c86">{_esc(d["title"])}</a>'


def body_html(who, base_url):
    P = ('<p style="margin:0 0 14px">', "</p>")
    HD = ('<p style="margin:18px 0 6px;font-weight:700">', "</p>")
    UL = ('<ul style="margin:0;padding-inline-start:20px">', "</ul>")
    _FONT = "font:15px/1.9 Tajawal,Arial,sans-serif;color:#1a284a"
    out = [f'<div dir="rtl" style="{_FONT};max-width:620px">']
    out += [P[0] + f"أهلاً {_esc(who['name'])}،" + P[1]]
    out += [P[0] + "هذه رسالة تلقائية بشأن مراجعة لوحات اي داتا." + P[1]]

    def block(items, head, tail=lambda d: ""):
        out.append(HD[0] + head + HD[1])
        out.append(UL[0])
        for d in items:
            out.append("<li>" + _link(d, base_url) + tail(d) + "</li>")
        out.append(UL[1])

    H = _heads(who)
    if who.get("unseen"):
        block(who["unseen"], H["unseen"],
              lambda d: f' <span style="color:#6b7280">— {d["n"]}</span>')
    if who["never"]:
        block(who["never"], H["never"])
    if who["quick"]:
        block(who["quick"], H["quick"],
              lambda d: f' <span style="color:#6b7280">— {_mins(d["ms"])}</span>')
    if who["partial"]:
        block(who["partial"], H["partial"],
              lambda d: f' <span style="color:#6b7280">— '
                        f'{d["seen"]} قسماً من {d["total"]}</span>')

    out.append(f'<p style="margin:22px 0 0">'
               f'<a href="{_esc(base_url)}" style="color:#0e7c86">'
               f'كلُّ الداشبوردات</a></p>')
    out.append("</div>")
    return "".join(out)


def subject(who):
    n = len(who["never"]) + len(who["quick"]) + len(who["partial"])
    r = sum(d["n"] for d in who.get("unseen") or [])
    if r and not n:
        return ("مراجعة الداشبوردات: ردٌّ لم ترَه" if r == 1
                else f"مراجعة الداشبوردات: {r} ردّاً لم ترَها")
    return f"مراجعة الداشبوردات: {_count(n)} بانتظارك"


# ── الإرسال: Resend، كما في موقع كابيتا ────────────────────────────────────
# **ولا مكتبةَ جديدة.** موقعُ الشركة يرسل بـResend عبر حزمة `resend` في
# Node، وهي غلافٌ على واجهةٍ HTTP بسيطة. فتُستدعى الواجهةُ مباشرةً من
# المكتبة القياسية، فيبقى هذا الخادمُ بلا اعتماديّاتٍ كما هو.
#
# **والمفتاحُ والمُرسِلُ هما نفسُهما**: `RESEND_API_KEY` و`MAIL_FROM`، ونطاقُ
# `kapita.iq` موثَّقٌ عندهم أصلاً — فلا إعدادَ نطاقٍ جديد.
API = "https://api.resend.com/emails"
FROM = os.environ.get("MAIL_FROM", "noreply@kapita.iq")
FROM_NAME = os.environ.get("MAIL_FROM_NAME", "مراجعة الداشبوردات")


def send(msgs):
    """يرسل واحدةً واحدة. **والفشلُ يُحصى ولا يُبتلع** — رسالةٌ لم تصل خبرٌ."""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not ENABLED:
        return 0, "معطَّل (MAIL_ENABLED ليست 1)"
    if not key:
        return 0, "بلا RESEND_API_KEY"
    sent, failed = 0, []
    for to, subj, text, html in msgs:
        payload = json.dumps({
            "from": f"{FROM_NAME} <{FROM}>", "to": [to],
            "subject": subj, "text": text, "html": html,
        }).encode("utf-8")
        req = urllib.request.Request(
            API, data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": "kapita-idata-review/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            sent += 1
        except urllib.error.HTTPError as e:
            failed.append(f"{to}: {e.code} {e.read()[:120].decode(errors='replace')}")
        except Exception as e:
            failed.append(f"{to}: {e}")
    note = "تمّ" if not failed else "أخفق: " + " · ".join(failed)
    return sent, note


def next_run(now=None):
    """أقربُ أحدٍ عاشرةً بتوقيت بغداد بعد `now`."""
    now = now or datetime.now(BAGHDAD)
    d = (SEND_DOW - now.weekday()) % 7
    t = (now + timedelta(days=d)).replace(hour=SEND_HOUR, minute=0,
                                          second=0, microsecond=0)
    return t + timedelta(days=7) if t <= now else t


def run_once(db_path, dash_dir, emails, base_url, do_send=False):
    rows = collect(db_path, dash_dir)
    msgs, skipped = [], []
    for w in rows:
        addr = emails.get(w["name"])
        (msgs.append((addr, subject(w), body(w, base_url),
                      body_html(w, base_url))) if addr
         else skipped.append(w["name"]))
    if do_send and msgs:
        n, note = send(msgs)
        return rows, msgs, skipped, (n, note)
    return rows, msgs, skipped, (0, "معاينة")


def loop(db_path, dash_dir, emails_fn, base_url):
    """خيطٌ في الخلفية يستيقظ للموعد. **ويتحقّق من الساعة لا من النوم**،
    فالنومُ الطويل يزحف والخادمُ يُعاد تشغيلُه فيضيع الموعد."""
    while True:
        nxt = next_run()
        while True:
            wait = (nxt - datetime.now(BAGHDAD)).total_seconds()
            if wait <= 0:
                break
            time.sleep(min(wait, 900))      # ربعُ ساعةٍ أقصى نومة
        try:
            run_once(db_path, dash_dir, emails_fn(), base_url, do_send=True)
        except Exception as e:
            print(f"[digest] فشل الإرسال: {e}")
        time.sleep(120)                      # كي لا يتكرّر في الدقيقة نفسها


if __name__ == "__main__":
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    DB = os.path.join(here, "data", "review.db")
    DD = os.path.join(here, "data", "dashboards")
    try:
        team = json.load(open(os.path.join(here, "config", "team.json"),
                              encoding="utf-8"))
    except OSError:
        team = []
    emails = {r["name"]: r["email"] for r in team if r.get("email")}
    url = os.environ.get("BASE_URL", "https://idata-review.up.railway.app")
    # **والتجربةُ تذهب إلى واحدٍ لا إلى الفريق.** `--to عنوان` يحوّل كلَّ
    # الرسائل إلى صندوقٍ واحد، فتُختبر الواجهةُ والصياغةُ والوصول بلا أن
    # يتلقّى أربعةُ زملاءَ رسالةً لم يُقصدوا بها.
    to = None
    if "--to" in sys.argv:
        to = sys.argv[sys.argv.index("--to") + 1]
        emails = {k: to for k in emails}
        print("⚠ وضعُ التجربة: كلُّ الرسائل إلى " + to)
    rows, msgs, skipped, res = run_once(DB, DD, emails, url,
                                        do_send="--send" in sys.argv)
    print(f"الموعد القادم: {next_run():%Y-%m-%d %H:%M} بتوقيت بغداد")
    print(f"عتبةُ «وقتٌ قليل»: {SEC_MS/1000:.0f} ثوانٍ للقسم "
          f"(أرضيّةٌ {FLOOR_MS/1000:.0f}) · "
          f"الإرسال {'مفعَّل' if ENABLED else 'معطَّل'}\n")
    for w in rows:
        print("═" * 62)
        print(f"إلى: {w['name']}  <{emails.get(w['name'], 'لا بريد')}>")
        print(f"الموضوع: {subject(w)}\n")
        print(body(w, url))
        print()
    if skipped:
        print("═" * 62)
        print("بلا بريدٍ في السجلّ، فلن تصلهم: " + " · ".join(skipped))
    if not rows:
        print("لا شيء يُرسَل — كلُّ مراجعٍ أنهى ما عليه.")
    print(f"\nالنتيجة: {res[0]} رسالة · {res[1]}")
