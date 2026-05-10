#!/usr/bin/env python3
"""
WiFi Handshake Cracker — xeno/matrix aesthetic
Flask backend + embedded HTML frontend
"""

import os, subprocess, threading, re, json, time
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

# ──────────────────────────────────────────────────────────────
BASE        = r"C:\HashCat\hashcat-7.1.2"
HASHCAT_EXE = os.path.join(BASE, "hashcat.exe")
WORDLIST    = os.path.join(BASE, "rockyou.txt")
HCXTOOL     = os.path.join(BASE, "hcxpcapngtool.exe")
TSHARK      = r"C:\Program Files\Wireshark\tshark.exe"
HASHES_DIR  = os.path.join(BASE, "hashes")
# ──────────────────────────────────────────────────────────────

os.makedirs(HASHES_DIR, exist_ok=True)

def win_to_wsl(path):
    """C:\foo\bar -> /mnt/c/foo/bar"""
    path = path.replace("\\", "/").replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest  = path[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return path.replace("\\", "/")

def wsl_available():
    try:
        r = subprocess.run(["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except: return False

def hcxtool_wsl_available():
    try:
        r = subprocess.run(["wsl", "which", "hcxpcapngtool"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except: return False

def install_hcxtools_wsl():
    log("sys", "installing hcxtools via WSL...")
    try:
        r = subprocess.run(
            ["wsl", "sudo", "apt-get", "install", "-y", "hcxtools"],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            log("ok",  "hcxtools installed")
            return True
        else:
            log("err", "apt install failed: " + r.stderr[:200])
            return False
    except Exception as e:
        log("err", "install error: " + str(e))
        return False


app   = Flask(__name__)
_proc = None
_log  = []

def ts():   return datetime.now().strftime("%H:%M:%S")
def log(tag, msg):
    _log.append({"ts": ts(), "tag": tag, "msg": msg})
    if len(_log) > 600: _log.pop(0)

@app.route("/")
def index(): return render_template_string(HTML)

@app.route("/api/pick")
def pick_file():
    import subprocess, tempfile, os
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk(); root.withdraw()\n"
        "root.wm_attributes('-topmost', True)\n"
        "p = filedialog.askopenfilename(title='Select PCAP',filetypes=[('PCAP','*.pcap *.pcapng *.cap'),('All','*.*')])\n"
        "print(p or '', end='')\n"
    )
    tmp = tempfile.mktemp(suffix=".py")
    with open(tmp, "w") as fh:
        fh.write(script)
    try:
        r = subprocess.run(["python", tmp], capture_output=True, text=True, timeout=60)
        path = r.stdout.strip()
    except Exception as e:
        path = ""
    finally:
        try: os.remove(tmp)
        except: pass
    return jsonify({"path": path})



@app.route("/api/status")
def status():
    return jsonify({
        "hashcat": os.path.exists(HASHCAT_EXE),
        "rockyou": os.path.exists(WORDLIST),
        "hcxtool": True,  # uses hashcat.net/cap2hashcat online
        "tshark":  os.path.exists(TSHARK),
        "base":    BASE,
    })

@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.json or {}
    pcap = data.get("pcap","").strip()
    if not pcap or not os.path.exists(pcap):
        return jsonify({"ok":False,"error":f"Файл не найден: {pcap}"})
    _log.clear()
    log("sys", f"TARGET  {Path(pcap).name}")
    log("sys", f"SIZE    {os.path.getsize(pcap)//1024} KB")
    result = {"ok":True,"eapol":0,"ssid":"—","bssid":"—","hash_file":None,"error":None}

    if os.path.exists(TSHARK):
        try:
            r = subprocess.run([
                TSHARK,"-r",pcap,"-Y","eapol",
                "-T","fields","-e","wlan.sa","-e","wlan_mgt.ssid"
            ], capture_output=True, text=True, timeout=30)
            lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
            result["eapol"] = len(lines)
            bssids,ssids=[],[]
            for line in lines:
                p=line.split("\t")
                if len(p)>0 and p[0]: bssids.append(p[0])
                if len(p)>1 and p[1]: ssids.append(p[1])
            if ssids:  result["ssid"]  = ssids[0]
            if bssids: result["bssid"] = bssids[0]
            log("ok",  f"EAPOL   {result['eapol']} frames captured")
            if result["ssid"]  != "—": log("data", f"SSID    {result['ssid']}")
            if result["bssid"] != "—": log("data", f"BSSID   {result['bssid']}")
        except Exception as e:
            log("err", f"tshark: {e}")
    else:
        log("warn","tshark not found — skip frame count")

    stem     = Path(pcap).stem
    out_file = os.path.join(HASHES_DIR, f"{stem}.hc22000")
    # Always delete old hash file before fresh conversion
    if os.path.exists(out_file):
        try: os.remove(out_file)
        except: pass

    # Convert via WSL hcxpcapngtool — copy to C:\HashCat (mounted as /mnt/c/HashCat in WSL)
    log("sys", "converting via WSL hcxpcapngtool...")
    try:
        import shutil
        # Copy pcap into the mounted folder so WSL can access it directly (no stdin pipe)
        tmp_win  = os.path.join(HASHES_DIR, "_wc_input.pcap")
        tmp_wsl  = "/mnt/c/HashCat/hashcat-7.1.2/hashes/_wc_input.pcap"
        out_wsl  = win_to_wsl(out_file)
        shutil.copy2(pcap, tmp_win)
        log("dim", f"copied {os.path.getsize(tmp_win)//1024} KB to hashes/_wc_input.pcap")

        # Run hcxpcapngtool directly on the file path (no stdin)
        conv_cmd = ["wsl", "hcxpcapngtool", "-o", out_wsl, tmp_wsl]
        r = subprocess.run(conv_cmd, capture_output=True, text=True, timeout=120)
        out_all = (r.stdout + r.stderr).strip()

        # Log full output (no truncation)
        for line in out_all.splitlines():
            if line.strip():
                log("dim", line)

        # Cleanup temp input
        try: os.remove(tmp_win)
        except: pass

        # Parse hcxpcapngtool summary
        m  = re.search(r"(\d+)\s+(?:unique\s+)?EAPOL", out_all, re.I)
        m2 = re.search(r"ESSID[:\s]+(.+)",  out_all)
        m3 = re.search(r"(?:BSSID|AP)[:\s]+([0-9a-fA-F:]{17})", out_all)
        if m  and result["eapol"] == 0:   result["eapol"] = int(m.group(1))
        if m2 and result["ssid"]  == "—": result["ssid"]  = m2.group(1).strip()
        if m3 and result["bssid"] == "—": result["bssid"] = m3.group(1).strip()

        # Parse SSID/BSSID/EAPOL from hash file directly
        if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
            with open(out_file) as hf:
                hlines = [l.strip() for l in hf if l.startswith("WPA*")]
            if hlines and result["ssid"] == "—":
                try:
                    parts = hlines[0].split("*")
                    if len(parts) > 5 and parts[5]:
                        result["ssid"]  = bytes.fromhex(parts[5]).decode("utf-8","replace")
                        result["bssid"] = ":".join(parts[3][i:i+2] for i in range(0,12,2))
                except: pass
            eapol_lines = [l for l in hlines if l.startswith("WPA*02")]
            if result["eapol"] == 0:
                result["eapol"] = len(eapol_lines)
            log("ok", f"converted: {len(hlines)} hash lines, {len(eapol_lines)} EAPOL")
        else:
            log("warn", "hash file empty — no EAPOL/PMKID in pcap")
            result["error"] = "hash file empty"

    except Exception as e:
        result["error"] = str(e)
        log("err", "convert error: " + str(e))
        return jsonify(result)

    if os.path.exists(out_file) and os.path.getsize(out_file)>0:
        result["hash_file"]=out_file
        log("ok", f"HASH    {Path(out_file).name}")
        log("ok", f"SIZE    {os.path.getsize(out_file)} bytes")
    else:
        log("warn","hash file empty — no EAPOL captured")
        result["error"]="hash file empty"
    return jsonify(result)

@app.route("/api/crack", methods=["POST"])
def crack():
    global _proc
    data=request.json or {}
    hf=data.get("hash_file","").strip()
    if not hf or not os.path.exists(hf):
        return jsonify({"ok":False,"error":"hash file not found"})
    if not os.path.exists(WORDLIST):
        return jsonify({"ok":False,"error":f"wordlist not found: {WORDLIST}"})
    if _proc and _proc.poll() is None:
        return jsonify({"ok":False,"error":"hashcat already running"})
    cracked=hf.replace(".hc22000","_cracked.txt")
    cmd=[HASHCAT_EXE,"-m","22000",hf,WORDLIST,
         "--status","--status-timer=4","--force","-o",cracked]
    log("sys","HASHCAT START")
    log("dim", f"MODE    -m 22000  (WPA-PBKDF2)")
    log("dim", f"DICT    {Path(WORDLIST).name}")
    log("dim", f"TARGET  {Path(hf).name}")
    def run():
        global _proc
        # Check potfile first — password may already be known
        try:
            show0=subprocess.run([HASHCAT_EXE,"-m","22000",hf,"--show"],
                capture_output=True,text=True,cwd=BASE)
            for line in show0.stdout.strip().splitlines():
                if line.count(":")>=4:
                    pwd=line.strip().split(":")[-1]
                    log("result",f"PASSWORD FOUND: {pwd}")
                    log("sys","(retrieved from potfile)")
                    return
        except: pass
        try:
            _proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,text=True,bufsize=1,cwd=BASE)
            for line in _proc.stdout:
                line=line.rstrip()
                if not line: continue
                if any(x in line for x in ["Cracked","cracked"]): tag="ok"
                elif any(x in line for x in ["Status","Progress"]): tag="sys"
                elif any(x in line for x in ["Error","Rejected"]): tag="err"
                elif any(x in line for x in ["Speed","Guess","Time"]): tag="data"
                else: tag="dim"
                log(tag,line)
            _proc.wait()
            # Check cracked file
            if os.path.exists(cracked):
                lines=[l.strip() for l in open(cracked) if l.strip()]
                if lines:
                    pwd=lines[0].split(":")[-1]
                    log("result",f"PASSWORD FOUND: {pwd}")
                    log("dim",f"hash: {lines[0]}")

            # Always check potfile via --show
            try:
                show=subprocess.run([HASHCAT_EXE,"-m","22000",hf,"--show"],
                    capture_output=True,text=True,cwd=BASE)
                for line in show.stdout.strip().splitlines():
                    if line.count(":")>=4:
                        pwd=line.strip().split(":")[-1]
                        log("result",f"PASSWORD FOUND: {pwd}")
                        break
            except: pass

            log("sys","hashcat done")
        except Exception as e: log("err",str(e))
    threading.Thread(target=run,daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def stop():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate(); log("warn","PROCESS TERMINATED")
    return jsonify({"ok":True})

@app.route("/api/log")
def get_log():
    since=int(request.args.get("since",0))
    return jsonify({"entries":_log[since:],"total":len(_log)})

@app.route("/api/running")
def running():
    return jsonify({"running":bool(_proc and _proc.poll() is None)})

# ═══════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WiFi Cracker</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --a:#42f5ef;--a2:rgba(66,245,239,.18);--a3:rgba(66,245,239,.08);--a4:rgba(66,245,239,.04);
  --ab:rgba(66,245,239,.28);--ag:rgba(66,245,239,.55);
  --bg:#071318;--bg2:#0a1c22;--bg3:#0d2229;
  --t1:#e8fffe;--t2:rgba(255,255,255,.82);--t3:rgba(200,220,220,.52);
  --red:#ff4060;--green:#42f5ef;
  --g-bg:rgba(66,245,239,.038);--g-bg2:rgba(66,245,239,.065);
  --g-bd:rgba(66,245,239,.18);--g-bd-h:rgba(66,245,239,.38);
  --g-spec:inset 0 1.5px 0 rgba(255,255,255,.28),inset 0 -1px 0 rgba(0,10,20,.25);
  --g-sh:0 4px 28px rgba(0,0,0,.55),0 1px 4px rgba(0,0,0,.35);
  --g-sh2:0 8px 44px rgba(0,0,0,.65),0 2px 10px rgba(0,0,0,.42);
  --blur:blur(52px) saturate(240%) brightness(1.08);
  --ease-out:cubic-bezier(0.23,1,0.32,1);
  --ease-spring:cubic-bezier(0.34,1.56,0.64,1);
  --r:14px;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:#e8fffe;font-family:'Outfit',sans-serif;font-size:15px}

/* Orbs */
.bg-orbs{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.bg-orbs span{position:absolute;border-radius:50%;animation:orbDrift ease-in-out infinite}
.bg-orbs span:nth-child(1){width:1100px;height:800px;background:radial-gradient(ellipse at 35% 40%,rgba(0,220,210,.2) 0%,rgba(0,80,120,.04) 60%,transparent 72%);filter:blur(65px);top:-25%;left:-18%;animation-duration:28s}
.bg-orbs span:nth-child(2){width:800px;height:700px;background:radial-gradient(ellipse at 50% 50%,rgba(20,200,230,.14) 0%,transparent 68%);filter:blur(80px);top:30%;right:-18%;animation-duration:36s;animation-delay:-12s}
.bg-orbs span:nth-child(3){width:600px;height:600px;background:radial-gradient(ellipse at 50% 50%,rgba(0,230,200,.1) 0%,transparent 70%);filter:blur(90px);bottom:-22%;left:32%;animation-duration:44s;animation-delay:-8s}
@keyframes orbDrift{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(50px,-35px) scale(1.05)}66%{transform:translate(-20px,48px) scale(.95)}}
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.024;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");background-size:256px 256px}
body::after{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(66,245,239,.007) 1px,transparent 1px),linear-gradient(90deg,rgba(66,245,239,.007) 1px,transparent 1px);background-size:68px 68px}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes up{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}

/* Layout */
.root{position:fixed;inset:0;z-index:1;display:flex;flex-direction:column}

/* Header */
.hdr{
  height:52px;display:flex;align-items:center;padding:0 22px;gap:14px;flex-shrink:0;
  border-bottom:1px solid var(--g-bd);
  background:rgba(5,14,20,.88);
  backdrop-filter:blur(20px);
}
.hdr-logo{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;letter-spacing:.2em;color:var(--a);text-transform:uppercase}
.hdr-sep{width:1px;height:18px;background:var(--g-bd)}
.hdr-sub{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.45);letter-spacing:.05em}
.hdr-r{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:11px;color:rgba(255,255,255,.5);font-family:'JetBrains Mono',monospace}
.status-dot{width:7px;height:7px;border-radius:50%;background:var(--t3);transition:all .3s}
.status-dot.ok {background:var(--a);box-shadow:0 0 10px var(--ag)}
.status-dot.err{background:var(--red);box-shadow:0 0 10px var(--red)}
.status-dot.run{background:var(--a);box-shadow:0 0 10px var(--ag);animation:pulse 1s ease-in-out infinite}

/* Body */
.body{flex:1;display:grid;grid-template-columns:300px 1fr;overflow:hidden;min-height:0}

/* Sidebar */
.side{
  border-right:1px solid var(--g-bd);
  overflow-y:auto;
  background:rgba(5,14,20,.92);
}
.side::-webkit-scrollbar{width:2px}
.side::-webkit-scrollbar-thumb{background:var(--g-bd)}

/* Glass card section */
.sec{
  margin:10px 12px;
  background:var(--g-bg);
  border:1px solid var(--g-bd);
  border-radius:var(--r);
  backdrop-filter:var(--blur);
  box-shadow:var(--g-sh),var(--g-spec);
  overflow:hidden;
  transition:border-color .2s,box-shadow .2s;
}
.sec:hover{border-color:var(--g-bd-h);box-shadow:var(--g-sh2),var(--g-spec)}
.sec-h{
  padding:10px 14px 9px;
  font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.18em;
  color:rgba(255,255,255,.45);text-transform:uppercase;
  display:flex;align-items:center;gap:8px;
  border-bottom:1px solid var(--g-bd);
}
.sec-h::after{content:'';flex:1;height:1px;background:var(--g-bd)}
.sec-b{padding:12px 14px;display:flex;flex-direction:column;gap:9px}

/* Pills */
.pills{display:flex;flex-wrap:wrap;gap:5px}
.pill{
  display:flex;align-items:center;gap:5px;padding:4px 10px;
  border:1px solid var(--g-bd);border-radius:20px;
  font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.07em;color:var(--t3);
  background:var(--g-bg);transition:all .2s;
}
.pill .pd{width:5px;height:5px;border-radius:50%;background:currentColor}
.pill.ok  {border-color:rgba(66,245,239,.35);color:var(--a);background:rgba(66,245,239,.06)}
.pill.fail{border-color:rgba(255,64,96,.35); color:var(--red);background:rgba(255,64,96,.06)}

/* Drop zone */
.dz{
  border:1px dashed var(--g-bd);padding:18px 14px;text-align:center;cursor:pointer;
  border-radius:10px;background:var(--a4);
  transition:border-color .2s,background .2s;
}
.dz:hover,.dz.over{border-color:var(--g-bd-h);background:var(--a3)}
.dz-ico{font-size:20px;color:var(--t3);margin-bottom:7px;font-family:'JetBrains Mono',monospace}
.dz-t{font-size:11px;color:rgba(255,255,255,.85);font-weight:500;letter-spacing:.08em}
.dz-s{font-size:10px;color:rgba(255,255,255,.38);margin-top:3px;font-family:'JetBrains Mono',monospace}
.dz-f{font-size:10px;color:var(--a);margin-top:5px;word-break:break-all;font-weight:500}
.dz.has-file .dz-ico,.dz.has-file .dz-t,.dz.has-file .dz-s{display:none}
.dz.has-file{padding:10px 14px}

/* Input */
.lbl{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.12em;color:rgba(255,255,255,.45);margin-bottom:4px;text-transform:uppercase}
.inp{
  width:100%;background:var(--a4);border:1px solid var(--g-bd);
  color:#fff;font-family:'JetBrains Mono',monospace;font-size:11px;
  padding:8px 11px;outline:none;border-radius:8px;
  transition:border-color .15s,background .15s;
}
.inp::placeholder{color:rgba(255,255,255,.28)}
.inp:focus{border-color:var(--g-bd-h);background:var(--a3)}

/* Info rows */
.rows{display:flex;flex-direction:column}
.row{display:flex;align-items:baseline;gap:10px;padding:6px 0;border-bottom:1px solid rgba(66,245,239,.06)}
.row:last-child{border:none}
.rk{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.1em;color:rgba(255,255,255,.45);width:52px;flex-shrink:0;text-transform:uppercase}
.rv{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.75);flex:1;word-break:break-all}
.rv.v {color:var(--t1);font-weight:500}
.rv.ok{color:var(--a)}
.rv.er{color:var(--red)}

/* Progress */
.pb{height:2px;background:var(--g-bd);overflow:hidden;margin-top:8px;border-radius:1px}
.pf{height:100%;width:0%;background:var(--a);transition:width .5s var(--ease-out);box-shadow:0 0 8px var(--ag)}
.pb.ok .pf{background:var(--a);box-shadow:0 0 10px var(--ag)}
.ph{font-family:'JetBrains Mono',monospace;font-size:9px;color:rgba(255,255,255,.45);margin-top:5px}
.ph.ok{color:var(--a)}.ph.er{color:var(--red)}

/* Buttons */
.btn{
  width:100%;padding:11px;border-radius:10px;
  border:1px solid var(--g-bd);
  font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;
  cursor:pointer;background:var(--g-bg);color:rgba(255,255,255,.72);
  position:relative;overflow:hidden;
  box-shadow:var(--g-sh),var(--g-spec);
  transition:color .15s,border-color .15s,background .15s;
}
.btn::after{content:'';position:absolute;left:0;top:0;height:100%;width:0;background:var(--a2);transition:width .28s var(--ease-out)}
.btn:hover{color:#fff;border-color:var(--g-bd-h)}
.btn:hover::after{width:100%}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.22;cursor:not-allowed;pointer-events:none}
.btn span{position:relative;z-index:1}
.btn.go  {border-color:var(--g-bd-h);color:var(--a)}
.btn.go::after{background:var(--a2)}
.btn.go:hover{border-color:var(--a);box-shadow:0 0 20px rgba(66,245,239,.15) inset}
.btn.stop{border-color:rgba(255,64,96,.28);color:var(--red)}
.btn.stop::after{background:rgba(255,64,96,.08)}
.btn.stop:hover{border-color:var(--red)}

/* Result banner */
.res{
  display:none;padding:13px;margin-top:4px;border-radius:10px;
  border:1px solid var(--g-bd-h);background:var(--a3);
  box-shadow:var(--g-sh),var(--g-spec);
}
.res.show{display:block;animation:fadeUp .35s var(--ease-spring)}
.r-l{font-family:'JetBrains Mono',monospace;font-size:8px;letter-spacing:.22em;color:var(--ag);margin-bottom:5px;text-transform:uppercase}
.r-v{font-size:18px;font-weight:800;color:var(--a);word-break:break-all;text-shadow:0 0 28px var(--ag);font-family:'JetBrains Mono',monospace}

/* Terminal */
.term{flex:1;display:flex;flex-direction:column;background:rgba(5,12,18,.85);min-height:0;position:relative;overflow:hidden}
#ft-cv{position:absolute;inset:0;z-index:0;opacity:.15;pointer-events:none}
.term-hdr{
  height:44px;display:flex;align-items:center;padding:0 18px;gap:10px;flex-shrink:0;
  border-bottom:1px solid var(--g-bd);
  background:rgba(7,19,24,.85);
  position:relative;z-index:2;
}
.term-title{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.18em;color:rgba(255,255,255,.42);flex:1;text-transform:uppercase}
.term-clr{font-family:'JetBrains Mono',monospace;font-size:9px;color:rgba(255,255,255,.38);cursor:pointer;padding:4px 8px;border:1px solid transparent;border-radius:6px;transition:color .15s,border-color .15s}
.term-clr:hover{color:#fff;border-color:var(--g-bd)}
.term-body{flex:1;overflow-y:auto;padding:14px 20px;font-size:11px;line-height:1.9;position:relative;z-index:1;overscroll-behavior:contain}
.term-body::-webkit-scrollbar{width:2px}
.term-body::-webkit-scrollbar-thumb{background:var(--g-bd)}
.tl{display:flex;gap:12px;animation:up .1s ease-out}
.ts{color:rgba(255,255,255,.28);flex-shrink:0;font-size:10px;user-select:none;font-family:'JetBrains Mono',monospace;opacity:.6}
.tm{word-break:break-all;white-space:pre-wrap;font-family:'JetBrains Mono',monospace}
.t-sys  .tm{color:rgba(255,255,255,.92)}
.t-ok   .tm{color:var(--a)}
.t-err  .tm{color:var(--red)}
.t-warn .tm{color:#f5c842}
.t-dim  .tm{color:rgba(255,255,255,.42)}
.t-data .tm{color:rgba(255,255,255,.75)}
.t-result .tm{color:var(--a);font-size:13px;font-weight:700;text-shadow:0 0 18px var(--ag)}
</style>
</head>
<body>
<div class="bg-orbs"><span></span><span></span><span></span></div>

<div class="root">
  <header class="hdr">
    <span class="hdr-logo">wificrack</span>
    <div class="hdr-sep"></div>
    <span class="hdr-sub">pcap &rarr; hc22000 &rarr; hashcat-7.1.2</span>
    <div class="hdr-r">
      <div class="status-dot" id="sdot"></div>
      <span id="stxt">idle</span>
    </div>
  </header>

  <div class="body">
    <div class="side">

      <div class="sec">
        <div class="sec-h">dependencies</div>
        <div class="sec-b">
          <div class="pills">
            <div class="pill" id="p-hc"><span class="pd"></span>hashcat</div>
            <div class="pill" id="p-ry"><span class="pd"></span>rockyou</div>
            <div class="pill" id="p-hx"><span class="pd"></span>hcxtool</div>
            <div class="pill" id="p-ts"><span class="pd"></span>tshark</div>
          </div>
        </div>
      </div>

      <div class="sec">
        <div class="sec-h">target</div>
        <div class="sec-b">
          <div class="dz" id="dz" onclick="pick()">
            <div class="dz-ico">//</div>
            <div class="dz-t">CLICK TO BROWSE</div>
            <div class="dz-s">.pcap &nbsp; .pcapng &nbsp; .cap</div>
            <div class="dz-f" id="dzn"></div>
          </div>
          <div>
            <div class="lbl">or paste full path</div>
            <input class="inp" id="pp" placeholder="C:\path\to\capture.pcap" oninput="onP(this.value)">
          </div>
        </div>
      </div>

      <div class="sec">
        <div class="sec-h">analysis</div>
        <div class="sec-b">
          <div class="rows">
            <div class="row" id="row-ssid"><div class="rk">ssid</div>  <div class="rv" id="v-s">&#x2014;</div></div>
            <div class="row"><div class="rk">bssid</div> <div class="rv" id="v-b">&#x2014;</div></div>
            <div class="row"><div class="rk">eapol</div> <div class="rv" id="v-e">&#x2014;</div></div>
            <div class="row"><div class="rk">hash</div>  <div class="rv" id="v-h">&#x2014;</div></div>
            <div class="row"><div class="rk">pass</div>  <div class="rv" id="v-p">&#x2014;</div></div>
          </div>
          <div class="pb" id="pb"><div class="pf" id="pf"></div></div>
          <div class="ph" id="ph">min 2 EAPOL required</div>
        </div>
      </div>

      <div class="sec">
        <div class="sec-h">actions</div>
        <div class="sec-b">
          <button class="btn"      id="bta" onclick="doA()"><span>analyze pcap</span></button>
          <button class="btn go"   id="btc" onclick="doC()" disabled><span>run hashcat</span></button>
          <button class="btn stop" id="bts" onclick="doS()" style="display:none"><span>terminate</span></button>
          <div class="res" id="res">
            <div class="r-l">password found</div>
            <div class="r-v" id="rv"></div>
          </div>
        </div>
      </div>

    </div>

    <div class="term">
      <canvas id="ft-cv"></canvas>
      <div class="term-hdr">
        <span class="term-title">stdout &mdash; <span id="lc" style="opacity:.5">0 lines</span></span>
        <span class="term-clr" onclick="clr()">clear</span>
      </div>
      <div class="term-body" id="tb"></div>
    </div>
  </div>
</div>

<script>
/* FaultyTerminal WebGL */
(function(){
  var cv=document.getElementById('ft-cv');
  if(!cv)return;
  var gl=cv.getContext('webgl');if(!gl)return;
  var vs='attribute vec2 p;void main(){gl_Position=vec4(p,0,1);}';
  var fs='precision mediump float;uniform float T;uniform vec2 R;'+
    'float ns(vec2 p,float t){return sin(p.x*10.)*sin(p.y*(3.+sin(t*.09)))+.2;}'+
    'mat2 ro(float a){return mat2(cos(a),-sin(a),sin(a),cos(a));}'+
    'float fb(vec2 p,float t){float f=0.,a=.5;f+=a*ns(p,t);p=ro(t*.02)*p*2.;a*=.45;f+=a*ns(p,t);p=ro(t*.02)*p*2.;a*=.45;f+=a*ns(p,t);return f;}'+
    'float pt(vec2 p,float t){vec2 q=vec2(fb(p+1.,t),fb(ro(t*.1)*p+1.,t));vec2 r=vec2(fb(ro(.1)*q,t),fb(q,t));return fb(p+r,t);}'+
    'float dg(vec2 p,float t){vec2 g=vec2(26.,13.);vec2 s=floor(p*g)/g;p=p*g;float iv=pt(s*.1,t)*1.4-.05;p=fract(p)*1.3;float px=p.x*5.,py=(1.-p.y)*5.,x=fract(px),y=fract(py);float i=floor(py)-2.,j=floor(px)-2.,n=i*i+j*j;float on=step(.1,iv-n*.0625);return step(0.,p.x)*step(p.x,1.)*step(0.,p.y)*step(p.y,1.)*on*(.2+y*.8)*(.75+x*.25);}'+
    'void main(){float t=T*.28;vec2 uv=gl_FragCoord.xy/R;float m=dg(uv,t);const float o=.002;'+
    'float s=dg(uv+vec2(-o,-o),t)+dg(uv+vec2(0,-o),t)+dg(uv+vec2(o,-o),t)+dg(uv+vec2(-o,0),t)+dg(uv,t)+dg(uv+vec2(o,0),t)+dg(uv+vec2(-o,o),t)+dg(uv+vec2(0,o),t)+dg(uv+vec2(o,o),t);'+
    'vec2 ca=vec2(1.5)/R;float r=dg(uv+ca,t),b=dg(uv-ca,t);float g2=m*.9+s*.07;'+
    'gl_FragColor=vec4(g2*.08+r*.06, g2, g2*.88+b*.06, 1.);}';
  function mk(t,src){var s=gl.createShader(t);gl.shaderSource(s,src);gl.compileShader(s);return s;}
  var pr=gl.createProgram();gl.attachShader(pr,mk(gl.VERTEX_SHADER,vs));gl.attachShader(pr,mk(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);gl.useProgram(pr);
  var bf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,bf);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
  var al=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(al);gl.vertexAttribPointer(al,2,gl.FLOAT,false,0,0);
  var uT=gl.getUniformLocation(pr,'T'),uR=gl.getUniformLocation(pr,'R');
  function rs(){var term=document.querySelector('.term');if(!term)return;cv.width=term.offsetWidth;cv.height=term.offsetHeight;gl.viewport(0,0,cv.width,cv.height);}
  rs();new ResizeObserver(rs).observe(document.querySelector('.term')||document.body);
  function fr(t){requestAnimationFrame(fr);gl.uniform1f(uT,t*.001);gl.uniform2f(uR,cv.width,cv.height);gl.drawArrays(gl.TRIANGLES,0,3);}
  requestAnimationFrame(fr);
})();

/* App logic */
var P='',H2='',LI=0,poll=null;

function st(t,c){var d=document.getElementById('sdot'),s=document.getElementById('stxt');d.className='status-dot '+(c||'');s.textContent=t;s.style.color=c==='ok'?'var(--a)':c==='err'?'var(--red)':c==='run'?'var(--a)':'var(--t3)'}

var dz=document.getElementById('dz');
dz.addEventListener('dragover',function(e){e.preventDefault();dz.classList.add('over')});
dz.addEventListener('dragleave',function(){dz.classList.remove('over')});
dz.addEventListener('drop',function(e){e.preventDefault();dz.classList.remove('over');pick()});

async function pick(){
  try{var r=await fetch('/api/hc/pick'),d=await r.json();if(!d.path)return;P=d.path;var nm=d.path.split(/[\\/]/).pop();document.getElementById('pp').value=d.path;document.getElementById('dzn').textContent=nm;dz.classList.add('has-file');rA();lg('sys','file    '+nm);}catch(e){lg('err','picker: '+e.message)}
}
function onP(v){P=v.trim();var nm=v.split(/[\\/]/).pop();document.getElementById('dzn').textContent=nm||'';dz.classList.toggle('has-file',!!v);if(v){rA();lg('sys','file    '+nm)}}
function rA(){
  ['v-s','v-b','v-e','v-h'].forEach(function(id){var e=document.getElementById(id);if(e){e.textContent='\u2014';e.className='rv'}});
  document.getElementById('pf').style.width='0%';document.getElementById('pb').className='pb';
  document.getElementById('ph').textContent='min 2 EAPOL required';document.getElementById('ph').className='ph';
  document.getElementById('res').classList.remove('show');document.getElementById('btc').disabled=true;H2='';
}
function lg(tag,msg){
  var tb=document.getElementById('tb'),now=new Date().toTimeString().slice(0,8);
  var row=document.createElement('div');row.className='tl t-'+tag;
  var ts=document.createElement('span');ts.className='ts';ts.textContent=now;
  var tm=document.createElement('span');tm.className='tm';
  row.appendChild(ts);row.appendChild(tm);tb.appendChild(row);tb.scrollTop=tb.scrollHeight;
  var i=0,txt=esc(msg);
  function tick(){if(i<txt.length){if(txt[i]==='&'){var end=txt.indexOf(';',i);if(end>-1){tm.innerHTML+=txt.slice(i,end+1);i=end+1;}else tm.innerHTML+=txt[i++];}else tm.innerHTML+=txt[i++];tb.scrollTop=tb.scrollHeight;setTimeout(tick,tag==='dim'?7:11);}else{document.getElementById('lc').textContent=tb.children.length+' lines';if(tag==='result'){var m=msg.match(/PASSWORD FOUND:\s*(.+)/);if(m){var pw=m[1].trim();document.getElementById('rv').textContent=pw;document.getElementById('res').classList.add('show');var vp=document.getElementById('v-p');if(vp){vp.textContent=pw;vp.className='rv ok';}}}}}
  tick();
}
function clr(){document.getElementById('tb').innerHTML='';LI=0;document.getElementById('lc').textContent='0 lines'}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function sv(id,v,c){var e=document.getElementById(id);if(e){e.textContent=v;e.className='rv'+(c?' '+c:'')}}

async function pf2(){
  try{var r=await fetch('/api/hc/log?since='+LI),d=await r.json();d.entries.forEach(function(e){lg(e.tag,e.msg)});LI=d.total;
    var ri=await fetch('/api/hc/running'),rd=await ri.json();
    if(rd.password){var pw=rd.password;document.getElementById('rv').textContent=pw;document.getElementById('res').classList.add('show');var vp=document.getElementById('v-p');if(vp){vp.textContent=pw;vp.className='rv ok';}}
    if(!rd.running&&poll){clearInterval(poll);poll=null;st('idle','');document.getElementById('btc').disabled=false;document.getElementById('btc').style.display='';document.getElementById('bts').style.display='none';}}catch(e){}
}
async function doA(){
  if(!P){lg('err','no file');return}
  st('analyzing','run');document.getElementById('bta').disabled=true;lg('sys','\u2500'.repeat(42));
  try{var r=await fetch('/api/hc/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pcap:P})});
    var d=await r.json();var lr=await fetch('/api/hc/log?since='+LI),ld=await lr.json();ld.entries.forEach(function(e){lg(e.tag,e.m||e.msg)});LI=ld.total;
    if(!d.ok){lg('err',d.error||'error');st('error','err');return}
    // Render SSIDs — multiple if present
    var rowSsid=document.getElementById('row-ssid');
    if(d.ssids&&d.ssids.length>1){
      rowSsid.innerHTML='<div class="rk">ssid</div><div class="rv v" id="v-s" style="display:flex;flex-direction:column;gap:3px">'
        +d.ssids.map(function(s){return '<span style="display:flex;gap:6px;align-items:baseline"><span style="color:var(--a)">'+esc(s.ssid)+'</span><span style="font-size:9px;opacity:.45;font-family:\'JetBrains Mono\',monospace">'+s.count+'h</span></span>';}).join('')+'</div>';
    }else{
      rowSsid.innerHTML='<div class="rk">ssid</div><div class="rv" id="v-s">'+(d.ssid&&d.ssid!=='\u2014'?'<span class="rv v">'+esc(d.ssid)+'</span>':'\u2014')+'</div>';
    }
    if(d.bssid&&d.bssid!=='\u2014')sv('v-b',d.bssid,'v');
    sv('v-e',String(d.eapol),d.eapol>=2?'ok':'er');
    if(d.hash_file){H2=d.hash_file;sv('v-h',d.hash_file.split(/[\\/]/).pop(),'v')}
    document.getElementById('pf').style.width=Math.min(d.eapol/4*100,100)+'%';
    if(d.eapol>=2){document.getElementById('pb').className='pb ok';document.getElementById('ph').textContent=d.eapol+' EAPOL \u2014 ready to crack';document.getElementById('ph').className='ph ok';document.getElementById('btc').disabled=false;st('ready','ok');}
    else{document.getElementById('ph').textContent=d.eapol+' EAPOL \u2014 need \u2265 2';document.getElementById('ph').className='ph er';st('need more eapol','err');}
  }catch(e){lg('err',e.message);st('error','err')}
  document.getElementById('bta').disabled=false;
}
async function doC(){
  if(!H2){lg('err','analyze first');return}
  st('cracking','run');document.getElementById('btc').disabled=true;document.getElementById('btc').style.display='none';document.getElementById('bts').style.display='';document.getElementById('res').classList.remove('show');
  var vp=document.getElementById('v-p');if(vp){vp.textContent='\u2014';vp.className='rv';}
  try{var r=await fetch('/api/hc/crack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hash_file:H2})});var d=await r.json();
    if(!d.ok){lg('err',d.error||'failed');st('error','err');document.getElementById('btc').disabled=false;document.getElementById('btc').style.display='';return}
    poll=setInterval(pf2,800);
  }catch(e){lg('err',e.message);st('error','err')}
}
async function doS(){await fetch('/api/hc/stop',{method:'POST'});if(poll){clearInterval(poll);poll=null}st('stopped','');document.getElementById('bts').style.display='none';document.getElementById('btc').disabled=false;document.getElementById('btc').style.display='';}
async function chk(){
  try{var r=await fetch('/api/hc/status'),d=await r.json();
    [['hashcat','p-hc'],['rockyou','p-ry'],['hcxtool','p-hx'],['tshark','p-ts']].forEach(function(kv){var el=document.getElementById(kv[1]);if(el)el.classList.add(d[kv[0]]?'ok':'fail')});
    st(d.hashcat&&d.rockyou?'ready':'missing deps',d.hashcat&&d.rockyou?'ok':'err');
  }catch(e){}
}
chk();
setTimeout(function(){lg('sys','wificrack ready');lg('dim','mode 22000  WPA-PBKDF2  rockyou attack');lg('dim','\u2500'.repeat(42));},400);

// Auto-analyze pcap sent from Flipper Zero panel via postMessage
window.addEventListener('message',function(e){
  if(!e.data||e.data.type!=='autoanalyze'||!e.data.pcap) return;
  var pcap=e.data.pcap;
  lg('sys','\u2500'.repeat(42));
  lg('sys','AUTO: pcap from Flipper Zero');
  P=pcap;
  var nm=pcap.split(/[\\\\/]/).pop();
  document.getElementById('pp').value=pcap;
  document.getElementById('dzn').textContent=nm;
  document.getElementById('dz').classList.add('has-file');
  setTimeout(doA,300);
});
</script>
</body>
</html>
"""



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5555, debug=False, threaded=True)
