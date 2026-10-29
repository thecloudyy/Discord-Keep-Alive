from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", "")
    _dirs = [_base, os.path.join(_base, "curl_cffi.libs")]
    for _d in _dirs:
        if _d and os.path.isdir(_d):
            try:
                os.add_dll_directory(_d)
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")

import asyncio
import json
import logging
import os
import random
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Load a .env file from the cwd or project root into os.environ."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(__file__).resolve().parent / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
        break

import discord

log = logging.getLogger("vc_keeper")

EXIT_OK = 0
EXIT_CRASH = 1
EXIT_CONFIG = 78

HEALTH_CHECK_EVERY_S = 60.0
LIBRARY_RECONNECT_GRACE_S = 30.0
MAX_BACKOFF_S = 60.0
STATE_PUBLISH_S = 5.0
SNOWFLAKE = re.compile(r"^\d{16,22}$")


def _root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


STATE_FILE = _root() / ".vc-keeper.state.json"

# Which platform to pretend to be. Desktop variants are spoofed with a full
# desktop client identity; the rest are spoofed the same way Equicord's
# PlatformSpoofer does it: by overriding the `browser` field of the gateway
# IDENTIFY properties, which is what Discord reads to fill in a session's
# client/os.
PLATFORM_CHOICES = ("windows", "mac", "linux", "web", "android", "ios", "xbox", "playstation", "vr")
PLATFORM_ALIASES = {"desktop": "windows", "macos": "mac", "mobile": "ios"}
SPOOFED_BROWSERS = {
    "android": "Discord Android",
    "ios": "Discord iOS",
    "xbox": "Discord Embedded",
    "playstation": "Discord Embedded",
    "vr": "Discord VR",
}


# --------------------------------------------------------------------------- config


