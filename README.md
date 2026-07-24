# tzomet-twin-relay

Zion-signed twin backward-comm + pinned enforcement + mesh daemon.

## tools/ (canonical)

| file | sha256 |
|---|---|
| tools/watchdog_controller.py | `7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07` |
| tools/tzomet_mesh.py | `9e215d5a9fb8936e965d43ca12e235f5f5891fa7625ecceaae4f399262f3c28f` |
| tools/tzomet_twin_relay.py | `8abf3bbefa4b14f076209824868b3b33ec25a0512ca02ae5d698414726d4519d` |

**Controller pin (blessed):** `7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07`  
**Rejected:** b51cf954b0a7… — no artifact on Zion; not blessed.

### Pin history

- `tools/tzomet_mesh.py`
  `9a77f200ec6b310b6cc5370b8b2e3dbe03fb0d837cd1c56eca58a23561432753`
  is **SUPERSEDED**, not rejected, by
  `9e215d5a9fb8936e965d43ca12e235f5f5891fa7625ecceaae4f399262f3c28f`.

### Mesh daemon

```
python3 tools/tzomet_mesh.py --repo ~/tzomet_twin_relay_repo \
  --controller tools/watchdog_controller.py \
  --pin 7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07 \
  --relay ~/tzomet_twin_relay_repo/tools/tzomet_twin_relay.py \
  --relay-pin 8abf3bbefa4b14f076209824868b3b33ec25a0512ca02ae5d698414726d4519d
```

- Fan-out: ~/tzomet_bus/outbox/all/*.txt
- Ingest: ~/tzomet_bus/inbox/<ai>/turn_*.txt
- Fixes: ~/tzomet_bus/fixes/<ai>/latest.txt (--auto-fix queues only; default OFF)
- Peer: peer/<a>__<b>/ from PEER lines

## docs/

| file | sha256 |
|---|---|
| docs/tzomet_onboarding_lite.txt | `e9e92fdd158e637f6f8ebf58ec41283103e7e3b0c19cf6928b307f2b291c4417` |
| docs/tzomet_party_policy.md | `bbcc7cdab878199f0e75b7873d9de518be5135a9c8db7917ca76c6edf67a1c01` |

raw: https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/tools/tzomet_mesh.py
