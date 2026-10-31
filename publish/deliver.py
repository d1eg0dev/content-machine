"""
Delivery: email notification through Inkbox (and, once the Google API project has
passed the YouTube audit, automatic upload to YouTube).

Env vars (GitHub Secrets):
    INKBOX_API_KEY      Inkbox API key (identity key of your agent, e.g. edmond-claude)
    NOTIFY_PHONE        your mobile in E.164 (+34600111222): the agent messages you (iMessage/SMS)
    INKBOX_IDENTITY_ID  only needed if INKBOX_API_KEY is an admin key: the agent identity UUID
    INKBOX_FROM         optional: agent mailbox for the email copy (edmond-claude@inkboxmail.com)
    NOTIFY_TO           optional: where to receive the email copy (your personal email)
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
IMESSAGE_URL = "https://inkbox.ai/api/v1/imessage/messages"


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
        return "skipped: INKBOX_FROM or NOTIFY_TO missing"
    body = {"recipients": {"to": [a.strip() for a in to.split(",")]}, "subject": subject[:990],
            "body_text": text, "body_html": body_html, "attachments": attachments or []}
    req = urllib.request.Request(INKBOX_URL.format(mailbox=urllib.parse.quote(sender)),
                                 data=json.dumps(body).encode(),
                                 headers={"X-API-Key": key, "Content-Type": "application/json",
                                          "Idempotency-Key": subject[:200] + (os.environ.get("GITHUB_RUN_ID") or "")})
    resp = urllib.request.urlopen(req, timeout=60)
    print(f"[deliver] email sent to {to} ({resp.status})")
    return f"ok: {resp.status}"


def phone_text(manifest: dict, uploaded: dict | None):
    kind = "Vídeo largo + 2 Shorts" if "long" in manifest else "2 Shorts"
    lines = [f"🎬 Listo para subir ({kind})", manifest["title"], f"💡 {manifest.get('hook', '')}"]
    if uploaded:
        lines += [f"https://youtu.be/{v}" for v in uploaded.values()]
    elif run_url():
        lines.append(f"⬇️ Descarga (Artifacts, abajo del todo): {run_url()}")
    if "long" in manifest:
        lines += ["", "Título:", manifest["long"]["title"]]
    for s in manifest.get("shorts", []):
        lines += ["", f"Short {s['file'][6]}:", s["title"]]
    lines += ["", "Descripciones y tags: UPLOAD.md dentro de la descarga."]
    return "\n".join(lines)


def send_imessage(text):
    key, phone = os.environ.get("INKBOX_API_KEY"), os.environ.get("NOTIFY_PHONE")
    if not (key and phone):
        print("[deliver] phone message not configured (INKBOX_API_KEY / NOTIFY_PHONE), skipping")
        return "skipped: INKBOX_API_KEY or NOTIFY_PHONE missing"
    url = IMESSAGE_URL
    if os.environ.get("INKBOX_IDENTITY_ID"):
        url += "?agent_identity_id=" + urllib.parse.quote(os.environ["INKBOX_IDENTITY_ID"])
    req = urllib.request.Request(url, data=json.dumps({"to": phone.strip(), "text": text[:18000]}).encode(),
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    msg = resp.get("message", resp)
    print(f"[deliver] message to {phone}: {msg.get('status', 'sent')}")
    return f"ok: {msg.get('status', 'sent')}"


def deliver(out: Path, manifest: dict, config: dict):
    uploaded = None
    if config.get("upload_mode") == "youtube":
        from publish import youtube_upload
        uploaded = youtube_upload.upload_all(out, manifest, config)
        (out / "uploaded.json").write_text(json.dumps(uploaded, indent=2))
    status = {}
    try:
        status["phone"] = send_imessage(phone_text(manifest, uploaded))
    except Exception as e:  # never fail the run because of a notification
        body = getattr(e, "read", lambda: b"")()
        status["phone"] = f"failed: {e} {body[:300].decode(errors='replace')}"
        print(f"[deliver] phone message {status['phone']}")
    try:
        subject, text, body_html = build_email(manifest, uploaded)
        atts = []
        thumb = out / "thumbnail.jpg"
        if thumb.exists():
            atts.append({"filename": "thumbnail.jpg", "content_type": "image/jpeg",
                         "content_base64": base64.b64encode(thumb.read_bytes()).decode()})
        status["email"] = send_inkbox(subject, text, body_html, atts)
    except Exception as e:  # never fail the run because of a notification
        body = getattr(e, "read", lambda: b"")()
        status["email"] = f"failed: {e} {body[:300].decode(errors='replace')}"
        print(f"[deliver] email {status['email']}")
    return status
