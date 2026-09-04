
#!/usr/bin/env python3
"""KikiHub — unified app: Kiki OSINT + WiFi Cracker. Port 7777"""
import os, sys, shutil, subprocess, threading, re, json, queue, asyncio, importlib.util, logging
from pathlib import Path
from datetime import datetime

# Windows' default console/text-mode encoding is the legacy ANSI codepage
# (cp1252), not UTF-8. maigret's scan touches non-ASCII site names/response
# text constantly (Baidu, VK, etc.) — anything downstream that writes text
# without an explicit encoding (including CPython's own default text mode)
# throws UnicodeEncodeError the first time a scan hits one, killing it mid-run.
# errors='replace' means a stray character gets substituted instead of
# crashing. stdout/stderr can be None in the frozen build (desktop.py detaches
# the console via FreeConsole()), hence the guard.
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import requests
import keys_store

# maigret and its heavy async/network deps (aiodns, aiohttp_socks,
# cloudscraper) used to be imported here at module load — meaning every
# single app launch paid that cost even if the user never opens the OSINT
# tab that session. Deferred into search_maigret()/_get_maigret_notify_cls()
# instead, so it's only paid the first time a username search actually runs.

# maigret logs per-site debug/error lines through this logger as it scans —
# we get results via the QueryNotify callback instead, so the log output
# itself is never read. Left unconfigured it falls back to Python's lastResort
# handler, which writes to stderr using the process's default console
# encoding — cp1252 on Windows, which throws on non-ASCII site names/URLs and
# kills the scan mid-run. NullHandler + propagate=False means it never tries
# to write anywhere, so it can't hit that.
_maigret_logger = logging.getLogger("maigret")
_maigret_logger.addHandler(logging.NullHandler())
_maigret_logger.propagate = False

# generativelanguage.googleapis.com sometimes fails to resolve via Python's own
# socket.getaddrinfo() even when the OS resolver (nslookup) works fine and a VPN
# is correctly routing everything else. xbox-dns.ru is a "smart DNS" whose own
# gateway for this host resolves to a Russia-hosted IP (confirmed via geoip:
# St. Petersburg / Selectel, AS49505) — it can never dodge Gemini's region
# check, it can only fix "doesn't resolve at all". So it's last-resort only:
#   1. socket.getaddrinfo() — works when nothing is interfering with Python's resolver.
#   2. shell out to `nslookup` — respects the OS/VPN's actual routing and gives
#      back a real global Google IP, so a VPN's tunnel correctly carries the
#      resulting connection (confirmed: a browser under the same VPN reaches
#      Gemini fine, proving the VPN itself isn't the problem).
#   3. xbox-dns.ru DoH — last resort for genuine RU ISP-level DNS poisoning with
#      no VPN at all; routes through a RU gateway, so it WILL still trip the
#      region check — it only helps when resolution itself was failing outright.
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

# AI runtime config (updated via /api/keys and /api/config). Mistral is
# preferred first — it's the only one of these confirmed to work without a
# VPN from Russia, unlike Gemini (geo-blocked) and Anthropic (also blocked).
def _bootstrap_ai_config():
    for provider, field in (("mistral", "MISTRAL_API_KEY"), ("gemini", "GEMINI_API_KEY"),
                             ("anthropic", "ANTHROPIC_API_KEY"), ("deepseek", "DEEPSEEK_API_KEY")):
        key = keys_store.get(field)
        if key:
            return {"provider": provider, "api_key": key}
    return {"provider": None, "api_key": None}

AI_CONFIG = _bootstrap_ai_config()

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
HUB_DIR    = BASE_DIR
OSINT_FE   = os.path.join(BASE_DIR, "frontend")
OSINT_SRC  = os.path.join(BASE_DIR, "sources")

HASHES_DIR    = os.path.join(BASE_DIR, "hashes")
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
TEMP_DIR      = os.path.join(BASE_DIR, "temp")
os.makedirs(HASHES_DIR, exist_ok=True)

# ════ EXTERNAL TOOL PATHS (hashcat / rockyou.txt / tshark) ═══════════════
# These used to be one hardcoded path each (C:\HashCat\hashcat-7.1.2\...,
# C:\Program Files\Wireshark\tshark.exe) — worked on the one machine that
# path was typed for, silently broken on every clean install where the user
# put hashcat somewhere else or hasn't installed Wireshark at all, with the
# only fix being to hand-edit this source file. Now: auto-detect a handful
# of common install spots + PATH, and if that comes up empty, fall back to a
# path the user picked once via Settings (persisted in keys.json through
# keys_store, same file the API keys already live in). Re-detected fresh on
# every request instead of cached at import time — a user fixing their setup
# and clicking "re-check" shouldn't need to restart the whole app.
_HASHCAT_DIR_GLOBS = [r"C:\HashCat\hashcat-*", r"C:\hashcat-*", r"C:\Program Files\hashcat*"]
_WORDLIST_CANDIDATES = [
    r"C:\HashCat\rockyou.txt", r"C:\Wordlists\rockyou.txt",
    r"C:\SecLists\Passwords\Leaked-Databases\rockyou.txt",
]
_TSHARK_CANDIDATES = [
    r"C:\Program Files\Wireshark\tshark.exe",
    r"C:\Program Files (x86)\Wireshark\tshark.exe",
]

def _find_hashcat_exe():
    import glob
    for pattern in _HASHCAT_DIR_GLOBS:
        for d in glob.glob(pattern):
            cand = os.path.join(d, "hashcat.exe")
            if os.path.isfile(cand):
                return cand
    return shutil.which("hashcat") or shutil.which("hashcat.exe")

def _find_wordlist(near=None):
    # rockyou.txt normally ships in the same folder as hashcat.exe itself
    # (that's how the hashcat/HashcatGUI installers commonly bundle it) —
    # check right next to whatever hashcat.exe was actually found before
    # falling back to the other well-known spots.
    if near:
        cand = os.path.join(os.path.dirname(near), "rockyou.txt")
        if os.path.isfile(cand):
            return cand
    for cand in _WORDLIST_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None

def _find_tshark_exe():
    for cand in _TSHARK_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return shutil.which("tshark") or shutil.which("tshark.exe")

def _resolve_tool_path(field, detector):
    """field: keys_store field holding a user-picked override.
    Returns (path_or_None, source) where source is 'manual', 'auto', or None."""
    override = keys_store.get(field)
    if override and os.path.isfile(override):
        return override, "manual"
    detected = detector()
    if detected:
        return detected, "auto"
    return None, None

def resolve_hashcat():
    return _resolve_tool_path("HASHCAT_EXE_PATH", _find_hashcat_exe)

def resolve_wordlist():
    hc_path, _ = resolve_hashcat()
    return _resolve_tool_path("WORDLIST_PATH", lambda: _find_wordlist(hc_path))

def resolve_tshark():
    return _resolve_tool_path("TSHARK_PATH", _find_tshark_exe)
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
    hashcat_exe, _ = resolve_hashcat()
    if not hashcat_exe:
        return None
    hc_dir = os.path.dirname(hashcat_exe)
    import glob
    for f in glob.glob(os.path.join(hc_dir, "show.*")):
        try: os.remove(f)
        except: pass
    try:
        r = subprocess.run(
            [hashcat_exe, "-m", "22000", hf, "--show"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=hc_dir, timeout=20)
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
        "DEEPSEEK_API_KEY":  mask(k.get("DEEPSEEK_API_KEY","")),
        "MISTRAL_API_KEY":   mask(k.get("MISTRAL_API_KEY","")),
        "HIBP_API_KEY":      mask(k.get("HIBP_API_KEY","")),
        "configured":        bool(k.get("VK_TOKEN") or k.get("GEMINI_API_KEY") or k.get("ANTHROPIC_API_KEY") or k.get("DEEPSEEK_API_KEY") or k.get("MISTRAL_API_KEY"))
    })

@app.route("/api/keys", methods=["POST"])
def save_keys_route():
    data = request.json or {}
    k = keys_store.load()
    for field in ["VK_TOKEN","GEMINI_API_KEY","ANTHROPIC_API_KEY","DEEPSEEK_API_KEY","MISTRAL_API_KEY","HIBP_API_KEY"]:
        if field in data and data[field] and "•" not in data[field]:
            k[field] = data[field]
    keys_store.save(k)
    return jsonify({"ok":True})

_PROVIDER_KEY_FIELD = {
    "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "mistral": "MISTRAL_API_KEY",
}

