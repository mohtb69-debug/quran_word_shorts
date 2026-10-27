"""
publish.py — «كلمة من القرآن»: مقطع تلاوة عشوائي من assets/audio ← الآية من اسمه ← Groq يختار كلمة وجملة شرح
منقولة حرفيًا من التفسير ← فيديو ← YouTube.

  python publish.py --check                    # يفحص مجلد الصوت ويطبع ما فُهم من الأسماء
  python publish.py --no-upload --keep         # يبني فيديو دون رفع
  python publish.py --no-upload --clip 002_255.mp3
متغيرات: GROQ_API_KEY — RECITER (اسم القارئ، يظهر في الفيديو والوصف) — PRIVACY — MADE_FOR_KIDS
"""
import argparse
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timezone

import ai
import audio_pick
import cards
import config
import quran_api as qa
import video

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.environ.get("HISTORY_PATH") or os.path.join(BASE_DIR, "history.json")
POSTS_DIR = os.environ.get("POSTS_DIR") or os.path.join(BASE_DIR, "posts")
DAILY_CAP = int(os.environ.get("DAILY_CAP", "6"))
PRIVACY = os.environ.get("PRIVACY", "public")
MADE_FOR_KIDS = os.environ.get("MADE_FOR_KIDS", "false").strip().lower() == "true"
REQUIRED_ENV = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
MAX_TRIES = 8
DEFAULTS = {"used": [], "clips": [], "published_log": [], "bad": []}


def load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            h = json.load(f)
    except (OSError, ValueError):
        h = {}
    for k, v in DEFAULTS.items():
        h.setdefault(k, json.loads(json.dumps(v)))
    return h


def save_history(h):
    h["published_log"] = h["published_log"][-300:]
    h["used"] = h["used"][-2000:]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def published_today(h):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(1 for t in h["published_log"] if t.startswith(today))


def metadata(ctx):
    ref = f"سورة {ctx['surah_name']} — الآية {qa.ar_num(ctx['ayah'])}"
    title = f"معنى «{ctx['token']}» في القرآن | {ctx['surah_name']} {qa.ar_num(ctx['ayah'])} #shorts"
    if len(title) > 100:
        title = f"معنى «{ctx['token']}» في القرآن #shorts"
    desc = (f"📖 كلمة «{ctx['token']}»\n\n💡 الشرح:\n«{ctx['excerpt']}»\n— {ctx['source_label']}\n\n"
            f"﴿ {ctx['verse']} ﴾\n({ref})\n\n"
            + (f"🎙️ التلاوة: {ctx['reciter']}\n" if ctx.get("reciter") else "")
            + f"\n{config.DISCLAIMER}\n📚 نص الآية: Quran.com\n\n{config.HASHTAGS}")
    return title, desc, config.BASE_TAGS + [ctx["surah_name"], ctx["token"]]


def check_only():
    clips, problems = audio_pick.scan()
    ok = audio_pick.eligible(clips, config.MIN_CLIP, config.MAX_CLIP)
    print(f"مقاطع مفهومة: {len(clips)} — صالحة للاستعمال ({config.MIN_CLIP:.0f}-{config.MAX_CLIP:.0f}ث): {len(ok)}")
    for c in clips:
        flag = "" if c in ok else "  ⚠ المدة خارج الحد"
        print(f"  {c['file']:<28} {c['surah']:>3}:{c['a1']}" + (f"-{c['a2']}" if c['a2'] != c['a1'] else "") + f"  {c['dur']:.1f}s{flag}")
    for p in problems:
        print("  ✗", p)
    return 0 if ok else 1


