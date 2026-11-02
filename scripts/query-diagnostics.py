"""Read aggregate diagnostics from this bot; credentials stay in an ignored local file."""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parent.parent
secret = (root / "data" / "diagnostics-token").read_text().strip()
benchmark = "--benchmark" in sys.argv
path = "/diagnostics/ocr-benchmark" if benchmark else "/diagnostics"
request = Request(
    "https://universal-yordamchi-bot.onrender.com" + path,
    headers={"X-Diagnostics-Token": secret},
    data=b"" if benchmark else None,
    method="POST" if benchmark else "GET",
)
try:
    with urlopen(request, timeout=140) as response:
        result = json.load(response)
except HTTPError as exc:
    if exc.code != 409:
        raise SystemExit(f"Diagnostics HTTP {exc.code}") from None
    result = {"status": "deferred", "reason": "bot_busy"}
print(json.dumps(result, indent=2))
if benchmark:
    (root / "data" / "ocr-server-benchmark.json").write_text(json.dumps(result, indent=2))
