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
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@400;500;600;700;800&family=Hanken+Grotesk:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
:root{
  --a:#5cd6ff;--a2:rgba(92,214,255,.18);--a3:rgba(92,214,255,.08);--a4:rgba(92,214,255,.04);
  --ab:rgba(92,214,255,.28);--ag:rgba(92,214,255,.55);
  --bg:#04111a;--bg2:#081c2a;--bg3:#0b2433;
  --t1:#eef2f7;--t2:rgba(255,255,255,.84);--t3:rgba(255,255,255,.58);
  --red:#ff4060;--green:#5cd6ff;
  --g-bg:rgba(255,255,255,.03);
  --g-bg2:rgba(255,255,255,.015);
  --g-bd:rgba(255,255,255,.06);
  --g-bd2:rgba(255,255,255,.08);
  --g-bd-h:rgba(92,214,255,.28);
  --ease-out:cubic-bezier(0.23,1,0.32,1);
  --ease-spring:cubic-bezier(0.34,1.56,0.64,1);
  --r:16px;
  --r2:10px;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:#eef2f7;font-family:'Hanken Grotesk',sans-serif;font-size:16px}

/* Ambient background — matches Kiki OSINT */
.aurora-container{position:fixed;inset:0;z-index:0;pointer-events:none;width:100%;height:100%;opacity:.5;mix-blend-mode:screen;overflow:hidden}
.aurora-container canvas{display:block;width:100%;height:100%}
.bg-orbs{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.bg-orbs span{position:absolute;border-radius:50%;animation:orbDrift ease-in-out infinite}
.bg-orbs span:nth-child(1){width:1200px;height:900px;background:radial-gradient(ellipse at 35% 40%,rgba(92,214,255,.16) 0%,rgba(5,20,35,.04) 60%,transparent 72%);filter:blur(60px);top:-25%;left:-20%;animation-duration:28s}
.bg-orbs span:nth-child(2){width:900px;height:800px;background:radial-gradient(ellipse at 50% 50%,rgba(92,214,255,.12) 0%,transparent 68%);filter:blur(80px);top:25%;right:-20%;animation-duration:36s;animation-delay:-12s}
.bg-orbs span:nth-child(3){width:700px;height:700px;background:radial-gradient(ellipse at 50% 50%,rgba(143,227,255,.10) 0%,transparent 70%);filter:blur(90px);bottom:-25%;left:30%;animation-duration:44s;animation-delay:-8s}
@keyframes orbDrift{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(55px,-40px) scale(1.06)}66%{transform:translate(-18px,52px) scale(.94)}}
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.025;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");background-size:256px 256px}
body::after{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(92,214,255,.008) 1px,transparent 1px),linear-gradient(90deg,rgba(92,214,255,.008) 1px,transparent 1px);background-size:68px 68px}
.scanline{position:fixed;left:0;width:100%;height:2px;z-index:200;pointer-events:none;background:linear-gradient(transparent,rgba(255,255,255,.04),transparent);animation:scanMove 7s linear infinite}
@keyframes scanMove{0%{transform:translateY(-100vh)}100%{transform:translateY(100vh)}}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes up{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}

/* Layout */
.root{position:fixed;inset:0;z-index:1;display:flex;flex-direction:column}

/* Header */
.hdr{
  height:48px;display:flex;align-items:center;padding:0 20px;gap:14px;flex-shrink:0;
  border-bottom:1px solid var(--g-bd);
  background:var(--g-bg2);
  position:relative;z-index:2;
}
.hdr-logo{display:flex;align-items:center;gap:8px;font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;letter-spacing:.18em;color:var(--a);text-transform:uppercase}
.hdr-logo::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--a);animation:pulse 2.5s ease-in-out infinite}
.hdr-sep{width:1px;height:18px;background:var(--g-bd)}
.hdr-sub{font-family:'JetBrains Mono',monospace;font-size:11px;color:rgba(255,255,255,.45);letter-spacing:.05em}
.hdr-r{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;color:rgba(255,255,255,.5);font-family:'JetBrains Mono',monospace}
.status-dot{width:7px;height:7px;border-radius:50%;background:var(--t3);transition:all .3s}
.status-dot.ok {background:var(--a)}
.status-dot.err{background:var(--red)}
.status-dot.run{background:var(--a);animation:pulse 1s ease-in-out infinite}

