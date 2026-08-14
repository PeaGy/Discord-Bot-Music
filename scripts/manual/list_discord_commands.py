import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config

headers = {"Authorization": f"Bot {config.TOKEN}"}
application = requests.get(
    "https://discord.com/api/v10/applications/@me",
    headers=headers,
    timeout=20,
).json()
commands = requests.get(
    f"https://discord.com/api/v10/applications/{application['id']}/commands",
    headers=headers,
    timeout=20,
).json()
print([command["name"] for command in commands])
