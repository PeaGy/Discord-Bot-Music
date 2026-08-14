import json
import random

import yt_dlp

url = "https://www.youtube.com/watch?v=kJQP7kiw5Fk&list=RDkJQP7kiw5Fk"
ydl_opts = {"extract_flat": True, "quiet": True, "playlist_end": 10}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    entries = info.get("entries", [])
    valid_entries = [entry for entry in entries if entry.get("id") != "kJQP7kiw5Fk"]
    if valid_entries:
        print(json.dumps(random.choice(valid_entries[:5]), indent=2))
    else:
        print("No entries")