def build_context(clip, args, h):
    """يبني سياق الفيديو لمقطع واحد (آية + كلمة + شرح موثّق) أو يرفع خطأً ليجرّب مقطعًا آخر."""
    surah_name, n_verses = qa.chapter(clip["surah"])
    if clip["a2"] > n_verses:
        raise ai.AIError(f"الآية {clip['a2']} خارج السورة ({n_verses})")
    recent = {u.rsplit(":", 1)[0] for u in h["used"][-40:]}
    ayahs = list(range(clip["a1"], clip["a2"] + 1))
    random.shuffle(ayahs)
    ayahs.sort(key=lambda a: f"{clip['surah']}:{a}" in recent)          # الآيات غير المستعملة حديثًا أولًا
    if args.ref:
        ayahs = [int(args.ref.split(":")[1])]
    last = "لا آيات"
    for ayah in ayahs[:3]:
        key = f"{clip['surah']}:{ayah}"
        verse = qa.verse_text(clip["surah"], ayah)
        tafs = qa.tafsirs(clip["surah"], ayah)
        if not tafs:
            last = f"لا تفسير متاح لـ {key}"
            continue
        taken = [int(u.rsplit(":", 1)[1]) for u in h["used"] if u.startswith(key + ":")]
        try:
            w = ai.pick_word(surah_name, clip["surah"], ayah, verse, tafs, avoid_idx=taken)
            ok, why = ai.review(w["token"], w["excerpt"], verse, w["source_label"])
        except ai.AIError as e:
            last = f"{key}: {e}"
            print("   ↺", last)
            continue
        if not ok:
            last = f"{key}: المراجعة رفضت الشرح ({why})"
            print("   ↺", last)
            continue
        return {"surah": clip["surah"], "ayah": ayah, "surah_name": surah_name, "verse": verse, "index": w["index"],
                "token": w["token"], "excerpt": w["excerpt"], "source_label": w["source_label"],
                "reciter": clip["reciter"] or config.RECITER,
                "range_note": (f"من تلاوة الآيات {qa.ar_num(clip['a1'])}–{qa.ar_num(clip['a2'])}" if clip["a2"] != clip["a1"] else "")}
    raise ai.AIError(last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--clip", help="اسم مقطع محدد")
    ap.add_argument("--ref", help="آية محددة داخل المقطع، مثل 2:255")
    args = ap.parse_args()
    if args.check:
        return check_only()

    cards.require_raqm()
    clips, problems = audio_pick.scan()
    for p in problems:
        print("  ⚠", p)
    pool = audio_pick.eligible(clips, config.MIN_CLIP, config.MAX_CLIP)
    pool = [c for c in pool if c["file"] not in set(load_history()["bad"])]
    online = config.AUDIO_SOURCE == "online" or (config.AUDIO_SOURCE == "auto" and not pool)
    if config.AUDIO_SOURCE == "local":
        online = False
    if online:
        print(f"مصدر الصوت: الإنترنت — {config.ONLINE_RECITERS.get(config.ONLINE_RECITER, config.ONLINE_RECITERS['sudais'])[1]}")
    if not pool and not online:
        print("::error::لا توجد مقاطع صالحة في assets/audio. سمِّ الملفات هكذا: 002_255.mp3 (سورة_آية) أو أضف manifest.json "
              "— شغّل `python publish.py --check` لرؤية التفاصيل.", file=sys.stderr)
        return 1
    h = load_history()
    missing = [n for n in REQUIRED_ENV if not os.environ.get(n)]
    can_upload = not args.no_upload and not missing
    if missing and not args.no_upload:
        print(f"متغيرات يوتيوب ناقصة {missing} — بناء فقط.")
    if can_upload and published_today(h) >= DAILY_CAP:
        print(f"بلغنا الحد اليومي ({DAILY_CAP}) — لا نشر.")
        return 0

    tried = set()
    used_keys = {u.rsplit(":", 1)[0] for u in h["used"][-3000:]}
    for _ in range(MAX_TRIES * (3 if online else 1)):
        if online:
            s, a = audio_pick.online_candidate(used_keys, tried, args.ref)
            tried.add(f"{s}:{a}")
            clip = audio_pick.online_clip(s, a, config.MAX_CLIP, config.MIN_CLIP)
            if not clip:
                if args.ref:
                    return 1
                continue
        else:
            remaining = [c for c in pool if c["file"] not in tried] or None
            if not remaining:
                break
            clip, wrapped = audio_pick.choose(remaining, h["clips"], forced=args.clip)
            if wrapped and not args.clip:
                h["clips"] = []
            tried.add(clip["file"])
        print(f"المقطع: {clip['file']} ({clip['dur']:.1f}s) — {clip['surah']}:{clip['a1']}-{clip['a2']}")
        try:
            ctx = build_context(clip, args, h)
        except (ai.AIError, qa.QuranError) as e:
            print("   ✗", e)
            if args.clip:
                return 1
            continue
        print(f"   الكلمة: {ctx['token']} — {ctx['surah_name']} {ctx['ayah']} — {ctx['source_label']}\n   الشرح: {ctx['excerpt']}")
        os.makedirs(POSTS_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = os.path.join(POSTS_DIR, f"quran_{ctx['surah']}_{ctx['ayah']}_{stamp}.mp4")
        with tempfile.TemporaryDirectory(prefix="qw_") as tmp:
            images = cards.render(ctx, tmp)
            plan = video.build(images, clip["path"], out, len(ctx["excerpt"]))
        print(f"   فيديو جاهز ({plan['total']:.1f}s): {out}")
        if can_upload:
            try:
                from youtube_upload import upload_short
                title, desc, tags = metadata(ctx)
                res = upload_short(out, title, desc, tags=tags, privacy=PRIVACY, language=config.UPLOAD_LANGUAGE,
                                   audio_language="ar", made_for_kids=MADE_FOR_KIDS)
                print(f"   نُشر ({PRIVACY}):", res.get("id"))
            except Exception as e:                                       # noqa: BLE001
                print("   ✗ خطأ YouTube:", e, file=sys.stderr)
                save_history(h)
                return 1
            h["used"].append(f"{ctx['surah']}:{ctx['ayah']}:{ctx['index']}")
            if not online:
                h["clips"].append(clip["file"])
            h["published_log"].append(datetime.now(timezone.utc).isoformat())
            if not args.keep:
                os.remove(out)
        save_history(h)
        return 0
    print("::error::لم ينجح أي مقطع (راجع الرسائل أعلاه).", file=sys.stderr)
    save_history(h)
    return 1


if __name__ == "__main__":
    sys.exit(main())
