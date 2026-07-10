
#!/usr/bin/env python3
"""KikiHub — unified app: Kiki OSINT + WiFi Cracker. Port 7777"""
import os, sys, shutil, subprocess, threading, re, json
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import requests
import keys_store

# generativelanguage.googleapis.com sometimes fails to resolve via Python's own
# socket.getaddrinfo() even when the OS resolver (nslookup) works fine and a VPN
# is correctly routing everything else. xbox-dns.ru was previously used as a
# fallback here, but it's a "smart DNS" — its own docs say it proxies
# geo-restricted requests through its own gateway rather than returning a real
# Google IP, and that gateway is RU-hosted, so Gemini still sees a RU source
# regardless of any VPN. It can never actually bypass the geo-block, only DNS
# poisoning, so it's now last-resort only. Two-tier fallback:
#   1. socket.getaddrinfo() — works when nothing is interfering with Python's resolver.
#   2. shell out to `nslookup` — respects the OS/VPN's actual routing and gives
#      back a real global Google IP, so a VPN's tunnel correctly carries the
#      resulting connection (confirmed: a browser under the same VPN reaches
#      Gemini fine, proving the VPN itself isn't the problem).
#   3. xbox-dns.ru DoH — last resort for genuine RU ISP-level DNS poisoning with
#      no VPN at all; won't fix the geo-block, only "can't resolve at all".
import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo
_GEMINI_HOST = "generativelanguage.googleapis.com"
_DNS_FALLBACK = {_GEMINI_HOST: "www.googleapis.com"}


