"""Lightweight local web UI for vc-keeper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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

_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


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
        "REJOIN_COOLDOWN_MIN", "REJOIN_COOLDOWN_MAX",
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            self._serve_file(WEB_DIR / "index.html")
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
            global _proc
            running = _proc is not None and _proc.poll() is None
            self._json(200, {"running": running, "log": _tail_log()})
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