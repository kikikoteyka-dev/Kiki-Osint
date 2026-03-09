from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import os, json, subprocess, sys, re, requests, shutil, socket, contextlib, threading
import keys_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ════════════════════════════════════════════════
#  GEMINI DNS ROUTING via xbox-dns.ru
#  Resolves generativelanguage.googleapis.com
#  through xbox-dns.ru nameservers so Gemini works
#  in regions where Google AI is blocked.
# ════════════════════════════════════════════════
_GEMINI_HOST  = "generativelanguage.googleapis.com"
_XBOX_DNS_NS  = ["176.99.11.77", "80.78.247.254"]
_gemini_ip    = None
_dns_lock     = threading.Lock()

def _resolve_gemini_ip():
    global _gemini_ip
    if _gemini_ip:
        return _gemini_ip
    try:
        import dns.resolver
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = _XBOX_DNS_NS
        r.timeout = 3
        r.lifetime = 5
        _gemini_ip = str(r.resolve(_GEMINI_HOST, "A")[0])
        print(f"[Gemini DNS] {_GEMINI_HOST} → {_gemini_ip} (via xbox-dns.ru)")
    except Exception as e:
        print(f"[Gemini DNS] resolve failed: {e} — falling back to system DNS")
        _gemini_ip = None
    return _gemini_ip

@contextlib.contextmanager
def _gemini_dns_ctx():
    """Temporarily patch socket.getaddrinfo so Gemini API calls use xbox-dns.ru resolved IP."""
    ip = _resolve_gemini_ip()
    if not ip:
        yield
        return
    orig = socket.getaddrinfo
    def _patched(host, port, *args, **kwargs):
        if host == _GEMINI_HOST:
            return orig(ip, port, *args, **kwargs)
        return orig(host, port, *args, **kwargs)
    with _dns_lock:
        socket.getaddrinfo = _patched
        try:
            yield
        finally:
            socket.getaddrinfo = orig

