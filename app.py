
#!/usr/bin/env python3
"""KikiHub — unified app: Kiki OSINT + WiFi Cracker. Port 7777"""
import os, sys, shutil, subprocess, threading, re, json
from pathlib import Path
from datetime import datetime

OSINT_DIR = r"C:\Users\newin\osint-portrait"
sys.path.insert(0, OSINT_DIR)

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import keys_store

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
HUB_DIR    = BASE_DIR
OSINT_FE   = os.path.join(OSINT_DIR, "frontend")
OSINT_SRC  = os.path.join(OSINT_DIR, "sources")

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
    return send_from_directory(HUB_DIR, "index.html")

@app.route("/hashcat_logo.png")
def hc_logo():
    return send_from_directory(HUB_DIR, "hashcat_logo.png")

@app.route("/open-padlock.png")
def padlock_icon():
    return send_from_directory(HUB_DIR, "open-padlock.png")

@app.route("/flipper_logo.png")
def flipper_logo():
    return send_from_directory(HUB_DIR, "flipper_logo.png")


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
        "OPENAI_API_KEY":    mask(k.get("OPENAI_API_KEY","")),
        "ANTHROPIC_API_KEY": mask(k.get("ANTHROPIC_API_KEY","")),
        "HIBP_API_KEY":      mask(k.get("HIBP_API_KEY","")),
        "configured":        bool(k.get("VK_TOKEN") or k.get("GEMINI_API_KEY") or k.get("OPENAI_API_KEY") or k.get("ANTHROPIC_API_KEY"))
    })

@app.route("/api/keys", methods=["POST"])
def save_keys_route():
    data = request.json or {}
    k = keys_store.load()
    for field in ["VK_TOKEN","GEMINI_API_KEY","OPENAI_API_KEY","ANTHROPIC_API_KEY","HIBP_API_KEY"]:
        if field in data and data[field] and "•" not in data[field]:
            k[field] = data[field]
    keys_store.save(k)
    return jsonify({"ok":True})

# Forward all OSINT API calls
def _forward_to_osint(path):
    """Forward request to osint app running on port 5050"""
    import requests as req_lib
    try:
        url = f"http://127.0.0.1:5050/api/{path}"
        if request.method=="GET":
            r = req_lib.get(url, params=request.args, timeout=60, stream=True)
        else:
            r = req_lib.request(request.method, url, json=request.json, timeout=60, stream=True)
        return Response(r.iter_content(chunk_size=None), status=r.status_code,
                       content_type=r.headers.get("content-type","application/json"))
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/search",  methods=["POST"])
def osint_search():  return _forward_to_osint("search")

@app.route("/api/stream",  methods=["POST","GET"])
def osint_stream():  return _forward_to_osint("stream")

@app.route("/api/vk/<path:p>")
def osint_vk(p):     return _forward_to_osint(f"vk/{p}")

@app.route("/api/ai",  methods=["POST"])
def osint_ai():      return _forward_to_osint("ai")

@app.route("/api/export",  methods=["POST"])
def osint_export():  return _forward_to_osint("export")

# ════ HASHCAT API ════════════════════════════════════════════

@app.route("/wc/")
@app.route("/wc")
def wc_index():
    """Serve wificracker HTML — reads from wifi_cracker.py"""
    import importlib.util
    wc_path = r"C:\HashCat\hashcat-7.1.2\wifi_cracker.py"
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
        # Write pcap into WSL /tmp via stdin
        subprocess.run(["wsl","sh","-c","cat > /tmp/wc_in.pcap"],
                       input=pcap_bytes, timeout=60)
        log("dim",f"wrote {len(pcap_bytes)//1024} KB to /tmp/wc_in.pcap")
        # Convert — output stays in /tmp
        r=subprocess.run(["wsl","sh","-c",
            "rm -f /tmp/wc_out.hc22000 && hcxpcapngtool --all -o /tmp/wc_out.hc22000 /tmp/wc_in.pcap 2>&1"],
            capture_output=True, text=True, timeout=120)
        out_all=(r.stdout+r.stderr).strip()
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
        # Read result back
        r2=subprocess.run(["wsl","cat","/tmp/wc_out.hc22000"],
                          capture_output=True, text=True, timeout=30)
        hash_content=r2.stdout.strip()
        if hash_content:
            with open(out_file,"w",encoding="utf-8") as fh:
                fh.write(hash_content+"\n")
        if hash_content:
            lines=[l for l in hash_content.splitlines() if l.startswith("WPA*")]
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
        global _proc, _password, _running
        _running = True
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
                if found: break
                log("sys",f"trying: {Path(wl).name}")
                cmd=[HASHCAT_EXE,"-m","22000",hf,wl,"--status","--status-timer=4","--force","-o",cracked]
                try:
                    _proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,cwd=BASE)
                    for line in _proc.stdout: log_hc(line.rstrip())
                    _proc.wait()
                    # Wait for lock files to be released
                    import glob, time as _t
                    for _ in range(20):
                        locks=glob.glob(os.path.join(BASE,"hashcat.pid"))+glob.glob(os.path.join(BASE,"*.pid"))
                        if not locks: break
                        _t.sleep(0.3)
                except Exception as e: log("err",str(e)); break
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
    global _proc
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
    """Read binary file from Flipper via serial CLI with exact byte count."""
    def open_and_flush(port):
        """Open serial, wait for banner, flush it."""
        s = serial.Serial(port, 115200, timeout=3)
        _tm.sleep(0.8)       # wait for Flipper to send banner
        s.reset_input_buffer()  # discard banner completely
        return s

    # 1. Get file size via storage stat
    with open_and_flush(port) as s:
        s.write(f"storage stat {path}\r\n".encode())
        _tm.sleep(1.0)
        raw = s.read_all().decode('utf-8', errors='replace')

    size = None
    for line in raw.splitlines():
        if 'size' in line.lower() and ':' in line:
            digits = ''.join(c for c in line.split(':')[-1] if c.isdigit())
            if digits: size = int(digits); break
    if not size:
        raise RuntimeError(f"could not get file size. stat output: {raw[-300:]}")

    # 2. Read file content — open fresh port, flush banner, send command
    with open_and_flush(port) as s:
        s.write(f"storage read {path}\r\n".encode())
        # Skip 2 header lines: 1) command echo, 2) "Size: N"
        for _ in range(2):
            t = _tm.time() + 4
            line = b""
            while _tm.time() < t:
                b = s.read(1)
                if b:
                    line += b
                    if line.endswith(b'\n'): break
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

if __name__=="__main__":
    print("\n  KikiHub  ->  http://localhost:7777\n")
    # Try to start OSINT backend
    app.run(host="127.0.0.1", port=7777, debug=False, threaded=True)
