"""
Visuals: background image -> seamless animated loop, thumbnail, vertical Short.

Image source priority:
  1. Owner photo from assets/photos/ (picked by the scout)
  2. Pexels stock photo (free API key in PEXELS_API_KEY)
  3. Procedural abstract background (always works, no network)

The loop is a slow "breathing" zoom with drifting light particles. Every motion is
periodic in the loop length, so the loop can repeat for hours with no visible seam,
and the long video is assembled with stream copy (no re-encode).
"""
from __future__ import annotations

import io
import json
import math
import os
import random
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
PHOTOS = ROOT / "assets" / "photos"
FONTS = ROOT / "assets" / "fonts"
FPS = 24
DIAG = []  # image-source diagnostics, copied into data/last_run.json


# ---------------------------------------------------------------- sources

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def procedural_background(palette, w=2400, h=1350, seed=0):
    rng = np.random.default_rng(seed)
    cols = [np.array(hex_rgb(c), float) for c in (palette or ["#0b1d2e", "#3b5a73", "#c9a66b"])]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32) + cols[0] * 0.6
    for k in range(7):
        c = cols[k % len(cols)]
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.25, 0.6) * w
        a = rng.uniform(0.25, 0.6)
        g = np.exp(-(((xx - cx) ** 2 + ((yy - cy) * 1.4) ** 2) / (2 * r * r)))
        img = img * (1 - a * g[..., None]) + c * (a * g[..., None])
    img += rng.normal(0, 3, img.shape)  # film grain
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def procedural_landscape(palette, w=2400, h=1350, seed=0):
    """Night/dusk landscape: gradient sky, moon glow, stars, layered misty ridges."""
    rng = np.random.default_rng(seed)
    pal = [np.array(hex_rgb(c), float) for c in (palette or ["#0b1d2e", "#3b5a73", "#c9a66b"])]
    while len(pal) < 3:
        pal.append(pal[-1])
    top = pal[0] * 0.35
    horizon = pal[1] * 0.9 + 25
    glow_c = pal[2]
    y = np.linspace(0, 1, h)[:, None, None]
    sky = top * (1 - y ** 0.8) + horizon * y ** 0.8
    img = np.broadcast_to(sky, (h, w, 3)).copy()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    mx, my = rng.uniform(0.2, 0.8) * w, rng.uniform(0.15, 0.35) * h
    d = np.sqrt((xx - mx) ** 2 + (yy - my) ** 2)
    img += glow_c * (0.55 * np.exp(-d / (0.18 * h)))[..., None]
    img = np.where((d < 0.035 * h)[..., None], np.minimum(255, glow_c * 0.4 + 200), img)
    ns = int(w * h / 4000)
    sx, sy = rng.integers(0, w, ns), rng.integers(0, int(h * 0.6), ns)
    img[sy, sx] += (rng.uniform(40, 200, ns) * (1 - sy / (h * 0.6)))[:, None]
    fog = horizon * 1.05 + 10
    x = np.linspace(0, 1, w)
    layers = 4
    for k in range(layers):
        base_y = h * (0.52 + 0.11 * k)
        ridge = np.zeros(w)
        for octave in range(1, 7):
            ridge += np.sin(2 * np.pi * (x * octave * rng.uniform(0.8, 2.2) + rng.uniform(0, 1))) / octave ** 1.3
        ridge = base_y - ridge / 2 * h * (0.12 - 0.02 * k)
        depth = 1 - k / layers  # far layers fade into fog
        col = pal[0] * 0.25 * (1 - depth) + fog * depth * 0.75
        mask = yy > ridge[None, :]
        img = np.where(mask[..., None], col, img)
        band = np.exp(-((yy - ridge[None, :] - 0.03 * h) / (0.05 * h)) ** 2) * 0.35 * depth
        img = img * (1 - band[..., None]) + fog * band[..., None]
    img += rng.normal(0, 2.5, img.shape)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(1.2))


