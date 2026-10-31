"""
Daily trend scout.

1. Pulls today's US trending searches (Google Trends RSS, no key needed).
2. Adds calendar/seasonal context (holidays, season, weather moods).
3. Asks Gemini (free tier, with Google Search grounding) to turn the strongest
   *mood-compatible* trend into a unique ambient video concept: title, description,
   tags, thumbnail text, visual brief and the full music parameter block the
   engine needs.
4. Checks it against data/history.json so the channel never repeats itself.

Without GEMINI_API_KEY it falls back to an offline seasonal generator so the
pipeline can always run.

Usage:
    python -m scout.trend_scout --out out/theme.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "history.json"
PHOTOS = ROOT / "assets" / "photos"
TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

LAYERS = ["pad", "drone", "bowls", "bells", "brown", "rain", "ocean", "wind", "fire", "birds", "binaural"]
MODES = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "pentatonic_major", "pentatonic_minor"]
KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


# ---------------------------------------------------------------- inputs

def fetch_trends(geo="US", limit=25):
    try:
        req = urllib.request.Request(TRENDS_RSS.format(geo=geo), headers={"User-Agent": "Mozilla/5.0"})
        xml = urllib.request.urlopen(req, timeout=20).read()
        root = ET.fromstring(xml)
        ns = {"ht": "https://trends.google.com/trending/rss"}
        out = []
        for item in root.iter("item"):
            title = item.findtext("title", "")
            traffic = item.findtext("ht:approx_traffic", "", ns)
            news = [n.findtext("ht:news_item_title", "", ns) for n in item.findall("ht:news_item", ns)][:2]
            out.append({"query": title, "traffic": traffic, "news": news})
        return out[:limit]
    except Exception as e:  # network or format change: the scout still works without it
        print(f"[scout] trends unavailable: {e}", file=sys.stderr)
        return []


def calendar_context(today: dt.date):
    m, d = today.month, today.day
    season = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
              6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}[m]
    events = {
        (1, 1): "New Year", (2, 14): "Valentine's Day", (3, 17): "St Patrick's Day",
        (10, 31): "Halloween", (11, 1): "Día de Muertos", (11, 2): "Día de Muertos",
        (12, 24): "Christmas Eve", (12, 25): "Christmas", (12, 31): "New Year's Eve",
    }
    upcoming = []
    for k in range(0, 10):
        day = today + dt.timedelta(days=k)
        if (day.month, day.day) in events:
            upcoming.append(f"{events[(day.month, day.day)]} in {k} days" if k else events[(day.month, day.day)] + " (today)")
    # US Thanksgiving: 4th Thursday of November
    if m == 11:
        first = dt.date(today.year, 11, 1)
        tg = first + dt.timedelta(days=(3 - first.weekday()) % 7 + 21)
        if 0 <= (tg - today).days <= 10:
            upcoming.append(f"Thanksgiving in {(tg - today).days} days")
    return {"date": today.isoformat(), "weekday": today.strftime("%A"), "season": season, "upcoming": upcoming}


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return []


def list_photos():
    if not PHOTOS.exists():
        return []
    return sorted(p.name for p in PHOTOS.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))


# ---------------------------------------------------------------- gemini

PROMPT = """You are the creative director of "Eyes of Diego", a YouTube channel of original
ambient / sleep / focus soundscapes. Every soundscape is synthesized from code (no samples,
no AI music models) and the visuals are the channel owner's own travel photography from Mexico
when one fits, otherwise a stock or generated image.

Today: {calendar}

Today's US trending searches (Google Trends):
{trends}

Recent videos already published (do NOT repeat their concept, location, key+mode or main layers):
{history}

Owner photos available (pick one filename if it genuinely fits, else null):
{photos}

TASK: Use Google Search to check what is trending in ambient, sleep, study and relaxation content right now,
and what moods today's trends, season and upcoming events evoke. Choose ONE concept that rides a
real, current hook (a trend, an event, the season, a weather event, a release) but is still evergreen enough
to be watched for months. Avoid people's names, brands, logos, copyrighted characters, game/movie names in the
title, tragedies, and anything political. Prefer a place/atmosphere/scene angle (e.g. a festival night in Oaxaca,
first snowfall in a cabin, a storm at sea). The title must be honest about what the video is.

