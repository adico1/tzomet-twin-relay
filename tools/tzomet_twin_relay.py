#!/usr/bin/env python3
"""tzomet_twin_relay.py — Zion twin backward-comm relay.

Watches ~/tzomet_bus/outbox/all/ (or --outbox). On each new .txt drop:
  - builds one payload (issuer=mekria)
  - sha256 + HMAC-SHA256 over canonical payload JSON
  - writes identical envelopes to relay/grok/ and relay/claude/
  - updates HEAD in both slots
  - one git commit + push

Usage:
  python3 tzomet_twin_relay.py --repo /path/to/clone
  python3 tzomet_twin_relay.py --repo /path/to/clone --once
  python3 tzomet_twin_relay.py --repo /path/to/clone --drop "text"
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULT_OUTBOX = HOME / "tzomet_bus" / "outbox" / "all"
DEFAULT_STATE = HOME / "tzomet_bus" / "twin_relay_state.json"
# default twin slots; extend via env TZOMET_TWIN_AIS=grok,claude,gemini,...
_AIS_ENV = os.environ.get("TZOMET_TWIN_AIS", "grok,claude,gemini")
AIS = tuple(a.strip() for a in _AIS_ENV.split(",") if a.strip())

KEY_CANDIDATES = [
    HOME / "Library" / "azm" / "hmac.key",
    Path("/Users/adicohen/Projects/.gateway_key"),
    HOME / "Projects" / ".gateway_key",
]


def load_key() -> bytes:
    for p in KEY_CANDIDATES:
        if p.is_file():
            raw = p.read_bytes()
            if raw:
                return raw
    raise SystemExit("CHECK|relay_daemon|DEAD|no_hmac_key")


def canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_envelope(payload: dict, key: bytes) -> dict:
    can = canonical_payload(payload)
    raw = can.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    sig = hmac.new(key, raw, hashlib.sha256).hexdigest()
    return {
        "payload": payload,
        "signature": {
            "alg": "HMAC-SHA256",
            "sig": sig,
            "sha256": sha,
        },
    }


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True, capture_output=True)


def load_state(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"seq": 0, "processed": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_repo_layout(repo: Path) -> None:
    for ai in AIS:
        (repo / "relay" / ai).mkdir(parents=True, exist_ok=True)
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# tzomet-twin-relay\n\n"
            "Zion-signed backward-comm for grok/ and claude/.\n"
            "Anonymous read: raw.githubusercontent.com/<owner>/<repo>/main/relay/<ai>/HEAD\n",
            encoding="utf-8",
        )


def next_seq(state: dict, repo: Path) -> int:
    seq = int(state.get("seq", 0)) + 1
    # also bump past any existing msg files
    for ai in AIS:
        d = repo / "relay" / ai
        if not d.is_dir():
            continue
        for f in d.glob("msg_*.json"):
            try:
                n = int(f.stem.split("_", 1)[1])
                if n >= seq:
                    seq = n + 1
            except ValueError:
                pass
    return seq


def process_text(
    repo: Path,
    state: dict,
    key: bytes,
    text: str,
    src: str,
    push: bool,
) -> dict:
    seq = next_seq(state, repo)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # byte-identical payload for both slots (anti-broken-telephone)
    payload = {
        "ai": "all",
        "seq": seq,
        "src": src,
        "host": "zion",
        "role": "backward_comm",
        "issuer": "mekria",
        "ts": ts,
        "text": text,
    }
    env = sign_envelope(payload, key)
    body = json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    fname = f"msg_{seq:05d}.json"

    ensure_repo_layout(repo)
    for ai in AIS:
        slot = repo / "relay" / ai
        (slot / fname).write_text(body, encoding="utf-8")
        (slot / "HEAD").write_text(fname + "\n", encoding="utf-8")

    # git commit
    run(["git", "add", "relay", "README.md"], cwd=repo, check=False)
    msg = f"twin relay seq={seq} sha={env['signature']['sha256'][:12]}"
    # allow empty? should not be empty
    r = run(["git", "commit", "-m", msg], cwd=repo, check=False)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        print(f"CHECK|relay_daemon|ERR|git_commit|{r.stderr.strip()[:200]}", file=sys.stderr)
        raise SystemExit(1)

    if push:
        pr = run(["git", "push", "origin", "HEAD"], cwd=repo, check=False)
        if pr.returncode != 0:
            print(f"CHECK|relay_daemon|ERR|git_push|{pr.stderr.strip()[:300]}", file=sys.stderr)
            raise SystemExit(1)

    state["seq"] = seq
    state.setdefault("processed", {})
    state["last_sha256"] = env["signature"]["sha256"]
    state["last_fname"] = fname
    sp = Path(state.get("_path", str(DEFAULT_STATE)))
    to_save = {k: v for k, v in state.items() if not str(k).startswith("_")}
    save_state(sp, to_save)

    print(
        json.dumps(
            {
                "ok": True,
                "seq": seq,
                "fname": fname,
                "sha256": env["signature"]["sha256"],
                "sha12": env["signature"]["sha256"][:12],
                "ts": ts,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return env


def process_file(repo: Path, state_path: Path, key: bytes, path: Path, push: bool) -> bool:
    st = load_state(state_path)
    st["_path"] = str(state_path)
    key_id = str(path.resolve())
    if key_id in st.get("processed", {}):
        return False
    text = path.read_text(encoding="utf-8").rstrip("\n")
    if not text:
        return False
    process_text(repo, st, key, text, src=path.name, push=push)
    st2 = load_state(state_path)
    st2.setdefault("processed", {})[key_id] = {
        "seq": st2.get("seq"),
        "sha256": st2.get("last_sha256"),
    }
    save_state(state_path, st2)
    return True


def watch_loop(repo: Path, outbox: Path, state: Path, key: bytes, push: bool, once: bool) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    print(f"CHECK|relay_daemon|UP|repo={repo}|outbox={outbox}", flush=True)
    while True:
        files = sorted(outbox.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        for f in files:
            try:
                process_file(repo, state, key, f, push=push)
            except SystemExit:
                raise
            except Exception as e:
                print(f"CHECK|relay_daemon|ERR|{type(e).__name__}|{e}", flush=True)
        if once:
            break
        time.sleep(1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="local clone path of public twin relay repo")
    ap.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--once", action="store_true", help="process current outbox and exit")
    ap.add_argument("--drop", default=None, help="inline text to publish immediately (no file)")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").is_dir():
        raise SystemExit(f"CHECK|relay_daemon|DEAD|not_a_git_repo|{repo}")

    key = load_key()
    push = not args.no_push
    state = Path(args.state).expanduser()
    outbox = Path(args.outbox).expanduser()

    ensure_repo_layout(repo)

    if args.drop is not None:
        st = load_state(state)
        st["_path"] = str(state)
        process_text(repo, st, key, args.drop, src="--drop", push=push)
        return

    watch_loop(repo, outbox, state, key, push=push, once=args.once)


if __name__ == "__main__":
    main()
