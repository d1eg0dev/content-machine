"""
Run ONCE on your own computer to get a YouTube refresh token for GitHub Secrets.

    pip install google-auth-oauthlib
    python tools/get_youtube_token.py path/to/client_secret.json

It opens a browser; log in with the Google account that owns @eyesofdiego.
Copy the three printed values into GitHub -> Settings -> Secrets -> Actions.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

if len(sys.argv) != 2:
    sys.exit(__doc__)
flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open(sys.argv[1]))
info = info.get("installed") or info.get("web")
print("\nYT_CLIENT_ID     =", info["client_id"])
print("YT_CLIENT_SECRET =", info["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
