"""
Delivery: email notification through Inkbox (and, once the Google API project has
passed the YouTube audit, automatic upload to YouTube).

Env vars (GitHub Secrets):
    INKBOX_API_KEY   your Inkbox API key
    INKBOX_FROM      the Inkbox mailbox that sends the email (e.g. my-agent@inkbox.ai)
    NOTIFY_TO        where you want to receive it (your personal email)
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN  -> only when upload_mode = "youtube"
"""
from __future__ import annotations

import base64
import html
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

INKBOX_URL = "https://inkbox.ai/api/v1/mail/mailboxes/{mailbox}/messages"


def run_url():
    s, r, i = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else None


def build_email(manifest: dict, uploaded: dict | None):
    url = run_url()
    kind = "Vídeo largo + Shorts" if "long" in manifest else "Shorts"
    subject = f"🎬 Listo para subir ({kind}): {manifest['title']}"
    t, h = [], []

    def para(text, tag="p"):
        t.append(text)
        h.append(f"<{tag}>{html.escape(text).replace(chr(10), '<br>')}</{tag}>")

    para(f"Idea de hoy: {manifest.get('hook', '')}")
    if uploaded:
        para("Ya subido a YouTube:\n" + "\n".join(f"{k}: https://youtu.be/{v}" for k, v in uploaded.items()))
    elif url:
        t.append(f"Descarga los archivos (sección 'Artifacts', abajo del todo): {url}")
        h.append(f'<p><a href="{url}"><b>⬇️ Descargar los vídeos de hoy</b></a> (sección "Artifacts", abajo del todo)</p>')

    if "long" in manifest:
        L = manifest["long"]
        para("VÍDEO LARGO: long.mp4 + thumbnail.jpg", "h3")
        para("Título:\n" + L["title"])
        para("Descripción:\n" + L["description"])
        para("Tags:\n" + ", ".join(L["tags"]))
    for s in manifest.get("shorts", []):
        para(f"SHORT: {s['file']}", "h3")
        para("Título:\n" + s["title"])
        para("Descripción:\n" + s["description"])
    return subject, "\n\n".join(t), "\n".join(h)


def send_inkbox(subject, text, body_html, attachments=None):
    key, sender, to = (os.environ.get(k) for k in ("INKBOX_API_KEY", "INKBOX_FROM", "NOTIFY_TO"))
    if not (key and sender and to):
        print("[deliver] Inkbox not configured (INKBOX_API_KEY / INKBOX_FROM / NOTIFY_TO), skipping")
        return
    body = {"recipients": {"to": [a.strip() for a in to.split(",")]}, "subject": subject[:990],
            "body_text": text, "body_html": body_html, "attachments": attachments or []}
    req = urllib.request.Request(INKBOX_URL.format(mailbox=urllib.parse.quote(sender)),
                                 data=json.dumps(body).encode(),
                                 headers={"X-API-Key": key, "Content-Type": "application/json",
                                          "Idempotency-Key": subject[:200] + (os.environ.get("GITHUB_RUN_ID") or "")})
    resp = urllib.request.urlopen(req, timeout=60)
    print(f"[deliver] email sent to {to} ({resp.status})")


def deliver(out: Path, manifest: dict, config: dict):
    uploaded = None
    if config.get("upload_mode") == "youtube":
        from publish import youtube_upload
        uploaded = youtube_upload.upload_all(out, manifest, config)
        (out / "uploaded.json").write_text(json.dumps(uploaded, indent=2))
    try:
        subject, text, body_html = build_email(manifest, uploaded)
        atts = []
        thumb = out / "thumbnail.jpg"
        if thumb.exists():
            atts.append({"filename": "thumbnail.jpg", "content_type": "image/jpeg",
                         "content_base64": base64.b64encode(thumb.read_bytes()).decode()})
        send_inkbox(subject, text, body_html, atts)
    except Exception as e:  # never fail the run because of a notification
        print(f"[deliver] email failed: {e}")
