#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tzomet_mesh.py — single Zion daemon for twin fan-out + turn ingest + pinned enforcement.

Imports the pin-checked watchdog_controller (no forked rule logic).
Does not hold AI judgment; only filesystem + git + controller.cmd_*.

Usage:
  python3 tzomet_mesh.py --repo ~/tzomet_twin_relay_repo \\
    --controller ~/tzomet_twin_relay_repo/tools/watchdog_controller.py \\
    --pin 7e3a150df7116296051521ba092a35b802e3f9850105acfdcaf9955fa2b20b07

  python3 tzomet_mesh.py ... --once          # process queues and exit
  python3 tzomet_mesh.py ... --auto-fix    # also write fixes/ (delivery still manual
                                             unless you paste); default OFF

Standing layout (created if missing):
  ~/tzomet_bus/outbox/all/*.txt     → fan-out (all AIS slots)
  ~/tzomet_bus/inbox/<ai>/turn_*.txt → AI turns (app AIs write here)
  ~/tzomet_bus/fixes/<ai>/latest.txt → last fix-prompt from controller
  peer/<a>__<b>/ in repo             → PEER lines extracted from turns
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
DEFAULT_BUS = HOME / "tzomet_bus"
DEFAULT_AIS = ("grok", "claude", "gemini")
PEER_RE = re.compile(r"^PEER\|([a-z0-9_]+)\|([a-z0-9_]+)\|(.*)$", re.I | re.M)
HEADER_RE = re.compile(r"\[חי\]\s*turn=(\d+)", re.I)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_controller(path: Path, pin: str):
    got = sha256_file(path)
    if got != pin:
        print(
            f"CHECK|mesh|ABORT|controller_pin_mismatch|expected={pin}|got={got}",
            flush=True,
        )
        raise SystemExit(2)
    print(f"CHECK|mesh|PIN|controller|{got[:12]}…|ok", flush=True)
    spec = importlib.util.spec_from_file_location("watchdog_controller_pinned", path)
    if spec is None or spec.loader is None:
        raise SystemExit("CHECK|mesh|ABORT|controller_load")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_twin_relay():
    candidates = [
        HOME / "Projects" / "tzomet_twin_relay.py",
        HOME / "tzomet_twin_relay_repo" / "tools" / "tzomet_twin_relay.py",
    ]
    for p in candidates:
        if p.is_file():
            spec = importlib.util.spec_from_file_location("tzomet_twin_relay", p)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader
            spec.loader.exec_module(mod)
            return mod, p
    print("CHECK|mesh|WARN|no_twin_relay_module", flush=True)
    return None, None


def ensure_layout(bus: Path, ais: tuple[str, ...]) -> None:
    (bus / "outbox" / "all").mkdir(parents=True, exist_ok=True)
    for ai in ais:
        (bus / "inbox" / ai).mkdir(parents=True, exist_ok=True)
        (bus / "fixes" / ai).mkdir(parents=True, exist_ok=True)
        (bus / "inbox" / ai / "processed").mkdir(parents=True, exist_ok=True)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def process_outbox(bus: Path, repo: Path, twin_mod) -> int:
    """Fan-out: each new .txt in outbox/all → twin_relay process_file."""
    if twin_mod is None:
        return 0
    outbox = bus / "outbox" / "all"
    n = 0
    state_path = bus / "twin_relay_state.json"
    # twin_mod expects load_key etc.
    key = twin_mod.load_key()
    for path in sorted(outbox.glob("*.txt")):
        try:
            done = twin_mod.process_file(repo, state_path, key, path, push=True)
            if done:
                n += 1
                print(f"CHECK|mesh|FANOUT|{path.name}", flush=True)
                # archive
                arch = outbox / "processed"
                arch.mkdir(exist_ok=True)
                path.rename(arch / path.name)
        except Exception as e:
            print(f"CHECK|mesh|FANOUT_ERR|{path.name}|{type(e).__name__}|{e}", flush=True)
    return n


def extract_peers(repo: Path, ai: str, text: str) -> int:
    n = 0
    for m in PEER_RE.finditer(text):
        a, b, body = m.group(1).lower(), m.group(2).lower(), m.group(3)
        pair = f"{a}__{b}"
        d = repo / "peer" / pair
        d.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        fname = f"peer_{ts}_{ai}.txt"
        (d / fname).write_text(
            f"from={ai}\nPEER|{a}|{b}|{body}\n", encoding="utf-8"
        )
        (d / "HEAD").write_text(fname + "\n", encoding="utf-8")
        n += 1
        print(f"CHECK|mesh|PEER|{pair}|{fname}", flush=True)
    return n


def process_inbox(
    bus: Path,
    repo: Path,
    ctrl,
    ais: tuple[str, ...],
    auto_fix: bool,
) -> int:
    """Ingest turn files → controller.cmd_collect → fixes/ + peer/."""
    n = 0
    for ai in ais:
        inbox = bus / "inbox" / ai
        seen = set()
        paths = []
        for path in list(inbox.glob("turn_*.txt")) + list(inbox.glob("*.txt")):
            if path.parent != inbox:
                continue
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            paths.append(path)
        for path in sorted(paths, key=lambda p: p.name):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                continue
            # controller.cmd_collect(ai, text, repo)
            try:
                code = ctrl.cmd_collect(ai, text, str(repo))
            except Exception as e:
                print(f"CHECK|mesh|COLLECT_ERR|{ai}|{path.name}|{e}", flush=True)
                continue
            print(f"CHECK|mesh|COLLECT|{ai}|{path.name}|exit={code}", flush=True)
            # copy fix prompt if any
            fix_src = None
            # controller stores under STATE_ROOT / ai / last_fix_prompt.txt
            # relative to cwd when controller ran
            candidates = [
                Path("watchdog_state") / ai / "last_fix_prompt.txt",
                bus / "watchdog_state" / ai / "last_fix_prompt.txt",
                HOME / "tzomet_bus" / "watchdog_state" / ai / "last_fix_prompt.txt",
            ]
            # also ai_dir from module if exposed
            if hasattr(ctrl, "ai_dir"):
                candidates.insert(0, ctrl.ai_dir(ai) / "last_fix_prompt.txt")
            for c in candidates:
                if c.is_file():
                    fix_src = c
                    break
            if fix_src and fix_src.is_file():
                dest = bus / "fixes" / ai / "latest.txt"
                shutil.copy2(fix_src, dest)
                print(f"CHECK|mesh|FIX|{ai}|{dest}", flush=True)
                if auto_fix:
                    # constitutional default OFF; when on, still only write local auto_fix queue
                    q = bus / "fixes" / ai / "auto_queue"
                    q.mkdir(exist_ok=True)
                    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    shutil.copy2(fix_src, q / f"fix_{ts}.txt")
                    print(f"CHECK|mesh|AUTO_FIX_QUEUED|{ai}|not_delivered_to_browser", flush=True)

            extract_peers(repo, ai, text)
            # archive
            arch = inbox / "processed"
            arch.mkdir(exist_ok=True)
            path.rename(arch / path.name)
            n += 1
    return n


def run_loop(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    ctrl_path = Path(args.controller).expanduser().resolve()
    pin = args.pin.strip()
    bus = Path(args.bus).expanduser().resolve()
    ais = tuple(a.strip() for a in args.ais.split(",") if a.strip())

    if not (repo / ".git").is_dir():
        print(f"CHECK|mesh|ABORT|not_git|{repo}", flush=True)
        return 2
    if not ctrl_path.is_file():
        print(f"CHECK|mesh|ABORT|no_controller|{ctrl_path}", flush=True)
        return 2

    ensure_layout(bus, ais)
    # pin-enforced load
    import os

    os.environ.setdefault("WATCHDOG_STATE", str(bus / "watchdog_state"))
    ctrl = load_controller(ctrl_path, pin)
    twin_mod, twin_path = load_twin_relay()
    if twin_path:
        print(f"CHECK|mesh|TWIN|{twin_path}", flush=True)

    print(
        f"CHECK|mesh|UP|repo={repo}|bus={bus}|ais={','.join(ais)}|auto_fix={args.auto_fix}",
        flush=True,
    )

    while True:
        process_outbox(bus, repo, twin_mod)
        process_inbox(bus, repo, ctrl, ais, auto_fix=args.auto_fix)
        # push any peer commits if dirty
        st = git(repo, "status", "--porcelain")
        if st.stdout.strip():
            git(repo, "add", "replies", "peer", "relay")
            c = git(repo, "commit", "-m", "mesh: fan-in/peer/relay")
            if c.returncode == 0:
                p = git(repo, "push")
                print(
                    f"CHECK|mesh|PUSH|{'ok' if p.returncode == 0 else 'fail'}",
                    flush=True,
                )
        if args.once:
            break
        time.sleep(args.interval)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Tzomet mesh daemon (Zion)")
    ap.add_argument("--repo", required=True, help="local twin-relay clone")
    ap.add_argument(
        "--controller",
        required=True,
        help="path to pinned tools/watchdog_controller.py",
    )
    ap.add_argument(
        "--pin",
        required=True,
        help="expected sha256 of controller (abort on mismatch)",
    )
    ap.add_argument("--bus", default=str(DEFAULT_BUS))
    ap.add_argument("--ais", default=",".join(DEFAULT_AIS))
    ap.add_argument("--once", action="store_true")
    ap.add_argument(
        "--auto-fix",
        action="store_true",
        help="queue fix prompts under fixes/<ai>/auto_queue (default OFF)",
    )
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()
    raise SystemExit(run_loop(args))


if __name__ == "__main__":
    main()
