import os
import re
import sys
import time
import socket
import subprocess
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import uvicorn

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

def find_free_port(start_port=8080):
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return start_port

def read_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

def update_env_url(url):
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("WEBAPP_URL="):
                    lines.append(f"WEBAPP_URL={url}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"WEBAPP_URL={url}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

def start_tunnel(port):
    cf_path = os.path.join(os.path.dirname(__file__), "cloudflared.exe")
    if not os.path.exists(cf_path):
        print("[!] cloudflared.exe not found, skipping tunnel.")
        return None, None

    cmd = [cf_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="ignore"
    )

    tunnel_url = None
    start_time = time.time()
    print(f"[*] Starting Cloudflare Tunnel for port {port}...")

    while time.time() - start_time < 30:
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.1)
            continue
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            tunnel_url = match.group(0)
            break

    if tunnel_url:
        print("\n" + "=" * 60)
        print(f"[+] PUBLIC HTTPS URL: {tunnel_url}/app")
        print("=" * 60 + "\n")
        update_env_url(tunnel_url)
        os.environ["WEBAPP_URL"] = tunnel_url
    else:
        print("[!] Tunnel URL not detected in 30 seconds.")

    return proc, tunnel_url

if __name__ == "__main__":
    port = find_free_port(8080)
    print(f"[*] Using local port: {port}")
    
    cf_proc, url = start_tunnel(port)
    
    env_vars = read_env()
    for k, v in env_vars.items():
        os.environ[k] = v
        
    try:
        import main
        main.WEBAPP_URL = os.environ.get("WEBAPP_URL", f"http://localhost:{port}")
        print(f"[*] Starting server on http://localhost:{port} (WebApp URL: {main.WEBAPP_URL})")
        uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
    finally:
        if cf_proc:
            cf_proc.terminate()