/* Body */
.body{flex:1;display:grid;grid-template-columns:296px 1fr;overflow:hidden;min-height:0;gap:10px;padding:10px}

/* Sidebar */
.side{
  display:flex;flex-direction:column;gap:8px;
  overflow-y:auto;
}
.side::-webkit-scrollbar{width:2px}
.side::-webkit-scrollbar-thumb{background:rgba(92,214,255,.22)}

/* Card section */
.sec{
  background:var(--g-bg);
  border:1px solid var(--g-bd);
  border-radius:var(--r);
  overflow:hidden;
  position:relative;
  transition:border-color .25s var(--ease-out),background .25s var(--ease-out),transform .25s var(--ease-out),box-shadow .25s var(--ease-out);
  flex-shrink:0;
}
.sec:hover{border-color:var(--g-bd-h);background:rgba(92,214,255,.05);transform:translateY(-2px);box-shadow:0 12px 28px -16px rgba(92,214,255,.35)}
.sec::before{
  content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(220px circle at var(--mx,50%) var(--my,50%),rgba(92,214,255,.10),transparent 70%);
  opacity:0;transition:opacity .35s var(--ease-out);z-index:0;
}
.sec:hover::before{opacity:1}
.sec-h,.sec-b{position:relative;z-index:1}
.sec-h{
  padding:10px 14px;
  font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.18em;
  color:rgba(92,214,255,.7);text-transform:uppercase;
  display:flex;align-items:center;gap:8px;
  border-bottom:1px solid var(--g-bd);
}
.sec-h::before{content:'';width:10px;height:1px;flex-shrink:0;background:rgba(92,214,255,.4)}
.sec-b{padding:12px 14px;display:flex;flex-direction:column;gap:8px}

/* Pills */
.pills{display:flex;flex-wrap:wrap;gap:4px}
.pill{
  display:flex;align-items:center;gap:5px;padding:4px 10px;
  border:1px solid var(--g-bd2);border-radius:var(--r2);
  font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:500;letter-spacing:.07em;color:var(--t3);
  background:var(--g-bg);transition:all .2s;
}
.pill .pd{width:5px;height:5px;border-radius:50%;background:currentColor}
.pill.ok  {border-color:rgba(92,214,255,.28);color:var(--a);background:rgba(92,214,255,.08)}
.pill.fail{border-color:rgba(255,64,96,.28); color:var(--red);background:rgba(255,64,96,.08)}

/* Drop zone */
.dz{
  border:1px dashed var(--g-bd2);padding:13px 14px;text-align:center;cursor:pointer;
  border-radius:var(--r2);background:var(--g-bg);
  display:flex;align-items:center;justify-content:center;gap:8px;
  transition:border-color .2s,background .2s;
}
.dz:hover,.dz.over{border-color:rgba(92,214,255,.3);background:rgba(92,214,255,.05)}
.dz-ico{font-size:15px;color:var(--t3);font-family:'JetBrains Mono',monospace}
.dz-t{font-size:12px;color:rgba(255,255,255,.85);font-weight:500;letter-spacing:.08em}
.dz-f{font-size:11px;color:var(--a);word-break:break-all;font-weight:500}
.dz.has-file .dz-ico,.dz.has-file .dz-t{display:none}

/* Input */
.lbl{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.12em;color:rgba(255,255,255,.48);margin-bottom:3px;text-transform:uppercase}
.inp{
  width:100%;background:var(--g-bg);border:1px solid var(--g-bd2);
  color:#fff;font-family:'JetBrains Mono',monospace;font-size:12px;
  padding:7px 12px;outline:none;border-radius:var(--r2);
  transition:border-color .2s,background .2s;
}
.inp::placeholder{color:rgba(255,255,255,.25)}
.inp:focus{border-color:rgba(92,214,255,.35);background:rgba(92,214,255,.05)}