def unsplash_image(query, seed=0):
    """Unsplash: free commercial use. API guidelines: credit photographer + Unsplash, trigger download."""
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key or not query:
        return None, None
    try:
        hdr = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1", "User-Agent": "content-machine"}
        url = "https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(
            {"query": query, "orientation": "landscape", "per_page": 20, "content_filter": "high"})
        data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read())
        photos = [p for p in data.get("results", []) if p.get("width", 0) >= 2400]
        if not photos:
            return None, None
        p = random.Random(seed).choice(photos[:10])
        # required by the API guidelines: register the download
        urllib.request.urlopen(urllib.request.Request(p["links"]["download_location"], headers=hdr), timeout=30).read()
        raw = urllib.request.urlopen(urllib.request.Request(p["urls"]["raw"] + "&w=2600&q=90&fm=jpg",
                                                            headers={"User-Agent": "content-machine"}), timeout=60).read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        utm = "?utm_source=eyes_of_diego&utm_medium=referral"
        credit = (f"Photo by {p['user']['name']} on Unsplash: {p['user']['links']['html']}{utm} "
                  f"(https://unsplash.com/{utm})")
        return img, credit
    except Exception as e:
        print(f"[visuals] unsplash failed: {e}")
        return None, None


def openverse_image(query, seed=0):
    """Openverse (openverse.org): openly licensed images, no API key needed.
    Public domain (CC0/PDM) first; CC BY only with attribution in the description."""
    if not query:
        return None, None
    hdr = {"User-Agent": "eyes-of-diego-content-machine/1.0 (github.com/d1eg0dev/content-machine)"}
    for licenses in ("cc0,pdm", "by"):
        try:
            url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(
                {"q": query, "license": licenses, "size": "large", "aspect_ratio": "wide",
                 "mature": "false", "page_size": 30})
            data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read())
            allres = data.get("results", [])
            res = [r for r in allres if (r.get("width") or 0) >= 1920 and r.get("url")]
            DIAG.append(f"openverse[{licenses}] q={query!r}: {len(allres)} results, {len(res)} wide enough")
            random.Random(seed).shuffle(res)
            for r in res[:6]:
                try:
                    raw = urllib.request.urlopen(urllib.request.Request(r["url"], headers=hdr), timeout=60).read()
                    img = Image.open(io.BytesIO(raw)).convert("RGB")
                    if img.width < 1600:
                        continue
                    credit = r.get("attribution") or (f"\"{r.get('title')}\" by {r.get('creator')} "
                                                       f"({r.get('license')} {r.get('license_version')})")
                    credit = f"Background: {credit} {r.get('foreign_landing_url') or ''} via Openverse".strip()
                    return img, credit
                except Exception as e:
                    DIAG.append(f"openverse download failed: {str(e)[:80]}")
                    continue
        except Exception as e:
            body = getattr(e, "read", lambda: b"")()[:200]
            DIAG.append(f"openverse[{licenses}] error: {e} {body!r}")
            print(f"[visuals] openverse failed: {e}")
    return None, None


def pexels_image(query, seed=0):
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not query:
        return None, None
    try:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
            {"query": query, "orientation": "landscape", "per_page": 15, "size": "large"})
        req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "content-machine"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        photos = [p for p in data.get("photos", []) if p["width"] >= 2400]
        if not photos:
            return None, None
        p = random.Random(seed).choice(photos[:8])
        raw = urllib.request.urlopen(urllib.request.Request(p["src"]["original"], headers={"User-Agent": "content-machine"}),
                                     timeout=60).read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        credit = f"Photo: {p['photographer']} on Pexels ({p['url']})"
        return img, credit
    except Exception as e:
        print(f"[visuals] pexels failed: {e}")
        return None, None


def resolve_image(theme, seed=0):
    v = theme.get("visual", {})
    if v.get("photo") and (PHOTOS / v["photo"]).exists():
        img = ImageOps.exif_transpose(Image.open(PHOTOS / v["photo"])).convert("RGB")
        return img, "Photo: Diego (Eyes of Diego)", "owner"
    for name, fn in (("unsplash", unsplash_image), ("pexels", pexels_image), ("openverse", openverse_image)):
        img, credit = fn(v.get("stock_query"), seed)
        if img is not None:
            return img, credit, name
    return procedural_landscape(v.get("palette"), seed=seed), None, "procedural"


def cover(img, w, h):
    """Resize+center-crop to exactly w x h."""
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.5))


