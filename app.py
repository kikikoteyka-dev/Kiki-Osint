from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import os, json, subprocess, sys, re
from config import GEMINI_API_KEY

app = Flask(__name__, static_folder="frontend")
CORS(app)

# Автоматически подставляем Gemini если ключ есть в .env
AI_CONFIG = {
    "provider": "gemini" if GEMINI_API_KEY else None,
    "api_key": GEMINI_API_KEY if GEMINI_API_KEY else None
}

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")

@app.route("/kiki_logo.png")
def kiki_logo():
    return send_from_directory("frontend", "kiki_logo.png")

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
    sources = data.get("sources", ["vk", "maigret"])
    maigret_limit = int(data.get("maigret_limit", 100))
    ai_lang = data.get("ai_lang", "ru")

    # Auto-detect query type from content
    if "@" in query and "." in query.split("@")[-1]:
        query_type = "email"
    elif re.match(r"^\+?[\d\s\-\(\)]{7,}$", query):
        query_type = "phone"
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

        elif query_type == "phone":
            yield from search_phone(query, send, collected)
            yield from search_vk_phone(query, send, collected)

        elif query_type == "email":
            yield from search_email_holehe(query, send, collected)
            yield from search_email_hibp(query, send, collected)

        req_ai_provider = data.get("ai_provider", "")
        if req_ai_provider and AI_CONFIG["api_key"] and AI_CONFIG["provider"]:
            yield send("progress", {"source": "ai", "status": "searching", "msg": "Generating AI portrait..."})
            try:
                portrait = generate_portrait(query, query_type, AI_CONFIG, collected, ai_lang)
                yield send("result", {"source": "ai", "data": portrait})
                yield send("progress", {"source": "ai", "status": "done", "msg": "Done"})
            except Exception as e:
                yield send("result", {"source": "ai", "data": {"error": str(e)}})
                yield send("progress", {"source": "ai", "status": "error", "msg": str(e)})

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
        subs  = soup.select_one(".tgme_page_extra")
        result = {
            "name":        name.get_text(strip=True) if name else query,
            "bio":         bio.get_text(strip=True) if bio else "",
            "photo":       photo["src"] if photo and photo.get("src") else "",
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
    yield send("progress", {"source": "maigret", "status": "searching", "msg": f"Starting scan ({limit} sites)..."})
    try:
        found_sites = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "maigret", query,
             "--no-color", f"--top-sites={limit}", "--timeout", "10"],
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
                # Permanently blacklisted prefixes (false positives / irrelevant)
                MAIGRET_BLACKLIST_PREFIXES = ("OP.GG",)
                if any(site_name.startswith(p) for p in MAIGRET_BLACKLIST_PREFIXES):
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

def search_email_holehe(query, send, collected=None):
    yield send("progress", {"source": "holehe", "status": "searching", "msg": "Running Holehe scan..."})
    try:
        found_sites = []
        rate_limited = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Use exe directly, not -m (holehe has no __main__)
        holehe_exe = os.path.join(os.path.dirname(sys.executable), "Scripts", "holehe.exe")
        proc = subprocess.Popen(
            [holehe_exe, query, "--no-color"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace", env=env
        )
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # [+] = email used (confirmed)
            if line.startswith("[+]"):
                m = re.search(r'\[\+\]\s+(\S+)', line)
                site = m.group(1) if m else line.split()[-1]
                found_sites.append({"site": site, "url": "", "confirmed": True})
                yield send("holehe_hit", {"site": site, "count": len(found_sites)})
                yield send("progress", {"source": "holehe", "status": "searching", "msg": f"Found {len(found_sites)} confirmed..."})
            # [x] = rate limited (probably registered)
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
        headers = {"User-Agent": "OSINT-Portrait/1.0"}
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
        from google import genai
        client = genai.Client(api_key=config["api_key"])
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return {"portrait": resp.text}

    return {"error": "Unknown provider"}

if __name__ == "__main__":
    os.makedirs("frontend", exist_ok=True)
    app.run(debug=False, port=5000, threaded=True)
