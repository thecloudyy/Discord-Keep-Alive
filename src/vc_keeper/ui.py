"""Lightweight local web UI for vc-keeper."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"
LOG_FILE = ROOT / ".vc-keeper.log"
STATE_FILE = ROOT / ".vc-keeper.state.json"
STATE_FRESH_S = 60.0

_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()

EVENT_LOG_LINES = 300
EVENT_TICK_S = 0.4

_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()
_hub_started = False


def _proc_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _sub_lock:
        _subscribers.add(q)
    return q


def _unsubscribe(q: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(q)


def _broadcast(event: dict) -> None:
    with _sub_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def _start_hub() -> None:
    global _hub_started
    with _sub_lock:
        if _hub_started:
            return
        _hub_started = True
    threading.Thread(target=_hub_loop, daemon=True, name="vc-keeper-events").start()


def _hub_loop() -> None:
    """Watch the bot's log, state file and process, and push changes to the UI."""
    offset = _size(LOG_FILE)  # history is sent in the snapshot instead
    state_mtime = _mtime(STATE_FILE)
    running = _proc_running()
    while True:
        time.sleep(EVENT_TICK_S)

        size = _size(LOG_FILE)
        if size < offset:
            offset = 0  # the log was truncated by a fresh start
        if size > offset:
            try:
                with LOG_FILE.open("rb") as f:
                    f.seek(offset)
                    chunk = f.read()
            except OSError:
                chunk = b""
            cut = chunk.rfind(b"\n")
            if cut != -1:
                data = chunk[: cut + 1]
                offset += len(data)
                lines = data.decode("utf-8", "replace").splitlines()
                if lines:
                    _broadcast({"type": "log", "lines": lines})

        current = _mtime(STATE_FILE)
        if current != state_mtime:
            state_mtime = current
            state = _read_state(True)
            if state:
                _broadcast({"type": "state", "data": state})

        current_running = _proc_running()
        if current_running != running:
            running = current_running
            _broadcast({"type": "status", "running": running})