@app.route("/api/config", methods=["POST"])
def osint_set_config():
    data = request.json or {}
    provider = data.get("provider")
    # api_key is optional — when the caller only knows the provider (e.g. the
    # hub Settings panel, which never holds a raw key in JS after saving), we
    # resolve it from keys_store ourselves instead of requiring the frontend
    # to smuggle the raw key back out of a save it already made.
    api_key = data.get("api_key") or (keys_store.get(_PROVIDER_KEY_FIELD[provider]) if provider in _PROVIDER_KEY_FIELD else None)
    AI_CONFIG["provider"] = provider
    AI_CONFIG["api_key"]  = api_key
    return jsonify({"status": "ok", "provider": provider, "has_key": bool(api_key)})

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
    if req_query_type in ("username", "email", "phone", "ip", "both"):
        query_type = req_query_type
    elif "@" in query and "." in query.split("@")[-1]:
        query_type = "email"
    elif re.match(r'^\d{1,3}(\.\d{1,3}){3}$', query):
        query_type = "ip"
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

        elif query_type == "ip":
            yield from search_ip_info(query, send, collected)

        yield from correlate_avatars(collected, send)

        req_ai_provider = data.get("ai_provider", "")
        if not isinstance(req_ai_provider, str):
            req_ai_provider = ""
        if req_ai_provider:
            key_map = {
                "gemini":    "GEMINI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "deepseek":  "DEEPSEEK_API_KEY",
                "mistral":   "MISTRAL_API_KEY",
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


# ─── Avatar cross-correlation ──────────────────────────────
# Maigret only confirms a username exists on 500+ sites — it never tells you
# whether the "kiki_ga" on GitHub is the same human as the "kiki_ga" on VK.
# A shared (or near-identical, re-compressed/re-cropped) profile photo across
# sources is a much stronger same-person signal than a matching nickname
# alone, so every source that hands back an avatar URL gets perceptual-hashed
# and cross-checked against every other one.
AVATAR_FIELDS = {"vk": "photo", "telegram": "photo", "github": "avatar",
                  "github_email": "avatar", "gravatar": "avatar"}

REVERSE_IMAGE_ENGINES = [
    {"name": "Yandex",      "url": "https://yandex.ru/images/search?rpt=imageview&url={u}"},
    {"name": "Bing",        "url": "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIIRP&sbisrc=UrlPaste&q=imgurl:{u}"},
    {"name": "TinEye",      "url": "https://tineye.com/search?url={u}"},
    {"name": "Google Lens", "url": "https://lens.google.com/uploadbyurl?url={u}"},
]

def _avatar_phash(url, timeout=6):
    """Perceptual hash of an avatar image — small edits (recompression,
    resize, different CDN) still hash close; a different photo doesn't.
    dhash, not phash: phash needs scipy.fftpack, and scipy is excluded from
    the PyInstaller build (KikiHub.spec) to keep it slim — dhash only needs
    PIL + numpy, both already bundled."""
    try:
        import imagehash
        from PIL import Image
        import io
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.content:
            return None
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        return str(imagehash.dhash(img))
    except Exception:
        return None

def correlate_avatars(collected, send):
    candidates = []
    seen_urls = set()
    for source, field in AVATAR_FIELDS.items():
        data = collected.get(source)
        if not isinstance(data, dict):
            continue
        url = data.get(field)
        if url and url.startswith("http") and url not in seen_urls:
            seen_urls.add(url)
            candidates.append((source, url))
    if not candidates:
        return

    yield send("progress", {"source": "avatar_match", "status": "searching", "msg": "Comparing avatars..."})

    hashes = {}
    for source, url in candidates:
        h = _avatar_phash(url)
        if h:
            hashes.setdefault(url, {"hash": h, "sources": []})["sources"].append(source)

    import imagehash, urllib.parse
    urls = list(hashes.keys())
    used = set()
    clusters = []
    for i, u1 in enumerate(urls):
        if u1 in used:
            continue
        group = [u1]
        h1 = imagehash.hex_to_hash(hashes[u1]["hash"])
        for u2 in urls[i + 1:]:
            if u2 in used:
                continue
            h2 = imagehash.hex_to_hash(hashes[u2]["hash"])
            if h1 - h2 <= 8:
                group.append(u2)
                used.add(u2)
        used.add(u1)
        if len(group) > 1:
            all_sources = [s for u in group for s in hashes[u]["sources"]]
            clusters.append({"sources": all_sources, "avatars": group})

    links = [{
        "source": source, "avatar": url,
        "engines": [{"name": e["name"], "search_url": e["url"].format(u=urllib.parse.quote(url, safe=""))}
                    for e in REVERSE_IMAGE_ENGINES],
    } for source, url in candidates]

    yield send("result", {"source": "avatar_match", "data": {"clusters": clusters, "links": links}})
    yield send("progress", {"source": "avatar_match", "status": "done",
                             "msg": f"{len(clusters)} match(es)" if clusters else "No matches"})


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


MAIGRET_BLACKLIST_PREFIXES = ("OP.GG",)


_maigret_notify_cls = None

def _get_maigret_notify_cls():
    """Builds (once) and caches the QueryNotify subclass — deferred behind
    this factory instead of a module-level class so the maigret.notify/
    maigret.result imports it needs don't run at app startup."""
    global _maigret_notify_cls
    if _maigret_notify_cls is not None:
        return _maigret_notify_cls
    from maigret.notify import QueryNotify
    from maigret.result import MaigretCheckStatus

    class _MaigretQueueNotify(QueryNotify):
        """Bridges maigret's per-site async callbacks onto a thread-safe queue so
        the sync SSE generator in search_maigret() can consume them without
        awaiting — maigret_check() itself runs on a background thread."""

        def __init__(self, result_queue):
            super().__init__()
            self.q = result_queue
            self.checked = 0
            self.found = 0

        def update(self, result, is_similar=False):
            self.result = result
            self.checked += 1
            if result.status == MaigretCheckStatus.CLAIMED:
                site_name = result.site_name
                if not any(site_name.startswith(p) for p in MAIGRET_BLACKLIST_PREFIXES):
                    self.found += 1
                    self.q.put(("hit", {"site": site_name, "url": result.site_url_user or ""}))
            elif self.checked % 20 == 0:
                self.q.put(("progress", {"found": self.found, "checked": self.checked}))
            return result

    _maigret_notify_cls = _MaigretQueueNotify
    return _maigret_notify_cls


def search_maigret(query, limit, send, collected=None):
    from maigret.sites import MaigretDatabase
    from maigret.checking import maigret as maigret_check
    label = "all sites" if not limit else f"{limit} sites"
    yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Starting scan ({label})..."})
    try:
        db_file = os.path.join(
            os.path.dirname(importlib.util.find_spec("maigret").origin),
            "resources", "data.json",
        )
        db = MaigretDatabase().load_from_path(db_file)
        top = limit if limit and limit > 0 else sys.maxsize
        site_data = db.ranked_sites_dict(top=top, id_type="username")

        q = queue.Queue()

        def _run():
            try:
                asyncio.run(maigret_check(
                    username=query,
                    site_dict=site_data,
                    logger=logging.getLogger("maigret"),
                    query_notify=_get_maigret_notify_cls()(q),
                    timeout=10,
                    id_type="username",
                ))
                q.put(("done", None))
            except Exception as e:
                q.put(("error", str(e)))

        threading.Thread(target=_run, daemon=True).start()

        found_sites = []
        while True:
            kind, payload = q.get()
            if kind == "hit":
                found_sites.append(payload)
                yield send("maigret_hit", {"site": payload["site"], "url": payload["url"], "count": len(found_sites)})
                yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Found {len(found_sites)} so far..."})
                if "Telegram" in payload["site"] and "t.me/" in payload["url"]:
                    tg = fetch_telegram_profile(payload["url"])
                    if tg:
                        yield send("result", {"source": "telegram", "data": tg})
            elif kind == "progress":
                yield send("progress", {
                    "source": "maigret", "status": "searching",
                    "msg": f"Scanning... ({payload['found']} found, {payload['checked']} checked)",
                    "found": payload["found"], "checked": payload["checked"],
                    "total": top if top != sys.maxsize else None,
                })
            elif kind == "error":
                raise RuntimeError(payload)
            else:  # "done"
                break

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


def search_ip_info(query, send, collected=None):
    yield send("progress", {"source": "ip", "status": "searching", "msg": "Looking up IP..."})
    try:
        r = requests.get(
            f"http://ip-api.com/json/{query}",
            params={"fields": "status,message,country,countryCode,regionName,city,zip,lat,lon,"
                               "timezone,isp,org,as,reverse,mobile,proxy,hosting,query"},
            timeout=8
        )
        d = r.json()
        if d.get("status") != "success":
            yield send("result", {"source": "ip", "data": {"error": d.get("message", "lookup failed")}})
            yield send("progress", {"source": "ip", "status": "error", "msg": d.get("message", "failed")})
            return
        data2 = {
            "ip":        d.get("query"),
            "country":   d.get("country"),
            "country_code": d.get("countryCode"),
            "region":    d.get("regionName"),
            "city":      d.get("city"),
            "zip":       d.get("zip"),
            "lat":       d.get("lat"),
            "lon":       d.get("lon"),
            "timezone":  d.get("timezone"),
            "isp":       d.get("isp"),
            "org":       d.get("org"),
            "asn":       d.get("as"),
            "reverse_dns": d.get("reverse"),
            "is_mobile": d.get("mobile", False),
            "is_proxy":  d.get("proxy", False),
            "is_hosting": d.get("hosting", False),
        }
        if collected is not None:
            collected["ip"] = data2
        yield send("result", {"source": "ip", "data": data2})
        yield send("progress", {"source": "ip", "status": "done", "msg": f"{data2['city'] or '?'}, {data2['country'] or '?'}"})
    except Exception as e:
        yield send("result", {"source": "ip", "data": {"error": str(e)}})
        yield send("progress", {"source": "ip", "status": "error", "msg": str(e)})


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


def _fetch_avatar_bytes(url, timeout=8):
    """Best-effort image fetch for cross-referencing avatars in generate_portrait
    — returns None on any failure so a slow/dead CDN link never blocks the
    whole portrait, it just means that platform's avatar isn't in the compare."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

def generate_portrait(query, query_type, config, collected=None, ai_lang="ru"):
    context_parts = []
    avatar_images = []  # [(label, bytes)] — real photos to actually show the model,
                         # not just tell it "there's an avatar" and let it guess.

    if collected:
        if "vk" in collected and "error" not in collected["vk"]:
            vk = collected["vk"]
            context_parts.append(f"VK profile: name={vk.get('name')}, city={vk.get('city')}, country={vk.get('country')}, "
                                  f"followers={vk.get('followers')}, bdate={vk.get('bdate')}, sex={vk.get('sex')}, "
                                  f"last_seen={vk.get('last_seen')}, education={vk.get('education')}, "
                                  f"groups={vk.get('groups', [])[:10]}, posts={vk.get('posts', [])[:3]}, closed={vk.get('closed')}")
            img = _fetch_avatar_bytes(vk.get("photo"))
            if img:
                avatar_images.append(("VK avatar", img))

        if "telegram" in collected and "error" not in collected["telegram"]:
            tg = collected["telegram"]
            context_parts.append(f"Telegram profile: name={tg.get('name')}, username=@{tg.get('username')}, "
                                  f"bio={tg.get('bio')}, subscribers={tg.get('subscribers')}")
            img = _fetch_avatar_bytes(tg.get("photo"))
            if img:
                avatar_images.append(("Telegram avatar", img))

        if "github" in collected and "error" not in collected["github"]:
            gh = collected["github"]
            img = _fetch_avatar_bytes(gh.get("avatar"))
            if img:
                avatar_images.append(("GitHub avatar", img))

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

    # DeepSeek's chat API has no vision support here — never claim a photo is
    # attached for a provider that will never actually receive the bytes.
    vision_capable = config.get("provider") in ("anthropic", "mistral", "gemini")
    effective_images = avatar_images if vision_capable else []
    if effective_images:
        photo_note = (
            f"\n\nПрикреплены реальные фото аватарок ({', '.join(l for l, _ in avatar_images)}) — "
            "посмотри на них по-настоящему: похожи ли это на одного и того же человека (лицо, "
            "стиль, окружение)? Не пиши ничего про внешность/пол/возраст для аккаунтов, чьё фото "
            "НЕ приложено — там у тебя просто нет данных, это будет угадайка, а не анализ."
            if ai_lang == "ru" else
            f"\n\nReal avatar photos are attached ({', '.join(l for l, _ in avatar_images)}) — actually "
            "look at them: do they plausibly show the same person (face, style, setting)? Don't state "
            "anything about appearance/gender/age for accounts whose photo is NOT attached — you have "
            "no data there, and that would be a guess dressed up as analysis."
        )
    else:
        photo_note = (
            "\n\nНи одной фотографии профиля не приложено к этому анализу — не выдумывай, как выглядит "
            "человек, его пол или возраст. Если хочешь упомянуть аватар, скажи только 'аватар есть' или "
            "'аватара нет', без домыслов о содержимом."
            if ai_lang == "ru" else
            "\n\nNo profile photo was attached to this analysis — do not invent what the person looks "
            "like, their gender, or age. If you mention an avatar at all, say only 'an avatar exists' "
            "or 'no avatar', never guess what's in it."
        )

    caveat = (
        "ВАЖНО: В конце анализа обязательно добавь раздел '⚠ Важная оговорка' где укажи, что "
        "не все найденные аккаунты могут принадлежать одному человеку — один и тот же никнейм на "
        "рандомном форуме/сервисе из списка Maigret совпадает ЧАЩЕ ВСЕГО ПРОСТО ПО СЛУЧАЙНОСТИ, "
        "особенно для короткого или частого никнейма — это НЕ доказательство, а лишь наводка. "
        "Высокую уверенность выражай только там, где есть прямое подтверждение (совпадающее фото, "
        "совпадающий текст био, явная перекрёстная ссылка между аккаунтами) — не потому что "
        "никнейм совпал на 30 сайтах подряд. Это ОБЯЗАТЕЛЬНАЯ часть анализа."
        if ai_lang == "ru" else
        "IMPORTANT: At the end of your analysis, add a section '⚠ Important Disclaimer' stating that "
        "not all found accounts belong to the same person — a matching username on a random forum/"
        "service from the Maigret list is MOST OFTEN JUST COINCIDENCE, especially for a short or "
        "common handle — that's a lead, not proof. Only express high confidence where there's direct "
        "corroboration (matching photo, matching bio text, an explicit cross-link between accounts) — "
        "not because the username happened to match on 30 sites in a row. This section is MANDATORY."
    )
    if context_parts:
        data_section = "\n".join(f"- {p}" for p in context_parts)
        prompt = (f"You are an OSINT analyst. Based on the following gathered data about {type_label}, "
                  f"write a concise analytical portrait: personality traits, online behavior, geographic hints, "
                  f"risk assessment, and interesting patterns.\n\nGathered data:\n{data_section}\n\n"
                  f"{lang_instruction} {caveat}{photo_note}")
    else:
        prompt = (f"You are an OSINT analyst. Write a brief analytical portrait for {type_label}. "
                  f"Include likely platforms, geographic hints, behavioral patterns, and risk assessment. "
                  f"{lang_instruction} {caveat}{photo_note}")

    if config["provider"] == "anthropic":
        import anthropic, base64
        client = anthropic.Anthropic(api_key=config["api_key"])
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(img).decode()}} for _, img in avatar_images]
        content.append({"type": "text", "text": prompt})
        msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=1000,
            messages=[{"role": "user", "content": content}])
        return {"portrait": msg.content[0].text}

    elif config["provider"] == "gemini":
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return {"error": "google-genai not installed. Run: py -m pip install google-genai"}
        try:
            client = genai.Client(api_key=config["api_key"])
            contents = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for _, img in avatar_images]
            contents.append(prompt)
            resp = client.models.generate_content(model=GEMINI_DEFAULT_MODEL, contents=contents)
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

    elif config["provider"] == "deepseek":
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}]},
                timeout=60
            )
            if r.status_code == 401:
                raise RuntimeError("DeepSeek API: неверный ключ." if ai_lang == "ru" else "DeepSeek API: invalid key.")
            if r.status_code == 429:
                raise RuntimeError("DeepSeek API: превышен лимит запросов." if ai_lang == "ru" else "DeepSeek API: rate limit exceeded.")
            r.raise_for_status()
            return {"portrait": r.json()["choices"][0]["message"]["content"]}
        except requests.RequestException as e:
            raise RuntimeError(f"DeepSeek API: {e}")

    elif config["provider"] == "mistral":
        try:
            if avatar_images:
                import base64
                content = [{"type": "text", "text": prompt}]
                for _, img in avatar_images:
                    content.append({"type": "image_url", "image_url": f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"})
                mistral_body = {"model": MISTRAL_VISION_MODELS[0], "messages": [{"role": "user", "content": content}]}
            else:
                mistral_body = {"model": MISTRAL_TEXT_MODEL, "messages": [{"role": "user", "content": prompt}]}
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                json=mistral_body,
                timeout=120  # a full OSINT-context portrait prompt on mistral-large can genuinely
                             # take over a minute on the free tier — 60s was clipping real requests
            )
            if r.status_code == 401:
                raise RuntimeError("Mistral API: неверный ключ." if ai_lang == "ru" else "Mistral API: invalid key.")
            if r.status_code == 429:
                raise RuntimeError("Mistral API: превышен лимит запросов (free tier — 2 req/min)." if ai_lang == "ru" else "Mistral API: rate limit exceeded (free tier — 2 req/min).")
            r.raise_for_status()
            return {"portrait": r.json()["choices"][0]["message"]["content"]}
        except requests.Timeout:
            raise RuntimeError(
                "Mistral API не ответил за 120 секунд — сервер перегружен или сеть подвисла. Попробуй ещё раз."
                if ai_lang == "ru" else
                "Mistral API didn't respond within 120s — server overloaded or the connection stalled. Try again."
            )
        except requests.RequestException as e:
            raise RuntimeError(f"Mistral API: {e}")

    return {"error": "Unknown provider"}


def _ai_chat_text(config, prompt, ai_lang="ru", max_tokens=500, timeout=120):
    """Minimal provider-dispatching text completion for small single-shot AI
    asks (username-guessing) that don't need generate_portrait's OSINT-
    context-building or its per-provider friendly-error messages — kept
    separate so those already-verified paths stay untouched."""
    provider = config.get("provider")
    api_key = config.get("api_key")
    if not provider or not api_key:
        raise RuntimeError("No AI provider configured — set an API key in Settings")

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=ANTHROPIC_MODELS[0], max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text

    if provider == "gemini":
        from google import genai
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=GEMINI_DEFAULT_MODEL, contents=prompt)
        return resp.text

    if provider in ("deepseek", "mistral"):
        url = "https://api.deepseek.com/chat/completions" if provider == "deepseek" else "https://api.mistral.ai/v1/chat/completions"
        model = "deepseek-chat" if provider == "deepseek" else MISTRAL_TEXT_MODEL
        r = requests.post(url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    raise RuntimeError(f"Unknown provider: {provider}")


@app.route("/api/search/suggest_users", methods=["POST"])
def suggest_alt_users():
    """Given the frontend's already-collected result set for one identity,
    ask the AI for OTHER usernames the same person plausibly uses — grounded
    in actual cross-references/patterns in the data, not free-associated
    guessing. Powers the 'Search another user's account' button."""
    data = request.json or {}
    query = (data.get("query") or "").strip()
    results = data.get("results") or []
    ai_provider = data.get("ai_provider") or ""
    ai_lang = data.get("ai_lang", "ru")
    if not query:
        return jsonify({"error": "no query"}), 400
    if not isinstance(results, list):
        return jsonify({"error": "results must be a list"}), 400

    key_map = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
               "deepseek": "DEEPSEEK_API_KEY", "mistral": "MISTRAL_API_KEY"}
    api_key = keys_store.get(key_map.get(ai_provider, "")) if ai_provider in key_map else None
    if not api_key:
        return jsonify({"error": "No AI provider configured — set an API key in Settings"}), 400

    context_parts = []
    for r in results:
        if not isinstance(r, dict):
            continue
        src, d = r.get("source"), r.get("data")
        if not isinstance(d, dict) or d.get("error"):
            continue
        if src == "vk":
            context_parts.append(f"VK: name={d.get('name')}, domain={d.get('domain')}, city={d.get('city')}, "
                                  f"status={d.get('status')}, groups={d.get('groups', [])[:15]}")
        elif src == "telegram":
            context_parts.append(f"Telegram: name={d.get('name')}, username=@{d.get('username')}, bio={d.get('bio')}")
        elif src in ("github", "github_email"):
            context_parts.append(f"GitHub: username={d.get('username')}, bio={d.get('bio')}, "
                                  f"blog={d.get('blog')}, company={d.get('company')}")
        elif src == "gravatar":
            context_parts.append(f"Gravatar: username={d.get('username')}, urls={d.get('urls', [])}, "
                                  f"accounts={[a.get('title') for a in d.get('accounts', [])]}")
        elif src == "maigret":
            sites = [s.get("site") for s in d.get("found", [])]
            context_parts.append(f"Confirmed accounts on: {', '.join(sites[:40])}")

    if not context_parts:
        return jsonify({"candidates": []})

    lang_instruction = "Отвечай строго на русском." if ai_lang == "ru" else "Respond strictly in English."
    prompt = (
        f"You are an OSINT analyst. The person searched under the username/identity '{query}' was found on "
        f"the platforms below. Extract OTHER usernames/handles this same person plausibly uses, split into two "
        f"confidence tiers.\n\n"
        f"TIER 'confirmed': ONLY where the gathered data gives an EXPLICIT textual reason — a handle literally "
        f"written in a bio, a blog URL, an 'also on X as Y' mention, or a name that already appears verbatim as "
        f"a confirmed account on another platform.\n\n"
        f"TIER 'guess': semantic/thematic inference from the person's name, bio, groups, or existing handle — "
        f"e.g. a bio saying 'Meow' or a cat emoji implies a cat-themed persona, so a handle like "
        f"'{{base}}_koteyka' or '{{base}}_cat' is a reasonable guess even with no literal text match; a handle "
        f"root that already evokes an animal/character (e.g. 'Kiki' evoking a cat) is itself a valid thematic "
        f"seed. Combine the known name/handle with the inferred theme the way real people actually pick "
        f"usernames (underscore-joined, transliterated or original language, no invented random digits/suffixes "
        f"that carry no meaning). Each guess must cite the specific theme it's derived from. Do NOT produce "
        f"guesses that are just random character/number permutations with no thematic or textual basis — those "
        f"are noise, not hypotheses.\n\n"
        f"Gathered data:\n" + "\n".join(f"- {p}" for p in context_parts) + "\n\n"
        f'Respond with ONLY raw JSON (no markdown, no other text): '
        f'{{"confirmed": [{{"username": "handle", "reason": "short phrase citing exactly where this came from"}}], '
        f'"guess": [{{"username": "handle", "reason": "short phrase naming the theme/signal this was inferred from"}}]}}, '
        f"max 5 items per tier. Empty arrays if there's nothing to report in a tier.\n{lang_instruction}"
    )

    try:
        text = _ai_chat_text({"provider": ai_provider, "api_key": api_key}, prompt, ai_lang, max_tokens=500)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        raw = json.loads(text)

        def _extract(items):
            out = []
            for item in items or []:
                if isinstance(item, dict):
                    u = str(item.get("username") or "").strip().lstrip("@")
                    reason = str(item.get("reason") or "").strip()
                elif isinstance(item, str):
                    u, reason = item.strip().lstrip("@"), ""
                else:
                    continue
                if u and u.lower() != query.lower():
                    out.append({"username": u, "reason": reason})
            return out[:5]

        if isinstance(raw, dict):
            confirmed = _extract(raw.get("confirmed"))
            guess = _extract(raw.get("guess"))
        else:
            # tolerate a bare array from a model that ignores the tiered shape
            confirmed = _extract(raw)
            guess = []
        return jsonify({"confirmed": confirmed, "guess": guess})
    except requests.Timeout:
        msg = ("AI не ответил за 120 секунд — сервер перегружен. Попробуй ещё раз."
               if ai_lang == "ru" else
               "AI didn't respond within 120s — server overloaded. Try again.")
        return jsonify({"error": msg}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ════ HASHCAT API ════════════════════════════════════════════
# The WiFi Cracker UI itself is baked into index.html now (#panel-hc) —
# used to be read at runtime from an external wifi_cracker.py the user had
# to drop next to hashcat.exe (string-extracted HTML, served through
# /wc/'s own route, loaded via <iframe>). No more external-file dependency
# or /wc/ route needed; the endpoints below are all that's left, and the
# frontend already calls them directly at their real /api/hc/* paths.

@app.route("/api/hc/status")
def hc_status():
    hc_path, hc_src = resolve_hashcat()
    wl_path, wl_src = resolve_wordlist()
    ts_path, ts_src = resolve_tshark()
    return jsonify({
        "hashcat": bool(hc_path), "hashcat_path": hc_path, "hashcat_source": hc_src,
        "rockyou": bool(wl_path), "rockyou_path": wl_path, "rockyou_source": wl_src,
        "hcxtool": True,
        "tshark":  bool(ts_path), "tshark_path": ts_path, "tshark_source": ts_src,
    })

@app.route("/api/hc/wsl_status")
def hc_wsl_status():
    # Distinguishes "WSL itself isn't installed" from "WSL is fine but
    # hcxtools was never apt-installed inside the distro" — the fix is a
    # different command in each case, so the frontend needs to know which.
    try:
        r = subprocess.run(["wsl", "--status"], capture_output=True, timeout=8,
                            creationflags=subprocess.CREATE_NO_WINDOW)
        wsl_installed = r.returncode == 0
    except Exception:
        wsl_installed = False
    hcxtools_installed = False
    if wsl_installed:
        try:
            r = subprocess.run(["wsl", "sh", "-c", "command -v hcxpcapngtool"],
                                capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
            hcxtools_installed = r.returncode == 0 and bool(r.stdout.strip())
        except Exception:
            hcxtools_installed = False
    return jsonify({"wsl": wsl_installed, "hcxtools": hcxtools_installed})

# GET/POST /api/hc/tool_paths — the Settings panel's "Browse..." flow: the
# frontend gets the real path from window.pywebview.api.pick_file(kind) (a
# native dialog — see desktop.py's Api.pick_file) and POSTs it here to save
# as this field's manual override, which _resolve_tool_path() then prefers
# over auto-detection on every future check.
_TOOL_PATH_FIELDS = {"hashcat": "HASHCAT_EXE_PATH", "wordlist": "WORDLIST_PATH", "tshark": "TSHARK_PATH"}

@app.route("/api/hc/pick")
def hc_pick():
    # The WiFi Cracker panel's "click to browse" (see index.html's wcPick())
    # used to write a temp tkinter script and run it via
    # `subprocess.run(["python", tmp])` — works only if a standalone Python
    # happens to be on PATH, which a machine set up to run the frozen exe
    # specifically won't have. The desktop window's own pywebview instance
    # already has a real native file dialog living in this same process
    # (see desktop.py's Api.pick_file, used by the Settings panel); calling
    # it directly here needs no scripting runtime and no temp files.
    try:
        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("Capture files (*.pcap;*.pcapng;*.cap)", "All files (*.*)"))
        path = (result[0] if isinstance(result, (list, tuple)) else result) if result else ""
    except Exception:
        # Not running under the desktop window (e.g. the dev-server-in-a-
        # browser path) — no native dialog host to marshal the call to.
        path = ""
    return jsonify({"path": path})

@app.route("/api/hc/tool_paths", methods=["POST"])
def hc_set_tool_path():
    data = request.json or {}
    kind = data.get("kind")
    path = (data.get("path") or "").strip()
    field = _TOOL_PATH_FIELDS.get(kind)
    if not field:
        return jsonify({"ok": False, "error": f"unknown kind: {kind}"}), 400
    if path and not os.path.isfile(path):
        return jsonify({"ok": False, "error": "file not found"}), 400
    k = keys_store.load()
    k[field] = path
    keys_store.save(k)
    return jsonify({"ok": True})

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
    tshark_exe, _ = resolve_tshark()
    if tshark_exe:
        try:
            r=subprocess.run([tshark_exe,"-r",pcap,"-Y","eapol","-T","fields","-e","frame.number"],
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
    hashcat_exe, _ = resolve_hashcat()
    if not hashcat_exe:
        return jsonify({"ok":False,"error":"hashcat.exe not found — set its path in Settings"})
    hc_dir = os.path.dirname(hashcat_exe)
    all_wl=[]
    for fn in os.listdir(hc_dir):
        if fn.lower().endswith(".txt") and fn.lower()!="show.log":
            fp=os.path.join(hc_dir,fn)
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
                show0=subprocess.run([hashcat_exe,"-m","22000",hf,"--show"],
                    capture_output=True,text=True,cwd=hc_dir)
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
                cmd=[hashcat_exe,"-m","22000",hf,wl,"--status","--status-timer=4","--force","-o",cracked]
                try:
                    _proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,cwd=hc_dir)
                    for line in _proc.stdout:
                        if _stop_requested:
                            _proc.terminate()
                            break
                        log_hc(line.rstrip())
                    _proc.wait()
                    # Wait for lock files to be released
                    import glob, time as _t
                    for _ in range(20):
                        locks=glob.glob(os.path.join(hc_dir,"hashcat.pid"))+glob.glob(os.path.join(hc_dir,"*.pid"))
                        if not locks: break
                        _t.sleep(0.3)
                except Exception as e: log("err",str(e)); break
                if _stop_requested:
                    log("warn","stopped by user")
                    break
                # Check --show after each wordlist
                try:
                    show=subprocess.run([hashcat_exe,"-m","22000",hf,"--show"],
                        capture_output=True,text=True,cwd=hc_dir)
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

def _flip_err(e):
    """Windows raises bare PermissionError(13) both when another process
    (qFlipper, Flipper Mobile) already holds the port exclusively AND when
    a Bluetooth-SPP virtual COM port has no live RFCOMM connection behind
    it — either way the raw exception text is meaningless to the user."""
    if isinstance(e, PermissionError):
        return "Порт занят другим приложением или Flipper сейчас не подключён по Bluetooth. Закрой qFlipper/Flipper Mobile и проверь подключение."
    return str(e)

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e),"ports":[]})

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e),"files":[]})

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e)}),500

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e)}),500

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e)}),500

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
    except Exception as e: return jsonify({"ok":False,"error":_flip_err(e)}),500

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

GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"]
# mistral-large-latest 403s ("tier_not_allowed") on a key with no billing —
# confirmed live. Back to large as default now that billing's being set up;
# if it 403s again, drop back to small/medium (both confirmed reachable
# free) rather than guessing at the tier again.
MISTRAL_TEXT_MODEL = "mistral-large-latest"
# Standalone "pixtral-*" model names are gone from Mistral's catalog as of
# 2026 — vision got folded straight into the main large/medium/small line,
# confirmed live against /v1/models' capabilities.vision flag.
MISTRAL_VISION_MODELS = ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"]

def geoint_ai_guess(image_bytes, config, ai_lang="ru", model=None):
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
        msg = client.messages.create(model=model or ANTHROPIC_MODELS[0], max_tokens=700,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt}]}])
        return _parse_ai_json(msg.content[0].text)

    if config["provider"] == "mistral":
        import base64
        b64 = base64.b64encode(image_bytes).decode()
        try:
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                json={
                    "model": model or MISTRAL_VISION_MODELS[0],
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64}"},
                    ]}],
                },
                timeout=60,
            )
            if r.status_code == 401:
                raise RuntimeError("Mistral API: неверный ключ." if ai_lang == "ru" else "Mistral API: invalid key.")
            if r.status_code == 429:
                raise RuntimeError(
                    "Mistral API: превышен лимит запросов (free tier — 2 req/min). Подожди немного."
                    if ai_lang == "ru" else
                    "Mistral API: rate limit exceeded (free tier — 2 req/min). Wait a bit."
                )
            r.raise_for_status()
            return _parse_ai_json(r.json()["choices"][0]["message"]["content"])
        except requests.Timeout:
            raise RuntimeError(
                "Mistral API не ответил за 60 секунд — сервер перегружен или сеть подвисла. Попробуй ещё раз."
                if ai_lang == "ru" else
                "Mistral API didn't respond within 60s — server overloaded or the connection stalled. Try again."
            )
        except requests.RequestException as e:
            raise RuntimeError(f"Mistral API: {e}")

    if config["provider"] == "gemini":
        from google import genai
        from google.genai import types

        # Bounded the same way as geoint_models(): no explicit SDK timeout
        # here, and a slow/blocked network path can hang this call far past
        # any reasonable wait instead of failing fast.
        outcome = {}

        def _call():
            try:
                client = genai.Client(api_key=config["api_key"])
                resp = client.models.generate_content(model=model or GEMINI_DEFAULT_MODEL,
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
                outcome["text"] = resp.text
            except Exception as e:
                outcome["exc"] = e

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=25)

        if "text" in outcome:
            return _parse_ai_json(outcome["text"])
        if "exc" not in outcome:
            is_ru = ai_lang == "ru"
            raise RuntimeError(
                "Gemini не ответил за 25 секунд — сеть/VPN слишком медленные или запрос завис. Попробуй другую модель или проверь соединение."
                if is_ru else
                "Gemini didn't respond within 25s — network/VPN too slow or the request stalled. Try a different model or check your connection."
            )
        try:
            raise outcome["exc"]
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
            if "NOT_FOUND" in msg or "404" in msg:
                raise RuntimeError(
                    "Модель "+(model or GEMINI_DEFAULT_MODEL)+" больше не доступна — выбери другую модель в выпадающем списке."
                    if is_ru else
                    "Model "+(model or GEMINI_DEFAULT_MODEL)+" is no longer available — pick a different model from the dropdown."
                )
            raise

    raise RuntimeError("No AI provider configured — set an API key in Settings")

