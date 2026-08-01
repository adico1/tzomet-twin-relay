#!/usr/bin/env python3
"""tzomet_zion_get.py — reverse mode: GET ping tells Zion what to do.

Shared understanding (both sides):
  GET  commands/zion_ping.json   → current command for Zion PC
  GET  commands/zion_ack.json    → last RESULT from Zion
  verbs in cmd.do (same as cmd_bridge): state|head|echo:|shell:|run:|bakasha:|…

Anyone (mobile Shortcut, another AI, CLI) PUBLISHES a ping (git push / this tool).
Zion PC only needs GET + local execute — pure reverse pull.

GET URLs (public):
  https://raw.githubusercontent.com/adico1/tzomet-twin-relay/main/commands/zion_ping.json
  https://api.github.com/repos/adico1/tzomet-twin-relay/contents/commands/zion_ping.json
  (prefer API or ?t=nonce — raw CDN can lag)

Usage:
  # publish command for Zion (from phone tooling / any host with gh)
  python3 tools/tzomet_zion_get.py ping --do state
  python3 tools/tzomet_zion_get.py ping --do 'shell:uname -a' --id phone-1
  python3 tools/tzomet_zion_get.py ping --text 'head'

  # Zion PC pull+execute once
  python3 tools/tzomet_zion_get.py pull
  python3 tools/tzomet_zion_get.py watch --interval 15

  # inspect
  python3 tools/tzomet_zion_get.py show
  python3 tools/tzomet_zion_get.py urls
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# reuse execute_do / helpers from cmd_bridge
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tzomet_cmd_bridge as bridge  # noqa: E402

HOME = Path.home()
DEFAULT_REPO = HOME / "tzomet_twin_relay_repo"
DEFAULT_BUS = HOME / "tzomet_bus"
OWNER = "adico1"
REPO_NAME = "tzomet-twin-relay"
BRANCH = "main"
PING_REL = "commands/zion_ping.json"
ACK_REL = "commands/zion_ack.json"
PING_LOG = "commands/ping_log"
SCHEMA_V = 1

RAW_PING = f"https://raw.githubusercontent.com/{OWNER}/{REPO_NAME}/main/{PING_REL}"
RAW_ACK = f"https://raw.githubusercontent.com/{OWNER}/{REPO_NAME}/main/{ACK_REL}"
API_PING = f"https://api.github.com/repos/{OWNER}/{REPO_NAME}/contents/{PING_REL}?ref={BRANCH}"
API_ACK = f"https://api.github.com/repos/{OWNER}/{REPO_NAME}/contents/{ACK_REL}?ref={BRANCH}"


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_payload(payload: dict) -> str:
    """sha256 over payload without digest field."""
    body = {k: v for k, v in payload.items() if k != "digest"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def load_pull_state(bus: Path) -> dict:
    p = bus / "zion_get_state.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"high_water_seq": 0, "last_id": None, "last_status": None}


def save_pull_state(bus: Path, state: dict) -> None:
    bus.mkdir(parents=True, exist_ok=True)
    state["updated"] = utc_iso()
    (bus / "zion_get_state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def http_get_json(url: str, timeout: float = 20.0) -> tuple[int, dict | None, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tzomet-zion-get/1.0",
            "Accept": "application/vnd.github+json, application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            text = raw.decode("utf-8", errors="replace")
            # GitHub contents API wraps base64
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                return r.status, None, text[:200]
            if isinstance(obj, dict) and obj.get("encoding") == "base64" and "content" in obj:
                decoded = base64.b64decode(obj["content"]).decode("utf-8")
                return r.status, json.loads(decoded), decoded
            return r.status, obj, text
    except urllib.error.HTTPError as e:
        return e.code, None, str(e)
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def fetch_ping_from_git(repo: Path) -> tuple[dict | None, str]:
    """Authoritative for Zion PC: fetch origin then show blob (no CDN)."""
    bridge.git(repo, "fetch", "origin", BRANCH)
    r = bridge.git(repo, "show", f"origin/{BRANCH}:{PING_REL}")
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None, f"git_show_fail:{(r.stderr or '')[:120]}"
    try:
        obj = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return None, f"git_json:{e}"
    if isinstance(obj, dict) and obj.get("kind") == "zion_ping":
        return obj, "git-origin"
    return None, "git_not_ping"


def fetch_ping_prefer_api(repo: Path | None = None) -> tuple[dict | None, str]:
    """Return (ping_dict, source). Prefer git-origin on Zion, else API, else raw."""
    if repo is not None and (repo / ".git").is_dir():
        obj, src = fetch_ping_from_git(repo)
        if obj is not None:
            return obj, src
    code, obj, err = http_get_json(API_PING)
    if code == 200 and isinstance(obj, dict) and obj.get("kind") == "zion_ping":
        return obj, "api"
    # fallback raw + cache bust
    code2, obj2, err2 = http_get_json(RAW_PING + f"?t={int(time.time())}")
    if code2 == 200 and isinstance(obj2, dict):
        return obj2, "raw"
    return None, f"git+api={code}:{err}; raw={code2}:{err2}"


def fetch_ack() -> dict | None:
    code, obj, _ = http_get_json(API_ACK)
    if code == 200 and isinstance(obj, dict):
        return obj
    code2, obj2, _ = http_get_json(RAW_ACK + f"?t={int(time.time())}")
    if code2 == 200 and isinstance(obj2, dict):
        return obj2
    return None


def next_seq(repo: Path) -> int:
    path = repo / PING_REL
    if path.is_file():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
            return int(cur.get("seq", 0)) + 1
        except Exception:
            pass
    return 1


def build_ping(
    *,
    do: str,
    ping_id: str,
    issuer: str,
    pri: str,
    expect: str,
    seq: int,
    text: str | None = None,
) -> dict:
    payload = {
        "v": SCHEMA_V,
        "kind": "zion_ping",
        "seq": seq,
        "id": ping_id,
        "ts": utc_iso(),
        "issuer": issuer,
        "to": "zion",
        "cmd": {
            "do": do,
            "pri": pri,
            "expect": expect,
        },
        "shared": {
            "protocol": "tzomet_zion_get/v1",
            "verbs": [
                "state",
                "head",
                "echo:<msg>",
                "shell:<cmd>",
                "run:<cmd>",
                "!<cmd>",
                "bakasha:<text>",
            ],
            "get_ping": RAW_PING,
            "get_ack": RAW_ACK,
        },
    }
    if text:
        payload["text"] = text
    payload["digest"] = digest_payload(payload)
    return payload


def verify_ping(ping: dict) -> tuple[bool, str]:
    if not isinstance(ping, dict):
        return False, "not_object"
    if ping.get("kind") != "zion_ping":
        return False, f"kind={ping.get('kind')}"
    if int(ping.get("v", 0)) != SCHEMA_V:
        return False, f"v={ping.get('v')}"
    if ping.get("to") not in ("zion", "zion-grok-build", "all"):
        return False, f"to={ping.get('to')}"
    want = ping.get("digest", "")
    got = digest_payload(ping)
    if want and want != got:
        return False, f"TAMPER digest got={got[:12]} want={str(want)[:12]}"
    cmd = ping.get("cmd") or {}
    if not (cmd.get("do") or ping.get("text")):
        return False, "empty_do"
    return True, "ok"


def publish_json(repo: Path, rel: str, obj: dict, commit_msg: str) -> dict:
    bridge.ensure_layout(repo)
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # history log for pings
    if rel == PING_REL:
        log_dir = repo / PING_LOG
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"ping_{obj.get('seq', 0):05d}_{obj.get('id', 'x')}.json").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        bridge.git(repo, "add", rel, PING_LOG)
    else:
        bridge.git(repo, "add", rel)
    c = bridge.git(repo, "commit", "-m", commit_msg)
    if c.returncode != 0 and "nothing to commit" not in ((c.stdout or "") + (c.stderr or "")):
        return {"ok": False, "error": (c.stderr or c.stdout or "")[-400:]}
    p = bridge.git(repo, "push", "origin", f"HEAD:{BRANCH}")
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or "")[-400:], "path": rel}
    return {"ok": True, "path": rel, "seq": obj.get("seq"), "id": obj.get("id")}


def cmd_ping(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    bridge.git(repo, "pull", "--ff-only", "origin", BRANCH)
    do = (args.do or "").strip()
    text = (args.text or "").strip() or None
    if not do and text:
        do = text
        text = None
    if not do:
        print("need --do or --text", file=sys.stderr)
        return 2
    seq = next_seq(repo)
    ping_id = args.id or f"ping-{bridge.utc_ts()}"
    ping = build_ping(
        do=do,
        ping_id=ping_id,
        issuer=args.issuer,
        pri=args.pri,
        expect=args.expect,
        seq=seq,
        text=text,
    )
    res = publish_json(repo, PING_REL, ping, f"zion_ping seq={seq} id={ping_id} do={do[:40]}")
    res["get"] = {
        "raw": RAW_PING,
        "api": API_PING,
        "ack_raw": RAW_ACK,
    }
    res["ping"] = ping
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def execute_ping(ping: dict, bus: Path, repo: Path) -> dict:
    cmd = ping.get("cmd") or {}
    do = (cmd.get("do") or "").strip()
    # optional full CMD text block
    if ping.get("text") and "CMD|to=" in str(ping.get("text")):
        blocks = bridge.parse_cmds(ping["text"])
        if blocks:
            do = blocks[0]["body"]
    exe = bridge.execute_do(do, bus, repo)
    result = {
        "v": SCHEMA_V,
        "kind": "zion_ack",
        "in_reply_to": ping.get("id"),
        "ping_seq": ping.get("seq"),
        "ping_digest": ping.get("digest"),
        "status": exe["status"],
        "kind_exec": exe["kind"],
        "output": exe["output"],
        "ts": utc_iso(),
        "host": "zion",
        "executor": "tzomet_zion_get",
    }
    result["digest"] = digest_payload(result)
    return result


def cmd_pull(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    bus = Path(args.bus).expanduser().resolve()
    state = load_pull_state(bus)

    # Prefer git-origin (authoritative on Zion), else GitHub API/raw GET.
    ping, source = fetch_ping_prefer_api(repo)
    if not ping:
        print(json.dumps({"ok": True, "event": "no_ping", "detail": source}, ensure_ascii=False))
        return 0

    ok, reason = verify_ping(ping)
    if not ok:
        print(json.dumps({"ok": False, "event": "reject", "reason": reason}, ensure_ascii=False))
        return 3

    seq = int(ping.get("seq", 0))
    hw = int(state.get("high_water_seq", 0))
    if seq <= hw:
        print(
            json.dumps(
                {
                    "ok": True,
                    "event": "IDLE",
                    "seq": seq,
                    "high_water": hw,
                    "source": source,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(
        json.dumps(
            {
                "ok": True,
                "event": "EXEC",
                "seq": seq,
                "id": ping.get("id"),
                "do": (ping.get("cmd") or {}).get("do"),
                "source": source,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    # ensure local repo for ack push + execute_do paths
    bridge.git(repo, "pull", "--ff-only", "origin", BRANCH)
    ack = execute_ping(ping, bus, repo)
    pub = publish_json(
        repo,
        ACK_REL,
        ack,
        f"zion_ack seq={seq} id={ping.get('id')} status={ack.get('status')}",
    )

    # also fan RESULT on twin-relay for mobile pollers already using relay HEAD
    if not args.no_relay:
        bridge.publish_result_to_relay(
            repo,
            bus,
            {
                "id": ping.get("id"),
                "status": ack.get("status"),
                "kind": ack.get("kind_exec"),
                "ts": ack.get("ts"),
                "output": {
                    "ping_seq": seq,
                    "ack": ack.get("output"),
                    "get_ack": RAW_ACK,
                },
            },
        )

    state["high_water_seq"] = seq
    state["last_id"] = ping.get("id")
    state["last_status"] = ack.get("status")
    save_pull_state(bus, state)

    print(
        json.dumps(
            {
                "ok": True,
                "event": "DONE",
                "seq": seq,
                "status": ack.get("status"),
                "ack_publish": pub,
                "get_ack": RAW_ACK,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ack.get("status") in ("ok", "partial") else 1


def cmd_watch(args) -> int:
    print(
        f"CHECK|zion_get|UP|interval={args.interval}|get={RAW_PING}",
        flush=True,
    )
    while True:
        try:
            # reuse pull
            class A:
                pass

            a = A()
            a.repo = args.repo
            a.bus = args.bus
            a.no_relay = args.no_relay
            cmd_pull(a)
        except Exception as e:
            print(f"CHECK|zion_get|ERR|{type(e).__name__}|{e}", flush=True)
        time.sleep(max(5, args.interval))


def cmd_show(args) -> int:
    repo = Path(args.repo).expanduser().resolve() if getattr(args, "repo", None) else None
    ping, src = fetch_ping_prefer_api(repo)
    ack = fetch_ack()
    print(
        json.dumps(
            {
                "ping_source": src,
                "ping": ping,
                "ack": ack,
                "urls": {"ping_raw": RAW_PING, "ping_api": API_PING, "ack_raw": RAW_ACK},
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_urls(_args) -> int:
    print(
        json.dumps(
            {
                "GET_ping_raw": RAW_PING,
                "GET_ping_api": API_PING,
                "GET_ack_raw": RAW_ACK,
                "GET_ack_api": API_ACK,
                "shared_verbs": [
                    "state",
                    "head",
                    "echo:<msg>",
                    "shell:<cmd>",
                    "run:<cmd>",
                    "!<cmd>",
                    "bakasha:<text>",
                ],
                "flow": [
                    "1. publish: tzomet_zion_get.py ping --do <verb>",
                    "2. Zion GET ping JSON (api preferred)",
                    "3. verify digest + seq high-water",
                    "4. execute_do(shared verbs)",
                    "5. publish zion_ack.json + optional twin-relay RESULT",
                    "6. mobile GET ack / poll relay HEAD",
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Tzomet reverse GET ping → Zion execute")
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--bus", default=str(DEFAULT_BUS))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ping", help="publish zion_ping.json (command for Zion)")
    p.add_argument("--do", default="", help="verb body (state|head|shell:…)")
    p.add_argument("--text", default="", help="alt free text / full CMD block")
    p.add_argument("--id", default="")
    p.add_argument("--issuer", default="tzomet")
    p.add_argument("--pri", default="normal")
    p.add_argument("--expect", default="done")

    pl = sub.add_parser("pull", help="GET ping + execute if new seq")
    pl.add_argument("--no-relay", action="store_true")

    w = sub.add_parser("watch", help="loop pull")
    w.add_argument("--interval", type=int, default=15)
    w.add_argument("--no-relay", action="store_true")

    sub.add_parser("show", help="GET current ping+ack")
    sub.add_parser("urls", help="print GET contract")

    args = ap.parse_args()
    if args.cmd == "ping":
        return cmd_ping(args)
    if args.cmd == "pull":
        return cmd_pull(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "urls":
        return cmd_urls(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
