# Shared understanding — reverse GET mode

## Contract `tzomet_zion_get/v1`

| GET | Meaning |
|---|---|
| `commands/zion_ping.json` | Current command **for Zion PC** |
| `commands/zion_ack.json` | Last execution RESULT from Zion |

Public:
- https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/commands/zion_ping.json
- https://api.github.com/repos/adico1/tzomet-twin-relay/contents/commands/zion_ping.json

Prefer **API** (no CDN lag). Raw: append `?t=<nonce>`.

## Ping object

```json
{
  "v": 1,
  "kind": "zion_ping",
  "seq": 1,
  "id": "ping-…",
  "ts": "2026-08-01T00:00:00Z",
  "issuer": "tzomet",
  "to": "zion",
  "cmd": { "do": "state", "pri": "normal", "expect": "done" },
  "digest": "<sha256 canonical without digest field>"
}
```

## Verbs (`cmd.do`) — both sides same list

| do | Zion action |
|---|---|
| `state` | read `~/tzomet_bus/twin_relay_state.json` |
| `head` | local vs public relay HEAD |
| `echo:…` | echo |
| `shell:…` / `run:…` / `!…` | bash (banned patterns refused) |
| `bakasha:…` | push twin-relay message |

## Flow

1. **Publisher** (mobile tooling / tzomet / CLI):  
   `python3 tools/tzomet_zion_get.py ping --do state`
2. **Zion** (auto): GET ping → verify digest → if `seq > high_water` execute → write ack  
   `python3 tools/tzomet_zion_get.py pull`  (or LaunchAgent)
3. **Reader** GET ack or poll `relay/grok/HEAD` for RESULT text

## High-water

Zion stores `~/tzomet_bus/zion_get_state.json` → `high_water_seq`.  
Replay of same/older seq = IDLE (no re-exec).

## Relation to deposit bus

| Mode | Path | Direction |
|---|---|---|
| deposit | `commands/incoming/*.txt` | push files / issues → Zion |
| **reverse GET** | `commands/zion_ping.json` | **GET-only poll** → Zion |

Same executor: `tzomet_cmd_bridge.execute_do`.
