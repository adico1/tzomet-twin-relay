#!/usr/bin/env python3
"""tzomet_poll.py — pull-side coordination client over the GitHub relay.

The relay repo on GitHub is the free internet coordination server. This client
fetches the remote state (never mutating the working tree) and surfaces new
מכריע messages for one AI, applying the BACKWARD COMM gates:
  DEAD   — remote unreachable / HEAD or message missing
  TAMPER — recomputed canonical sha256 != signature.sha256
  REJECT — payload.issuer != "mekria"
  REPLAY — seq <= this AI's high-water

Push side is handled by watchdog_controller.py collect. Stdlib only.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".tzomet" / "poll_state"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def canonical_sha256(payload):
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def high_water(ai):
    f = STATE_DIR / f"{ai}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8")).get("high_water", 0)
    return 0


def set_high_water(ai, seq):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{ai}.json").write_text(
        json.dumps({"ai": ai, "high_water": seq}), encoding="utf-8")


def show(repo, ref):
    r = git(repo, "show", ref)
    return r.stdout if r.returncode == 0 else None


def poll_once(repo, ai, branch="main"):
    origin = f"origin/{branch}"
    if git(repo, "fetch", "origin", branch).returncode != 0:
        print(f"CHECK|relay|DEAD|fetch_failed")
        return 2

    ls = git(repo, "ls-tree", "-r", "--name-only", origin, f"relay/{ai}/")
    if ls.returncode != 0:
        print(f"CHECK|relay|DEAD|no_slot|relay/{ai}")
        return 2
    msgs = sorted(p for p in ls.stdout.split()
                  if p.rsplit("/", 1)[-1].startswith("msg_")
                  and p.endswith(".json"))
    if not msgs:
        print(f"CHECK|relay|DEAD|no_messages|relay/{ai}")
        return 2

    hw = high_water(ai)
    emitted = 0
    for path in msgs:
        raw = show(repo, f"{origin}:{path}")
        if raw is None:
            print(f"CHECK|relay|DEAD|unreadable|{path}")
            return 2
        obj = json.loads(raw)
        payload = obj["payload"]
        want = obj.get("signature", {}).get("sha256", "")
        got = canonical_sha256(payload)
        if got != want:
            print(f"CHECK|relay|TAMPER|sha256|{path}|got={got[:12]}|want={want[:12]}")
            return 3
        if payload.get("issuer") != "mekria":
            print(f"CHECK|relay|REJECT|issuer={payload.get('issuer')}|{path}")
            return 4
        seq = int(payload["seq"])
        if seq <= hw:
            continue  # already ingested; silent, not an error
        print(f"CHECK|relay|OK|seq={seq}|sha={got[:12]}")
        print(payload.get("text", ""))
        set_high_water(ai, seq)
        hw = seq
        emitted += 1

    if emitted == 0:
        print(f"CHECK|relay|IDLE|ai={ai}|high_water={hw}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Tzomet relay poll client (pull side)")
    ap.add_argument("--repo", required=True, help="local twin-relay clone")
    ap.add_argument("--ai", required=True, help="which slot to read (e.g. claude)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--watch", action="store_true", help="loop instead of one poll")
    ap.add_argument("--interval", type=int, default=30, help="seconds between polls")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    if not (repo / ".git").is_dir():
        print(f"CHECK|relay|DEAD|not_a_git_repo|{repo}")
        return 2

    if not args.watch:
        return poll_once(repo, args.ai, args.branch)
    while True:
        poll_once(repo, args.ai, args.branch)
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