def grade(img, palette=None, darken=0.9, vignette=0.5, strength=1.0, seed=0):
    """Editorial "film" look: muted colours, lifted blacks, split-toning, halation, grain, vignette."""
    a = np.asarray(img).astype(np.float32) / 255.0
    k = strength
    lum = (a @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    a = a * (1 - 0.28 * k) + lum * 0.28 * k                       # mute saturation
    a = a ** 1.06                                                  # a touch of density
    a = 0.055 * k + a * (1 - 0.055 * k - 0.04 * k)                 # lifted blacks, softened whites
    shadow_tone = np.array(hex_rgb(palette[0]) if palette else (24, 40, 52), np.float32) / 255.0
    high_tone = np.array((255, 226, 190), np.float32) / 255.0      # warm highlights
    lum = (a @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    a = a + 0.10 * k * (1 - lum) ** 2 * (shadow_tone - 0.25)        # tint shadows
    a = a * (1 - 0.10 * k * lum ** 2) + high_tone * 0.10 * k * lum ** 2
    # halation: soft warm glow around bright areas
    small = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).resize(
        (max(1, a.shape[1] // 8), max(1, a.shape[0] // 8)), Image.BILINEAR)
    glow = np.asarray(small.filter(ImageFilter.GaussianBlur(6)).resize((a.shape[1], a.shape[0]), Image.BILINEAR),
                      np.float32) / 255.0
    glow = np.clip(glow - 0.6, 0, 1) * np.array((1.0, 0.75, 0.55), np.float32)
    a = a + 0.35 * k * glow
    a *= darken
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    a *= (1 - vignette * np.clip(d - 0.35, 0, 1) ** 1.5)[..., None]
    rng = np.random.default_rng(seed)
    a += rng.normal(0, 0.018 * k, (h, w, 1)).astype(np.float32)    # luminance grain
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


# ---------------------------------------------------------------- animation

class Particles:
    """Soft drifting light motes; every path is periodic in `period` seconds."""

    def __init__(self, w, h, period, n=55, seed=0, color=(255, 236, 200)):
        rng = np.random.default_rng(seed)
        self.w, self.h, self.T = w, h, period
        self.x0 = rng.uniform(0, w, n)
        self.y0 = rng.uniform(0, h, n)
        self.cycles = rng.integers(1, 3, n)              # full vertical passes per loop
        self.ax = rng.uniform(10, 40, n)
        self.kx = rng.integers(1, 4, n)
        self.ph = rng.uniform(0, 2 * np.pi, n)
        self.r = rng.uniform(1.2, 4.0, n) * (h / 1080)
        self.a = rng.uniform(0.15, 0.55, n)
        self.tw = rng.integers(2, 6, n)
        self.col = np.array(color, np.float32) / 255

    def draw(self, frame, t):
        h, w = self.h, self.w
        u = t / self.T
        ys = (self.y0 - u * self.cycles * (h + 40)) % (h + 40) - 20
        xs = self.x0 + self.ax * np.sin(2 * np.pi * self.kx * u + self.ph)
        alpha = self.a * (0.6 + 0.4 * np.sin(2 * np.pi * self.tw * u + self.ph))
        for x, y, r, al in zip(xs, ys, self.r, alpha):
            R = int(r * 4) + 1
            x0, x1 = max(0, int(x) - R), min(w, int(x) + R + 1)
            y0, y1 = max(0, int(y) - R), min(h, int(y) + R + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            g = np.exp(-((gx - x) ** 2 + (gy - y) ** 2) / (2 * r * r)) * al
            frame[y0:y1, x0:x1] += g[..., None] * self.col * 255
        return frame


def ffmpeg_writer(out, w, h, fps=FPS, crf=22, extra=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
    cmd += extra or []
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-g", str(fps * 2), "-movflags", "+faststart", out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render_motion(img, out, w, h, seconds, zoom=0.07, seed=0, particles=True, overlay=None,
                  extra_ffmpeg=None, loop_period=None):
    """Breathing zoom + parallax drift + particles. Periodic in loop_period (defaults to seconds)."""
    T = loop_period or seconds
    over = 1 + zoom + 0.02
    base = cover(img, int(w * over), int(h * over))
    bw, bh = base.size
    rng = np.random.default_rng(seed)
    px, py = rng.uniform(0, 2 * np.pi, 2)
    parts = Particles(w, h, T, seed=seed) if particles else None
    proc = ffmpeg_writer(out, w, h, extra=extra_ffmpeg)
    n = int(round(seconds * FPS))
    for i in range(n):
        t = i / FPS
        u = t / T
        z = 1 + zoom * (0.5 - 0.5 * math.cos(2 * math.pi * u))
        cw, ch = bw / z / over * (1 + 0.02), bh / z / over * (1 + 0.02)
        cx = bw / 2 + 0.012 * bw * math.sin(2 * math.pi * u + px)
        cy = bh / 2 + 0.008 * bh * math.sin(2 * math.pi * u + py)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        fr = base.resize((w, h), Image.BICUBIC, box=box)
        a = np.asarray(fr).astype(np.float32)
        a *= 1 + 0.025 * math.sin(2 * math.pi * u * 2)   # gentle light "breathing"
        if parts:
            parts.draw(a, t)
        if overlay is not None:
            ov, alpha = overlay(t)
            if alpha > 0:
                a = a * (1 - ov[..., 3:4] * alpha) + ov[..., :3] * (ov[..., 3:4] * alpha)
        proc.stdin.write(np.clip(a, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode:
        raise RuntimeError("ffmpeg failed rendering motion")
    return out


def render_loop(img, out, w=1920, h=1080, seconds=30, seed=0):
    return render_motion(img, out, w, h, seconds, seed=seed)


def assemble_long(loop_mp4, audio, out, duration_s):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", loop_mp4, "-i", audio,
           "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-t", f"{duration_s:.2f}",
           "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    return out


# ---------------------------------------------------------------- text

def font(size, weight="Bold"):
    p = FONTS / f"Poppins-{weight}.ttf"
    return ImageFont.truetype(str(p), size) if p.exists() else ImageFont.load_default()


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        test = (cur + " " + wd).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def text_layer(w, h, text, size, y_frac, weight="Bold", max_w_frac=0.86, shadow=True, fill=(255, 255, 255)):
    """RGBA float array (0..255 rgb, 0..1 alpha) with centered wrapped text."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fnt = font(size, weight)
    lines = wrap(d, text, fnt, w * max_w_frac)
    lh = int(size * 1.15)
    y = int(h * y_frac - lh * len(lines) / 2)
    if shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        for i, ln in enumerate(lines):
            tw = sd.textlength(ln, font=fnt)
            sd.text(((w - tw) / 2, y + i * lh + size * 0.04), ln, font=fnt, fill=(0, 0, 0, 200))
        sh = sh.filter(ImageFilter.GaussianBlur(size * 0.12))
        im = Image.alpha_composite(im, sh)
        d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=fnt)
        d.text(((w - tw) / 2, y + i * lh), ln, font=fnt, fill=fill + (255,))
    a = np.asarray(im).astype(np.float32)
    a[..., 3] /= 255
    return a


def thumbnail(img, text, out, badge="1 HOUR", palette=None):
    w, h = 1280, 720
    base = grade(cover(img, w, h), palette, darken=0.9, vignette=0.6).convert("RGBA")
    layer = text_layer(w, h, text.upper(), 118, 0.5, max_w_frac=0.8)
    a = np.asarray(base).astype(np.float32)
    a[..., :3] = a[..., :3] * (1 - layer[..., 3:4]) + layer[..., :3] * layer[..., 3:4]
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB")
    if badge:
        d = ImageDraw.Draw(im)
        f = font(38, "Medium")
        tw = d.textlength(badge, font=f)
        x, y = w - tw - 60, 40
        d.rounded_rectangle((x - 20, y - 8, x + tw + 20, y + 52), 14, fill=(0, 0, 0))
        d.text((x, y - 2), badge, font=f, fill=(255, 255, 255))
    im.save(out, quality=92)
    return out


def render_short(img, audio, out, hook_text, seconds=45, seed=0, cta="Full 1-hour version on the channel"):
    """Vertical 1080x1920 Short: animated photo, hook text fading in, CTA at the bottom."""
    w, h = 1080, 1920
    hook = text_layer(w, h, hook_text, 92, 0.30)
    foot = text_layer(w, h, cta, 44, 0.86, weight="Medium")
    combined = hook.copy()
    m = foot[..., 3:4] > combined[..., 3:4]
    combined = np.where(m, foot, combined)

    def overlay(t):
        return combined, min(1.0, max(0.0, (t - 0.4) / 1.2))

    tmp = str(Path(out).with_suffix(".silent.mp4"))
    render_motion(img, tmp, w, h, seconds, zoom=0.09, seed=seed, overlay=overlay)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out],
                   check=True)
    os.remove(tmp)
    return out


def audio_excerpt(src, out, start_s, seconds):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start_s}", "-t", f"{seconds}", "-i", src,
                    "-af", f"afade=t=in:d=1.5,afade=t=out:st={seconds-2.5}:d=2.5", "-c:a", "aac", "-b:a", "192k", out],
                   check=True)
    return out
