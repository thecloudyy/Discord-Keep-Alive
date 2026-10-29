# Discord-Keep-Alive

Keeps your Discord account in a voice channel 24/7, online with optional custom status/activity.
Built on discord.py-self. Automating a user account is against Discord's ToS: use at your own risk.

## Features

- Keeps your account in a target voice channel 24/7 with auto-rejoin
- Randomized rejoin cooldowns, zero API polling
- Optional custom status and activity (playing/listening/watching/competing)
- Status modes: online, idle, dnd, invisible
- Platform spoofer: Windows, macOS, Linux, web, Android, iOS, Xbox, PlayStation or VR
- Live platform indicators: one status-coloured icon per platform your account is online on, read from Discord's own session list
- Self-deafen / self-mute support
- Local web UI (Discord-Keep-Alive.exe) with account auto-detection
- Token auto-detection from local Discord profiles (Windows)
- Multi-account selection
- Exit codes: 0 stopped, 1 crashed (auto-restarts), 78 bad config/token

## Disclaimer

By using this code, you are automating your Discord account. This is against Discord's Terms of Service and Community Guidelines. Your account might get suspended or terminated. The developer is not responsible for any consequences. Use at your own risk.

**DO NOT GIVE YOUR DISCORD TOKEN TO ANYONE.**

## Installation

### From release

Download `Discord-Keep-Alive.exe` from releases and run it. The UI will detect your Discord accounts automatically.

### From source

```
python install.py
```

On Windows you can also double-click `install.bat`.

The installer creates a virtual environment, installs dependencies, and prompts for your Discord token / guild / channel on first run (saved to `.env`).

## Everyday stuff

```
Discord-Keep-Alive.exe          # web UI (account selection + dashboard)
```

Edit `.env` (or use the UI) and restart to apply settings.

## Configuration (.env)

```
DISCORD_TOKEN=
DISCORD_GUILD_ID=
DISCORD_CHANNEL_ID=
LOG_LEVEL=INFO
SELF_DEAF=false
STATUS=online
ACTIVITY_TYPE=
ACTIVITY_TEXT=
CUSTOM_STATUS=
PLATFORM=windows
REJOIN_COOLDOWN_MIN=10
REJOIN_COOLDOWN_MAX=120
```

## Platforms

`PLATFORM` picks what Discord thinks you are running:

| Value | Shows as |
| --- | --- |
| `windows`, `mac`, `linux` | Desktop on that OS (full desktop client identity) |
| `web` | Browser |
| `android`, `ios` | Mobile on that OS |
| `xbox`, `playstation` | Embedded (console or game) |
| `vr` | VR |

Desktop values send a matching desktop client identity. The rest override the `browser` entry of
the gateway IDENTIFY payload (the same mechanism Equicord's PlatformSpoofer uses), which is what
Discord turns into a session's client and OS. Changes apply on the next login; Xbox,
PlayStation and VR can make your client look out of date.

The header of the UI shows one status-coloured icon per platform the account is actually online
on, merged from Discord's session list the way Vencord's PlatformIndicators does it.

## Obtaining your token

1. Log in to your Discord account
2. Press Ctrl+Shift+I to open DevTools
3. Go to the Network tab, refresh the page
4. Filter by `/api`, click the `science` entry
5. Under Headers, find `Authorization` — that is your token

Or use the UI's auto-detect (Windows).

## Credits

Inspired by:
- tonyizdabezt/vc-keeper
- wtfrj/Online-account-24-7
- snipcola/Discord-Keep-Alive
- Masterain98/discord-quest-helper (UI library & token detection)
- thecloudyy/Discord-Keep-Alive

## License

MIT