def _nw():
    """Скрыть CMD-окно при запуске дочерних процессов на Windows."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}

def _pythonw(exe):
    """Вернуть pythonw.exe рядом с python.exe, если существует."""
    if sys.platform != "win32":
        return exe
    w = exe.replace("python.exe", "pythonw.exe").replace("python3.exe", "pythonw.exe")
    return w if (w != exe and os.path.exists(w)) else exe

def _find_python_with(package):
    """Найти python-интерпретатор, в котором установлен пакет. Кешируется при старте."""
    candidates = [
        sys.executable,
        r"C:\Users\newin\AppData\Local\Programs\Python\Python311\python.exe",
        shutil.which("python") or "",
        shutil.which("python3") or "",
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            try:
                r = subprocess.run([cand, "-c", f"import {package}"],
                                   capture_output=True, timeout=5, **_nw())
                if r.returncode == 0:
                    return _pythonw(cand)  # используем pythonw.exe — без консоли
            except Exception:
                continue
    return None

# Кеш — ищем один раз при старте
_MAIGRET_PYTHON = _find_python_with("maigret")
_HOLEHE_PYTHON  = _find_python_with("holehe")
# Кешируем Gemini IP при старте (в фоне, не блокируем запуск)
threading.Thread(target=_resolve_gemini_ip, daemon=True).start()

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "frontend"))
CORS(app)

# ════════════════════════════════════════════════
#  KEYS STORAGE — keys.json > .env fallback
# ════════════════════════════════════════════════
KEYS_FILE = os.path.join(os.path.dirname(__file__), "keys.json")

def load_keys():
    return keys_store.load()

def save_keys(keys: dict):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)

def get_key(name: str) -> str:
    return keys_store.get(name)

# AI runtime config (updated per-request)
AI_CONFIG = {
    "provider": "gemini" if get_key("GEMINI_API_KEY") else None,
    "api_key":  get_key("GEMINI_API_KEY") or None
}

@app.route("/")
def index():
    resp = send_from_directory(os.path.join(BASE_DIR, "frontend"), "index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["ETag"] = ""
    return resp

@app.route("/kiki_logo.png")
def kiki_logo():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), "kiki_logo.png")

@app.route("/api/keys", methods=["GET"])
def get_keys():
    """Return saved keys (mask secrets for display)"""
    k = load_keys()
    def mask(v):
        if not v or len(v) < 8: return v
        return v[:4] + "•" * (len(v) - 8) + v[-4:]
    return jsonify({
        "VK_TOKEN":          mask(k.get("VK_TOKEN","")),
        "GEMINI_API_KEY":    mask(k.get("GEMINI_API_KEY","")),
        "OPENAI_API_KEY":    mask(k.get("OPENAI_API_KEY","")),
        "ANTHROPIC_API_KEY": mask(k.get("ANTHROPIC_API_KEY","")),
        "HIBP_API_KEY":      mask(k.get("HIBP_API_KEY","")),
        "configured": bool(
            k.get("VK_TOKEN") or
            k.get("GEMINI_API_KEY") or k.get("OPENAI_API_KEY") or
            k.get("ANTHROPIC_API_KEY")
        )
    })

@app.route("/api/keys", methods=["POST"])
def post_keys():
    """Save keys to keys.json"""
    global AI_CONFIG
    data = request.json or {}
    existing = load_keys()
    existing = load_keys()

    # Only update fields that are provided and not masked
    for field in ["VK_TOKEN",
                  "GEMINI_API_KEY","OPENAI_API_KEY","ANTHROPIC_API_KEY","HIBP_API_KEY"]:
        val = data.get(field)
        if val is not None and "•" not in str(val):
            existing[field] = str(val).strip()

    save_keys(existing)

    # Update AI_CONFIG based on what's available
    if existing.get("GEMINI_API_KEY"):
        AI_CONFIG["provider"] = "gemini"
        AI_CONFIG["api_key"]  = existing["GEMINI_API_KEY"]
    elif existing.get("OPENAI_API_KEY"):
        AI_CONFIG["provider"] = "openai"
        AI_CONFIG["api_key"]  = existing["OPENAI_API_KEY"]
    elif existing.get("ANTHROPIC_API_KEY"):
        AI_CONFIG["provider"] = "anthropic"
        AI_CONFIG["api_key"]  = existing["ANTHROPIC_API_KEY"]

    return jsonify({"status": "ok", "configured": True})

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    AI_CONFIG["provider"] = data.get("provider")
    AI_CONFIG["api_key"] = data.get("api_key")
    return jsonify({"status": "ok"})

@app.route("/api/search/stream", methods=["POST"])
def search_stream():
    data = request.json
    query = data.get("query", "").strip()
    # Strip leading @ for username searches
    if query.startswith("@"):
        query = query[1:]
    sources = data.get("sources", ["vk", "maigret"])
    if not isinstance(sources, list):
        sources = list(sources) if sources else ["vk", "maigret"]
    sources = [s for s in sources if isinstance(s, str)]
    maigret_limit = data.get("maigret_limit", 100)
    maigret_limit = int(maigret_limit) if maigret_limit is not None else 100
    ai_lang = data.get("ai_lang", "ru")

    # Auto-detect query type from content (phone removed in v1.3)
    if "@" in query and "." in query.split("@")[-1]:
        query_type = "email"
    else:
        query_type = "username"

    def generate():
        def send(event, payload):
            return f"data: {json.dumps({'event': event, 'data': payload}, ensure_ascii=False)}\n\n"

        # Collector — собираем все результаты для передачи в ИИ
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
            yield from search_emailrep(query, send, collected)
            yield from search_skype(query, send, collected)
            yield from search_protonmail(query, send, collected)
            yield from search_google_account(query, send, collected)
            yield from search_twitter_email(query, send, collected)

        # AI portrait — always load key fresh from keys.json for the requested provider
        req_ai_provider = data.get("ai_provider", "")
        if not isinstance(req_ai_provider, str):
            req_ai_provider = ""
        if req_ai_provider:
            key_map = {
                "gemini":    "GEMINI_API_KEY",
                "openai":    "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY"
            }
            ai_key = get_key(key_map.get(req_ai_provider, ""))
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
                yield send("progress", {"source": "ai", "status": "error", "msg": f"No API key for {req_ai_provider}. Open ⚙ Settings."})

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
    import re
    # Telegram usernames: 5-32 chars, only a-z 0-9 _
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
    label = "all sites" if not limit else f"{limit} sites"
    yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Starting scan ({label})..."})
    try:
        found_sites = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # PYTHONUTF8=1 breaks Python 3.11, use PYTHONIOENCODING instead

        if not _MAIGRET_PYTHON:
            yield send("result", {"source": "maigret", "data": {"error": "maigret not found"}})
            yield send("progress", {"source": "maigret", "status": "error", "msg": "maigret not installed"})
            return

        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(reports_dir, exist_ok=True)

        cmd = [_MAIGRET_PYTHON, "-u", "-m", "maigret", query,
               "--no-color", "--no-progressbar",
               "--timeout", "10",
               "--folderoutput", reports_dir]
        if limit and limit > 0:
            cmd += [f"--top-sites={limit}"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace", env=env,
            cwd=os.path.dirname(os.path.abspath(__file__)), **_nw()
        )
        # Точные совпадения — всегда false positive
        MAIGRET_BLACKLIST_EXACT = {"Roblox"}
        # Префиксы — блокирует все региональные варианты (OP.GG [LeagueOfLegends] Russia, etc.)
        MAIGRET_BLACKLIST_PREFIX = ("OP.GG", "opensea.io", "OpenSea")
        # Сайты, не поддерживающие не-ASCII/кириллические ники
        LATIN_ONLY_SITES = {"Roblox", "OpenSea", "Steam", "Twitch", "Reddit",
                            "Twitter", "Instagram", "TikTok", "Pinterest", "Flickr"}
        query_has_cyrillic = bool(re.search(r'[а-яёА-ЯЁ]', query))

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
                if site_name in MAIGRET_BLACKLIST_EXACT:
                    continue
                if any(site_name.startswith(p) for p in MAIGRET_BLACKLIST_PREFIX):
                    continue
                if query_has_cyrillic and site_name in LATIN_ONLY_SITES:
                    continue
                found_sites.append({"site": site_name, "url": url})
                yield send("maigret_hit", {"site": site_name, "url": url, "count": len(found_sites)})
                yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Found {len(found_sites)} so far..."})
                # If Telegram found — fetch profile immediately
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
        # Repos
        repos_r = requests.get(f"https://api.github.com/users/{query}/repos?per_page=5&sort=pushed",
                                headers={"Accept": "application/vnd.github+json"}, timeout=8)
        repos = []
        if repos_r.status_code == 200:
            repos = [{"name": x["name"], "url": x["html_url"],
                      "stars": x["stargazers_count"], "lang": x["language"]} for x in repos_r.json()]
        data = {
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
            collected["github"] = data
        yield send("result", {"source": "github", "data": data})
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
        data = {
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
            collected["gravatar"] = data
        yield send("result", {"source": "gravatar", "data": data})
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
        # Fetch full profile for top result
        user = items[0]
        profile_r = requests.get(f"https://api.github.com/users/{user['login']}",
                                  headers={"Accept": "application/vnd.github+json"}, timeout=8)
        if profile_r.status_code == 200:
            u = profile_r.json()
            data = {
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
                collected["github_email"] = data
            yield send("result", {"source": "github_email", "data": data})
            yield send("progress", {"source": "github_email", "status": "done", "msg": f"Found: @{data['username']}"})
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
        import socket
        try:
            import dns.resolver
            has_dns = True
        except ImportError:
            has_dns = False
        domain = query.split("@")[-1].lower()
        result = {"source": "email_domain", "domain": domain}

        # Disposable check
        result["disposable"] = domain in DISPOSABLE_DOMAINS

        # MX records
        if has_dns:
          try:
            mx = dns.resolver.resolve(domain, "MX")
            mx_list = sorted([(r.preference, str(r.exchange).rstrip(".")) for r in mx])
            result["mx"] = mx_list
            # Detect mail provider
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
        else:
            result["mx"] = []
            # Fallback: guess from domain name
            if "gmail" in domain or "google" in domain:
                result["mail_provider"] = "Google / Gmail"
            elif "outlook" in domain or "hotmail" in domain or "live" in domain:
                result["mail_provider"] = "Microsoft / Outlook"
            elif "yahoo" in domain:
                result["mail_provider"] = "Yahoo Mail"
            elif "yandex" in domain or "ya.ru" in domain:
                result["mail_provider"] = "Yandex Mail"
            elif "mail.ru" in domain or "bk.ru" in domain or "list.ru" in domain or "inbox.ru" in domain:
                result["mail_provider"] = "Mail.ru"
            elif "proton" in domain:
                result["mail_provider"] = "ProtonMail"
            else:
                result["mail_provider"] = domain

        # A record — does domain exist?
        if has_dns:
          try:
            a = dns.resolver.resolve(domain, "A")
            result["ip"] = str(list(a)[0])
          except Exception:
            result["ip"] = None
        else:
          try:
            result["ip"] = socket.gethostbyname(domain)
          except Exception:
            result["ip"] = None

        # WHOIS via RDAP (no external lib needed)
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

        # Free vs corporate
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
    yield send("progress", {"source": "holehe", "status": "searching", "msg": "Running Holehe scan..."})
    try:
        sites = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if not _HOLEHE_PYTHON:
            yield send("result", {"source": "holehe", "data": {"error": "holehe not found"}})
            yield send("progress", {"source": "holehe", "status": "error", "msg": "holehe not installed"})
            return

        cmd_h = [_HOLEHE_PYTHON, "-m", "holehe.holehe", query, "--no-color"]
        proc = subprocess.Popen(
            cmd_h,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace", env=env, **_nw()
        )
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # [+] = email used (confirmed)
            if line.startswith("[+]"):
                m = re.search(r'\[\+\]\s+(\S+)(?:\s+(https?://\S+))?', line)
                site = m.group(1) if m else line.split()[-1]
                url  = m.group(2) if m and m.group(2) else ""
                sites.append({"site": site, "url": url, "confidence": 100, "label": "Confirmed"})
                confirmed_count = len([s for s in sites if s["confidence"] == 100])
                yield send("holehe_hit", {"site": site, "count": confirmed_count})
                yield send("progress", {"source": "holehe", "status": "searching", "msg": f"Found {confirmed_count} confirmed..."})
            # [x] = rate limited (probably registered)
            elif line.startswith("[x]"):
                m = re.search(r'\[x\]\s+(\S+)(?:\s+(https?://\S+))?', line)
                site = m.group(1) if m else line.split()[-1]
                url  = m.group(2) if m and m.group(2) else ""
                sites.append({"site": site, "url": url, "confidence": 60, "label": "Likely"})
        try:
            proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
        confirmed    = [s for s in sites if s["confidence"] == 100]
        rate_limited = [s for s in sites if s["confidence"] < 100]
        if collected is not None:
            collected["holehe"] = {"sites": sites, "found": confirmed, "rate_limited": rate_limited}
        yield send("result", {"source": "holehe", "data": {
            "sites": sites,
            "found": confirmed,
            "rate_limited": rate_limited,
            "total": len(sites)
        }})
        yield send("progress", {"source": "holehe", "status": "done",
                                 "msg": f"Done - {len(confirmed)} confirmed, {len(rate_limited)} likely"})
    except Exception as e:
        yield send("result", {"source": "holehe", "data": {"error": str(e)}})
        yield send("progress", {"source": "holehe", "status": "error", "msg": str(e)})

def search_email_hibp(query, send, collected=None):
    yield send("progress", {"source": "hibp", "status": "searching", "msg": "Checking HaveIBeenPwned..."})
    try:
        import httpx
        hibp_key = get_key("HIBP_API_KEY")
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
            yield send("progress", {"source": "hibp", "status": "done", "msg": "No breaches found ✓"})
        elif r.status_code == 401:
            yield send("result", {"source": "hibp", "data": {"error": "API key required — get it at haveibeenpwned.com/API/Key"}})
            yield send("progress", {"source": "hibp", "status": "error", "msg": "API key required"})
        else:
            yield send("result", {"source": "hibp", "data": {"error": f"HTTP {r.status_code}"}})
            yield send("progress", {"source": "hibp", "status": "error", "msg": f"HTTP {r.status_code}"})
    except Exception as e:
        yield send("result", {"source": "hibp", "data": {"error": str(e)}})
        yield send("progress", {"source": "hibp", "status": "error", "msg": str(e)})

# ── Emailrep.io ─────────────────────────────────────────────────────────────
def search_emailrep(query, send, collected=None):
    yield send("progress", {"source": "emailrep", "status": "searching", "msg": "Checking email reputation..."})
    try:
        r = requests.get(
            f"https://emailrep.io/{query}",
            headers={"User-Agent": "KikiOSINT/1.5", "Accept": "application/json"},
            timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            rep = d.get("reputation", "none")  # high / medium / low / none
            suspicious = d.get("suspicious", False)
            details = d.get("details", {})
            result = {
                "reputation":        rep,
                "suspicious":        suspicious,
                "spam":              details.get("spam", False),
                "malicious_activity":details.get("malicious_activity", False),
                "credentials_leaked":details.get("credentials_leaked", False),
                "data_breach":       details.get("data_breach", False),
                "first_seen":        details.get("first_seen", "unknown"),
                "last_seen":         details.get("last_seen", "unknown"),
                "days_since_domain_creation": details.get("days_since_domain_creation"),
                "profiles":          details.get("profiles", []),
                "references":        details.get("references", 0),
                "blacklisted":       details.get("blacklisted", False),
                "free_provider":     details.get("free_provider", False),
                "disposable":        details.get("disposable", False),
                "deliverable":       details.get("deliverable", False),
            }
            if collected is not None:
                collected["emailrep"] = result
            yield send("result", {"source": "emailrep", "data": result})
            yield send("progress", {"source": "emailrep", "status": "done",
                                     "msg": f"Reputation: {rep}{' ⚠ suspicious' if suspicious else ''}"})
        elif r.status_code == 429:
            # Rate limited — try hunter.io as fallback (no key needed for basic check)
            yield send("progress", {"source": "emailrep", "status": "searching", "msg": "Rate limited, trying fallback..."})
            try:
                h = requests.get(
                    f"https://api.hunter.io/v2/email-verifier?email={query}",
                    timeout=8
                )
                if h.status_code == 200:
                    hd = h.json().get("data", {})
                    result = {
                        "reputation": "unknown (emailrep rate limited)",
                        "deliverable": hd.get("result") == "deliverable",
                        "disposable": hd.get("disposable", False),
                        "free_provider": hd.get("webmail", False),
                        "score": hd.get("score"),
                        "sources": [s.get("domain","") for s in hd.get("sources",[])[:5]],
                    }
                    if collected is not None:
                        collected["emailrep"] = result
                    yield send("result", {"source": "emailrep", "data": result})
                    yield send("progress", {"source": "emailrep", "status": "done", "msg": f"Fallback: deliverable={result['deliverable']}"})
                else:
                    yield send("result", {"source": "emailrep", "data": {"error": "Rate limited (429)"}})
                    yield send("progress", {"source": "emailrep", "status": "error", "msg": "Rate limited — try again later"})
            except Exception:
                yield send("result", {"source": "emailrep", "data": {"error": "Rate limited (429)"}})
                yield send("progress", {"source": "emailrep", "status": "error", "msg": "Rate limited — try again later"})
        elif r.status_code == 400:
            yield send("result", {"source": "emailrep", "data": {"error": "Invalid email address"}})
            yield send("progress", {"source": "emailrep", "status": "error", "msg": "Invalid email"})
        else:
            yield send("result", {"source": "emailrep", "data": {"error": f"HTTP {r.status_code}"}})
            yield send("progress", {"source": "emailrep", "status": "error", "msg": f"HTTP {r.status_code}"})
    except Exception as e:
        yield send("result", {"source": "emailrep", "data": {"error": str(e)}})
        yield send("progress", {"source": "emailrep", "status": "error", "msg": str(e)})


# ── Skype lookup ─────────────────────────────────────────────────────────────
def search_skype(query, send, collected=None):
    yield send("progress", {"source": "skype", "status": "searching", "msg": "Looking up Skype..."})
    try:
        # Microsoft people search endpoint — returns Skype profile if email linked
        r = requests.get(
            f"https://skype.com/en/search/?username={query}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=True
        )
        # Try the actual Skype search API
        r2 = requests.get(
            f"https://api.skype.com/users/self/contacts/search?searchstring={query}&requestId=1",
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Skypetoken": "",
            },
            timeout=8
        )
        # Alternative: check via Microsoft account recovery page scrape
        r3 = requests.post(
            "https://account.live.com/API/CheckAvailableSigninNames",
            json={"signInName": query},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            timeout=8
        )
        result = {}
        if r3.status_code == 200:
            d = r3.json()
            # IfExistsResult: 0 = doesn't exist, 1 = exists
            exists = d.get("IfExistsResult", -1)
            result["account_exists"] = (exists == 1)
            result["is_federated"]   = d.get("IsFederated", False)
            result["throttled"]      = d.get("ThrottleStatus", 0) != 0
        else:
            result["account_exists"] = None
            result["note"] = "Could not verify (endpoint unavailable)"

        if collected is not None:
            collected["skype"] = result
        yield send("result", {"source": "skype", "data": result})
        if result.get("account_exists"):
            yield send("progress", {"source": "skype", "status": "done", "msg": "Microsoft account found"})
        elif result.get("account_exists") is False:
            yield send("progress", {"source": "skype", "status": "done", "msg": "No Microsoft account"})
        else:
            yield send("progress", {"source": "skype", "status": "done", "msg": "Check inconclusive"})
    except Exception as e:
        yield send("result", {"source": "skype", "data": {"error": str(e)}})
        yield send("progress", {"source": "skype", "status": "error", "msg": str(e)})


# ── ProtonMail check ─────────────────────────────────────────────────────────
def search_protonmail(query, send, collected=None):
    # Only relevant for proton.me / protonmail.com / pm.me addresses
    domain = query.split("@")[-1].lower() if "@" in query else ""
    proton_domains = {"proton.me", "protonmail.com", "protonmail.ch", "pm.me"}
    if domain not in proton_domains:
        return  # skip silently — not a ProtonMail address
    yield send("progress", {"source": "protonmail", "status": "searching", "msg": "Checking ProtonMail..."})
    try:
        r = requests.get(
            f"https://api.proton.me/core/v4/keys?Email={query}",
            headers={"User-Agent": "Mozilla/5.0", "x-pm-appversion": "Web_4.0.0"},
            timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            keys = d.get("Keys", [])
            result = {
                "exists": True,
                "key_count": len(keys),
                "email": query,
                "key_fingerprints": [k.get("Fingerprint", "")[:16] for k in keys[:3]]
            }
            if collected is not None:
                collected["protonmail"] = result
            yield send("result", {"source": "protonmail", "data": result})
            yield send("progress", {"source": "protonmail", "status": "done",
                                     "msg": f"ProtonMail account found ({len(keys)} key{'s' if len(keys)!=1 else ''})"})
        elif r.status_code == 422:
            result = {"exists": False, "email": query}
            if collected is not None:
                collected["protonmail"] = result
            yield send("result", {"source": "protonmail", "data": result})
            yield send("progress", {"source": "protonmail", "status": "done", "msg": "No ProtonMail account"})
        else:
            yield send("result", {"source": "protonmail", "data": {"error": f"HTTP {r.status_code}"}})
            yield send("progress", {"source": "protonmail", "status": "error", "msg": f"HTTP {r.status_code}"})
    except Exception as e:
        yield send("result", {"source": "protonmail", "data": {"error": str(e)}})
        yield send("progress", {"source": "protonmail", "status": "error", "msg": str(e)})


# 🔍 Google Account check ─────────────────────────────────────────────────────
def search_google_account(query, send, collected=None):
    yield send("progress", {"source": "google_account", "status": "searching", "msg": "Checking Google account..."})
    try:
        # Use Epieos-style approach: Google People API returns photo if account exists
        photo_url = f"https://profiles.google.com/{query}/photo"
        r = requests.get(photo_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8, allow_redirects=True)
        has_photo = r.status_code == 200 and "image" in r.headers.get("content-type", "")

        # Check via Google account recovery lookup (unofficial)
        lookup_r = requests.post(
            "https://accounts.google.com/_/lookup/accountlookup",
            data={"continue": "https://myaccount.google.com/", "email": query, "flowName": "GlifWebSignIn"},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=8, allow_redirects=False
        )

        # Alternative: Gravatar-style Google photo
        import hashlib
        email_hash = hashlib.md5(query.lower().encode()).hexdigest()
        grav_check = requests.get(
            f"https://www.gravatar.com/avatar/{email_hash}?d=404",
            timeout=6
        )

        exists = lookup_r.status_code in (302, 200) or has_photo
        result = {
            "exists": exists,
            "photo": photo_url if has_photo else None,
            "services": (["Gmail"] if exists else []) + (["Gravatar"] if grav_check.status_code == 200 else [])
        }
        if collected is not None:
            collected["google_account"] = result
        yield send("result", {"source": "google_account", "data": result})
        yield send("progress", {"source": "google_account", "status": "done",
                                 "msg": "Google account found" if exists else "No Google account detected"})
    except Exception as e:
        yield send("result", {"source": "google_account", "data": {"error": str(e)}})
        yield send("progress", {"source": "google_account", "status": "error", "msg": str(e)})


# 🐦 Twitter/X email check ────────────────────────────────────────────────────
def search_twitter_email(query, send, collected=None):
    yield send("progress", {"source": "twitter_email", "status": "searching", "msg": "Checking X/Twitter..."})
    try:
        # Twitter deprecated guest token API in 2023.
        # Use password-reset page check instead (doesn't require auth)
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

        # Step 1: fetch flow token from password reset page
        r1 = session.post(
            "https://api.twitter.com/1.1/onboarding/task.json?flow_name=password_reset",
            json={"input_flow_data": {"flow_context": {"debug_overrides": {}, "start_location": {"location": "login"}}},
                  "subtask_versions": {}},
            headers={
                "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCbkeQkEgvPEO20uj",
                "Content-Type": "application/json",
            },
            timeout=8
        )
        flow_token = None
        if r1.status_code == 200:
            flow_token = r1.json().get("flow_token")

        if not flow_token:
            # Final fallback: check if email appears in any public Twitter search results
            raise Exception("X/Twitter API unavailable")

        # Step 2: submit email
        r2 = session.post(
            "https://api.twitter.com/1.1/onboarding/task.json",
            json={"flow_token": flow_token,
                  "subtask_inputs": [{"subtask_id": "PasswordResetBeginSubtask",
                                       "enter_text": {"text": query, "link": "next_link"}}]},
            headers={
                "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCbkeQkEgvPEO20uj",
                "Content-Type": "application/json",
            },
            timeout=8
        )
        result = {"exists": False}
        if r2.status_code == 200:
            data = r2.json()
            subtasks = data.get("subtasks", [])
            # If we get a SelectAccountSubtask or EnterPasswordSubtask → email exists
            found_ids = [s.get("subtask_id","") for s in subtasks]
            exists = any(x in found_ids for x in
                         ["PasswordResetChooseChallenge","LoginSelectAccount","EnterPassword"])
            obfuscated = ""
            for s in subtasks:
                for k in ["enter_text","show_user","select_account"]:
                    v = s.get(k, {})
                    if isinstance(v, dict) and v.get("header", {}).get("text"):
                        obfuscated = v["header"]["text"]
            result = {"exists": exists, "obfuscated": obfuscated}
        elif r2.status_code in (400, 403):
            # 400 = "We couldn't find your account" → not registered
            result = {"exists": False}

        if collected is not None:
            collected["twitter_email"] = result
        yield send("result", {"source": "twitter_email", "data": result})
        yield send("progress", {"source": "twitter_email", "status": "done",
                                 "msg": "X/Twitter account found" if result.get("exists") else "No X/Twitter account"})
    except Exception as e:
        yield send("result", {"source": "twitter_email", "data": {"error": str(e)}})
        yield send("progress", {"source": "twitter_email", "status": "error", "msg": str(e)})


def generate_portrait(query, query_type, config, collected=None, ai_lang="ru"):
    # Формируем контекст из реально собранных данных
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

    elif config["provider"] == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=config["api_key"])
        resp = client.chat.completions.create(model="gpt-4o-mini", max_tokens=1000,
            messages=[{"role": "user", "content": prompt}])
        return {"portrait": resp.choices[0].message.content}

    elif config["provider"] == "gemini":
        try:
            from google import genai
        except ImportError:
            return {"error": "google-genai not installed. Run: py -m pip install google-genai"}
        client = genai.Client(api_key=config["api_key"])
        with _gemini_dns_ctx():
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return {"portrait": resp.text}

    return {"error": "Unknown provider"}

if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "frontend"), exist_ok=True)
    app.run(debug=False, port=5000, threaded=True)