@app.route("/api/geoint/models", methods=["GET"])
def geoint_models():
    provider = AI_CONFIG.get("provider")
    if provider == "anthropic":
        return jsonify({"provider": "anthropic", "models": ANTHROPIC_MODELS, "default": ANTHROPIC_MODELS[0]})
    if provider == "mistral":
        return jsonify({"provider": "mistral", "models": MISTRAL_VISION_MODELS, "default": MISTRAL_VISION_MODELS[0]})
    if provider == "gemini":
        if not AI_CONFIG.get("api_key"):
            return jsonify({"error": "no API key configured"}), 400

        # client.models.list() has no explicit timeout in this SDK version and
        # can hang far longer than a normal failed request when the network
        # path is slow/flaky (seen under a VPN: fails fast in a plain
        # interpreter, but hangs well past 40s in the frozen exe) — bound it
        # in a thread so a slow Gemini call can never wedge this route.
        result = {}

        def _fetch():
            try:
                from google import genai
                client = genai.Client(api_key=AI_CONFIG["api_key"])
                names = []
                for m in client.models.list():
                    actions = getattr(m, "supported_actions", None) or []
                    if "generateContent" not in actions:
                        continue
                    name = (m.name or "").split("/", 1)[-1]
                    if name and "vision" not in name and "embedding" not in name:
                        names.append(name)
                result["names"] = sorted(set(names))
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=8)

        if "names" in result:
            names = result["names"]
            default = GEMINI_DEFAULT_MODEL if GEMINI_DEFAULT_MODEL in names else (names[0] if names else GEMINI_DEFAULT_MODEL)
            return jsonify({"provider": "gemini", "models": names, "default": default})
        err = result.get("error", "timed out after 8s (slow/blocked network path)")
        return jsonify({"error": err, "models": [GEMINI_DEFAULT_MODEL], "default": GEMINI_DEFAULT_MODEL})
    return jsonify({"provider": None, "models": [], "default": None})