/* Info rows */
.rows{display:flex;flex-direction:column}
.row{display:flex;align-items:baseline;gap:10px;padding:4px 0;border-bottom:1px solid rgba(92,214,255,.06)}
.row:last-child{border:none}
.rk{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.1em;color:rgba(255,255,255,.48);width:56px;flex-shrink:0;text-transform:uppercase}
.rv{font-family:'JetBrains Mono',monospace;font-size:11px;color:rgba(255,255,255,.78);flex:1;word-break:break-all}
.rv.v {color:var(--t1);font-weight:500}
.rv.ok{color:var(--a)}
.rv.er{color:var(--red)}

/* Progress */
.pb{height:6px;background:rgba(255,255,255,.06);overflow:hidden;margin-top:6px;border-radius:var(--r2)}
.pf{height:100%;width:0%;background:linear-gradient(90deg,#5cd6ff,#8fe3ff);transition:width .5s var(--ease-out);border-radius:var(--r2)}
.pb.ok .pf{background:linear-gradient(90deg,#5cd6ff,#8fe3ff)}
.ph{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.48);margin-top:4px}
.ph.ok{color:var(--a)}.ph.er{color:var(--red)}

/* Buttons */
.btn{
  width:100%;padding:11px 12px;border-radius:var(--r2);
  border:1px solid var(--g-bd2);
  font-family:'Switzer','Bricolage Grotesque',sans-serif;font-size:13px;font-weight:600;letter-spacing:.02em;
  cursor:pointer;background:var(--g-bg);color:var(--t2);
  position:relative;overflow:hidden;
  transition:color .15s,border-color .15s,background .15s,transform .15s;
}
.btn:hover{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.14)}
.btn:active{transform:scale(.97);transition:transform 100ms var(--ease-out)}
.btn:disabled{opacity:.3;cursor:not-allowed;pointer-events:none}
.btn span{position:relative;z-index:1}
.btn.go  {background:rgba(92,214,255,.08);border-color:rgba(92,214,255,.18);color:var(--a)}
.btn.go:hover{background:rgba(92,214,255,.14);border-color:rgba(92,214,255,.32);color:#fff}
.btn.stop{background:rgba(255,64,96,.08);border-color:rgba(255,64,96,.18);color:var(--red)}
.btn.stop:hover{background:rgba(255,64,96,.14);border-color:rgba(255,64,96,.32)}

/* Result banner */
.res{
  display:none;padding:14px;margin-top:4px;border-radius:var(--r);
  border:1px solid rgba(92,214,255,.18);background:rgba(92,214,255,.06);
}
.res.show{display:block;animation:fadeUp .35s var(--ease-spring)}
.r-l{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.22em;color:var(--a);margin-bottom:5px;text-transform:uppercase}
.r-v{font-size:20px;font-weight:800;color:var(--a);word-break:break-all;font-family:'JetBrains Mono',monospace}

/* Password found overlay — confined to the terminal pane */
.pw-overlay{
  position:absolute;inset:0;z-index:50;display:none;flex-direction:column;align-items:center;justify-content:center;gap:18px;
  background:rgba(4,17,26,.18);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  opacity:0;transition:opacity .4s var(--ease-out);cursor:pointer;
}
.pw-overlay.show{display:flex;opacity:1}
.pw-check{width:140px;height:140px;opacity:0}
.pw-overlay.show .pw-check{opacity:1;animation:pwPop .45s var(--ease-spring) forwards}
@keyframes pwPop{0%{transform:scale(.7)}100%{transform:scale(1)}}
.pw-overlay.show .pw-check{animation:pwPop .45s var(--ease-spring) forwards,pwPulse .4s var(--ease-out) .85s}
@keyframes pwPulse{0%{transform:scale(1)}50%{transform:scale(1.06)}100%{transform:scale(1)}}
.pw-circle{fill:none;stroke:#34e6a0;stroke-width:4;stroke-linecap:round;stroke-dasharray:290;stroke-dashoffset:290;transform-origin:50% 50%;transform:rotate(-90deg)}
.pw-overlay.show .pw-circle{animation:pwCircle .5s var(--ease-out) .1s forwards}
@keyframes pwCircle{to{stroke-dashoffset:0}}
.pw-tick{fill:none;stroke:#34e6a0;stroke-width:6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:62;stroke-dashoffset:62}
.pw-overlay.show .pw-tick{animation:pwTick .3s var(--ease-out) .5s forwards}
@keyframes pwTick{to{stroke-dashoffset:0}}
.pw-text{
  font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;color:#34e6a0;
  text-align:center;letter-spacing:.02em;word-break:break-all;max-width:80%;
  opacity:0;transform:translateY(8px);filter:blur(4px);
}
.pw-overlay.show .pw-text{animation:pwTextIn .5s var(--ease-out) .85s forwards}
@keyframes pwTextIn{to{opacity:1;transform:translateY(0);filter:blur(0)}}

/* Terminal */
.term{
  display:flex;flex-direction:column;min-height:0;position:relative;overflow:hidden;
  background:var(--g-bg);
  border:1px solid var(--g-bd);
  border-radius:var(--r);
}
.term-hdr{
  height:44px;display:flex;align-items:center;padding:0 18px;gap:10px;flex-shrink:0;
  border-bottom:1px solid var(--g-bd);
}
.term-title{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.18em;color:rgba(255,255,255,.42);flex:1;text-transform:uppercase}
.term-clr{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.38);cursor:pointer;padding:4px 8px;border:1px solid transparent;border-radius:var(--r2);transition:color .15s,border-color .15s}
.term-clr:hover{color:#fff;border-color:var(--g-bd)}
.term-body{flex:1;overflow-y:auto;padding:14px 20px;font-size:12px;line-height:1.85;background:rgba(0,0,0,.15);overscroll-behavior:contain}
.term-body::-webkit-scrollbar{width:2px}
.term-body::-webkit-scrollbar-thumb{background:rgba(92,214,255,.22)}
.tl{display:flex;gap:12px;animation:up .1s ease-out}
.ts{color:rgba(255,255,255,.28);flex-shrink:0;font-size:11px;user-select:none;font-family:'JetBrains Mono',monospace;opacity:.6}
.tm{word-break:break-all;white-space:pre-wrap;font-family:'JetBrains Mono',monospace}
.t-sys  .tm{color:rgba(255,255,255,.92)}
.t-ok   .tm{color:var(--a)}
.t-err  .tm{color:var(--red)}
.t-warn .tm{color:#f5c842}
.t-dim  .tm{color:rgba(255,255,255,.42)}
.t-data .tm{color:rgba(255,255,255,.75)}
.t-result .tm{color:var(--a);font-size:14px;font-weight:700}
</style>
</head>
<body>
<div class="aurora-container" id="auroraContainer"></div>
<div class="bg-orbs"><span></span><span></span><span></span></div>
<div class="scanline"></div>
<script>
(function(){
  var ctn=document.getElementById('auroraContainer');
  var canvas=document.createElement('canvas');
  ctn.appendChild(canvas);
  var gl=canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,antialias:true});
  if(!gl){ctn.style.display='none';return;}
  gl.clearColor(0,0,0,0);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);

  var vertSrc=`#version 300 es
in vec2 position;
void main(){gl_Position=vec4(position,0.0,1.0);}`;

  var fragSrc=`#version 300 es
precision highp float;
uniform float uTime;
uniform float uAmplitude;
uniform vec3 uColorStops[3];
uniform vec2 uResolution;
uniform float uBlend;
out vec4 fragColor;
vec3 permute(vec3 x){return mod(((x*34.0)+1.0)*x,289.0);}
float snoise(vec2 v){
  const vec4 C=vec4(0.211324865405187,0.366025403784439,-0.577350269189626,0.024390243902439);
  vec2 i=floor(v+dot(v,C.yy));
  vec2 x0=v-i+dot(i,C.xx);
  vec2 i1=(x0.x>x0.y)?vec2(1.0,0.0):vec2(0.0,1.0);
  vec4 x12=x0.xyxy+C.xxzz;
  x12.xy-=i1;
  i=mod(i,289.0);
  vec3 p=permute(permute(i.y+vec3(0.0,i1.y,1.0))+i.x+vec3(0.0,i1.x,1.0));
  vec3 m=max(0.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.0);
  m=m*m;m=m*m;
  vec3 x=2.0*fract(p*C.www)-1.0;
  vec3 h=abs(x)-0.5;
  vec3 ox=floor(x+0.5);
  vec3 a0=x-ox;
  m*=1.79284291400159-0.85373472095314*(a0*a0+h*h);
  vec3 g;
  g.x=a0.x*x0.x+h.x*x0.y;
  g.yz=a0.yz*x12.xz+h.yz*x12.yw;
  return 130.0*dot(m,g);
}
struct ColorStop{vec3 color;float position;};
#define COLOR_RAMP(colors,factor,finalColor){int index=0;for(int i=0;i<2;i++){ColorStop currentColor=colors[i];bool isInBetween=currentColor.position<=factor;index=int(mix(float(index),float(i),float(isInBetween)));}ColorStop currentColor=colors[index];ColorStop nextColor=colors[index+1];float range=nextColor.position-currentColor.position;float lerpFactor=(factor-currentColor.position)/range;finalColor=mix(currentColor.color,nextColor.color,lerpFactor);}
void main(){
  vec2 uv=gl_FragCoord.xy/uResolution;
  ColorStop colors[3];
  colors[0]=ColorStop(uColorStops[0],0.0);
  colors[1]=ColorStop(uColorStops[1],0.5);
  colors[2]=ColorStop(uColorStops[2],1.0);
  vec3 rampColor;
  COLOR_RAMP(colors,uv.x,rampColor);
  float height=snoise(vec2(uv.x*2.0+uTime*0.1,uTime*0.25))*0.5*uAmplitude;
  height=exp(height);
  height=(uv.y*2.0-height+0.2);
  float intensity=0.6*height;
  float midPoint=0.20;
  float auroraAlpha=smoothstep(midPoint-uBlend*0.5,midPoint+uBlend*0.5,intensity);
  vec3 auroraColor=intensity*rampColor;
  fragColor=vec4(auroraColor*auroraAlpha,auroraAlpha);
}`;

  function compile(type,src){var s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);return s;}
  var vs=compile(gl.VERTEX_SHADER,vertSrc);
  var fs=compile(gl.FRAGMENT_SHADER,fragSrc);
  var prog=gl.createProgram();
  gl.attachShader(prog,vs);gl.attachShader(prog,fs);gl.linkProgram(prog);
  gl.useProgram(prog);

  var buf=gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER,buf);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1, 3,-1, -1,3]),gl.STATIC_DRAW);
  var posLoc=gl.getAttribLocation(prog,'position');
  gl.enableVertexAttribArray(posLoc);
  gl.vertexAttribPointer(posLoc,2,gl.FLOAT,false,0,0);

  var uTime=gl.getUniformLocation(prog,'uTime');
  var uAmp=gl.getUniformLocation(prog,'uAmplitude');
  var uRes=gl.getUniformLocation(prog,'uResolution');
  var uBlend=gl.getUniformLocation(prog,'uBlend');
  var uCS=[gl.getUniformLocation(prog,'uColorStops[0]'),gl.getUniformLocation(prog,'uColorStops[1]'),gl.getUniformLocation(prog,'uColorStops[2]')];

  function hexToRgb(hex){var n=parseInt(hex.slice(1),16);return [((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255];}
  var stops=['#0b2433','#5cd6ff','#2a9fe0'].map(hexToRgb);
  for(var i=0;i<3;i++){gl.uniform3f(uCS[i],stops[i][0],stops[i][1],stops[i][2]);}
  gl.uniform1f(uAmp,1.0);
  gl.uniform1f(uBlend,0.5);

  function resize(){
    var dpr=Math.min(window.devicePixelRatio||1,2);
    var w=ctn.offsetWidth,h=ctn.offsetHeight;
    canvas.width=Math.max(1,w*dpr);
    canvas.height=Math.max(1,h*dpr);
    gl.viewport(0,0,canvas.width,canvas.height);
    gl.uniform2f(uRes,canvas.width,canvas.height);
  }
  window.addEventListener('resize',resize);
  resize();

  function render(t){
    gl.uniform1f(uTime,t*0.001);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES,0,3);
    requestAnimationFrame(render);
  }
  requestAnimationFrame(render);
})();
</script>
<script>
document.addEventListener("pointermove",(e)=>{
  const el=e.target.closest(".sec");
  if(!el)return;
  const r=el.getBoundingClientRect();
  el.style.setProperty("--mx",(e.clientX-r.left)+"px");
  el.style.setProperty("--my",(e.clientY-r.top)+"px");
});
</script>
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
      <div class="term-hdr">
        <span class="term-title">stdout &mdash; <span id="lc" style="opacity:.5">0 lines</span></span>
        <span class="term-clr" onclick="clr()">clear</span>
      </div>
      <div class="term-body" id="tb"></div>
      <div class="pw-overlay" id="pw-overlay" onclick="this.classList.remove('show')">
        <svg class="pw-check" viewBox="0 0 100 100">
          <circle class="pw-circle" cx="50" cy="50" r="46"/>
          <path class="pw-tick" d="M30 52 L44 66 L72 36"/>
        </svg>
        <div class="pw-text" id="pw-text"></div>
      </div>
    </div>
  </div>