class ConfigError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    guild_id: int
    channel_id: int
    self_deaf: bool
    cooldown_min_s: float
    cooldown_max_s: float
    log_level: str
    status: str = "online"
    activity_type: str = ""
    activity_text: str = ""
    custom_status: str = ""
    platform: str = "windows"

    @classmethod
    def from_env(cls) -> Config:
        problems: list[str] = []

        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            problems.append("DISCORD_TOKEN is missing")

        def snowflake(name: str) -> int:
            raw = os.environ.get(name, "").strip()
            if not SNOWFLAKE.fullmatch(raw):
                problems.append(f"{name} must be a Discord ID (16-22 digits)")
                return 0
            return int(raw)

        def seconds(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                problems.append(f"{name} must be a number of seconds")
                return default
            if value < 0:
                problems.append(f"{name} can't be negative")
            return value

        guild_id = snowflake("DISCORD_GUILD_ID")
        channel_id = snowflake("DISCORD_CHANNEL_ID")
        cooldown_min = seconds("REJOIN_COOLDOWN_MIN", 10)
        cooldown_max = seconds("REJOIN_COOLDOWN_MAX", 120)
        if cooldown_max < cooldown_min:
            problems.append("REJOIN_COOLDOWN_MAX must be >= REJOIN_COOLDOWN_MIN")

        self_deaf = os.environ.get("SELF_DEAF", "false").strip().lower() in {"1", "true", "yes", "on"}
        log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            problems.append("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

        status = os.environ.get("STATUS", "online").strip().lower() or "online"
        if status not in {"online", "idle", "dnd", "invisible"}:
            problems.append("STATUS must be online, idle, dnd or invisible")

        activity_type = os.environ.get("ACTIVITY_TYPE", "").strip().lower()
        if activity_type and activity_type not in {"playing", "listening", "watching", "competing", "custom"}:
            problems.append("ACTIVITY_TYPE must be playing, listening, watching, competing or custom")

        activity_text = os.environ.get("ACTIVITY_TEXT", "").strip()
        custom_status = os.environ.get("CUSTOM_STATUS", "").strip()

        platform = os.environ.get("PLATFORM", "windows").strip().lower() or "windows"
        platform = PLATFORM_ALIASES.get(platform, platform)
        if platform not in PLATFORM_CHOICES:
            problems.append("PLATFORM must be one of " + ", ".join(PLATFORM_CHOICES))

        if problems:
            raise ConfigError("; ".join(problems))

        return cls(
            token, guild_id, channel_id, self_deaf, cooldown_min, cooldown_max, log_level,
            status, activity_type, activity_text, custom_status, platform,
        )


# --------------------------------------------------------------------------- client


class VcKeeper(discord.Client):
    def __init__(self, config: Config) -> None:
        super().__init__(
            afk=True,
            sync_presence=False,
            max_messages=None,
            guild_subscriptions=False,
            member_cache_flags=discord.MemberCacheFlags(joined=False),
        )
        self.config = config
        self.started_at = time.time()
        self._wake = asyncio.Event()
        self._kicked = False
        self._supervisor: asyncio.Task[None] | None = None
        self._state: asyncio.Task[None] | None = None
        self._shutdown: asyncio.Task[None] | None = None

    # ---- lifecycle --------------------------------------------------------

    async def setup_hook(self) -> None:
        self._supervisor = asyncio.create_task(self._supervise(), name="vc-keeper-supervisor")
        self._supervisor.add_done_callback(self._on_supervisor_done)
        self._state = asyncio.create_task(self._publish_loop(), name="vc-keeper-state")

    def _on_supervisor_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self.is_closed():
            return
        log.critical(
            "supervisor died unexpectedly, shutting down so systemd restarts us", exc_info=task.exception()
        )
        self.request_shutdown()

    def request_shutdown(self) -> None:
        if self._shutdown is None:
            self._shutdown = asyncio.get_running_loop().create_task(self.close())

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("logged in as %s (%s)", self.user, self.user.id)
        await self._update_presence()
        self._wake.set()
        self.publish_state()

    async def on_resumed(self) -> None:
        log.info("gateway session resumed")
        await self._update_presence()
        self._wake.set()
        self.publish_state()

    async def on_session_create(self, session: discord.Session) -> None:
        self.publish_state()

    async def on_session_update(self, before: discord.Session, after: discord.Session) -> None:
        self.publish_state()

    async def on_session_delete(self, session: discord.Session) -> None:
        self.publish_state()

    async def headers_context(self) -> discord.HeadersContext:
        http = self.http
        session = http._HTTPClient__asession  # type: ignore
        platform = (self.config.platform or "windows").lower()
        platform = PLATFORM_ALIASES.get(platform, platform)

        try:
            if platform == "windows":
                return await discord.HeadersContext.desktop(session)
            if platform in {"mac", "linux"}:
                data = await discord.HeadersContext.fetch_api_properties(session, platform)
                bv = int(data["metadata"]["native_chrome_version"].split(".")[0])
                return discord.HeadersContext(
                    platform="macOS" if platform == "mac" else "Linux",
                    browser_type="electron",
                    browser_major_version=bv,
                    super_properties=data["properties"],
                    encoded_super_properties=data.get("encoded"),
                    extra_gateway_properties=data.get("extra_gateway_properties"),
                )
            context = await discord.HeadersContext.default(session, http.proxy, http.proxy_auth)
        except Exception as exc:
            log.warning("platform spoof failed, falling back to default: %s", exc)
            return await discord.HeadersContext.default(session, http.proxy, http.proxy_auth)

        browser = SPOOFED_BROWSERS.get(platform)
        if browser is not None:
            # Same trick as Equicord's PlatformSpoofer: the gateway IDENTIFY
            # properties' `browser` field is what Discord turns into a session's
            # client + os, so overriding it reports us as that platform.
            context.extra_gateway_properties = {**context.extra_gateway_properties, "browser": browser}
        log.info("spoofing platform %s%s", platform, f" (identify browser {browser!r})" if browser else "")
        return context

    async def _update_presence(self) -> None:
        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        status = status_map.get(self.config.status, discord.Status.online)

        activity = None
        if self.config.activity_type and self.config.activity_text:
            if self.config.activity_type == "playing":
                activity = discord.Game(name=self.config.activity_text)
            elif self.config.activity_type == "listening":
                activity = discord.Activity(type=discord.ActivityType.listening, name=self.config.activity_text)
            elif self.config.activity_type == "watching":
                activity = discord.Activity(type=discord.ActivityType.watching, name=self.config.activity_text)
            elif self.config.activity_type == "competing":
                activity = discord.Activity(type=discord.ActivityType.competing, name=self.config.activity_text)
            elif self.config.activity_type == "custom":
                activity = discord.CustomActivity(name=self.config.activity_text or self.config.custom_status)
        if activity is None and self.config.custom_status:
            activity = discord.CustomActivity(name=self.config.custom_status)

        try:
            await self.change_presence(status=status, activity=activity)
        except Exception as exc:
            log.warning("could not update presence: %s", exc)

    async def on_voice_state_update(
        self, member: discord.Member | discord.User, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if self.user is None or member.id != self.user.id:
            return
        guild = getattr(after.channel or before.channel, "guild", None)
        if guild is not None and guild.id != self.config.guild_id:
            return

        now = after.channel.id if after.channel else None
        if now == self.config.channel_id:
            log.debug("voice state: in target channel")
            return

        where = f"moved to {now}" if now else "disconnected"
        log.warning("voice state changed: %s", where)
        self._kicked = True
        self._wake.set()
        self.publish_state()

    # ---- live state for the UI ----------------------------------------------

    def _session_rows(self) -> list[dict[str, object]]:
        """Every session Discord reports for this account (the platforms it's online on)."""
        rows: list[dict[str, object]] = []
        for session in self.sessions:
            if getattr(session, "session_id", None) == "all":
                continue  # synthetic aggregate session, not a platform
            rows.append(
                {
                    "client": str(getattr(session, "client", "unknown")),
                    "os": getattr(getattr(session, "os", None), "name", "unknown"),
                    "status": getattr(getattr(session, "status", None), "name", "offline"),
                    "active": bool(getattr(session, "active", False)),
                }
            )
        return rows

    def publish_state(self) -> None:
        user = None
        if self.user is not None:
            user = {
                "id": str(self.user.id),
                "username": self.user.name,
                "global_name": getattr(self.user, "global_name", None),
            }
        payload = {
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "user": user,
            "spoofed_platform": self.config.platform,
            "sessions": self._session_rows(),
        }
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError as exc:
            log.debug("could not publish state: %s", exc)

    async def _publish_loop(self) -> None:
        while not self.is_closed():
            self.publish_state()
            await asyncio.sleep(STATE_PUBLISH_S)

    # ---- the one loop that decides everything -------------------------------

    async def _supervise(self) -> None:
        await self.wait_until_ready()
        failures = 0

        while not self.is_closed():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=HEALTH_CHECK_EVERY_S)
            except TimeoutError:
                pass
            self._wake.clear()

            if self._in_target():
                failures = 0
                self._kicked = False
                continue

            if self._kicked:
                self._kicked = False
                delay = random.uniform(self.config.cooldown_min_s, self.config.cooldown_max_s)
                log.info("rejoining in %.0fs (cooldown)", delay)
                await asyncio.sleep(delay)
                if self._in_target():
                    continue

            try:
                await self._ensure_joined()
                failures = 0
                log.info("in voice channel %s", self.config.channel_id)
            except (discord.ClientException, discord.HTTPException, TimeoutError, OSError) as exc:
                failures += 1
                delay = min(2.0**failures, MAX_BACKOFF_S) * random.uniform(0.8, 1.2)
                log.error(
                    "join attempt %d failed (%s: %s), retrying in %.0fs",
                    failures,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                self._wake.set()

    # ---- helpers --------------------------------------------------------------

    def _voice_client(self) -> discord.VoiceClient | None:
        guild = self.get_guild(self.config.guild_id)
        vc = guild.voice_client if guild else None
        return vc if isinstance(vc, discord.VoiceClient) else None

    def _in_target(self) -> bool:
        vc = self._voice_client()
        return (
            vc is not None and vc.is_connected() and getattr(vc.channel, "id", None) == self.config.channel_id
        )

    def _target_channel(self) -> discord.VoiceChannel | discord.StageChannel:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            raise discord.ClientException(f"not in guild {self.config.guild_id} (check DISCORD_GUILD_ID)")
        channel = guild.get_channel(self.config.channel_id)
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            raise discord.ClientException(f"{self.config.channel_id} isn't a voice/stage channel you can see")
        return channel

    async def _ensure_joined(self) -> None:
        channel = self._target_channel()
        vc = self._voice_client()

        if vc is not None:
            if getattr(vc.channel, "id", None) == channel.id:
                log.debug("voice client reconnecting on its own, giving it %.0fs", LIBRARY_RECONNECT_GRACE_S)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + LIBRARY_RECONNECT_GRACE_S
                while loop.time() < deadline:
                    await asyncio.sleep(1)
                    if self._in_target():
                        return
                log.warning("library reconnect didn't finish, starting fresh")
                await vc.disconnect(force=True)
            elif vc.is_connected():
                log.info("moving back to target channel")
                await vc.move_to(channel)
                return
            else:
                await vc.disconnect(force=True)

        await channel.connect(self_mute=True, self_deaf=self.config.self_deaf, reconnect=True, timeout=30.0)


# --------------------------------------------------------------------------- entrypoint


def _setup_logging(level: str) -> None:
    fmt = "%(levelname)-7s %(name)s: %(message)s"
    if "INVOCATION_ID" not in os.environ:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)
    logging.getLogger("discord").setLevel(level if level == "DEBUG" else "WARNING")


async def _run(config: Config) -> None:
    async with VcKeeper(config) as client:
        loop = asyncio.get_running_loop()

        def shutdown(sig: signal.Signals) -> None:
            log.info("got %s, leaving voice and logging out", sig.name)
            client.request_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown, sig)
            except NotImplementedError:
                pass
        await client.start(config.token)


def main() -> None:
    _load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.basicConfig(format="%(levelname)s %(message)s")
        log.error("config error: %s", exc)
        sys.exit(EXIT_CONFIG)

    _setup_logging(config.log_level)
    log.info("starting vc-keeper (target %s in guild %s)", config.channel_id, config.guild_id)

    backoff = 5.0
    while True:
        try:
            asyncio.run(_run(config))
            log.info("stopped")
            sys.exit(EXIT_OK)
        except KeyboardInterrupt:
            log.info("stopped")
            sys.exit(EXIT_OK)
        except discord.LoginFailure:
            log.error("discord rejected the token. not restarting (exit 78). get a fresh token and restart.")
            sys.exit(EXIT_CONFIG)
        except discord.ConnectionClosed as exc:
            if exc.code == 4004:
                log.error("gateway says the token is invalid (4004). not restarting (exit 78).")
                sys.exit(EXIT_CONFIG)
            log.error("gateway closed (code %s), restarting in %.0fs", exc.code, backoff)
        except SystemExit:
            raise
        except Exception:
            log.exception("crashed, restarting in %.0fs", backoff)

        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_S)


if __name__ == "__main__":
    main()