def _nslookup_resolve(host, timeout=5):
    try:
        out = subprocess.run(
            ["nslookup", host], capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout
        idx = out.find(host)
        if idx < 0:
            return None
        m = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", out[idx:])
        return m.group(1) if m else None
    except Exception:
        return None


def _doh_resolve(host, attempts=3):
    import dns.message, dns.query, dns.rdatatype
    for _ in range(attempts):
        try:
            q = dns.message.make_query(host, dns.rdatatype.A)
            r = dns.query.https(q, "https://xbox-dns.ru/dns-query", timeout=8)
            for ans in r.answer:
                for item in ans.items:
                    if item.rdtype == dns.rdatatype.A:
                        return item.address
        except Exception:
            continue
    return None


def _resolve_gemini_ip_bounded(host, hard_timeout=10):
    # Runs at import time, on the same thread desktop.py's startup-animation
    # loop races against with its own timeout — a thread + join with a hard
    # cap keeps this bounded even if every tier above hangs past its own
    # per-attempt timeout.
    result = [None]

    def _try():
        result[0] = _nslookup_resolve(host) or _doh_resolve(host)

    t = threading.Thread(target=_try, daemon=True)
    t.start()
    t.join(timeout=hard_timeout)
    return result[0]

_GEMINI_IP = _resolve_gemini_ip_bounded(_GEMINI_HOST)

def _patched_getaddrinfo(host, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, *args, **kwargs)
    except _socket.gaierror:
        if host == _GEMINI_HOST and _GEMINI_IP:
            try:
                return _orig_getaddrinfo(_GEMINI_IP, *args, **kwargs)
            except _socket.gaierror:
                pass
        alt = _DNS_FALLBACK.get(host)
        if alt:
            return _orig_getaddrinfo(alt, *args, **kwargs)
        raise

_socket.getaddrinfo = _patched_getaddrinfo

# AI runtime config (updated via /api/keys and /api/config)
AI_CONFIG = {
    "provider": "gemini" if keys_store.get("GEMINI_API_KEY") else None,
    "api_key":  keys_store.get("GEMINI_API_KEY") or None,
}

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
HUB_DIR    = BASE_DIR
OSINT_FE   = os.path.join(BASE_DIR, "frontend")
OSINT_SRC  = os.path.join(BASE_DIR, "sources")

BASE        = r"C:\HashCat\hashcat-7.1.2"
HASHCAT_EXE = os.path.join(BASE, "hashcat.exe")
WORDLIST    = os.path.join(BASE, "rockyou.txt")
HCXTOOL     = os.path.join(BASE, "hcxpcapngtool.exe")
TSHARK      = r"C:\Program Files\Wireshark\tshark.exe"
HASHES_DIR    = os.path.join(BASE, "hashes")
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
TEMP_DIR      = os.path.join(BASE_DIR, "temp")
os.makedirs(HASHES_DIR, exist_ok=True)
os.makedirs(TEMP_DIR,   exist_ok=True)

app  = Flask(__name__)
CORS(app)
_proc = None
_log  = []
_password = None
_running  = False   # True while crack thread is alive
_stop_requested = False  # set by /api/hc/stop to break the wordlist loop

def ts():   return datetime.now().strftime("%H:%M:%S")
def log(tag, msg):
    _log.append({"ts":ts(),"tag":tag,"msg":msg})
    if len(_log)>600: _log.pop(0)

_NOISE = ("Initializing","Initialized","NVIDIA","CUDA","OpenCL","nvml",
          "Device #","Platform #","* Device","=====","-----","wsl-user",
          "developer.nvidia","hashcat.net/faq","For more information",
          "Falling back","If you are using","Users must not","Follow the",
          "TLDR;","Linux ->","Counting lines","Parsed Hashes","Parsing Hash",
          "Sorting hashes","Sorted hash","Removing duplicate","Removed duplicate",
          "Sorting salts","Sorted salts","Comparing hashes","Compared hashes",
          "Minimum password","Maximum password","Minimum salt","Maximum salt",
          "You have enabled","This can hide","Do not report","hashcat issues",
          "bypass dangerous","hide serious")

def log_hc(line):
    if not line.strip(): return
    if any(line.strip().startswith(n) or n in line for n in _NOISE): return
    if any(x in line for x in ["Cracked","cracked"]): tag="ok"
    elif any(x in line for x in ["Status","Progress"]): tag="sys"
    elif any(x in line for x in ["Error","Rejected","WARNING"]): tag="err"
    elif any(x in line for x in ["Speed","Guess","Time","Recovered"]): tag="data"
    else: tag="dim"
    log(tag, line)

def hc_show(hf):
    """Run --show only returns password if it matches hashes in THIS file"""
    import glob
    for f in glob.glob(os.path.join(BASE, "show.*")):
        try: os.remove(f)
        except: pass
    try:
        r = subprocess.run(
            [HASHCAT_EXE, "-m", "22000", hf, "--show"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=BASE, timeout=20)
        for line in r.stdout.splitlines():
            line = line.strip()
            # Format: MIC*AP*STA*SSID:password
            if line.count(":") >= 4:
                pwd = line.rsplit(":", 1)[-1]
                if pwd: return pwd
    except: pass
    return None

# ════ HUB ROUTES ═══════════════════════════════════════════
@app.route("/")
def hub_index():
    resp = send_from_directory(HUB_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/hashcat_logo.png")
def hc_logo():
    return send_from_directory(HUB_DIR, "hashcat_logo.png")

@app.route("/open-padlock.png")
def padlock_icon():
    return send_from_directory(HUB_DIR, "open-padlock.png")

@app.route("/flipper_logo.png")
def flipper_logo():
    return send_from_directory(HUB_DIR, "flipper_logo.png")

@app.route("/kiki_logo.png")
def kiki_logo():
    return send_from_directory(HUB_DIR, "kiki_logo.png")

# ════ OSINT IFRAME ══════════════════════════════════════════
@app.route("/osint/")
@app.route("/osint")
def osint_index():
    return send_from_directory(OSINT_FE, "index.html")

@app.route("/osint/<path:filename>")
def osint_static(filename):
    # Try frontend dir first
    fe_path = os.path.join(OSINT_FE, filename)
    if os.path.exists(fe_path):
        return send_from_directory(OSINT_FE, filename)
    return ("Not found", 404)

# ════ OSINT API PROXY ═══════════════════════════════════════
@app.route("/api/keys", methods=["GET"])
def get_keys():
    k = keys_store.load()
    def mask(v):
        if not v or len(v)<8: return v
        return v[:4]+"•"*(len(v)-8)+v[-4:]
    return jsonify({
        "VK_TOKEN":          mask(k.get("VK_TOKEN","")),
        "GEMINI_API_KEY":    mask(k.get("GEMINI_API_KEY","")),
        "ANTHROPIC_API_KEY": mask(k.get("ANTHROPIC_API_KEY","")),
        "HIBP_API_KEY":      mask(k.get("HIBP_API_KEY","")),
        "configured":        bool(k.get("VK_TOKEN") or k.get("GEMINI_API_KEY") or k.get("ANTHROPIC_API_KEY"))
    })

@app.route("/api/keys", methods=["POST"])
def save_keys_route():
    data = request.json or {}
    k = keys_store.load()
    for field in ["VK_TOKEN","GEMINI_API_KEY","ANTHROPIC_API_KEY","HIBP_API_KEY"]:
        if field in data and data[field] and "•" not in data[field]:
            k[field] = data[field]
    keys_store.save(k)
    return jsonify({"ok":True})

@app.route("/api/config", methods=["POST"])
def osint_set_config():
    data = request.json or {}
    AI_CONFIG["provider"] = data.get("provider")
    AI_CONFIG["api_key"]  = data.get("api_key")
    return jsonify({"status": "ok"})

@app.route("/api/search/stream", methods=["POST"])
def osint_search_stream():
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if query.startswith("@"):
        query = query[1:]
    sources = data.get("sources", ["vk", "maigret"])
    if not isinstance(sources, list):
        sources = list(sources) if sources else ["vk", "maigret"]
    sources = [s for s in sources if isinstance(s, str)]
    maigret_limit = data.get("maigret_limit", 100)
    maigret_limit = int(maigret_limit) if maigret_limit is not None else 100
    ai_lang = data.get("ai_lang", "ru")

    req_query_type = data.get("query_type")
    if req_query_type in ("username", "email", "phone", "both"):
        query_type = req_query_type
    elif "@" in query and "." in query.split("@")[-1]:
        query_type = "email"
    else:
        query_type = "username"

    def generate():
        def send(event, payload):
            return f"data: {json.dumps({'event': event, 'data': payload}, ensure_ascii=False)}\n\n"

        collected = {}
        yield send("start", {"query": query, "type": query_type})

        if query_type == "username":
            if "vk" in sources:
                yield from search_vk_username(query, send, collected)
            if "maigret" in sources:
                yield from search_maigret(query, maigret_limit, send, collected)
            yield from search_telegram(query, send, collected)
            yield from search_github(query, send, collected)

        elif query_type == "email":
            yield from search_email_holehe(query, send, collected)
            yield from search_email_hibp(query, send, collected)
            yield from search_gravatar(query, send, collected)
            yield from search_github_by_email(query, send, collected)
            yield from search_email_domain(query, send, collected)

        elif query_type == "phone":
            yield from search_phone(query, send, collected)
            if "vk" in sources:
                yield from search_vk_phone(query, send, collected)

        req_ai_provider = data.get("ai_provider", "")
        if not isinstance(req_ai_provider, str):
            req_ai_provider = ""
        if req_ai_provider:
            key_map = {
                "gemini":    "GEMINI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }
            ai_key = keys_store.get(key_map.get(req_ai_provider, ""))
            if ai_key:
                ai_cfg = {"provider": req_ai_provider, "api_key": ai_key}
                yield send("progress", {"source": "ai", "status": "searching", "msg": "Generating AI portrait..."})
                try:
                    portrait = generate_portrait(query, query_type, ai_cfg, collected, ai_lang)
                    yield send("result", {"source": "ai", "data": portrait})
                    yield send("progress", {"source": "ai", "status": "done", "msg": "Done"})
                except Exception as e:
                    yield send("result", {"source": "ai", "data": {"error": str(e)}})
                    yield send("progress", {"source": "ai", "status": "error", "msg": str(e)})
            else:
                yield send("progress", {"source": "ai", "status": "error", "msg": f"No API key for {req_ai_provider}. Open Settings."})

        yield send("done", {})

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def fetch_telegram_profile(url):
    """Fetch Telegram public profile from t.me page"""
    try:
        import httpx
        from bs4 import BeautifulSoup
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        html = r.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        name  = soup.select_one(".tgme_page_title span")
        bio   = soup.select_one(".tgme_page_description")
        photo = soup.select_one(".tgme_page_photo_image")
        username = url.rstrip("/").split("/")[-1]
        return {
            "name":     name.get_text(strip=True) if name else username,
            "bio":      bio.get_text(strip=True) if bio else "",
            "photo":    photo["src"] if photo and photo.get("src") else "",
            "url":      url,
            "username": username,
        }
    except Exception:
        return None


def search_telegram(query, send, collected=None):
    if not re.match(r'^[a-zA-Z0-9_]{5,32}$', query):
        yield send("progress", {"source": "telegram", "status": "done", "msg": "Skipped (invalid TG username)"})
        return
    yield send("progress", {"source": "telegram", "status": "searching", "msg": "Fetching Telegram profile..."})
    try:
        import httpx
        from bs4 import BeautifulSoup
        url = f"https://t.me/{query}"
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            yield send("result", {"source": "telegram", "data": {"error": f"Not found (HTTP {r.status_code})"}})
            yield send("progress", {"source": "telegram", "status": "error", "msg": "Not found"})
            return
        html = r.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        name  = soup.select_one(".tgme_page_title span")
        bio   = soup.select_one(".tgme_page_description")
        photo = soup.select_one(".tgme_page_photo_image")
        photo_url = ""
        if photo:
            photo_url = photo.get("src") or photo.get("data-src") or ""
        subs  = soup.select_one(".tgme_page_extra")
        result = {
            "name":        name.get_text(strip=True) if name else query,
            "bio":         bio.get_text(strip=True) if bio else "",
            "photo":       photo_url,
            "subscribers": subs.get_text(strip=True) if subs else "",
            "url":         url,
            "username":    query,
        }
        if collected is not None:
            collected["telegram"] = result
        yield send("result", {"source": "telegram", "data": result})
        yield send("progress", {"source": "telegram", "status": "done",
                                "msg": result["name"] or "Found"})
    except Exception as e:
        yield send("result", {"source": "telegram", "data": {"error": str(e)}})
        yield send("progress", {"source": "telegram", "status": "error", "msg": str(e)})


def search_vk_username(query, send, collected=None):
    yield send("progress", {"source": "vk", "status": "searching", "msg": "Searching VK..."})
    try:
        from vk_module import get_vk_profile
        result = get_vk_profile(query)
        if collected is not None:
            collected["vk"] = result
        yield send("result", {"source": "vk", "data": result})
        if "error" not in result:
            yield send("progress", {"source": "vk", "status": "done", "msg": result.get("name", "found")})
        else:
            yield send("progress", {"source": "vk", "status": "error", "msg": result["error"]})
    except Exception as e:
        yield send("result", {"source": "vk", "data": {"error": str(e)}})
        yield send("progress", {"source": "vk", "status": "error", "msg": str(e)})


def search_vk_phone(query, send, collected=None):
    yield send("progress", {"source": "vk", "status": "searching", "msg": "Searching VK by phone..."})
    try:
        from vk_module import get_vk_profile
        result = get_vk_profile(query)
        if collected is not None:
            collected["vk"] = result
        yield send("result", {"source": "vk", "data": result})
        if "error" not in result:
            yield send("progress", {"source": "vk", "status": "done", "msg": result.get("name", "found")})
        else:
            yield send("progress", {"source": "vk", "status": "error", "msg": result["error"]})
    except Exception as e:
        yield send("result", {"source": "vk", "data": {"error": str(e)}})
        yield send("progress", {"source": "vk", "status": "error", "msg": str(e)})


def search_maigret(query, limit, send, collected=None):
    if getattr(sys, "frozen", False):
        # sys.executable points at the bundled app itself when frozen, not a
        # real Python interpreter — "[exe] -m maigret" just relaunches the
        # app instead of running maigret, so this only works from source.
        msg = "Maigret is unavailable in the packaged build — run from source (python desktop.py) for username search."
        if collected is not None:
            collected["maigret"] = {"found": [], "total": 0, "error": msg}
        yield send("result", {"source": "maigret", "data": {"found": [], "total": 0, "error": msg}})
        yield send("progress", {"source": "maigret", "status": "error", "msg": msg})
        return
    label = "all sites" if not limit else f"{limit} sites"
    yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Starting scan ({label})..."})
    try:
        found_sites = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, "-u", "-m", "maigret", query, "--no-color", "--timeout", "10"]
        if limit and limit > 0:
            cmd += [f"--top-sites={limit}"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace", env=env
        )
        checked = 0
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            checked += 1
            if "[+]" in line:
                parts = line.split("[+]", 1)[-1].strip()
                site_name, url = (parts.split(": ", 1) if ": " in parts else (parts, ""))
                site_name = site_name.strip()
                url = url.strip()
                MAIGRET_BLACKLIST_PREFIXES = ("OP.GG",)
                if any(site_name.startswith(p) for p in MAIGRET_BLACKLIST_PREFIXES):
                    continue
                found_sites.append({"site": site_name, "url": url})
                yield send("maigret_hit", {"site": site_name, "url": url, "count": len(found_sites)})
                yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Found {len(found_sites)} so far..."})
                if "Telegram" in site_name and "t.me/" in url:
                    tg = fetch_telegram_profile(url)
                    if tg:
                        yield send("result", {"source": "telegram", "data": tg})
            elif checked % 20 == 0:
                yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Scanning... ({len(found_sites)} found)"})
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
        if collected is not None:
            collected["maigret"] = {"found": found_sites, "total": len(found_sites)}
        yield send("result", {"source": "maigret", "data": {"found": found_sites, "total": len(found_sites)}})
        yield send("progress", {"source": "maigret", "status": "done", "msg": f"Done — {len(found_sites)} accounts found"})
    except Exception as e:
        yield send("result", {"source": "maigret", "data": {"error": str(e)}})
        yield send("progress", {"source": "maigret", "status": "error", "msg": str(e)})


def search_phone(query, send, collected=None):
    yield send("progress", {"source": "phone", "status": "searching", "msg": "Analyzing phone number..."})
    try:
        import phonenumbers
        from phonenumbers import geocoder, carrier, timezone
        raw = query.strip().replace(" ", "").replace("-", "")
        if not raw.startswith("+"):
            raw = "+" + raw
        pn = phonenumbers.parse(raw, None)
        result = {
            "number": phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "country": geocoder.description_for_number(pn, "en"),
            "carrier": carrier.name_for_number(pn, "en"),
            "timezones": list(timezone.time_zones_for_number(pn)),
            "valid": phonenumbers.is_valid_number(pn),
            "type": str(phonenumbers.number_type(pn)).split(".")[-1],
            "national": phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL),
            "country_code": pn.country_code,
        }
        if collected is not None:
            collected["phone"] = result
        yield send("result", {"source": "phone", "data": result})
        yield send("progress", {"source": "phone", "status": "done",
                                "msg": f"{result['country']} · {result['carrier'] or 'unknown'}"})
    except Exception as e:
        yield send("result", {"source": "phone", "data": {"error": str(e)}})
        yield send("progress", {"source": "phone", "status": "error", "msg": str(e)})


def search_github(query, send, collected=None):
    yield send("progress", {"source": "github", "status": "searching", "msg": "Searching GitHub..."})
    try:
        r = requests.get(f"https://api.github.com/users/{query}",
                         headers={"Accept": "application/vnd.github+json"}, timeout=8)
        if r.status_code == 404:
            yield send("progress", {"source": "github", "status": "done", "msg": "Not found"})
            return
        if r.status_code != 200:
            yield send("progress", {"source": "github", "status": "error", "msg": f"GitHub API error {r.status_code}"})
            return
        u = r.json()
        repos_r = requests.get(f"https://api.github.com/users/{query}/repos?per_page=5&sort=pushed",
                                headers={"Accept": "application/vnd.github+json"}, timeout=8)
        repos = []
        if repos_r.status_code == 200:
            repos = [{"name": x["name"], "url": x["html_url"],
                      "stars": x["stargazers_count"], "lang": x["language"]} for x in repos_r.json()]
        data2 = {
            "source": "github",
            "name":       u.get("name") or u.get("login"),
            "username":   u.get("login"),
            "avatar":     u.get("avatar_url"),
            "bio":        u.get("bio") or "",
            "location":   u.get("location") or "",
            "company":    u.get("company") or "",
            "blog":       u.get("blog") or "",
            "email":      u.get("email") or "",
            "followers":  u.get("followers", 0),
            "following":  u.get("following", 0),
            "public_repos": u.get("public_repos", 0),
            "created_at": (u.get("created_at") or "")[:10],
            "url":        u.get("html_url"),
            "repos":      repos,
        }
        if collected is not None:
            collected["github"] = data2
        yield send("result", {"source": "github", "data": data2})
        yield send("progress", {"source": "github", "status": "done", "msg": "Done"})
    except Exception as e:
        yield send("progress", {"source": "github", "status": "error", "msg": str(e)})


def search_gravatar(query, send, collected=None):
    yield send("progress", {"source": "gravatar", "status": "searching", "msg": "Checking Gravatar..."})
    try:
        import hashlib
        h = hashlib.md5(query.strip().lower().encode()).hexdigest()
        r = requests.get(f"https://www.gravatar.com/{h}.json", timeout=8)
        if r.status_code == 404:
            yield send("progress", {"source": "gravatar", "status": "done", "msg": "No Gravatar profile"})
            return
        if r.status_code != 200:
            yield send("progress", {"source": "gravatar", "status": "error", "msg": f"Error {r.status_code}"})
            return
        entry = r.json().get("entry", [{}])[0]
        data2 = {
            "source":      "gravatar",
            "name":        (entry.get("displayName") or entry.get("preferredUsername") or ""),
            "username":    entry.get("preferredUsername") or "",
            "avatar":      f"https://www.gravatar.com/avatar/{h}?s=200",
            "bio":         entry.get("aboutMe") or "",
            "location":    (entry.get("currentLocation") or ""),
            "urls":        [u.get("value") for u in entry.get("urls", []) if u.get("value")],
            "accounts":    [{"title": a.get("shortname",""), "url": a.get("url","")}
                            for a in entry.get("accounts", [])],
            "profile_url": f"https://gravatar.com/{entry.get('preferredUsername',h)}",
            "hash":        h,
        }
        if collected is not None:
            collected["gravatar"] = data2
        yield send("result", {"source": "gravatar", "data": data2})
        yield send("progress", {"source": "gravatar", "status": "done", "msg": "Done"})
    except Exception as e:
        yield send("progress", {"source": "gravatar", "status": "error", "msg": str(e)})


def search_github_by_email(query, send, collected=None):
    yield send("progress", {"source": "github_email", "status": "searching", "msg": "Searching GitHub by email..."})
    try:
        r = requests.get(
            f"https://api.github.com/search/users?q={query}+in:email",
            headers={"Accept": "application/vnd.github+json"},
            timeout=8
        )
        if r.status_code != 200:
            yield send("progress", {"source": "github_email", "status": "done", "msg": "No results"})
            return
        items = r.json().get("items", [])
        if not items:
            yield send("progress", {"source": "github_email", "status": "done", "msg": "Not found on GitHub"})
            return
        user = items[0]
        profile_r = requests.get(f"https://api.github.com/users/{user['login']}",
                                  headers={"Accept": "application/vnd.github+json"}, timeout=8)
        if profile_r.status_code == 200:
            u = profile_r.json()
            data2 = {
                "source":       "github_email",
                "name":         u.get("name") or u.get("login"),
                "username":     u.get("login"),
                "avatar":       u.get("avatar_url"),
                "bio":          u.get("bio") or "",
                "location":     u.get("location") or "",
                "company":      u.get("company") or "",
                "blog":         u.get("blog") or "",
                "followers":    u.get("followers", 0),
                "public_repos": u.get("public_repos", 0),
                "created_at":   (u.get("created_at") or "")[:10],
                "url":          u.get("html_url"),
                "total_found":  len(items),
            }
            if collected is not None:
                collected["github_email"] = data2
            yield send("result", {"source": "github_email", "data": data2})
            yield send("progress", {"source": "github_email", "status": "done", "msg": f"Found: @{data2['username']}"})
    except Exception as e:
        yield send("progress", {"source": "github_email", "status": "error", "msg": str(e)})


DISPOSABLE_DOMAINS = {
    "mailinator.com","guerrillamail.com","10minutemail.com","tempmail.com",
    "throwaway.email","yopmail.com","sharklasers.com","guerrillamailblock.com",
    "grr.la","guerrillamail.info","guerrillamail.biz","guerrillamail.de",
    "guerrillamail.net","guerrillamail.org","spam4.me","trashmail.com",
    "trashmail.me","trashmail.net","dispostable.com","mailnull.com",
    "spamgourmet.com","spamgourmet.net","spamgourmet.org","maildrop.cc",
}

def search_email_domain(query, send, collected=None):
    yield send("progress", {"source": "email_domain", "status": "searching", "msg": "Analysing email domain..."})
    try:
        import dns.resolver
        domain = query.split("@")[-1].lower()
        result = {"source": "email_domain", "domain": domain}
        result["disposable"] = domain in DISPOSABLE_DOMAINS

        try:
            mx = dns.resolver.resolve(domain, "MX")
            mx_list = sorted([(r.preference, str(r.exchange).rstrip(".")) for r in mx])
            result["mx"] = mx_list
            mx_str = " ".join(h for _, h in mx_list).lower()
            if "google" in mx_str or "gmail" in mx_str:
                result["mail_provider"] = "Google Workspace / Gmail"
            elif "outlook" in mx_str or "microsoft" in mx_str or "hotmail" in mx_str:
                result["mail_provider"] = "Microsoft / Outlook"
            elif "protonmail" in mx_str or "proton.ch" in mx_str:
                result["mail_provider"] = "ProtonMail"
            elif "yandex" in mx_str:
                result["mail_provider"] = "Yandex Mail"
            elif "mail.ru" in mx_str:
                result["mail_provider"] = "Mail.ru"
            elif "zoho" in mx_str:
                result["mail_provider"] = "Zoho Mail"
            else:
                result["mail_provider"] = mx_list[0][1] if mx_list else "Unknown"
        except Exception:
            result["mx"] = []
            result["mail_provider"] = "No MX (invalid domain?)"

        try:
            a = dns.resolver.resolve(domain, "A")
            result["ip"] = str(list(a)[0])
        except Exception:
            result["ip"] = None

        try:
            rdap = requests.get(f"https://rdap.org/domain/{domain}", timeout=6)
            if rdap.status_code == 200:
                j = rdap.json()
                events = {e["eventAction"]: e["eventDate"][:10]
                          for e in j.get("events", []) if "eventDate" in e}
                result["registered"] = events.get("registration", "")
                result["updated"]    = events.get("last changed", "")
                result["expiry"]     = events.get("expiration", "")
                entities = j.get("entities", [])
                registrar = next((e.get("vcardArray") for e in entities
                                  if "registrar" in e.get("roles", [])), None)
                if registrar:
                    for field in registrar[1]:
                        if field[0] == "fn":
                            result["registrar"] = field[3]
                            break
        except Exception:
            pass

        FREE_DOMAINS = {"gmail.com","yahoo.com","hotmail.com","outlook.com","mail.ru",
                        "yandex.ru","protonmail.com","icloud.com","me.com","live.com",
                        "inbox.ru","bk.ru","list.ru","proton.me","tutanota.com"}
        result["account_type"] = "personal/free" if domain in FREE_DOMAINS else "corporate/custom"

        if collected is not None:
            collected["email_domain"] = result
        yield send("result", {"source": "email_domain", "data": result})
        yield send("progress", {"source": "email_domain", "status": "done",
                                 "msg": result.get("mail_provider", domain)})
    except Exception as e:
        yield send("progress", {"source": "email_domain", "status": "error", "msg": str(e)})


def search_email_holehe(query, send, collected=None):
    if getattr(sys, "frozen", False):
        # holehe_exe is derived from sys.executable's folder, which is the
        # packaged app's own install dir when frozen — no such CLI exists there.
        msg = "Holehe is unavailable in the packaged build — run from source (python desktop.py) for email checks."
        if collected is not None:
            collected["holehe"] = {"found": [], "error": msg}
        yield send("result", {"source": "holehe", "data": {"found": [], "error": msg}})
        yield send("progress", {"source": "holehe", "status": "error", "msg": msg})
        return
    yield send("progress", {"source": "holehe", "status": "searching", "msg": "Running Holehe scan..."})
    try:
        found_sites = []
        rate_limited = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if sys.platform == "win32":
            holehe_exe = os.path.join(os.path.dirname(sys.executable), "Scripts", "holehe.exe")
        else:
            holehe_exe = os.path.join(os.path.dirname(sys.executable), "holehe")
        proc = subprocess.Popen(
            [holehe_exe, query, "--no-color"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace", env=env
        )
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("[+]"):
                m = re.search(r'\[\+\]\s+(\S+)', line)
                site = m.group(1) if m else line.split()[-1]
                found_sites.append({"site": site, "url": "", "confirmed": True})
                yield send("holehe_hit", {"site": site, "count": len(found_sites)})
                yield send("progress", {"source": "holehe", "status": "searching", "msg": f"Found {len(found_sites)} confirmed..."})
            elif line.startswith("[x]"):
                m = re.search(r'\[x\]\s+(\S+)', line)
                site = m.group(1) if m else line.split()[-1]
                rate_limited.append({"site": site, "url": "", "confirmed": False})
        try:
            proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
        all_sites = found_sites + rate_limited
        if collected is not None:
            collected["holehe"] = {"found": found_sites, "rate_limited": rate_limited, "total": len(all_sites)}
        yield send("result", {"source": "holehe", "data": {"found": found_sites, "rate_limited": rate_limited, "total": len(all_sites)}})
        yield send("progress", {"source": "holehe", "status": "done", "msg": f"Done — {len(found_sites)} confirmed, {len(rate_limited)} rate-limited"})
    except Exception as e:
        yield send("result", {"source": "holehe", "data": {"error": str(e)}})
        yield send("progress", {"source": "holehe", "status": "error", "msg": str(e)})


def search_email_hibp(query, send, collected=None):
    yield send("progress", {"source": "hibp", "status": "searching", "msg": "Checking HaveIBeenPwned..."})
    try:
        import httpx
        hibp_key = keys_store.get("HIBP_API_KEY")
        headers = {"User-Agent": "OSINT-Portrait/1.0"}
        if hibp_key:
            headers["hibp-api-key"] = hibp_key
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{query}?truncateResponse=false"
        r = httpx.get(url, headers=headers, follow_redirects=True, timeout=10)
        if r.status_code == 200:
            breaches = r.json()
            result = {"breaches": [{"name": b["Name"], "date": b.get("BreachDate","?"),
                                    "pwn_count": b.get("PwnCount",0),
                                    "data_classes": b.get("DataClasses",[])} for b in breaches], "total": len(breaches)}
            if collected is not None:
                collected["hibp"] = result
            yield send("result", {"source": "hibp", "data": result})
            yield send("progress", {"source": "hibp", "status": "done", "msg": f"Found in {len(breaches)} breaches"})
        elif r.status_code == 404:
            yield send("result", {"source": "hibp", "data": {"breaches": [], "total": 0}})
            yield send("progress", {"source": "hibp", "status": "done", "msg": "No breaches found"})
        elif r.status_code == 401:
            yield send("result", {"source": "hibp", "data": {"error": "API key required — get it at haveibeenpwned.com/API/Key"}})
            yield send("progress", {"source": "hibp", "status": "error", "msg": "API key required"})
        else:
            yield send("result", {"source": "hibp", "data": {"error": f"HTTP {r.status_code}"}})
            yield send("progress", {"source": "hibp", "status": "error", "msg": f"HTTP {r.status_code}"})
    except Exception as e:
        yield send("result", {"source": "hibp", "data": {"error": str(e)}})
        yield send("progress", {"source": "hibp", "status": "error", "msg": str(e)})


def generate_portrait(query, query_type, config, collected=None, ai_lang="ru"):
    context_parts = []

    if collected:
        if "vk" in collected and "error" not in collected["vk"]:
            vk = collected["vk"]
            context_parts.append(f"VK profile: name={vk.get('name')}, city={vk.get('city')}, country={vk.get('country')}, "
                                  f"followers={vk.get('followers')}, bdate={vk.get('bdate')}, sex={vk.get('sex')}, "
                                  f"last_seen={vk.get('last_seen')}, education={vk.get('education')}, "
                                  f"groups={vk.get('groups', [])[:10]}, posts={vk.get('posts', [])[:3]}, closed={vk.get('closed')}")

        if "telegram" in collected and "error" not in collected["telegram"]:
            tg = collected["telegram"]
            context_parts.append(f"Telegram profile: name={tg.get('name')}, username=@{tg.get('username')}, "
                                  f"bio={tg.get('bio')}, subscribers={tg.get('subscribers')}")

        if "maigret" in collected:
            sites = [s["site"] for s in collected["maigret"].get("found", [])]
            context_parts.append(f"Accounts found on {len(sites)} sites: {', '.join(sites[:30])}")

        if "phone" in collected and "error" not in collected["phone"]:
            ph = collected["phone"]
            context_parts.append(f"Phone info: number={ph.get('number')}, country={ph.get('country')}, "
                                  f"carrier={ph.get('carrier')}, type={ph.get('type')}, timezones={ph.get('timezones')}")

        if "holehe" in collected:
            confirmed = [s["site"] for s in collected["holehe"].get("found", [])]
            context_parts.append(f"Email registered on: {', '.join(confirmed) if confirmed else 'none confirmed'}")

        if "hibp" in collected:
            breaches = [b["name"] for b in collected["hibp"].get("breaches", [])]
            context_parts.append(f"Found in data breaches: {', '.join(breaches) if breaches else 'none'}")

    type_label = {"username": f"username '{query}'", "phone": f"phone '{query}'", "email": f"email '{query}'"}.get(query_type, f"'{query}'")

    lang_instruction = {
        "ru": "Отвечай строго на русском языке.",
        "en": "Respond strictly in English.",
    }.get(ai_lang, "Respond strictly in English.")

    caveat = (
        "ВАЖНО: В конце анализа обязательно добавь раздел '⚠ Важная оговорка' где укажи, что "
        "не все найденные аккаунты могут принадлежать одному человеку — на разных платформах "
        "один и тот же никнейм мог быть занят разными людьми. Указывай уверенность только для "
        "тех платформ, где есть прямые совпадения (аватар, биография, стиль). Это ОБЯЗАТЕЛЬНАЯ часть анализа."
        if ai_lang == "ru" else
        "IMPORTANT: At the end of your analysis, add a section '⚠ Important Disclaimer' stating that "
        "not all found accounts may belong to the same person — the same username could be registered "
        "by different people on different platforms. Only express high confidence for platforms with "
        "direct cross-references (avatar, bio, writing style). This section is MANDATORY."
    )
    if context_parts:
        data_section = "\n".join(f"- {p}" for p in context_parts)
        prompt = (f"You are an OSINT analyst. Based on the following gathered data about {type_label}, "
                  f"write a concise analytical portrait: personality traits, online behavior, geographic hints, "
                  f"risk assessment, and interesting patterns.\n\nGathered data:\n{data_section}\n\n"
                  f"{lang_instruction} {caveat}")
    else:
        prompt = (f"You are an OSINT analyst. Write a brief analytical portrait for {type_label}. "
                  f"Include likely platforms, geographic hints, behavioral patterns, and risk assessment. "
                  f"{lang_instruction} {caveat}")

    if config["provider"] == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=config["api_key"])
        msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
            messages=[{"role": "user", "content": prompt}])
        return {"portrait": msg.content[0].text}

    elif config["provider"] == "gemini":
        try:
            from google import genai
        except ImportError:
            return {"error": "google-genai not installed. Run: py -m pip install google-genai"}
        try:
            client = genai.Client(api_key=config["api_key"])
            resp = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            return {"portrait": resp.text}
        except Exception as e:
            msg = str(e)
            is_ru = ai_lang == "ru"
            if "getaddrinfo failed" in msg or "11001" in msg:
                raise RuntimeError(
                    "Gemini API недоступен: ошибка DNS (generativelanguage.googleapis.com). Проверь интернет/VPN — в РФ доступ к Google API часто требует VPN."
                    if is_ru else
                    "Gemini API unreachable: DNS error (generativelanguage.googleapis.com). Check your internet/VPN — Google API access often requires a VPN from some regions."
                )
            if "User location is not supported" in msg or "FAILED_PRECONDITION" in msg:
                raise RuntimeError(
                    "Gemini API недоступен из текущего региона (User location is not supported). Нужен VPN с выходом за пределы РФ."
                    if is_ru else
                    "Gemini API unavailable in your region (User location is not supported). A VPN exiting outside the blocked region is required."
                )
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                raise RuntimeError(
                    "Gemini API: превышен лимит запросов (бесплатный тариф). Подожди немного и попробуй снова."
                    if is_ru else
                    "Gemini API: rate limit exceeded (free tier). Wait a bit and try again."
                )
            raise

    return {"error": "Unknown provider"}

# ════ HASHCAT API ════════════════════════════════════════════

_WC_MISSING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#04111a;color:#eef2f7;font-family:'JetBrains Mono',monospace}}
.box{{max-width:480px;padding:36px;border:1px solid rgba(255,255,255,.08);border-radius:16px;
background:rgba(255,255,255,.03);text-align:center}}
.box h1{{font-size:15px;letter-spacing:.08em;color:#5cd6ff;margin:0 0 14px;text-transform:uppercase}}
.box p{{font-size:13px;line-height:1.7;color:rgba(255,255,255,.6);margin:0 0 18px}}
.box code{{display:block;background:rgba(92,214,255,.08);border:1px solid rgba(92,214,255,.18);
border-radius:8px;padding:10px 14px;color:#5cd6ff;font-size:12px;word-break:break-all;margin-bottom:18px}}
.box a{{color:#5cd6ff;text-decoration:none;border-bottom:1px solid rgba(92,214,255,.3)}}
.box a:hover{{border-color:#5cd6ff}}
.box .sep{{margin:18px 0;border-top:1px solid rgba(255,255,255,.08)}}
</style></head><body>
<div class="box">
<h1>HashCat не найден</h1>
<p>Эта вкладка требует HashCat, установленный отдельно — он не входит в KikiHub.</p>
<code>{path}</code>
<p>Скачай и распакуй HashCat по этому пути, чтобы включить WiFi Cracker.<br>
<a href="https://hashcat.net/hashcat/" target="_blank">hashcat.net/hashcat ↗</a></p>
<div class="sep"></div>
<h1>HashCat not found</h1>
<p>This tab requires HashCat installed separately — it isn't bundled with KikiHub.</p>
<code>{path}</code>
<p>Download and extract HashCat to the path above to enable WiFi Cracker.<br>
<a href="https://hashcat.net/hashcat/" target="_blank">hashcat.net/hashcat ↗</a></p>
</div></body></html>"""


@app.route("/wc/")
@app.route("/wc")
def wc_index():
    """Serve wificracker HTML — reads from wifi_cracker.py"""
    import importlib.util
    wc_path = r"C:\HashCat\hashcat-7.1.2\wifi_cracker.py"
    if not os.path.exists(wc_path):
        from flask import make_response
        return make_response(_WC_MISSING_HTML.format(path=wc_path), 200, {"Content-Type": "text/html; charset=utf-8"})
    try:
        with open(wc_path, encoding="utf-8") as f:
            src = f.read()
        # Extract HTML between HTML = r""" and closing """
        start = src.index('HTML = r"""') + len('HTML = r"""')
        end   = src.rindex('"""', start)
        html  = src[start:end]
        # Fix API paths: ensure /api/hc/ prefix (avoid double-prefix)
        for ep in ["pick","analyze","crack","stop","log","running","status"]:
            # Replace /api/ep -> /api/hc/ep (only if not already /api/hc/)
            html = html.replace(f"'/api/hc/{ep}'",  f"__SAFE_{ep}__")
            html = html.replace(f'"/api/hc/{ep}"',  f'__SAFE2_{ep}__')
            html = html.replace(f"'/api/hc/{ep}?",  f'__SAFE3_{ep}__')
            html = html.replace(f'"/api/hc/{ep}?',  f'__SAFE4_{ep}__')
            html = html.replace(f"'/api/{ep}'",      f"'/api/hc/{ep}'")
            html = html.replace(f'"/api/{ep}"',      f'"/api/hc/{ep}"')
            html = html.replace(f"'/api/{ep}?",      f"'/api/hc/{ep}?")
            html = html.replace(f'"/api/{ep}?',      f'"/api/hc/{ep}?')
            html = html.replace(f"__SAFE_{ep}__",   f"'/api/hc/{ep}'")
            html = html.replace(f"__SAFE2_{ep}__",  f'"/api/hc/{ep}"')
            html = html.replace(f"__SAFE3_{ep}__",  f"'/api/hc/{ep}?")
            html = html.replace(f"__SAFE4_{ep}__",  f'"/api/hc/{ep}?')
        from flask import make_response
        return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})
    except Exception as e:
        return f"Error loading wificracker: {e}", 500

@app.route("/api/hc/status")
def hc_status():
    return jsonify({
        "hashcat": os.path.exists(HASHCAT_EXE),
        "rockyou": os.path.exists(WORDLIST),
        "hcxtool": True,
        "tshark":  os.path.exists(TSHARK),
        "base":    BASE,
    })

@app.route("/api/hc/pick")
def hc_pick():
    script="import tkinter as tk\nfrom tkinter import filedialog\nroot=tk.Tk();root.withdraw();root.wm_attributes('-topmost',True)\np=filedialog.askopenfilename(title='Select PCAP',filetypes=[('PCAP','*.pcap *.pcapng *.cap'),('All','*.*')])\nprint(p or '',end='')\n"
    tmp=os.path.join(BASE,"_picker.py")
    with open(tmp,"w") as f: f.write(script)
    try: r=subprocess.run(["python",tmp],capture_output=True,text=True,timeout=60); path=r.stdout.strip()
    except: path=""
    finally:
        try: os.remove(tmp)
        except: pass
    return jsonify({"path":path})

@app.route("/api/hc/analyze", methods=["POST"])
def hc_analyze():
    data=request.json or {}
    pcap=data.get("pcap","").strip()
    if not pcap or not os.path.exists(pcap):
        return jsonify({"ok":False,"error":f"File not found: {pcap}"})
    _log.clear()
    log("sys",f"TARGET  {Path(pcap).name}")
    log("sys",f"SIZE    {os.path.getsize(pcap)//1024} KB")
    result={"ok":True,"eapol":0,"ssid":"—","bssid":"—","ssids":[],"hash_file":None,"error":None}
    if os.path.exists(TSHARK):
        try:
            r=subprocess.run([TSHARK,"-r",pcap,"-Y","eapol","-T","fields","-e","frame.number"],
                             capture_output=True,text=True,timeout=30)
            lines=[l for l in r.stdout.strip().split("\n") if l.strip()]
            result["eapol"]=len(lines)
            log("ok",f"EAPOL   {result['eapol']} frames")
        except Exception as e: log("warn","tshark: "+str(e))
    stem=Path(pcap).stem
    out_file=os.path.join(HASHES_DIR,f"{stem}.hc22000")
    # Always delete old hash file before fresh conversion
    if os.path.exists(out_file):
        try: os.remove(out_file)
        except: pass
    log("sys","converting via WSL hcxpcapngtool...")
    try:
        with open(pcap,"rb") as fh: pcap_bytes=fh.read()
        # Write pcap into WSL /tmp via stdin (the default WSL distro's /mnt/c
        # doesn't map to the real Windows C: drive, so file paths must stay in /tmp)
        subprocess.run(["wsl","sh","-c","cat > /tmp/wc_in.pcap"],
                       input=pcap_bytes, timeout=60)
        log("dim",f"wrote {len(pcap_bytes)//1024} KB to /tmp/wc_in.pcap")
        # Convert — output stays in /tmp
        r=subprocess.run(["wsl","sh","-c",
            "rm -f /tmp/wc_out.hc22000 && hcxpcapngtool --all -o /tmp/wc_out.hc22000 /tmp/wc_in.pcap 2>&1"],
            capture_output=True, text=True, timeout=120)
        out_all=(r.stdout+r.stderr).strip()
        # Read result back from /tmp
        r2=subprocess.run(["wsl","cat","/tmp/wc_out.hc22000"],
                          capture_output=True, text=True, timeout=30)
        hash_content=r2.stdout.strip()
        if hash_content:
            with open(out_file,"w",encoding="utf-8") as fh:
                fh.write(hash_content+"\n")
        # Log filtered hcxpcapngtool output
        _hcx_keep = ("packets inside","ESSID","BEACON","EAPOL","pairs","written",
                     "Warning","Error","converted","session summary")
        _hcx_skip = ("Information:","https://","http://","recommended","attempt",
                     "overcome","libpcap","widely","radiotap is","de facto",
                     "standard for","mechanism to","supply additional",
                     "Duration was","It always","That makes","An undirected",
                     "captures file was","filter options","nonce-error")
        for line in out_all.splitlines():
            s = line.strip()
            if not s or s == "-"*20: continue
            if any(s.startswith(sk) or sk in s for sk in _hcx_skip): continue
            if any(k.lower() in s.lower() for k in _hcx_keep):
                log("dim", s)
        # Fallback: parse EAPOL count from hcxpcapngtool's own summary
        m=re.search(r"(\d+)\s+(?:unique\s+)?EAPOL", out_all, re.I)
        if m and result["eapol"]==0: result["eapol"]=int(m.group(1))
        if os.path.exists(out_file) and os.path.getsize(out_file)>0:
            with open(out_file,encoding="utf-8") as fh:
                lines=[l.strip() for l in fh if l.startswith("WPA*")]
            if lines:
                result["hash_file"]=out_file
                eapol_l=[l for l in lines if l.startswith("WPA*02")]
                if result["eapol"]==0: result["eapol"]=len(eapol_l)
                # Collect all SSIDs with counts and BSSIDs
                ssid_map={}  # ssid -> {count, bssid}
                for l in lines:
                    try:
                        p=l.split("*")
                        if len(p)>5 and p[5]:
                            s=bytes.fromhex(p[5]).decode("utf-8","replace")
                            if s not in ssid_map:
                                ssid_map[s]={"count":0,"bssid":":".join(p[3][i:i+2] for i in range(0,12,2))}
                            ssid_map[s]["count"]+=1
                    except: pass
                if ssid_map:
                    # Sort by count descending
                    sorted_ssids=sorted(ssid_map.items(),key=lambda x:-x[1]["count"])
                    result["ssid"]=sorted_ssids[0][0]
                    result["bssid"]=sorted_ssids[0][1]["bssid"]
                    result["ssids"]=[{"ssid":s,"bssid":d["bssid"],"count":d["count"]} for s,d in sorted_ssids]
                log("ok",f"converted: {len(lines)} hash lines, {len(eapol_l)} EAPOL")
            else: result["error"]="hash empty"; log("warn","no WPA* lines in hash file")
        else: result["error"]="hash empty"; log("warn","hash file empty — no EAPOL/PMKID captured")
    except Exception as e: result["error"]=str(e); log("err",str(e))
    return jsonify(result)

@app.route("/api/hc/crack", methods=["POST"])
def hc_crack():
    global _proc
    data=request.json or {}
    hf=data.get("hash_file","").strip()
    if not hf or not os.path.exists(hf): return jsonify({"ok":False,"error":"hash file not found"})
    all_wl=[]
    for fn in os.listdir(BASE):
        if fn.lower().endswith(".txt") and fn.lower()!="show.log":
            fp=os.path.join(BASE,fn)
            if fn.lower()=="rockyou.txt": all_wl.insert(0,fp)
            else: all_wl.append(fp)
    if not all_wl: return jsonify({"ok":False,"error":"no wordlists"})
    cracked=hf.replace(".hc22000","_cracked.txt")
    log("sys","hashcat start"); log("dim",f"wordlists: {len(all_wl)}")
    def run():
        global _proc, _password, _running, _stop_requested
        _running = True
        _stop_requested = False
        try:
            # Check potfile first — log found passwords but DON'T stop
            found_in_pot = []
            try:
                show0=subprocess.run([HASHCAT_EXE,"-m","22000",hf,"--show"],
                    capture_output=True,text=True,cwd=BASE)
                for line in show0.stdout.strip().splitlines():
                    line=line.strip()
                    if line.count(":")>=4:
                        pwd=line.rsplit(":",1)[-1]
                        if pwd and pwd not in found_in_pot:
                            found_in_pot.append(pwd)
                            _password=pwd
                            log("result",f"PASSWORD FOUND: {pwd}")
                            log("sys","(from potfile)")
            except: pass

            # Always run hashcat — it will skip already-cracked hashes
            # and crack remaining ones
            found=False
            for wl in all_wl:
                if found or _stop_requested:
                    if _stop_requested: log("warn","stopped by user")
                    break
                log("sys",f"trying: {Path(wl).name}")
                cmd=[HASHCAT_EXE,"-m","22000",hf,wl,"--status","--status-timer=4","--force","-o",cracked]
                try:
                    _proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,cwd=BASE)
                    for line in _proc.stdout:
                        if _stop_requested:
                            _proc.terminate()
                            break
                        log_hc(line.rstrip())
                    _proc.wait()
                    # Wait for lock files to be released
                    import glob, time as _t
                    for _ in range(20):
                        locks=glob.glob(os.path.join(BASE,"hashcat.pid"))+glob.glob(os.path.join(BASE,"*.pid"))
                        if not locks: break
                        _t.sleep(0.3)
                except Exception as e: log("err",str(e)); break
                if _stop_requested:
                    log("warn","stopped by user")
                    break
                # Check --show after each wordlist
                try:
                    show=subprocess.run([HASHCAT_EXE,"-m","22000",hf,"--show"],
                        capture_output=True,text=True,cwd=BASE)
                    for line in show.stdout.strip().splitlines():
                        line=line.strip()
                        if line.count(":")>=4:
                            pwd=line.rsplit(":",1)[-1]
                            if pwd and pwd not in found_in_pot:
                                found_in_pot.append(pwd)
                                _password=pwd
                                log("result",f"PASSWORD FOUND: {pwd}")
                                found=True
                except: pass
                if not found: log("warn",f"not found in {Path(wl).name}")
            if not found_in_pot: log("warn","exhausted all wordlists")
            log("sys","hashcat done")
        finally:
            _running = False
    threading.Thread(target=run,daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/hc/stop", methods=["POST"])
def hc_stop():
    global _proc, _stop_requested
    _stop_requested = True
    if _proc and _proc.poll() is None: _proc.terminate(); log("warn","terminated")
    return jsonify({"ok":True})

@app.route("/api/hc/log")
def hc_log():
    since=int(request.args.get("since",0))
    return jsonify({"entries":_log[since:],"total":len(_log)})

@app.route("/api/hc/running")
def hc_running():
    running = _running or bool(_proc and _proc.poll() is None)
    return jsonify({"running": running, "password": _password})

# ════ OSINT BACKEND SUBPROCESS ══════════════════════════════


def start_wificrack():
    """Start wificrack on port 5555"""
    try:
        import socket
        s = socket.socket()
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", 5555)) != 0:
            subprocess.Popen(
                ["python", r"C:\HashCat\hashcat-7.1.2\wifi_cracker.py"],
                cwd=r"C:\HashCat\hashcat-7.1.2",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        s.close()
    except: pass

# ════ FLIPPER ZERO ══════════════════════════════════════════

_SERIAL_OK = False
try:
    import serial, serial.tools.list_ports; _SERIAL_OK = True
except ImportError:
    try:
        subprocess.run([sys.executable,"-m","pip","install","pyserial"],
                       capture_output=True,timeout=60)
        import serial, serial.tools.list_ports; _SERIAL_OK = True
    except: pass

import time as _tm

# ─── Minimal Protobuf encoder/decoder ──────────────────────
def _vi_enc(n):
    buf=[]
    while True:
        b=n&0x7F; n>>=7
        if n: buf.append(b|0x80)
        else: buf.append(b); break
    return bytes(buf)

def _vi_dec(d,p=0):
    r,s=0,0
    while p<len(d):
        b=d[p]; p+=1; r|=(b&0x7F)<<s; s+=7
        if not(b&0x80): break
    return r,p

def _pb_parse(data):
    out={}; pos=0
    while pos<len(data):
        try: tag,pos=_vi_dec(data,pos)
        except: break
        fn=tag>>3; wt=tag&7
        try:
            if wt==0: v,pos=_vi_dec(data,pos); out.setdefault(fn,[]).append(v)
            elif wt==2:
                l,pos=_vi_dec(data,pos)
                out.setdefault(fn,[]).append(data[pos:pos+l]); pos+=l
            elif wt==1: out.setdefault(fn,[]).append(data[pos:pos+8]); pos+=8
            elif wt==5: out.setdefault(fn,[]).append(data[pos:pos+4]); pos+=4
            else: break
        except: break
    return out

def _pb_vi(fn,v): return _vi_enc((fn<<3)|0)+_vi_enc(v)
def _pb_b(fn,b):  return _vi_enc((fn<<3)|2)+_vi_enc(len(b))+b
def _pb_s(fn,s):  return _pb_b(fn,s.encode('utf-8'))

def _flip_read_msg(cmd_id, path):
    # storage_read_request = field 13 in flipper.proto Main message
    inner = _pb_s(1, path)                           # ReadRequest.path = field 1
    body  = _pb_vi(1, cmd_id) + _pb_vi(2, 0) + _pb_b(13, inner)  # field 13!
    return _vi_enc(len(body)) + body

def _flip_serial_recv(s):
    l=0; sh=0
    for _ in range(10):
        b=s.read(1)
        if not b: raise TimeoutError("timeout varint")
        byte=b[0]; l|=(byte&0x7F)<<sh; sh+=7
        if not(byte&0x80): break
    data=b""; t=_tm.time()+20
    while len(data)<l:
        c=s.read(l-len(data))
        if c: data+=c
        elif _tm.time()>t: raise TimeoutError("timeout body")
    return data

def _flipper_read_binary(port, path):
    """Read binary file from Flipper via serial CLI with exact byte count.

    The Flipper re-prints its full CLI banner on every fresh serial connect,
    and the timing of that banner relative to our flush is not deterministic —
    so instead of blindly skipping a fixed number of header lines, scan the
    response text for the authoritative "Size: N" line and start the binary
    read right after it, however much banner/echo noise precedes it.
    """
    with serial.Serial(port, 115200, timeout=3) as s:
        _tm.sleep(0.8)           # let the banner start arriving
        s.reset_input_buffer()   # discard whatever banner text arrived so far
        s.write(f"storage read {path}\r\n".encode())

        size = None
        line = b""
        deadline = _tm.time() + 10
        while size is None and _tm.time() < deadline:
            b = s.read(1)
            if not b:
                continue
            line += b
            if b == b"\n":
                m = re.search(r"Size:\s*(\d+)", line.decode("utf-8", errors="replace"), re.I)
                if m: size = int(m.group(1))
                line = b""
        if size is None:
            raise RuntimeError("could not find 'Size:' line in storage read response")

        # Now read exactly `size` bytes of raw binary content
        s.timeout = 30
        data = b""
        deadline = _tm.time() + 120
        while len(data) < size and _tm.time() < deadline:
            chunk = s.read(min(size - len(data), 4096))
            if chunk: data += chunk
        if len(data) < size:
            raise RuntimeError(f"incomplete: got {len(data)}/{size} bytes")
        return data

def _flip_rpc(port, msg_bytes, timeout=60):
    with serial.Serial(port,115200,timeout=2) as s:
        # Wake CLI with blank line, then start RPC
        _tm.sleep(0.1); s.reset_input_buffer()
        s.write(b"\r\n"); _tm.sleep(0.3); s.read_all()
        s.write(b"start_rpc_session\r"); _tm.sleep(1.5)
        s.read_all()  # discard echo
        s.write(msg_bytes); s.flush()
        resps=[]; deadline=_tm.time()+timeout
        while _tm.time()<deadline:
            try:
                data=_flip_serial_recv(s); resps.append(data)
                f=_pb_parse(data)
                if not bool(f.get(2,[0])[0]): break
            except: break
    return resps

# ─── Flipper endpoints ──────────────────────────────────────
@app.route("/api/flipper/ports")
def flipper_ports():
    if not _SERIAL_OK: return jsonify({"ok":False,"error":"run: pip install pyserial","ports":[]})
    try:
        ports=[]
        for p in serial.tools.list_ports.comports():
            desc=(p.description or "").strip(); mfr=(p.manufacturer or "").strip()
            likely=any(x in (desc+mfr).lower() for x in ["flipper","stm32 virtual","stm32 usb","0483:5740","usb serial","usb-serial"])
            ports.append({"port":p.device,"desc":desc,"mfr":mfr,"likely":likely})
        ports.sort(key=lambda x:(0 if x["likely"] else 1,x["port"]))
        return jsonify({"ok":True,"ports":ports})
    except Exception as e: return jsonify({"ok":False,"error":str(e),"ports":[]})

@app.route("/api/flipper/list")
def flipper_list_ep():
    if not _SERIAL_OK: return jsonify({"ok":False,"error":"pyserial not installed","files":[]})
    port=request.args.get("port","").strip()
    path=request.args.get("path","/ext").strip()
    if not port: return jsonify({"ok":False,"error":"no port","files":[]})
    try:
        with serial.Serial(port,115200,timeout=2) as s:
            _tm.sleep(0.8); s.reset_input_buffer()
            s.write(f"storage list {path}\r\n".encode())
            _tm.sleep(2.5); raw=s.read_all().decode("utf-8",errors="replace")
        files=[]; seen=set()
        for line in raw.splitlines():
            t=line.strip()
            if t.startswith("[F]"):
                p2=t.split(); nm=p2[1] if len(p2)>1 else ""
                if not nm or nm in seen: continue; seen.add(nm)
                try: sz=int(p2[2].rstrip("b")) if len(p2)>2 else 0
                except: sz=0
                files.append({"name":nm,"size":sz,"is_dir":False})
            elif t.startswith("[D]"):
                p2=t.split(); nm=p2[1] if len(p2)>1 else ""
                if not nm or nm in seen: continue; seen.add(nm)
                files.append({"name":nm,"size":0,"is_dir":True})
        files.sort(key=lambda x:(0 if x["is_dir"] else 1,x["name"].lower()))
        return jsonify({"ok":True,"files":files,"path":path})
    except Exception as e: return jsonify({"ok":False,"error":str(e),"files":[]})

@app.route("/api/flipper/readtest")
def flipper_readtest():
    """Debug: show first 200 bytes after storage read command"""
    port=request.args.get("port","").strip()
    path=request.args.get("path","").strip()
    if not port or not path: return jsonify({"ok":False,"error":"missing params"}),400
    try:
        with serial.Serial(port,115200,timeout=3) as s:
            _tm.sleep(0.8); s.reset_input_buffer()
            s.write(f"storage read {path}\r\n".encode())
            _tm.sleep(3.0)
            raw=s.read_all()
        return jsonify({
            "ok":True,
            "total_bytes":len(raw),
            "hex_first_100":raw[:100].hex(),
            "text_first_200":raw[:200].decode('utf-8','replace')
        })
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/api/flipper/stat")
def flipper_stat_ep():
    """Debug: show raw storage stat output"""
    port=request.args.get("port","").strip()
    path=request.args.get("path","").strip()
    if not port or not path: return jsonify({"ok":False,"error":"missing params"}),400
    try:
        with serial.Serial(port,115200,timeout=3) as s:
            _tm.sleep(0.1); s.reset_input_buffer()
            s.write(f"storage stat {path}\r\n".encode())
            _tm.sleep(1.5)
            raw=s.read_all()
        text=raw.decode('utf-8',errors='replace')
        return jsonify({"ok":True,"raw":text,"lines":text.splitlines()})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/api/flipper/save")
def flipper_save_ep():
    if not _SERIAL_OK: return jsonify({"ok":False,"error":"pyserial not installed"}),400
    port=request.args.get("port","").strip()
    path=request.args.get("path","").strip()
    if not port or not path: return jsonify({"ok":False,"error":"missing params"}),400
    try:
        filename=path.split("/")[-1]
        local_path=os.path.join(TEMP_DIR, filename)
        data=_flipper_read_binary(port, path)
        with open(local_path,"wb") as f: f.write(data)
        return jsonify({"ok":True,"local_path":local_path,"filename":filename,"size":len(data)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.route("/api/flipper/file")
def flipper_file_ep():
    """Serve temp file to browser and delete it after."""
    filename = request.args.get("name","").strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return ("bad request", 400)
    path = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(path):
        return ("not found", 404)
    from flask import send_file
    import io
    with open(path, "rb") as f:
        data = f.read()
    try: os.remove(path)
    except: pass
    return send_file(io.BytesIO(data), as_attachment=True, download_name=filename, mimetype="application/octet-stream")

@app.route("/api/flipper/download")
def flipper_download_ep():
    if not _SERIAL_OK: return jsonify({"ok":False,"error":"pyserial not installed"}),400
    port=request.args.get("port","").strip()
    path=request.args.get("path","").strip()
    if not port or not path: return jsonify({"ok":False,"error":"missing params"}),400
    try:
        import io; from flask import send_file
        filename=path.split("/")[-1]
        data=_flipper_read_binary(port, path)
        return send_file(io.BytesIO(data),as_attachment=True,
                        download_name=filename,mimetype="application/octet-stream")
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

# ════ DOWNLOADER API (yt-dlp) ════════════════════════════════

@app.route("/api/dl/info", methods=["POST"])
def dl_info():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel"),
            "extractor": info.get("extractor_key"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/dl/download", methods=["POST"])
def dl_download():
    data = request.json or {}
    url  = (data.get("url") or "").strip()
    mode = data.get("mode", "video")
    if not url:
        return jsonify({"error": "URL required"}), 400
    import yt_dlp, uuid
    out_id = uuid.uuid4().hex
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "outtmpl": os.path.join(TEMP_DIR, out_id + ".%(ext)s"),
    }
    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    else:
        opts["format"] = "bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        if mode == "audio":
            filename = os.path.splitext(filename)[0] + ".mp3"
        if not os.path.exists(filename):
            base = os.path.splitext(filename)[0]
            for ext in ("mp4", "mkv", "webm", "mp3", "m4a"):
                cand = base + "." + ext
                if os.path.exists(cand):
                    filename = cand
                    break
        return jsonify({"ok": True, "file": os.path.basename(filename), "title": info.get("title")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/dl/file")
def dl_file_ep():
    """Serve a downloaded file from TEMP_DIR and delete it after."""
    filename = request.args.get("name", "").strip()
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return ("bad request", 400)
    path = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(path):
        return ("not found", 404)
    import io
    from flask import send_file
    with open(path, "rb") as f:
        data = f.read()
    try: os.remove(path)
    except: pass
    return send_file(io.BytesIO(data), as_attachment=True, download_name=filename, mimetype="application/octet-stream")

# ════ GEOINT API ═════════════════════════════════════════════

def _gps_to_deg(value, ref):
    d, m, s = [float(x) for x in value]
    deg = d + m / 60.0 + s / 3600.0
    if ref in ("S", "W"):
        deg = -deg
    return deg

def reverse_geocode(lat, lon, lang="ru"):
    """Free reverse geocoding via Nominatim (OpenStreetMap) — no API key required."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "json", "lat": lat, "lon": lon, "zoom": 18, "accept-language": lang},
            headers={"User-Agent": "KikiHub-GEOINT/1.0"},
            timeout=8,
        )
        data = r.json()
        return data.get("display_name")
    except Exception:
        return None

def geocode_place(query, lang="ru"):
    """Free forward geocoding via Nominatim — turns a place name into coordinates."""
    if not query:
        return None

    def _try(q):
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 1, "accept-language": lang},
            headers={"User-Agent": "KikiHub-GEOINT/1.0"},
            timeout=12,
        )
        results = r.json()
        if results:
            item = results[0]
            return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name")}
        return None

    try:
        result = _try(query)
        if result:
            return result
        # Retry with just the first segment (city name, drop country/region)
        parts = [p.strip() for p in query.split(",")]
        if len(parts) > 1:
            result = _try(parts[0])
            if result:
                return result
    except Exception as e:
        import sys
        print(f"[geocode_place] error for {query!r}: {e}", file=sys.stderr)
    return None

def _exif_full_dict(img):
    """Extract all human-readable EXIF tags (excluding raw GPS IFD)."""
    from PIL.ExifTags import TAGS, GPSTAGS
    exif = img.getexif()
    if not exif:
        return {}
    data = {}
    for tag_id, value in exif.items():
        tag = TAGS.get(tag_id, str(tag_id))
        if tag in ("GPSInfo",):
            continue
        if tag_id in (0x8769, 0x8825):  # ExifOffset / GPSInfo IFD pointers
            continue
        if isinstance(value, bytes):
            try:
                value = value.decode(errors="replace")
            except Exception:
                value = str(value)
        data[tag] = str(value)
    try:
        exif_ifd = exif.get_ifd(0x8769)
        for tag_id, value in exif_ifd.items():
            tag = TAGS.get(tag_id, str(tag_id))
            if isinstance(value, bytes):
                try:
                    value = value.decode(errors="replace")
                except Exception:
                    value = str(value)
            data[tag] = str(value)
    except Exception:
        pass
    try:
        gps_ifd = exif.get_ifd(0x8825)
        for tag_id, value in gps_ifd.items():
            tag = GPSTAGS.get(tag_id, str(tag_id))
            data["GPS" + tag if not tag.startswith("GPS") else tag] = str(value)
    except Exception:
        pass
    return data

def _parse_ai_json(text):
    """Best-effort parse of a JSON object out of an LLM response, stripping ```json fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        data = json.loads(text)
        return {
            "location": str(data.get("location") or "").strip(),
            "confidence": str(data.get("confidence") or "").strip(),
            "reasoning": str(data.get("reasoning") or "").strip(),
            "alternatives": str(data.get("alternatives") or "").strip(),
        }
    except Exception:
        return {"location": "", "confidence": "", "reasoning": text, "alternatives": ""}

def geoint_ai_guess(image_bytes, config, ai_lang="ru"):
    lang_instruction = {
        "ru": "Отвечай строго на русском языке.",
        "en": "Respond strictly in English.",
    }.get(ai_lang, "Respond strictly in English.")
    prompt = (
        "You are a GEOINT analyst. Look at this photo and identify the most likely location "
        "based on visible clues: architecture, signage and language, vegetation, terrain, "
        "vehicles/license plates, road markings, climate, and any other geographic indicators. "
        "Be as specific as you can, but be honest about uncertainty.\n\n"
        "Respond with ONLY a raw JSON object (no markdown formatting, no code fences, no "
        "asterisks or bullet points anywhere) with exactly these fields:\n"
        '- "location": your single best-guess location, as a short geocodable place name '
        '(e.g. "Belgrade, Serbia"). If you cannot narrow it down to a specific place, use the '
        'broadest area you are confident about (e.g. "Balkans" or "Southeast Asia"). Empty '
        "string if you have no guess at all.\n"
        '- "confidence": one of "high", "medium", "low".\n'
        '- "reasoning": plain flowing text (no markdown, no lists) explaining which visual '
        "clues led to this guess.\n"
        '- "alternatives": plain text naming other plausible locations, phrased like "or '
        'possibly X" / "or somewhere nearby" — empty string if you have no other candidates.\n\n'
        f"{lang_instruction}"
    )
    if config["provider"] == "anthropic":
        import anthropic, base64
        client = anthropic.Anthropic(api_key=config["api_key"])
        b64 = base64.b64encode(image_bytes).decode()
        msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=700,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt}]}])
        return _parse_ai_json(msg.content[0].text)

    if config["provider"] == "gemini":
        from google import genai
        from google.genai import types
        try:
            client = genai.Client(api_key=config["api_key"])
            resp = client.models.generate_content(model="gemini-2.5-flash",
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "location": types.Schema(type=types.Type.STRING),
                            "confidence": types.Schema(type=types.Type.STRING),
                            "reasoning": types.Schema(type=types.Type.STRING),
                            "alternatives": types.Schema(type=types.Type.STRING),
                        },
                        required=["location", "confidence", "reasoning", "alternatives"],
                    ),
                ))
            return _parse_ai_json(resp.text)
        except Exception as e:
            msg = str(e)
            is_ru = ai_lang == "ru"
            if "getaddrinfo failed" in msg or "11001" in msg:
                raise RuntimeError(
                    "Gemini API недоступен: ошибка DNS (generativelanguage.googleapis.com). Проверь интернет/VPN — в РФ доступ к Google API часто требует VPN."
                    if is_ru else
                    "Gemini API unreachable: DNS error (generativelanguage.googleapis.com). Check your internet/VPN — Google API access often requires a VPN from some regions."
                )
            if "User location is not supported" in msg or "FAILED_PRECONDITION" in msg:
                raise RuntimeError(
                    "Gemini API недоступен из текущего региона (User location is not supported). Нужен VPN с выходом за пределы РФ."
                    if is_ru else
                    "Gemini API unavailable in your region (User location is not supported). A VPN exiting outside the blocked region is required."
                )
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                raise RuntimeError(
                    "Gemini API: превышен лимит запросов (бесплатный тариф). Подожди немного и попробуй снова."
                    if is_ru else
                    "Gemini API: rate limit exceeded (free tier). Wait a bit and try again."
                )
            raise

    raise RuntimeError("No AI provider configured — set an API key in Settings")

@app.route("/api/geoint/analyze", methods=["POST"])
def geoint_analyze():
    if "photo" not in request.files:
        return jsonify({"error": "no file"}), 400
    image_bytes = request.files["photo"].read()
    result = {}

    ai_lang = request.form.get("ai_lang", "ru")

    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS
        import io
        img = Image.open(io.BytesIO(image_bytes))
        exif = img.getexif()
        result["exif_data"] = _exif_full_dict(img)
        gps_ifd = exif.get_ifd(0x8825) if exif else None
        if gps_ifd:
            gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
            if "GPSLatitude" in gps and "GPSLongitude" in gps:
                result["lat"] = _gps_to_deg(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
                result["lon"] = _gps_to_deg(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
                result["address"] = reverse_geocode(result["lat"], result["lon"], ai_lang)
    except Exception as e:
        result["exif_error"] = str(e)

    if AI_CONFIG.get("provider") and AI_CONFIG.get("api_key"):
        try:
            ai_guess = geoint_ai_guess(image_bytes, AI_CONFIG, ai_lang)
            result["ai_guess"] = ai_guess
            if ai_guess.get("location"):
                try:
                    loc = geocode_place(ai_guess["location"], ai_lang)
                    if loc:
                        result["ai_location"] = loc
                    else:
                        result["ai_location_error"] = "Nominatim returned no results for: " + ai_guess["location"]
                except Exception as ge:
                    result["ai_location_error"] = str(ge)
        except Exception as e:
            result["ai_error"] = str(e)

    return jsonify(result)


def _deg_to_dms_rational(deg_float):
    deg_float = abs(deg_float)
    degrees = int(deg_float)
    minutes_float = (deg_float - degrees) * 60
    minutes = int(minutes_float)
    seconds_float = (minutes_float - minutes) * 60
    seconds = int(round(seconds_float * 100))
    return [(degrees, 1), (minutes, 1), (seconds, 100)]


@app.route("/api/geoint/spoof", methods=["POST"])
def geoint_spoof():
    if "photo" not in request.files:
        return jsonify({"error": "no file"}), 400
    import io
    import piexif
    from PIL import Image

    image_bytes = request.files["photo"].read()
    strip = request.form.get("strip") == "1"

    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        if strip:
            exif_bytes = piexif.dump({"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}})
        else:
            try:
                exif_dict = piexif.load(image_bytes)
            except Exception:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}}

            lat = request.form.get("lat", "").strip()
            lon = request.form.get("lon", "").strip()
            make = request.form.get("make", "").strip()
            model = request.form.get("model", "").strip()
            dt = request.form.get("datetime", "").strip()

            if lat and lon:
                lat_f, lon_f = float(lat), float(lon)
                exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = _deg_to_dms_rational(lat_f)
                exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = "N" if lat_f >= 0 else "S"
                exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = _deg_to_dms_rational(lon_f)
                exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = "E" if lon_f >= 0 else "W"
            if make:
                exif_dict["0th"][piexif.ImageIFD.Make] = make.encode("utf-8")
            if model:
                exif_dict["0th"][piexif.ImageIFD.Model] = model.encode("utf-8")
            if dt:
                # <input type="datetime-local"> sends "YYYY-MM-DDTHH:MM" — EXIF wants "YYYY:MM:DD HH:MM:SS"
                exif_date = dt.replace("-", ":").replace("T", " ")
                if len(exif_date) == 16:
                    exif_date += ":00"
                exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_date.encode("utf-8")
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode("utf-8")

            exif_bytes = piexif.dump(exif_dict)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95, exif=exif_bytes)
        out.seek(0)
        from flask import send_file
        resp = send_file(out, mimetype="image/jpeg", as_attachment=True, download_name="spoofed.jpg")
        resp.headers["X-Filename"] = "spoofed.jpg"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unredact/marker", methods=["POST"])
def unredact_marker():
    if "photo" not in request.files:
        return jsonify({"error": "no file"}), 400
    import io as _io, base64 as _b64
    try:
        import numpy as np
        from PIL import Image, ImageEnhance
    except ImportError as e:
        return jsonify({"error": f"Missing dependency: {e}"}), 500

    image_bytes = request.files["photo"].read()
    try:
        brightness = float(request.form.get("brightness", 100))
        exposure   = float(request.form.get("exposure",   100))
        contrast   = float(request.form.get("contrast",   -56))
        shadows    = float(request.form.get("shadows",    -50))
        color      = float(request.form.get("color",      100))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid params"}), 400

    try:
        img = Image.open(_io.BytesIO(image_bytes))
        if img.mode not in ("RGB",):
            img = img.convert("RGB")

        arr = np.array(img, dtype=np.float32)

        # Exposure: multiplicative EV shift. 0=1×, ±100=4×/0.25×
        if exposure != 0:
            arr = arr * (2.0 ** (exposure / 50.0))

        # Brightness: additive lift (0=no change, +100=+128, -100=-128)
        if brightness != 0:
            arr = arr + (brightness / 100.0) * 128.0

        # Shadows: adjust dark pixels (< 128). Negative = deepen, positive = lift.
        if shadows != 0:
            s = shadows / 100.0
            mask = arr < 128.0
            arr[mask] = arr[mask] + s * (128.0 - arr[mask])

        arr = np.clip(arr, 0, 255)
        img = Image.fromarray(arr.astype(np.uint8))

        # Contrast: factor 0..2 (0=grey, 1=original, 2=double)
        if contrast != 0:
            img = ImageEnhance.Contrast(img).enhance(max(0.0, 1.0 + contrast / 100.0))

        # Color saturation: 0=greyscale, 1=original, 2=double
        if color != 0:
            img = ImageEnhance.Color(img).enhance(max(0.0, 1.0 + color / 100.0))

        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        b64 = _b64.b64encode(buf.getvalue()).decode()
        return jsonify({"ok": True, "image": b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__=="__main__":
    print("\n  KikiHub  ->  http://localhost:7777\n")
    # Try to start OSINT backend
    app.run(host="127.0.0.1", port=7777, debug=False, threaded=True)
