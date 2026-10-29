"""Local web UI for vc-keeper: a static page plus a live event stream."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .envfile import app_dir, read as read_env, write as write_env

ROOT = app_dir()
WEB_DIR = Path(__file__).resolve().parent / "web"
ENV_FILE = ROOT / ".env"
LOG_FILE = ROOT / ".vc-keeper.log"
STATE_FILE = ROOT / ".vc-keeper.state.json"

STATE_FRESH_S = 60.0
EVENT_LOG_LINES = 300
EVENT_TICK_S = 0.4

_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()

_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()
_hub_started = False


# ------------------------------------------------------------------ bot process


def _proc_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _venv_python() -> str:
    candidates = (".venv/Scripts/python.exe", ".venv/bin/python")
    for name in candidates:
        path = ROOT / name
        if path.is_file():
            return str(path)
    return sys.executable


def _start_bot() -> bool:
    """Start the bot; False when it is already running."""
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return False
        LOG_FILE.write_text("", encoding="utf-8")
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--run-bot"]
        else:
            cmd = [_venv_python(), "-m", "vc_keeper"]
        with LOG_FILE.open("a", encoding="utf-8") as out:
            _proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT)
    _broadcast({"type": "status", "running": True})
    return True


def _stop_bot() -> None:
    global _proc
    with _proc_lock:
        proc, _proc = _proc, None
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    _broadcast({"type": "status", "running": False})


# ------------------------------------------------------------------ config/state


def _config() -> dict[str, str]:
    return read_env(ENV_FILE) if ENV_FILE.is_file() else {}


def _read_state(running: bool) -> dict:
    """Live state published by the bot: the platforms the account is online on."""
    if not running or not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        age = time.time() - float(data.get("updated_at", 0))
    except (OSError, ValueError, TypeError):
        return {}
    return {} if age > STATE_FRESH_S else data


def _tail_log(n: int) -> str:
    if not LOG_FILE.is_file():
        return ""
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def _profile(token: str, size: int) -> dict:
    request = urllib.request.Request(
        "https://discord.com/api/v9/users/@me",
        headers={"Authorization": token, "User-Agent": "vc-keeper"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        user = json.loads(response.read().decode("utf-8"))
    avatar = user.get("avatar")
    if avatar:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar}.png?size={size}"
    else:
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{int(user.get('discriminator') or 0) % 5}.png"
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "global_name": user.get("global_name"),
        "avatar_url": avatar_url,
    }


# ------------------------------------------------------------------ event stream


def _subscribe() -> queue.Queue:
    inbox: queue.Queue = queue.Queue(maxsize=2000)
    with _sub_lock:
        _subscribers.add(inbox)
    return inbox


def _unsubscribe(inbox: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(inbox)


def _broadcast(event: dict) -> None:
    with _sub_lock:
        targets = list(_subscribers)
    for inbox in targets:
        try:
            inbox.put_nowait(event)
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
    """Watch the bot's log, state file and process, and push changes to the page."""
    offset = _size(LOG_FILE)  # history goes in the snapshot instead
    state_mtime = _mtime(STATE_FILE)
    running = _proc_running()

    while True:
        time.sleep(EVENT_TICK_S)

        size = _size(LOG_FILE)
        if size < offset:
            # A fresh start truncated the log, so the page should forget the old run.
            offset = 0
            _broadcast({"type": "log_reset"})
        if size > offset:
            try:
                with LOG_FILE.open("rb") as log:
                    log.seek(offset)
                    chunk = log.read()
            except OSError:
                chunk = b""
            cut = chunk.rfind(b"\n")  # only emit complete lines
            if cut != -1:
                data = chunk[: cut + 1]
                offset += len(data)
                lines = data.decode("utf-8", "replace").splitlines()
                if lines:
                    _broadcast({"type": "log", "lines": lines})

        current_mtime = _mtime(STATE_FILE)
        if current_mtime != state_mtime:
            state_mtime = current_mtime
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


# ------------------------------------------------------------------ HTTP handler


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self) -> None:
        index = WEB_DIR / "index.html"
        if not index.is_file():
            self.send_error(404)
            return
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_event(self, payload: dict) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream_events(self) -> None:
        """Keep the page live: a snapshot on connect, then pushed updates."""
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
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_index()
            return
        if path == "/api/events":
            self._stream_events()
            return
        if path == "/api/config":
            self._json(200, _config())
            return
        if path == "/api/profile":
            token = _config().get("DISCORD_TOKEN", "")
            if not token:
                self._json(200, {})
                return
            try:
                self._json(200, _profile(token, 128))
            except Exception as exc:
                self._json(200, {"error": str(exc)})
            return
        if path == "/api/tokens":
            # Imported lazily: token_detect uses Windows-only APIs.
            from .token_detect import detect_tokens

            found = []
            for token in detect_tokens():
                entry = {"client": token.client, "token": token.token}
                try:
                    entry.update(_profile(token.token, 64))
                except Exception:
                    pass
                found.append(entry)
            self._json(200, found)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {}

        if path == "/api/config":
            write_env(ENV_FILE, {str(key): str(value) for key, value in data.items()})
            self._json(200, {"ok": True})
            return
        if path == "/api/start":
            started = _start_bot()
            self._json(200, {"ok": True, "message": "" if started else "already running"})
            return
        if path == "/api/stop":
            _stop_bot()
            self._json(200, {"ok": True})
            return
        if path == "/api/restart":
            _stop_bot()
            _start_bot()
            self._json(200, {"ok": True})
            return
        if path == "/api/terminate":
            _stop_bot()
            self._json(200, {"ok": True})
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return
        self.send_error(404)

    def log_message(self, *args) -> None:  # quiet
        pass


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
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


def main() -> None:
    if "--run-bot" in sys.argv:
        from .app import main as app_main

        app_main()
        return
    run()


if __name__ == "__main__":
    main()