</div>

<script>
/* App logic */
var P='',H2='',LI=0,poll=null,pwShown=false;

function showPwFound(pw){
  if(pwShown)return;pwShown=true;
  document.getElementById('pw-text').textContent='Password Found: '+pw;
  document.getElementById('pw-overlay').classList.add('show');
}

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
  pwShown=false;document.getElementById('pw-overlay').classList.remove('show');
}
function lg(tag,msg){
  var tb=document.getElementById('tb'),now=new Date().toTimeString().slice(0,8);
  var row=document.createElement('div');row.className='tl t-'+tag;
  var ts=document.createElement('span');ts.className='ts';ts.textContent=now;
  var tm=document.createElement('span');tm.className='tm';
  row.appendChild(ts);row.appendChild(tm);tb.appendChild(row);tb.scrollTop=tb.scrollHeight;
  var i=0,txt=esc(msg);
  function tick(){if(i<txt.length){if(txt[i]==='&'){var end=txt.indexOf(';',i);if(end>-1){tm.innerHTML+=txt.slice(i,end+1);i=end+1;}else tm.innerHTML+=txt[i++];}else tm.innerHTML+=txt[i++];tb.scrollTop=tb.scrollHeight;setTimeout(tick,tag==='dim'?7:11);}else{document.getElementById('lc').textContent=tb.children.length+' lines';if(tag==='result'){var m=msg.match(/PASSWORD FOUND:\s*(.+)/);if(m){var pw=m[1].trim();document.getElementById('rv').textContent=pw;document.getElementById('res').classList.add('show');var vp=document.getElementById('v-p');if(vp){vp.textContent=pw;vp.className='rv ok';}showPwFound(pw);}}}}
  tick();
}
function clr(){document.getElementById('tb').innerHTML='';LI=0;document.getElementById('lc').textContent='0 lines'}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function sv(id,v,c){var e=document.getElementById(id);if(e){e.textContent=v;e.className='rv'+(c?' '+c:'')}}

async function pf2(){
  try{var r=await fetch('/api/hc/log?since='+LI),d=await r.json();d.entries.forEach(function(e){lg(e.tag,e.msg)});LI=d.total;
    var ri=await fetch('/api/hc/running'),rd=await ri.json();
    if(rd.password){var pw=rd.password;document.getElementById('rv').textContent=pw;document.getElementById('res').classList.add('show');var vp=document.getElementById('v-p');if(vp){vp.textContent=pw;vp.className='rv ok';}showPwFound(pw);}
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
      var ssidTxt=d.ssids.map(function(s){return s.ssid+' ('+s.count+'h)';}).join('  ·  ');
      rowSsid.innerHTML='<div class="rk">ssid</div><div class="rv v" id="v-s">'+esc(ssidTxt)+'</div>';
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
</script>
</body>
</html>
"""



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5555, debug=False, threaded=True)
