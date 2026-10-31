"""
Delivery: notify on Telegram (Shorts go straight to your phone) and, when the
Google API project has passed the YouTube audit, upload to YouTube.

Env vars (GitHub Secrets):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID      -> notifications + Shorts to your phone
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN -> YouTube upload (upload_mode = "youtube")
"""
from __future__ import annotations

import json
import os
import uuid
import urllib.request
from pathlib import Path


def run_url():
    s, r, i = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else None


# ---------------------------------------------------------------- telegram

def _tg(method, fields, files=None):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, path in (files or {}).items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{Path(path).name}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n").encode() + Path(path).read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


def telegram(out: Path, manifest: dict, uploaded: dict | None):
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        print("[deliver] telegram not configured, skipping")
        return
    chat = os.environ["TELEGRAM_CHAT_ID"]
    lines = [f"🎬 {manifest['date']}: {manifest['title']}", f"💡 {manifest.get('hook', '')}"]
    if uploaded:
        lines.append("✅ Uploaded as " + ", ".join(f"{k}: https://youtu.be/{v}" for k, v in uploaded.items()))
    elif "long" in manifest:
        url = run_url()
        lines.append("📦 Long video ready to download" + (f": {url}" if url else ""))
        lines.append("Title, description and tags are in UPLOAD.md inside the artifact.")
    _tg("sendMessage", {"chat_id": chat, "text": "\n".join(lines)})
    if (out / "thumbnail.jpg").exists():
        _tg("sendPhoto", {"chat_id": chat, "caption": "Thumbnail"}, {"photo": str(out / "thumbnail.jpg")})
    for s in manifest.get("shorts", []):
        f = out / s["file"]
        if f.exists() and f.stat().st_size < 49 * 1024 * 1024:
            _tg("sendVideo", {"chat_id": chat, "caption": f"{s['title']}\n\n{s['description']}"[:1000],
                              "supports_streaming": "true"}, {"video": str(f)})


# ---------------------------------------------------------------- main

def deliver(out: Path, manifest: dict, config: dict):
    uploaded = None
    if config.get("upload_mode") == "youtube":
        from publish import youtube_upload
        uploaded = youtube_upload.upload_all(out, manifest, config)
        (out / "uploaded.json").write_text(json.dumps(uploaded, indent=2))
    try:
        telegram(out, manifest, uploaded)
    except Exception as e:  # never fail the run because of a notification
        print(f"[deliver] telegram failed: {e}")