def _geoint_analyze_core(image_bytes, ai_lang="ru", model=None):
    """EXIF+AI geolocation analysis on raw image bytes — shared by the HTTP
    route (multipart upload) and the assistant tool (reads a local file)."""
    result = {}
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
            ai_guess = geoint_ai_guess(image_bytes, AI_CONFIG, ai_lang, model)
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
    return result

@app.route("/api/geoint/analyze", methods=["POST"])
def geoint_analyze():
    if "photo" not in request.files:
        return jsonify({"error": "no file"}), 400
    image_bytes = request.files["photo"].read()
    ai_lang = request.form.get("ai_lang", "ru")
    model = request.form.get("model") or None
    return jsonify(_geoint_analyze_core(image_bytes, ai_lang, model))


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
        sharpen    = float(request.form.get("sharpen",    0))
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

        # Sharpen: UnsharpMask (0=off, 100=strong). radius scales 1→4, percent 50→400
        if sharpen > 0:
            from PIL import ImageFilter
            radius  = 1 + (sharpen / 100.0) * 3      # 1..4
            percent = int(50 + (sharpen / 100.0) * 350)  # 50..400
            img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))

        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        b64 = _b64.b64encode(buf.getvalue()).decode()
        return jsonify({"ok": True, "image": b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unredact/depix", methods=["POST"])
def unredact_depix():
    if "photo" not in request.files:
        return jsonify({"error": "no file"}), 400
    import io as _io, base64 as _b64
    try:
        import numpy as np
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError as e:
        return jsonify({"error": f"Missing dependency: {e}"}), 500

    image_bytes = request.files["photo"].read()
    try:
        blur_radius = float(request.form.get("blur_radius", 3))
        iterations  = max(1, int(float(request.form.get("iterations", 10))))
        contrast    = float(request.form.get("contrast", 30))
        sharpen     = float(request.form.get("sharpen", 50))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid params"}), 400

    try:
        img = Image.open(_io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        arr = np.array(img, dtype=np.float64) / 255.0

        # Build Gaussian PSF
        psf_size = max(3, int(blur_radius * 4) | 1)
        c = psf_size // 2
        ys, xs = np.mgrid[-c:c+1, -c:c+1].astype(np.float64)
        psf = np.exp(-(xs**2 + ys**2) / (2 * blur_radius**2))
        psf /= psf.sum()

        # Richardson-Lucy via FFT convolution — pure numpy, no skimage/scipy needed
        def _rl_fft(channel, h, psf, num_iter):
            """R-L deconvolution using FFT. PSF embedded at (0,0) for circular conv."""
            psf_full = np.zeros((h, channel.shape[1]), dtype=np.float64)
            ph, pw = psf.shape
            psf_full[:ph, :pw] = psf
            psf_full = np.roll(np.roll(psf_full, -(ph // 2), axis=0), -(pw // 2), axis=1)
            PSF = np.fft.rfft2(psf_full)
            PSF_T = np.conj(PSF)  # symmetric PSF: conj == flipped
            est = channel.copy()
            for _ in range(num_iter):
                conv = np.fft.irfft2(np.fft.rfft2(est) * PSF, s=channel.shape)
                ratio = channel / np.maximum(conv, 1e-10)
                est = np.clip(est * np.fft.irfft2(np.fft.rfft2(ratio) * PSF_T, s=channel.shape), 0, 1)
            return est

        H = arr.shape[0]
        result = np.stack([_rl_fft(arr[:, :, ch], H, psf, iterations) for ch in range(3)], axis=2)

        img = Image.fromarray((np.clip(result, 0, 1) * 255).astype(np.uint8))

        if contrast > 0:
            img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 50.0)
        if sharpen > 0:
            radius  = 1 + (sharpen / 100.0) * 3
            percent = int(100 + (sharpen / 100.0) * 300)
            img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=1))

        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return jsonify({"ok": True, "image": _b64.b64encode(buf.getvalue()).decode()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ════ AI ASSISTANT ═════════════════════════════════════════
# Agentic chat that can actually drive the app (tool calling), not just
# answer questions about it. Starting with Mistral + DeepSeek — both speak
# the same OpenAI-style tools/tool_calls dialect, so one call path covers
# both; Anthropic/Gemini use different SDKs/shapes and come in a follow-up.
_ASST_APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
ASSISTANT_SESSIONS_DIR = os.path.join(_ASST_APP_DIR, "assistant_sessions")
os.makedirs(ASSISTANT_SESSIONS_DIR, exist_ok=True)

ASSISTANT_TOOLS = [
    {"type": "function", "function": {
        "name": "geoint_analyze_photo",
        "description": "Analyze a local photo for EXIF GPS coordinates and an AI-guessed shooting location based on visual clues (architecture, signage, vegetation, etc).",
        "parameters": {"type": "object", "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the image file on disk."}
        }, "required": ["file_path"]},
    }},
    {"type": "function", "function": {
        "name": "wifi_status",
        "description": "Check whether the WiFi Cracker tool's dependencies (hashcat, rockyou wordlist, tshark) are found and where.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "osint_search_username",
        "description": "Search a username/handle across VK, Telegram, GitHub, and dozens of other platforms (via Maigret) to find matching profiles. Defaults to a full search (all sources, Maigret checking 500 sites) — do NOT lower maigret_limit just because the username looks common/popular; only narrow it down when the user explicitly asks for a faster/lighter search.",
        "parameters": {"type": "object", "properties": {
            "username": {"type": "string", "description": "The username/handle to search for, without the @."},
            "maigret_limit": {"type": "integer", "description": "How many sites Maigret checks. Defaults to 500 (full search). Only lower this if the user explicitly asks for speed over coverage."},
            "sources": {"type": "array", "items": {"type": "string", "enum": ["vk", "telegram", "github", "maigret"]}, "description": "Which sources to run. Defaults to all four. Only restrict this if the user explicitly asks to check just one/some platforms."}
        }, "required": ["username"]},
    }},
]
ASSISTANT_GATED_TOOLS = set()  # e.g. {"wifi_crack"} once that tool lands

def _assistant_session_path(sid):
    # sid comes from the client (URL/body) — scrub to a bare filename-safe
    # token before it ever touches a path, since it's used directly below.
    safe = re.sub(r"[^a-zA-Z0-9_]", "", sid or "")
    if not safe:
        raise ValueError("bad session id")
    return os.path.join(ASSISTANT_SESSIONS_DIR, safe + ".json")

def _assistant_new_session_id():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")

def _assistant_load_session(sid):
    try:
        with open(_assistant_session_path(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None

def _assistant_save_session(sess):
    sess["updated"] = datetime.now().isoformat()
    with open(_assistant_session_path(sess["id"]), "w", encoding="utf-8") as f:
        json.dump(sess, f, indent=2, ensure_ascii=False)

def _assistant_list_sessions():
    out = []
    for fn in os.listdir(ASSISTANT_SESSIONS_DIR):
        if not fn.endswith(".json"): continue
        try:
            with open(os.path.join(ASSISTANT_SESSIONS_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            out.append({"id": d.get("id"), "title": d.get("title") or "New chat", "updated": d.get("updated", "")})
        except Exception:
            pass
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out

def _osint_username_search_core(username, maigret_limit=500, sources=None):
    """Runs the same VK/Telegram/GitHub/Maigret checks as the Kiki OSINT tab's
    SSE stream, but synchronously — the assistant tool loop needs one plain
    result, not a progress stream. Defaults to the full sweep (matches the
    manual tab); the AI can narrow sources/maigret_limit only when the user
    explicitly asks for a lighter/faster search."""
    if sources is None:
        sources = ["vk", "telegram", "github", "maigret"]
    def noop_send(event, payload):
        return None
    collected = {}
    if "vk" in sources:
        for _ in search_vk_username(username, noop_send, collected): pass
    if "telegram" in sources:
        for _ in search_telegram(username, noop_send, collected): pass
    if "github" in sources:
        for _ in search_github(username, noop_send, collected): pass
    if "maigret" in sources:
        for _ in search_maigret(username, maigret_limit, noop_send, collected): pass
        # Maigret's own site catalog carries more than one checker entry for
        # some platforms (different URL patterns for the same service), so a
        # raw hit list can list the same profile URL twice — collapse those
        # before handing results to the model, instead of relying on it to
        # notice and caveat the duplicate in its answer.
        if "maigret" in collected:
            seen_urls = set()
            deduped = []
            for hit in collected["maigret"].get("found", []):
                url = hit.get("url")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                deduped.append(hit)
            collected["maigret"] = {"found": deduped, "total": len(deduped)}
    return collected

def _osint_username_search_core_stream(username, maigret_limit=500, sources=None):
    """Generator twin of _osint_username_search_core — for the one source
    that actually has a meaningful multi-step scan (Maigret checking up to
    500 sites), yields ('progress', {found, checked, total}) tuples as they
    happen instead of going dark until the whole thing finishes. Ends with
    a ('result', collected) tuple carrying the same shape the plain version
    returns. search_maigret()'s own `send` callback normally builds an SSE
    string; here it just hands back the raw (event, payload) tuple instead,
    since search_maigret only ever yields whatever `send` returns."""
    if sources is None:
        sources = ["vk", "telegram", "github", "maigret"]
    def raw_send(event, payload):
        return (event, payload)
    collected = {}
    if "vk" in sources:
        for _ in search_vk_username(username, raw_send, collected): pass
    if "telegram" in sources:
        for _ in search_telegram(username, raw_send, collected): pass
    if "github" in sources:
        for _ in search_github(username, raw_send, collected): pass
    if "maigret" in sources:
        for ev in search_maigret(username, maigret_limit, raw_send, collected):
            event, payload = ev
            if event == "progress" and payload.get("source") == "maigret" and "checked" in payload:
                yield ("progress", {"found": payload["found"], "checked": payload["checked"], "total": payload["total"]})
        if "maigret" in collected:
            seen_urls = set()
            deduped = []
            for hit in collected["maigret"].get("found", []):
                url = hit.get("url")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                deduped.append(hit)
            collected["maigret"] = {"found": deduped, "total": len(deduped)}
    yield ("result", collected)

def _assistant_run_tool(name, args):
    """Executes one tool call and returns a JSON-serializable result. Kept
    separate from the provider call loop so gating logic doesn't have to
    know anything about tool internals."""
    try:
        if name == "geoint_analyze_photo":
            fp = args.get("file_path", "")
            if not fp or not os.path.isfile(fp):
                return {"error": "file not found: " + fp}
            with open(fp, "rb") as f:
                image_bytes = f.read()
            return _geoint_analyze_core(image_bytes, args.get("ai_lang", "ru"))
        if name == "wifi_status":
            hc_path, hc_src = resolve_hashcat()
            wl_path, wl_src = resolve_wordlist()
            ts_path, ts_src = resolve_tshark()
            return {
                "hashcat": bool(hc_path), "hashcat_path": hc_path,
                "rockyou": bool(wl_path), "rockyou_path": wl_path,
                "tshark": bool(ts_path), "tshark_path": ts_path,
            }
        if name == "osint_search_username":
            username = (args.get("username") or "").strip().lstrip("@")
            if not username:
                return {"error": "no username given"}
            maigret_limit = args.get("maigret_limit") or 500
            sources = args.get("sources") or None
            return _osint_username_search_core(username, maigret_limit=maigret_limit, sources=sources)
        return {"error": "unknown tool: " + name}
    except Exception as e:
        return {"error": str(e)}

ASSISTANT_SYSTEM_PROMPT = (
    "You are Kiki, the in-app assistant for KikiHub — an OSINT/security toolkit. "
    "Your whole point is knowing this app cold and walking the user through it — "
    "not vague pointers like 'check the settings', but the actual field names, "
    "buttons, and order of steps, like someone who built the thing. Here is what "
    "every tab actually does:\n\n"
    "**Kiki OSINT** — username/email search across VK, Telegram, GitHub, and "
    "Maigret (500+ sites). Pick search type (Username/Email), set sources and "
    "Maigret's site limit via the slider, hit Search. Results land in a grid: VK/"
    "Telegram/GitHub cards with full profile detail, HIBP breach results (needs "
    "its own API key in Settings, ~$3.50/mo), a Maigret site-chip grid, and an "
    "AI-generated portrait (needs a provider key in Settings — Mistral/DeepSeek "
    "work without a VPN in Russia, Gemini is region-blocked without one). "
    "'Search another user's account' below the results re-runs the same search "
    "for a second/related nickname. Export TXT/JSON or copy raw JSON from the "
    "bar under the results.\n"
    "**WiFi Cracker** — cracks a captured WPA handshake. Needs HashCat installed "
    "separately (defaults to C:\\HashCat\\hashcat-7.1.2\\) plus a rockyou "
    "wordlist; tshark is optional and only used for deeper pcap inspection. "
    "Flow: browse or paste a .pcap path → Analyze pcap extracts SSID/BSSID/EAPOL/"
    "hash (needs at least 2 EAPOL handshakes captured) → Run hashcat attack "
    "against rockyou. Live output streams in the STDOUT panel on the right.\n"
    "**GEOINT** — Choose a photo, then Analyze: pulls GPS straight from EXIF if "
    "present, otherwise (or in addition) asks the configured AI to guess the "
    "shooting location from visual clues (architecture, signage, plants), "
    "labeled high/medium/low confidence — always tell the user to verify an AI "
    "guess, never state it as fact. EXIF Spoofer (a toggle in the same tab) "
    "writes fake lat/lon/camera-make/model into a copy of the photo, or strips "
    "EXIF entirely, for a downloadable output.\n"
    "**Reveal Text** — recovers text hidden by amateur redaction, not real "
    "blur/pixelation. Modes: Marker, Depix, iPhone Marker, Blur/WA, Highlighter, "
    "Custom — pick the one matching how the original was covered. This does not "
    "reverse a proper Gaussian blur or a fully opaque box; set expectations.\n"
    "**Downloader** — paste a video/audio URL (YouTube, TikTok, Instagram, X, "
    "etc, via yt-dlp) and pick Download video or Download MP3.\n"
    "**Flipper Zero** — browses a connected Flipper's SD card over serial; pick "
    "its COM port from the dropdown (Refresh ports if it's not listed).\n"
    "**Settings** — every API key (VK Service Token, Telegram API ID/hash, "
    "Mistral/Gemini/Anthropic/DeepSeek, HIBP, GitHub) lives here, saved to "
    "keys.json locally, never sent anywhere but the respective API. Also nav "
    "style (Dock/Rail), app theme, and background.\n\n"
    "You only have THREE real tools right now: geoint_analyze_photo (needs a local "
    "file path), wifi_status, and osint_search_username (searches a username across "
    "VK/Telegram/GitHub/Maigret). osint_search_username defaults to a full search "
    "(all sources, Maigret at 500 sites) — never shrink it just because a username "
    "looks common; only narrow sources/maigret_limit if the user explicitly asks for "
    "something faster or more targeted. Everything else above is knowledge, not "
    "capability yet — for WiFi Cracker/GEOINT's spoofer/Reveal Text/Downloader/"
    "Flipper Zero, walk the user through doing it by hand using the exact steps "
    "above; never say 'I can run X' for a tab you have no tool for. If something "
    "isn't working (no results, an error, a missing dependency), your first move "
    "is diagnosing against what you know above — wrong tab, missing API key, "
    "HashCat not installed, wrong Reveal Text mode — before shrugging. Keep "
    "answers short and concrete.\n\n"
    "After osint_search_username: your job is to run the search AND analyze what "
    "came back, not just repeat it as a list of links. The tool result carries an "
    "`ai_analysis` field when one could be generated — a synthesis correlating the "
    "hits (shared avatar across platforms, likely real name, location, confidence "
    "signals). Lead your answer with that synthesis in your own words: who this "
    "probably is, what's confirmed vs speculative, what stands out. VK/Telegram/"
    "GitHub hits still get their own concrete detail (name, bio, photo) since "
    "that's high-signal, not noise. For the raw Maigret site list, mention the "
    "count and name only the handful that are actually identity-relevant "
    "(social/professional profiles) — don't enumerate 30+ generic forum/service "
    "hits unless the user explicitly asks to see the full list. Never paste a raw "
    "avatar/CDN URL inline; just say a photo was found.\n\n"
    "If the username search comes back thin (little or nothing found) or the "
    "conversation gives you a plausible alternate spelling — dots/underscores "
    "swapped, a common numeric suffix, a first.last variant, a real name to try "
    "as a handle — call osint_search_username again for your best 1-2 guesses "
    "without waiting to be asked. Say in your answer which variants you tried."
)

_ASST_PROVIDER_DEFAULTS = {
    # Back to large now that billing's being set up — see MISTRAL_TEXT_MODEL's
    # comment above for the same 403 story if it needs reverting again.
    "mistral":  {"base_url": "https://api.mistral.ai/v1/chat/completions", "model": "mistral-large-latest", "key_field": "MISTRAL_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com/chat/completions",  "model": "deepseek-chat",         "key_field": "DEEPSEEK_API_KEY"},
}

def _assistant_call_openai_style(provider, messages):
    cfg = _ASST_PROVIDER_DEFAULTS[provider]
    api_key = keys_store.get(cfg["key_field"])
    if not api_key:
        raise RuntimeError(f"No API key configured for {provider} — add one in Settings")
    r = requests.post(cfg["base_url"],
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": cfg["model"], "messages": messages, "tools": ASSISTANT_TOOLS},
        timeout=60)
    if r.status_code == 401:
        raise RuntimeError(f"{provider}: invalid API key")
    if r.status_code == 429:
        raise RuntimeError(f"{provider}: rate limited, try again shortly")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]

@app.route("/api/assistant/sessions")
def assistant_sessions():
    return jsonify({"sessions": _assistant_list_sessions()})

@app.route("/api/assistant/sessions", methods=["POST"])
def assistant_sessions_create():
    sess = {"id": _assistant_new_session_id(), "title": "New chat", "messages": []}
    _assistant_save_session(sess)
    return jsonify({"ok": True, "id": sess["id"], "title": sess["title"]})

@app.route("/api/assistant/sessions/<sid>", methods=["DELETE"])
def assistant_sessions_delete(sid):
    try:
        os.remove(_assistant_session_path(sid))
    except FileNotFoundError:
        pass
    except ValueError:
        return jsonify({"error": "bad session id"}), 400
    return jsonify({"ok": True})

@app.route("/api/assistant/history")
def assistant_history():
    sess = _assistant_load_session(request.args.get("session_id", ""))
    return jsonify({"messages": sess["messages"] if sess else []})

@app.route("/api/assistant/chat", methods=["POST"])
def assistant_chat():
    data = request.json or {}
    user_text = (data.get("message") or "").strip()
    provider = data.get("provider", "mistral")
    session_id = data.get("session_id", "")
    if provider not in _ASST_PROVIDER_DEFAULTS:
        return jsonify({"error": "unsupported provider: " + provider}), 400
    if not user_text:
        return jsonify({"error": "empty message"}), 400
    sess = _assistant_load_session(session_id)
    if not sess:
        return jsonify({"error": "unknown session — create one first"}), 400

    def generate():
        def send(event, payload):
            return f"data: {json.dumps({'event': event, 'data': payload}, ensure_ascii=False)}\n\n"

        lang_instruction = "Always reply in the same language the user's message is written in — match their language exactly, message by message."
        history = sess["messages"]
        api_messages = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT + " " + lang_instruction}]
        api_messages += [{"role": m["role"], "content": m.get("content", "")} for m in history if m["role"] in ("user", "assistant")]
        api_messages.append({"role": "user", "content": user_text})
        history.append({"role": "user", "content": user_text, "ts": ts()})
        # First message in a fresh session doubles as its title, same as every
        # chat product does — nothing to type twice, nothing to name up front.
        if sess.get("title", "New chat") == "New chat":
            sess["title"] = (user_text[:48] + "…") if len(user_text) > 48 else user_text
        # Persist right away — a provider error (a 403, a timeout) further
        # down would otherwise silently drop the user's own message and the
        # title update along with it. Save now, save again once the reply lands.
        _assistant_save_session(sess)

        tool_log = []
        try:
            # Agentic loop: model may ask for tools repeatedly before giving a
            # final text answer. Capped so a confused model can't loop forever.
            for _ in range(4):
                msg = _assistant_call_openai_style(provider, api_messages)
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    final_text = msg.get("content", "") or "(no response)"
                    history.append({"role": "assistant", "content": final_text, "tools": tool_log, "ts": ts()})
                    _assistant_save_session(sess)
                    yield send("done", {"ok": True, "reply": final_text, "tools": tool_log, "title": sess["title"]})
                    return
                api_messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = call["function"]["name"]
                    try:
                        fn_args = json.loads(call["function"].get("arguments") or "{}")
                    except Exception:
                        fn_args = {}
                    yield send("tool_start", {"name": fn, "args": fn_args})
                    if fn == "osint_search_username" and (fn_args.get("username") or "").strip():
                        # Only tool with a meaningful multi-step scan (Maigret,
                        # up to 500 sites) — stream real found/checked progress
                        # instead of leaving the UI dark for the whole run.
                        username = fn_args["username"].strip().lstrip("@")
                        result = None
                        try:
                            for kind, payload in _osint_username_search_core_stream(
                                username,
                                maigret_limit=fn_args.get("maigret_limit") or 500,
                                sources=fn_args.get("sources") or None,
                            ):
                                if kind == "progress":
                                    yield send("tool_progress", {"name": fn, **payload})
                                else:
                                    result = payload
                            # Give the model the same synthesized writeup the
                            # Kiki OSINT tab itself generates — cross-platform
                            # avatar correlation plus an AI read of the hits —
                            # instead of making it reason over a raw JSON dump
                            # cold. Reuses whatever provider/key is already
                            # driving this chat, so no separate config needed.
                            if result and any(k in result for k in ("vk", "telegram", "github", "maigret")):
                                try:
                                    for _ in correlate_avatars(result, lambda *a, **k: None):
                                        pass
                                    ai_key = keys_store.get(_ASST_PROVIDER_DEFAULTS[provider]["key_field"])
                                    if ai_key:
                                        portrait = generate_portrait(
                                            username, "username",
                                            {"provider": provider, "api_key": ai_key},
                                            result, "ru")
                                        if portrait.get("portrait"):
                                            result["ai_analysis"] = portrait["portrait"]
                                except Exception:
                                    pass
                        except Exception as e:
                            result = {"error": str(e)}
                    else:
                        result = _assistant_run_tool(fn, fn_args)
                    tool_log.append({"name": fn, "args": fn_args, "result": result})
                    yield send("tool_done", {"name": fn, "args": fn_args, "result": result})
                    api_messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(result)})
            history.append({"role": "assistant", "content": "(stopped after too many tool calls)", "tools": tool_log, "ts": ts()})
            _assistant_save_session(sess)
            yield send("done", {"ok": True, "reply": "(stopped after too many tool calls)", "tools": tool_log, "title": sess["title"]})
        except Exception as e:
            yield send("error", {"error": str(e), "tools": tool_log})

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__=="__main__":
    print("\n  KikiHub  ->  http://localhost:7777\n")
    # Try to start OSINT backend
    app.run(host="127.0.0.1", port=7777, debug=False, threaded=True)
