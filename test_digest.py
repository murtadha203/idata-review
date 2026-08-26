"""اختبارُ بناء الرسالة — يُشغَّل بـ`python -X utf8 test_digest.py`.

**ولماذا وُجد.** كُسر `body_html` بطمسِ اسمٍ واحدٍ، ومرّ الكسرُ إلى الإنتاج
لأنّي جرّبتُ النسخةَ النصّيةَ وإقلاعَ الخادم ولم أجرّب نسخةَ الـHTML. فأيُّ
تعديلٍ في الصياغة يمرّ من هنا على النسختين معاً.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
import digest

URL = "https://idata-review-production.up.railway.app"
D = lambda t, s, **k: dict(title=t, slug=s, **k)

CASES = []
for fem in (False, True):
    for never, quick, partial in (
        ([D("أ", "a")], [], []),
        ([], [D("ب", "b", ms=41000)], []),
        ([], [], [D("ج", "c", seen=18, total=44)]),
        ([D("أ", "a"), D("د", "d")], [D("ب", "b", ms=9000)],
         [D("ج", "c", seen=60, total=72)]),
        ([D("أ%d" % i, "s%d" % i) for i in range(12)], [], []),
    ):
        CASES.append({"name": "س", "fem": fem, "never": never,
                      "quick": quick, "partial": partial})

fails = 0
for i, w in enumerate(CASES):
    for fn, label in ((digest.body, "نصّية"), (digest.body_html, "HTML")):
        try:
            out = fn(w, URL)
            assert out and len(out) > 40, "مخرَجٌ فارغ"
            assert "{" not in out and "None" not in out, "قالبٌ لم يُملأ"
        except Exception as e:
            print(f"  ✘ حالة {i+1} · {label}: {type(e).__name__}: {e}")
            fails += 1
    try:
        digest.subject(w)
    except Exception as e:
        print(f"  ✘ حالة {i+1} · الموضوع: {e}")
        fails += 1

# والروابطُ تُرمَّز، والصيغةُ تتبع المخاطَب
h = digest.body_html(CASES[-1], URL)
if "/d/s0" not in h and "%" not in h:
    print("  ✘ الروابطُ لا تُبنى")
    fails += 1
m = digest.body(dict(CASES[0], fem=False), URL)
f = digest.body(dict(CASES[0], fem=True), URL)
if m == f:
    print("  ✘ صيغةُ المذكّر والمؤنّث واحدة")
    fails += 1

print(f"  {len(CASES)} حالةً × نسختين")
print(f"\n{'✘ إخفاقات: ' + str(fails) if fails else '✔ لا إخفاق'}\n")
sys.exit(1 if fails else 0)
