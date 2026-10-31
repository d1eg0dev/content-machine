"""
Daily pipeline: scout -> music -> visuals -> package -> deliver.

    python -m pipeline.run_daily                 # normal daily run
    python -m pipeline.run_daily --long          # force a long video today
    python -m pipeline.run_daily --minutes 3     # quick test (3-minute "long" video)
    python -m pipeline.run_daily --theme themes/example.json   # skip the scout

Outputs go to out/<date>/ :
    long.mp4, thumbnail.jpg, short_1.mp4, short_2.mp4, manifest.json, UPLOAD.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from engine import music, visuals
from scout import trend_scout

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_long_day(date: dt.date) -> bool:
    return date.strftime("%a") in CONFIG["long_video_days"]


def build_description(theme, credit, minutes):
    parts = [theme.get("description", "").strip()]
    footer = CONFIG["description_footer"].format(minutes=int(minutes))
    if credit:
        footer += f"\n{credit}"
    parts.append(footer)
    return "\n\n".join(p for p in parts if p)[:4900]


def run(date: dt.date, force_long=False, minutes=None, theme_path=None, deliver=True):
    out = ROOT / "out" / date.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    # 1. concept
    if theme_path:
        theme = json.loads(Path(theme_path).read_text())
    else:
        log("scouting today's trend...")
        theme = trend_scout.scout(date)
    theme["date"] = date.isoformat()
    (out / "theme.json").write_text(json.dumps(theme, indent=2, ensure_ascii=False))
    log(f"concept: {theme['title']}")
    log(f"hook: {theme.get('hook', '-')}")

    long_day = force_long or is_long_day(date)
    m = dict(theme["music"])
    m["title"] = theme["title"] + date.isoformat()  # unique seed per day
    dur_min = minutes if minutes is not None else m.get("duration_min", 60)
    seed = int(hashlib.sha256(theme["title"].encode()).hexdigest()[:8], 16)

    # 2. visual
    img, credit, source = visuals.resolve_image(theme, seed=seed)
    log(f"visual source: {source}")
    pal = theme.get("visual", {}).get("palette")
    graded = visuals.grade(img, pal)

    manifest = {"date": date.isoformat(), "title": theme["title"], "hook": theme.get("hook"),
                "scout_status": theme.get("scout_status", "theme file"),
                "visual_source": source, "credit": credit, "files": {}}

    # 3. long video
    if long_day:
        log(f"rendering {dur_min:.0f}-minute soundscape...")
        audio = str(out / "long_audio.m4a")
        music.render(m, audio, minutes=dur_min, verbose=False)
        log("rendering visual loop...")
        loop = str(out / "loop.mp4")
        visuals.render_loop(graded, loop, seconds=CONFIG["loop_seconds"], seed=seed)
        log("assembling long video...")
        long_mp4 = str(out / "long.mp4")
        visuals.assemble_long(loop, audio, long_mp4, dur_min * 60)
        visuals.thumbnail(img, theme.get("thumbnail_text", "DEEP SLEEP"), str(out / "thumbnail.jpg"),
                          badge=f"{int(dur_min // 60)} HOUR" if dur_min >= 60 else f"{int(dur_min)} MIN",
                          palette=pal)
        manifest["files"]["long"] = "long.mp4"
        manifest["files"]["thumbnail"] = "thumbnail.jpg"
        manifest["long"] = {"title": theme["title"],
                            "description": build_description(theme, credit, dur_min),
                            "tags": theme.get("tags", [])[:15]}
        Path(loop).unlink(missing_ok=True)
        short_src = audio
    else:
        log("short-only day: rendering 2-minute soundscape for Shorts...")
        short_src = str(out / "short_audio.m4a")
        music.render(m, short_src, minutes=2.2, verbose=False)

    # 4. shorts
    shorts_meta = []
    src_len = dur_min * 60 if long_day else 132
    sec = CONFIG["short_seconds"]
    for i, s in enumerate(theme.get("shorts", [])[:CONFIG["shorts_per_day"]], start=1):
        log(f"rendering short {i}...")
        start = min(src_len - sec - 5, (src_len * (0.2 + 0.35 * (i - 1))))
        excerpt = str(out / f"short_{i}_audio.m4a")
        visuals.audio_excerpt(short_src, excerpt, max(5, start), sec)
        cta = (CONFIG["short_cta_long"].replace("1-hour", f"{int(dur_min // 60)}-hour") if dur_min >= 60
               else CONFIG["short_cta_channel"]) if long_day else CONFIG["short_cta_channel"]
        visuals.render_short(graded, excerpt, str(out / f"short_{i}.mp4"), s.get("hook_text", ""), seconds=sec,
                             seed=seed + i, cta=cta)
        Path(excerpt).unlink(missing_ok=True)
        desc = f"{theme['title']}\n\n{CONFIG['short_description']}"
        shorts_meta.append({"file": f"short_{i}.mp4", "title": s.get("title", theme["title"])[:100],
                            "description": desc, "tags": theme.get("tags", [])[:8]})
    manifest["shorts"] = shorts_meta
    for tmp in ("short_audio.m4a", "long_audio.m4a"):
        (out / tmp).unlink(missing_ok=True)

    # 5. package
    manifest["render_seconds"] = round(time.time() - t_start)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    (out / "UPLOAD.md").write_text(upload_sheet(manifest))
    log(f"done in {manifest['render_seconds']}s -> {out}")

    # 6. history (so the scout never repeats itself)
    hist_path = ROOT / "data" / "history.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else []
    hist.append({"date": date.isoformat(), "title": theme["title"], "long": long_day,
                 "music": {k: theme["music"].get(k) for k in ("key", "mode", "layers")}})
    hist_path.parent.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist[-200:], indent=1, ensure_ascii=False))

    # 7. deliver
    delivery = {}
    if deliver:
        from publish import deliver as dl
        delivery = dl.deliver(out, manifest, CONFIG) or {}

    # 8. diagnostics committed to the repo (no secrets, safe to publish)
    import os
    report = {"date": date.isoformat(), "title": theme["title"], "scout": manifest["scout_status"],
              "visual_source": source, "long": long_day, "render_seconds": manifest["render_seconds"],
              "delivery": delivery,
              "secrets_present": {k: bool(os.environ.get(k)) for k in (
                  "GEMINI_API_KEY", "INKBOX_API_KEY", "NOTIFY_PHONE", "INKBOX_IDENTITY_ID", "INKBOX_FROM",
                  "NOTIFY_TO", "UNSPLASH_ACCESS_KEY", "PEXELS_API_KEY")}}
    (ROOT / "data" / "last_run.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return out, manifest


def upload_sheet(m):
    lines = [f"# {m['date']} — {m['title']}", "", f"Hook: {m.get('hook')}", ""]
    if "long" in m:
        L = m["long"]
        lines += ["## Long video (long.mp4 + thumbnail.jpg)", "", "**Title**", "", L["title"], "",
                  "**Description**", "", L["description"], "", "**Tags**", "", ", ".join(L["tags"]), ""]
    for s in m.get("shorts", []):
        lines += [f"## {s['file']}", "", "**Title**", "", s["title"], "", "**Description**", "", s["description"], ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--long", action="store_true")
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--theme")
    ap.add_argument("--no-deliver", action="store_true")
    a = ap.parse_args()
    date = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    run(date, a.long, a.minutes, a.theme, deliver=not a.no_deliver)


if __name__ == "__main__":
    sys.exit(main())
