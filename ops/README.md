# Headlines Ops

Double-clickable Terminal commands for day-to-day development.

## Commands

### Start Headlines.command
Starts the FastAPI backend on `http://localhost:8000` with `--reload` active.

Kills any existing process on port 8000 before starting, so it's always safe to double-click.

### Pull 50 Stories.command
Fetches up to 50 articles from GNews and runs them through the full pipeline synchronously:

```
Ingest → Normalise → Cluster → Categorise → Rank → Summarise
```

Prints progress per stage and a final summary (articles ingested, stories produced, category breakdown, ranking run ID). Runs entirely in-process — does not require the dispatcher or consumer to be running.

Requires `GNEWS_API_KEY` to be set in `backend/.env`.

### Reset Profile.command ⚠️ DEV ONLY

Clears all `user_story_state` rows for profile 1 (or a profile passed as the first argument).

**When to use:** When testing the manifest endpoint repeatedly with the same story pool, the profile accumulates `queued`/`consumed` rows that exclude all available stories, causing a 404. Running this reset makes every story eligible again so the next manifest request assembles a fresh bulletin.

**WARNING: Do not run this against a production database.** It permanently removes all story consumption history for the profile, destroying the dedup guarantee that prevents users from hearing the same story twice.

```bash
# Reset profile 1 (default)
ops/"Reset Profile.command"

# Reset a different profile
ops/"Reset Profile.command" 2
```

## iOS device testing — LAN IP configuration

When testing on a real iOS device (not Simulator), the server must be reachable by your Mac's LAN IP. Two settings must stay in sync:

**1. `backend/.env` — `PUBLIC_API_BASE_URL`**

The server uses this to build absolute segment URLs in manifest responses. Set it to your Mac's LAN IP:

```
PUBLIC_API_BASE_URL=http://192.168.1.111:8000
```

**2. `ios/Headlines/Headlines/Core/AppConfig.swift` — `deviceLANIP`**

iOS uses this as the API base URL when running on a physical device:

```swift
static let deviceLANIP = "192.168.1.111"
```

Both must point to the same IP. When your LAN IP changes:
1. Update `.env` and restart the server (double-click **Start Headlines.command**)
2. Update `AppConfig.swift` and rebuild the iOS app

**Finding your current LAN IP:**
```bash
ipconfig getifaddr en0
```
or: System Settings → Wi-Fi → your network → Details → IP Address

> The Simulator always uses `127.0.0.1` automatically — no configuration needed.

---

## Desktop aliases

For quick access without navigating into the project folder, macOS aliases to both commands live at:

```
~/Desktop/Headlines Ops/
├── Start Headlines.command  (alias)
└── Pull 50 Stories.command  (alias)
```

The originals stay in `ops/`. The aliases survive moving the originals because macOS aliases track the file by identity, not path.

### Recreating the aliases

If the aliases break (e.g. after reinstalling macOS), run these three steps:

```bash
# 1. Create the desktop folder
mkdir -p ~/Desktop/"Headlines Ops"

# 2. Create the aliases via Finder
osascript -e '
tell application "Finder"
    set d to folder (POSIX file (POSIX path of (path to desktop)) & "Headlines Ops/")
    make alias file to (POSIX file "/Users/paulbroome/Desktop/Headlines/ops/Start Headlines.command") at d
    make alias file to (POSIX file "/Users/paulbroome/Desktop/Headlines/ops/Pull 50 Stories.command") at d
end tell'

# 3. Set execute bit + Gatekeeper approval on each alias
chmod +x ~/Desktop/"Headlines Ops"/*.command
spctl --add ~/Desktop/"Headlines Ops"/"Start Headlines.command"
spctl --add ~/Desktop/"Headlines Ops"/"Pull 50 Stories.command"
```

## macOS first-run setup (originals and aliases)

On macOS Sonoma, newly-created `.command` files are blocked by Gatekeeper until they're approved.
Run this once from Terminal after creating any new `.command` file:

```bash
chmod +x "/path/to/My Command.command"
spctl --add "/path/to/My Command.command"
```

The existing commands and their desktop aliases are already approved.

If a file still doesn't open after `spctl --add`, right-click → Open in Finder the first time — this brings any remaining approval dialog to the foreground.

## Adding new commands

1. Create a new `.command` file in this folder
2. Make it executable: `chmod +x "ops/My Command.command"`
3. The script should `cd` into the relevant directory before doing anything

Template:
```bash
#!/usr/bin/env bash
set -e

BACKEND="$(cd "$(dirname "$0")/../backend" && pwd)"
cd "$BACKEND"

# your commands here
```
