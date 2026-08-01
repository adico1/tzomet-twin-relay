# Mobile ↔ Zion without copy-paste

## Bus

| Path | Role |
|---|---|
| `commands/incoming/*.txt` | CMD deposited (phone / Shortcut / CLI) |
| `commands/results/*.json` | RESULT after Zion runs |
| `commands/done/*.txt` | archived CMD |
| `relay/grok/HEAD` | mobile **poll** for RESULT bakasha |
| GitHub Issues label `zion-cmd` | phone-friendly deposit |

## Loop (no paste between Grok chats)

1. **Phone → GitHub** (you type intent once, not between AIs):
   - iOS Shortcut / `gh` / GitHub mobile issue with label `zion-cmd`
2. **Zion worker** (`tzomet_cmd_bridge.py once|watch`) pulls, runs DO, pushes RESULT
3. **Phone Grok** polls twin-relay (`.` or auto if tools allow):
   - `https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/relay/grok/HEAD`

## Deposit (CLI on Mac)

```sh
python3 ~/tzomet_twin_relay_repo/tools/tzomet_cmd_bridge.py deposit --text 'state'
python3 ~/tzomet_twin_relay_repo/tools/tzomet_cmd_bridge.py once
```

## Deposit (GitHub Issue — easy on phone)

```sh
python3 ~/tzomet_twin_relay_repo/tools/tzomet_cmd_bridge.py issue-deposit \
  --title 'CMD state' --body 'state'
```

Or open on phone:
https://github.com/adico1/tzomet-twin-relay/issues/new?labels=zion-cmd

Body example:

```cmd
CMD|to=zion-grok-build|id=phone-1|pri=normal|expect=done
DO:
state
END
```

## Structured DO verbs (auto-executed)

| DO | Action |
|---|---|
| `state` | twin_relay_state.json |
| `head` | local vs public HEAD |
| `echo:…` | echo |
| `shell:…` / `!…` / `run:…` | bash (logged, banned patterns refused) |
| `bakasha:…` | push twin-relay message |
| free text | inbox drop + note (interactive) |

## Worker

```sh
# one shot
python3 ~/tzomet_twin_relay_repo/tools/tzomet_cmd_bridge.py once

# continuous
python3 ~/tzomet_twin_relay_repo/tools/tzomet_cmd_bridge.py watch --interval 15
```

LaunchAgent (optional): `~/Library/LaunchAgents/com.adicohen.tzomet-cmd-bridge.plist`

## Mobile Grok prompt change

Keep CONNECT + poll. Replace paste-CMD instruction with:

> Do not ask Adi to paste. Emit CMD. Adi (or Shortcut) deposits to GitHub
> `zion-cmd` issue / commands/incoming. Poll relay HEAD for RESULT.

## What still needs a human once

Phone Grok cannot `git push` alone. Deposit is either:
- Adi Shortcut with GitHub PAT → issue/file, or
- Adi types one issue on GitHub mobile, or
- SSH/gh from phone terminal

That is **one operator action**, not Grok↔Grok paste.