Return ONLY a JSON object, no markdown, with exactly these fields:
{{
  "hook": "one sentence: what trend/event/season this rides and why now",
  "title": "YouTube title, <= 90 chars, mood + scene + use case + duration, e.g. 'Rainy Night in Oaxaca | 1 Hour Deep Sleep Ambient'",
  "description": "150-250 word YouTube description, first line is a hook, mention the soundscape is original and generated from code, add 2-3 timestamps-free usage tips, end with 3-5 hashtags",
  "tags": ["10-15 tags"],
  "thumbnail_text": "2-4 words, uppercase-friendly",
  "visual": {{"photo": "filename or null", "stock_query": "2-4 word Unsplash search, editorial/moody film-photography feel, e.g. foggy pine forest, rainy window night, desert dusk", "palette": ["#hex", "#hex", "#hex"]}},
  "shorts": [{{"hook_text": "<= 8 words on-screen text", "title": "<= 70 chars incl. #shorts"}}, {{"hook_text": "...", "title": "..."}}],
  "music": {{
    "key": one of {keys},
    "mode": one of {modes},
    "duration_min": 60,
    "brightness": 0.0-1.0,
    "lowpass_hz": 4000-12000,
    "chord_seconds": [min, max] seconds between 12 and 60,
    "reverb": {{"seconds": 3-12, "mix": 0.2-0.65}},
    "layers": {{each of {layers}: 0.0-1.0, use 3-6 non-zero layers that match the scene}},
    "density": {{"bowls_every": 15-90, "bells_every": 4-30, "birds_every": 6-40}},
    "binaural": {{"carrier_hz": 100-250, "beat_hz": 1-14}}
  }}
}}
Music guidance: sleep = dark (brightness <0.35, lowpass <7000, slow chords 35-60s); focus = mid brightness,
bells sparse, maybe binaural beat 10-14 Hz; meditation = bowls + drone; spooky = phrygian/aeolian, low brightness;
festive = lydian/ionian, bells. Only use binaural if the title says headphones.
"""


def ask_gemini(prompt, api_key, model):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 1.0},
    }
    url = GEMINI_URL.format(model=model) + "?key=" + urllib.parse.quote(api_key)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    parts = resp["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts)
    sources = []
    gm = resp["candidates"][0].get("groundingMetadata", {})
    for ch in gm.get("groundingChunks", [])[:8]:
        w = ch.get("web", {})
        if w:
            sources.append({"title": w.get("title"), "uri": w.get("uri")})
    return text, sources


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("no JSON in model output")
    return json.loads(text[s:e + 1])


# ---------------------------------------------------------------- validation

def clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def sanitize(theme: dict, photos: list[str]) -> dict:
    m = theme.setdefault("music", {})
    m["key"] = m.get("key") if m.get("key") in KEYS else random.choice(KEYS)
    m["mode"] = m.get("mode") if m.get("mode") in MODES else "dorian"
    m["duration_min"] = clamp(m.get("duration_min"), 10, 180, 60)
    m["brightness"] = clamp(m.get("brightness"), 0, 1, 0.4)
    m["lowpass_hz"] = clamp(m.get("lowpass_hz"), 3000, 14000, 8000)
    cs = m.get("chord_seconds") or [25, 45]
    lo = clamp(cs[0], 10, 60, 25)
    m["chord_seconds"] = [lo, max(lo + 4, clamp(cs[-1], 12, 80, 45))]
    rv = m.get("reverb") or {}
    m["reverb"] = {"seconds": clamp(rv.get("seconds"), 2, 14, 7), "mix": clamp(rv.get("mix"), 0.1, 0.7, 0.45)}
    lv = m.get("layers") or {}
    m["layers"] = {k: clamp(lv.get(k, 0), 0, 1, 0) for k in LAYERS}
    if not any(m["layers"][k] > 0 for k in ("pad", "drone", "bowls", "bells")):
        m["layers"]["pad"] = 0.7
    if "headphone" not in theme.get("title", "").lower():
        m["layers"]["binaural"] = 0.0
    dn = m.get("density") or {}
    m["density"] = {"bowls_every": clamp(dn.get("bowls_every"), 10, 120, 35),
                    "bells_every": clamp(dn.get("bells_every"), 3, 40, 9),
                    "birds_every": clamp(dn.get("birds_every"), 4, 60, 18)}
    bn = m.get("binaural") or {}
    m["binaural"] = {"carrier_hz": clamp(bn.get("carrier_hz"), 80, 300, 180),
                     "beat_hz": clamp(bn.get("beat_hz"), 0.5, 20, 4)}
    theme["title"] = (theme.get("title") or "Ambient Soundscape")[:100]
    v = theme.setdefault("visual", {})
    if v.get("photo") not in photos:
        v["photo"] = None
    theme.setdefault("tags", [])
    theme.setdefault("shorts", [])
    theme.setdefault("thumbnail_text", "DEEP SLEEP")
    m["title"] = theme["title"]
    return theme


def too_similar(theme, history):
    t = set(re.findall(r"[a-z]+", theme["title"].lower())) - {"ambient", "sleep", "hour", "music", "for", "the", "and", "a", "of", "in", "deep"}
    for h in history[-20:]:
        h_words = set(re.findall(r"[a-z]+", h.get("title", "").lower()))
        if len(t & h_words) >= max(2, len(t) // 2):
            return h["title"]
        hm = h.get("music", {})
        if hm.get("key") == theme["music"]["key"] and hm.get("mode") == theme["music"]["mode"] and h in history[-5:]:
            return h["title"] + " (same key+mode)"
    return None


# ---------------------------------------------------------------- offline fallback

FALLBACK_SCENES = [
    ("Rainy Night in a Mountain Cabin", "sleep", {"rain": .6, "pad": .7, "drone": .4, "fire": .2}, "aeolian"),
    ("Misty Pine Forest at Dawn", "focus", {"birds": .4, "wind": .2, "pad": .7, "bells": .3}, "lydian"),
    ("Ocean Waves Under the Stars", "sleep", {"ocean": .6, "pad": .7, "drone": .5}, "pentatonic_major"),
    ("Tibetan Singing Bowls by a Fire", "meditation", {"bowls": .5, "drone": .6, "fire": .4}, "pentatonic_minor"),
    ("Snowy Cabin Fireplace", "sleep", {"fire": .5, "wind": .3, "pad": .6, "bells": .2}, "dorian"),
    ("Desert Night in Baja California", "meditation", {"wind": .3, "drone": .6, "bowls": .3, "pad": .5}, "phrygian"),
    ("Thunderstorm over the Jungle", "sleep", {"rain": .7, "brown": .3, "pad": .5, "drone": .4}, "aeolian"),
    ("Quiet Café on a Rainy Afternoon", "focus", {"rain": .4, "pad": .6, "bells": .4}, "mixolydian"),
]


def offline_theme(cal, history):
    rng = random.Random(cal["date"])
    used = {h.get("title", "") for h in history[-12:]}
    choices = [s for s in FALLBACK_SCENES if not any(s[0] in u for u in used)] or FALLBACK_SCENES
    scene, use, layers, mode = rng.choice(choices)
    use_txt = {"sleep": "Deep Sleep Ambient", "focus": "Calm Focus Ambient", "meditation": "Meditation Soundscape"}[use]
    title = f"{scene} | 1 Hour {use_txt}"
    bright = {"sleep": 0.25, "focus": 0.55, "meditation": 0.35}[use]
    return {
        "hook": f"Seasonal fallback for {cal['season']} ({', '.join(cal['upcoming']) or 'no events'})",
        "title": title,
        "description": (f"{scene}. An original soundscape generated entirely from code - no samples, no loops, "
                        f"every minute is unique.\n\nUse it to fall asleep, study or slow down.\n\n#ambient #sleep #relax"),
        "tags": ["ambient", "sleep music", "relaxing", "meditation", "study music", scene.lower()],
        "thumbnail_text": scene.split(" ")[0].upper() + " " + scene.split(" ")[-1].upper(),
        "visual": {"photo": None, "stock_query": scene.lower(), "palette": ["#0b1d2e", "#3b5a73", "#c9a66b"]},
        "shorts": [{"hook_text": "Close your eyes for 30 seconds", "title": f"{scene} #shorts #ambient"},
                   {"hook_text": "POV: you finally slow down", "title": f"{scene} 🌙 #shorts #sleep"}],
        "music": {"key": rng.choice(KEYS), "mode": mode, "duration_min": 60, "brightness": bright,
                  "lowpass_hz": 6500 if use == "sleep" else 9500,
                  "chord_seconds": [35, 55] if use == "sleep" else [20, 35],
                  "reverb": {"seconds": 8, "mix": 0.5}, "layers": layers},
    }


# ---------------------------------------------------------------- main

def scout(today=None, geo="US", model=None, attempts=3):
    today = today or dt.date.today()
    cal = calendar_context(today)
    history = load_history()
    photos = list_photos()
    key = os.environ.get("GEMINI_API_KEY")
    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    if not key:
        print("[scout] no GEMINI_API_KEY, using offline seasonal theme", file=sys.stderr)
        return sanitize(offline_theme(cal, history), photos)

    trends = fetch_trends(geo)
    trend_txt = "\n".join(f"- {t['query']} ({t['traffic']}): {' / '.join(t['news'])}" for t in trends) or "(unavailable)"
    hist_txt = "\n".join(f"- {h['date']}: {h['title']} [{h.get('music', {}).get('key')} {h.get('music', {}).get('mode')}]"
                         for h in history[-20:]) or "(none yet)"
    prompt = PROMPT.format(calendar=json.dumps(cal), trends=trend_txt, history=hist_txt,
                           photos=json.dumps(photos) if photos else "(none)",
                           keys=KEYS, modes=MODES, layers=LAYERS)
    last_err = None
    for i in range(attempts):
        try:
            text, sources = ask_gemini(prompt, key, model)
            theme = sanitize(extract_json(text), photos)
            dup = too_similar(theme, history)
            if dup:
                prompt += f"\n\nYour previous idea '{theme['title']}' is too close to '{dup}'. Pick something different."
                last_err = f"duplicate of {dup}"
                continue
            theme["sources"] = sources
            theme["trends_seen"] = [t["query"] for t in trends[:10]]
            return theme
        except Exception as e:
            last_err = e
            print(f"[scout] attempt {i+1} failed: {e}", file=sys.stderr)
    print(f"[scout] falling back to offline theme ({last_err})", file=sys.stderr)
    return sanitize(offline_theme(cal, history), photos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--geo", default="US")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else None
    theme = scout(today, a.geo)
    theme["date"] = (today or dt.date.today()).isoformat()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(theme, indent=2, ensure_ascii=False))
    print(f"[scout] {theme['title']}")


if __name__ == "__main__":
    main()
