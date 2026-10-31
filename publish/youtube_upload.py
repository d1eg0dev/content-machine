"""
YouTube upload via Data API v3 (resumable).

IMPORTANT: videos uploaded from a Google Cloud project that has NOT passed the
YouTube API compliance audit are locked to private. Keep upload_mode = "artifact"
in config.json until the audit is approved, then switch to "youtube".
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(yt, path, title, description, tags, config, publish_at=None, thumbnail=None):
    from googleapiclient.http import MediaFileUpload
    status = {"privacyStatus": config.get("youtube_privacy", "private"),
              "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    body = {"snippet": {"title": title[:100], "description": description[:5000], "tags": tags,
                        "categoryId": config.get("youtube_category_id", "10"),
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status}
    media = MediaFileUpload(str(path), chunksize=16 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            print(f"[youtube] {Path(path).name}: {int(st.progress() * 100)}%")
    vid = resp["id"]
    if thumbnail and Path(thumbnail).exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail))).execute()
        except Exception as e:  # needs a phone-verified channel
            print(f"[youtube] thumbnail failed: {e}")
    return vid


def upload_all(out: Path, manifest: dict, config: dict):
    yt = client()
    delay = config.get("youtube_publish_delay_hours")
    when = None
    if delay is not None:
        when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=delay)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = {}
    if "long" in manifest:
        L = manifest["long"]
        ids["long"] = upload(yt, out / "long.mp4", L["title"], L["description"], L["tags"], config, when,
                             out / "thumbnail.jpg")
    for s in manifest.get("shorts", []):
        ids[s["file"]] = upload(yt, out / s["file"], s["title"], s["description"], s["tags"], config, when)
    return ids
