#!/usr/bin/env python3
"""Headless Chrome screenshot via CDP websocket (pure stdlib)."""
import subprocess, time, json, base64, sys, os, urllib.request, hashlib, struct, socket

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/"
LABEL = sys.argv[2] if len(sys.argv) > 2 else ""

OUT_DIR = os.path.join(os.path.dirname(__file__), "temporary screenshots")
os.makedirs(OUT_DIR, exist_ok=True)
n = 1
while os.path.exists(os.path.join(OUT_DIR, f"screenshot-{n}{'-'+LABEL if LABEL else ''}.png")):
    n += 1
OUT = os.path.join(OUT_DIR, f"screenshot-{n}{'-'+LABEL if LABEL else ''}.png")

PORT = 9223
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

subprocess.run(["pkill", "-f", f"remote-debugging-port={PORT}"], capture_output=True)
time.sleep(0.5)

proc = subprocess.Popen([
    CHROME,
    f"--remote-debugging-port={PORT}",
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-extensions",
    "--disable-background-networking",
    "--window-size=1440,900",
    URL,
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

time.sleep(3)

# Get websocket URL
resp = urllib.request.urlopen(f"http://localhost:{PORT}/json/list", timeout=5)
tabs = json.loads(resp.read())
page = next((t for t in tabs if t.get("url","").startswith("http://localhost:3000")), tabs[0])
ws_url = page["webSocketDebuggerUrl"]
# ws_url like ws://localhost:PORT/devtools/page/ID
host_port = ws_url.split("//")[1].split("/")[0]
path = "/" + "/".join(ws_url.split("//")[1].split("/")[1:])

# --- Minimal WebSocket client ---
def ws_connect(host_port, path):
    h, p = host_port.split(":") if ":" in host_port else (host_port, 80)
    s = socket.create_connection((h, int(p)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(handshake.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(4096)
    return s

def ws_send(s, msg):
    data = msg.encode() if isinstance(msg, str) else msg
    ln = len(data)
    mask = os.urandom(4)
    if ln < 126:
        header = bytes([0x81, 0x80 | ln]) + mask
    elif ln < 65536:
        header = bytes([0x81, 0xFE]) + struct.pack(">H", ln) + mask
    else:
        header = bytes([0x81, 0xFF]) + struct.pack(">Q", ln) + mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    s.sendall(header + masked)

def ws_recv(s):
    def read_exact(n):
        buf = b""
        while len(buf) < n:
            buf += s.recv(n - len(buf))
        return buf
    header = read_exact(2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    ln = header[1] & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", read_exact(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", read_exact(8))[0]
    mask_key = read_exact(4) if masked else b""
    data = read_exact(ln)
    if masked:
        data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    return data.decode("utf-8", errors="replace")

ws = ws_connect(host_port, path)

# Wait for page load then evaluate
time.sleep(2)

# Enable Runtime
ws_send(ws, json.dumps({"id": 1, "method": "Runtime.enable"}))
ws_recv(ws)

# Wait for full load via Runtime.evaluate
ws_send(ws, json.dumps({
    "id": 2,
    "method": "Runtime.evaluate",
    "params": {"expression": "document.readyState", "returnByValue": True}
}))
ws_recv(ws)

# Force all reveal elements visible
ws_send(ws, json.dumps({
    "id": 3,
    "method": "Runtime.evaluate",
    "params": {
        "expression": """
            document.querySelectorAll('.reveal').forEach(e => {
                e.classList.add('visible');
                e.style.opacity='1';
                e.style.transform='none';
            });
            document.querySelectorAll('.hero-eyebrow,.hero-title,.hero-sub,.hero-actions,.hero-scroll-ind').forEach(e => {
                e.style.opacity='1';
                e.style.transform='none';
                e.style.animation='none';
            });
        """,
        "returnByValue": True
    }
}))
ws_recv(ws)

time.sleep(0.5)

# Screenshot
ws_send(ws, json.dumps({
    "id": 4,
    "method": "Page.captureScreenshot",
    "params": {"format": "png", "clip": {"x":0,"y":0,"width":1440,"height":900,"scale":1}}
}))

raw = ""
while True:
    raw = ws_recv(ws)
    try:
        obj = json.loads(raw)
        if obj.get("id") == 4:
            break
    except:
        pass

img = base64.b64decode(obj["result"]["data"])
with open(OUT, "wb") as f:
    f.write(img)
print(f"Saved: {OUT}")

ws.close()
proc.terminate()
