# tzomet-twin-relay

Zion-signed twin backward-comm + pinned Tzomet contracts.

## tools/watchdog_controller.py (canonical)

- **sha256:** `7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07`
- **path:** `tools/watchdog_controller.py`

## docs/ (mekria-pinned)

| file | sha256 |
|---|---|
| `docs/tzomet_onboarding_lite.txt` | `e9e92fdd158e637f6f8ebf58ec41283103e7e3b0c19cf6928b307f2b291c4417` |
| `docs/tzomet_party_policy.md` | `bbcc7cdab878199f0e75b7873d9de518be5135a9c8db7917ca76c6edf67a1c01` |

- **LITE:** TZOMET-LITE v1 for sub-floor models (ruling b).
- **policy:** kael exclude formal + floor policy + delivery via `onboard.sh`.
- Local drafts deprecated; patch via diff against repo, re-pin sha.

## relay/

- `relay/grok|claude|gemini/HEAD` — fan-out (issuer=mekria)
- `replies/<ai>/HEAD` — fan-in (issuer=controller)

Anonymous raw:

- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/docs/tzomet_onboarding_lite.txt
- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/docs/tzomet_party_policy.md
- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/tools/watchdog_controller.py
