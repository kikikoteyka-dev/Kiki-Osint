import subprocess
import json
import tempfile
import os

def run_maigret(username: str) -> dict:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["maigret", username, "--json", "--folderoutput", tmpdir, "--timeout", "10"],
                capture_output=True, text=True, timeout=120
            )
            # Ищем JSON файл
            for f in os.listdir(tmpdir):
                if f.endswith(".json"):
                    with open(os.path.join(tmpdir, f), "r", encoding="utf-8") as jf:
                        data = json.load(jf)
                        found = []
                        for site, info in data.items():
                            if isinstance(info, dict) and info.get("status", {}).get("status") == "Claimed":
                                found.append({
                                    "site": site,
                                    "url": info.get("url_user", "")
                                })
                        return {"source": "maigret", "username": username, "found": found}
        return {"source": "maigret", "username": username, "found": []}
    except Exception as e:
        return {"error": str(e)}
