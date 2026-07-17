# tzomet-twin-relay

Zion-signed twin backward-comm + fan-in controller pin.

## tools/watchdog_controller.py (canonical)

- **sha256:** `7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07`
- **path:** `tools/watchdog_controller.py`
- **role:** authoritative Zion watchdog controller (check / collect / status / fixprompt / reset)
- **rule:** local drafts and sandbox copies are deprecated; patch only via diff against this file, then re-pin sha here.

## relay/

- `relay/grok/HEAD` and `relay/claude/HEAD` — fan-out messages (issuer=mekria)
- `replies/<ai>/HEAD` — fan-in AI turns (issuer=controller)

Anonymous read:

- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/relay/grok/HEAD
- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/tools/watchdog_controller.py