def _snapshot() -> dict:
    running = _proc_running()
    state = _read_state(running)
    return {
        "type": "snapshot",
        "running": running,
        "log": _tail_log(EVENT_LOG_LINES).splitlines(),
        "sessions": state.get("sessions", []),
        "user": state.get("user"),
        "spoofed_platform": state.get("spoofed_platform"),
    }


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _write_env(data: dict[str, str]) -> None:
    order = [
        "DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID", "LOG_LEVEL",
        "SELF_DEAF", "STATUS", "ACTIVITY_TYPE", "ACTIVITY_TEXT", "CUSTOM_STATUS",
        "PLATFORM", "REJOIN_COOLDOWN_MIN", "REJOIN_COOLDOWN_MAX",
    ]
    lines = []
    for k in order:
        if k in data:
            lines.append(f"{k}={data[k]}")
    for k, v in data.items():
        if k not in order:
            lines.append(f"{k}={v}")
    (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _venv_python() -> str:
    if os.name == "nt":
        cand = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        cand = ROOT / ".venv" / "bin" / "python"
    return str(cand if cand.is_file() else sys.executable)


def _read_state(running: bool) -> dict:
    """Live state published by the bot: the platforms the account is online on."""
    if not running or not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        age = time.time() - float(data.get("updated_at", 0))
    except (OSError, ValueError, TypeError):
        return {}
    if age > STATE_FRESH_S:
        return {}
    return data


def _tail_log(n: int = 80) -> str:
    if not LOG_FILE.is_file():
        return ""
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = "text/html; charset=utf-8"
        if path.suffix == ".js":
            ctype = "application/javascript"
        elif path.suffix == ".css":
            ctype = "text/css"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_event(self, payload: dict) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream_events(self) -> None:
        """Keep the page live: snapshot on connect, then pushed updates."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        inbox = _subscribe()
        try:
            self._write_event(_snapshot())
            while True:
                try:
                    payload = inbox.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self._write_event(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _unsubscribe(inbox)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            self._serve_file(WEB_DIR / "index.html")
            return
        if path == "/api/events":
            self._stream_events()
            return
        if path == "/api/tokens":
            try:
                from .token_detect import detect_tokens
                toks = detect_tokens()
                out = []
                for t in toks:
                    profile = {}
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            "https://discord.com/api/v9/users/@me",
                            headers={"Authorization": t.token, "User-Agent": "vc-keeper"},
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = json.loads(resp.read().decode("utf-8"))
                        avatar = data.get("avatar")
                        if avatar:
                            avatar_url = f"https://cdn.discordapp.com/avatars/{data['id']}/{avatar}.png?size=64"
                        else:
                            avatar_url = f"https://cdn.discordapp.com/embed/avatars/{int(data.get('discriminator','0')) % 5}.png"
                        profile = {
                            "username": data.get("username"),
                            "global_name": data.get("global_name"),
                            "avatar_url": avatar_url,
                            "id": data.get("id"),
                        }
                    except Exception:
                        pass
                    out.append({"client": t.client, "token": t.token, **profile})
                self._json(200, out)
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/config":
            self._json(200, _read_env())
            return
        if path == "/api/profile":
            env = _read_env()
            token = env.get("DISCORD_TOKEN", "")
            if not token:
                self._json(200, {})
                return
            try:
                import urllib.request
                req = urllib.request.Request(
                    "https://discord.com/api/v9/users/@me",
                    headers={"Authorization": token, "User-Agent": "vc-keeper"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                avatar = data.get("avatar")
                if avatar:
                    avatar_url = f"https://cdn.discordapp.com/avatars/{data['id']}/{avatar}.png?size=128"
                else:
                    avatar_url = f"https://cdn.discordapp.com/embed/avatars/{int(data.get('discriminator','0')) % 5}.png"
                self._json(200, {
                    "id": data.get("id"),
                    "username": data.get("username"),
                    "global_name": data.get("global_name"),
                    "avatar_url": avatar_url,
                })
            except Exception as exc:
                self._json(200, {"error": str(exc)})
            return
        if path == "/api/status":
            running = _proc_running()
            state = _read_state(running)
            self._json(200, {
                "running": running,
                "log": _tail_log(),
                "sessions": state.get("sessions", []),
                "user": state.get("user"),
                "spoofed_platform": state.get("spoofed_platform"),
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        global _proc
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}

        if path == "/api/config":
            _write_env({str(k): str(v) for k, v in data.items()})
            self._json(200, {"ok": True})
            return
        if path == "/api/start":
            with _proc_lock:
                if _proc is not None and _proc.poll() is None:
                    self._json(200, {"ok": True, "message": "already running"})
                    return
                # write log fresh
                LOG_FILE.write_text("", encoding="utf-8")
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, "--run-bot"]
                else:
                    cmd = [_venv_python(), "-m", "vc_keeper"]
                f = open(LOG_FILE, "a", encoding="utf-8")
                _proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
            _broadcast({"type": "status", "running": True})
            self._json(200, {"ok": True})
            return
        if path == "/api/stop":
            with _proc_lock:
                if _proc is not None and _proc.poll() is None:
                    _proc.terminate()
                    try:
                        _proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        _proc.kill()
                _proc = None
            _broadcast({"type": "status", "running": False})
            self._json(200, {"ok": True})
            return
        if path == "/api/terminate":
            with _proc_lock:
                if _proc is not None and _proc.poll() is None:
                    _proc.terminate()
                    try:
                        _proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        _proc.kill()
                _proc = None
            self._json(200, {"ok": True})
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return
        self.send_error(404)

    def log_message(self, *args) -> None:  # quiet
        pass


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    if not (WEB_DIR / "index.html").is_file():
        (WEB_DIR / "index.html").write_text(_FALLBACK_HTML, encoding="utf-8")
    httpd = ThreadingHTTPServer((host, port), Handler)
    _start_hub()
    url = f"http://{host}:{port}"
    print(f"[vc-keeper] UI running at {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


_FALLBACK_HTML = """<!doctype html><html><body><h1>vc-keeper UI</h1><p>Missing index.html</p></body></html>"""


def main() -> None:
    if "--run-bot" in sys.argv:
        from .app import main as app_main
        app_main()
        return
    run()


if __name__ == "__main__":
    main()