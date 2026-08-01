#!/usr/bin/env python3
"""tzomet_cmd_bridge.py — no-paste command bus: mobile/operator ↔ Zion via GitHub.

GitHub (adico1/tzomet-twin-relay) is the coordination server:
  commands/incoming/<id>.txt   → deposit (phone Shortcut / gh / this tool)
  commands/results/<id>.json   → RESULT after Zion runs
  commands/done/<id>.txt       → archive of original CMD
  relay/<ai>/msg_*.json        → mobile poll (Zion-signed bakasha/RESULT text)

Local state:
  ~/tzomet_bus/cmd_bridge_state.json
  ~/tzomet_bus/inbox/grok/     (also drops a turn copy for mesh)

Usage:
  python3 tools/tzomet_cmd_bridge.py deposit --text '...'
  python3 tools/tzomet_cmd_bridge.py deposit --stdin
  python3 tools/tzomet_cmd_bridge.py once
  python3 tools/tzomet_cmd_bridge.py watch --interval 15
  python3 tools/tzomet_cmd_bridge.py status
  python3 tools/tzomet_cmd_bridge.py issue-deposit --title '...' --body '...'

Stdlib + git + gh. No new listening ports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULT_REPO = HOME / "tzomet_twin_relay_repo"
DEFAULT_BUS = HOME / "tzomet_bus"
RELAY_MOD = DEFAULT_REPO / "tools" / "tzomet_twin_relay.py"
OWNER = "adico1"
REPO_NAME = "tzomet-twin-relay"
BRANCH = "main"
INCOMING = "commands/incoming"
RESULTS = "commands/results"
DONE = "commands/done"
ISSUE_LABEL = "zion-cmd"

CMD_RE = re.compile(
    r"```cmd\s*\n"
    r"CMD\|to=(?P<to>[^|]+)\|id=(?P<id>[^|]+)\|pri=(?P<pri>[^|]+)\|expect=(?P<expect>[^\n]+)\n"
    r"DO:\n(?P<body>.*?)\nEND\n?"
    r"```",
    re.S,
)

# looser: CMD line without fence
CMD_LOOSE = re.compile(
    r"CMD\|to=(?P<to>[^|]+)\|id=(?P<id>[^|]+)\|pri=(?P<pri>[^|]+)\|expect=(?P<expect>[^\n]+)\n"
    r"DO:\n(?P<body>.*?)\nEND\b",
    re.S,
)


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gh_token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if t.strip():
        return t.strip()
    r = run(["gh", "auth", "token"])
    return (r.stdout or "").strip()


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args], timeout=180)


def ensure_layout(repo: Path) -> None:
    for rel in (INCOMING, RESULTS, DONE, "commands"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    keep = repo / "commands" / "README.md"
    if not keep.is_file():
        keep.write_text(
            "# commands bus\n\n"
            f"- `{INCOMING}/` deposit CMD turns (mobile / Shortcut / gh)\n"
            f"- `{RESULTS}/` Zion RESULT json\n"
            f"- `{DONE}/` archived CMD text\n"
            "Worker: `python3 tools/tzomet_cmd_bridge.py once|watch`\n",
            encoding="utf-8",
        )


def load_state(bus: Path) -> dict:
    p = bus / "cmd_bridge_state.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"processed_files": {}, "processed_issues": {}, "last_run": None}


def save_state(bus: Path, state: dict) -> None:
    bus.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    (bus / "cmd_bridge_state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_cmds(text: str) -> list[dict]:
    out = []
    for rx in (CMD_RE, CMD_LOOSE):
        for m in rx.finditer(text):
            out.append(
                {
                    "to": m.group("to").strip(),
                    "id": m.group("id").strip(),
                    "pri": m.group("pri").strip(),
                    "expect": m.group("expect").strip(),
                    "body": m.group("body").strip(),
                }
            )
        if out:
            return out
    # bare DO fallback: whole text as one command
    if text.strip():
        cid = f"auto-{utc_ts()}"
        out.append(
            {
                "to": "zion-grok-build",
                "id": cid,
                "pri": "normal",
                "expect": "done",
                "body": text.strip(),
            }
        )
    return out


def execute_do(body: str, bus: Path, repo: Path) -> dict:
    """Run DO body. Returns {status, output, kind}."""
    b = body.strip()
    lines = [ln.rstrip() for ln in b.splitlines() if ln.strip()]
    joined = "\n".join(lines)

    # structured verbs
    low = joined.lower()
    if low in ("state", "status", "twin_state", "report state"):
        sp = bus / "twin_relay_state.json"
        data = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
        return {"status": "ok", "kind": "state", "output": data}

    if low in ("head", "heads", "align"):
        local = (repo / "relay" / "grok" / "HEAD").read_text(encoding="utf-8").strip()
        code, remote = _http_get(
            f"https://raw.githubusercontent.com/{OWNER}/{REPO_NAME}/main/relay/grok/HEAD"
        )
        remote_s = remote.decode("utf-8", errors="replace").strip() if code == 200 else f"http={code}"
        return {
            "status": "ok" if code == 200 and local == remote_s else "partial",
            "kind": "head",
            "output": {"local": local, "public": remote_s, "aligned": local == remote_s},
        }

    if low.startswith("echo:") or low.startswith("echo "):
        msg = joined.split(":", 1)[-1].strip() if ":" in joined[:6] else joined[5:].strip()
        return {"status": "ok", "kind": "echo", "output": msg}

    if low.startswith("shell:") or joined.startswith("!"):
        cmd = joined[6:].strip() if low.startswith("shell:") else joined[1:].strip()
        return _run_shell(cmd)

    if low.startswith("bakasha:"):
        text = joined.split(":", 1)[1].strip()
        return _push_bakasha(repo, bus, text)

    # natural-language helpers (measured patterns)
    if "twin_relay_state" in low or "seq" in low and "head" in low:
        st = execute_do("state", bus, repo)
        hd = execute_do("head", bus, repo)
        return {
            "status": "ok" if st["status"] == "ok" else "partial",
            "kind": "state+head",
            "output": {"state": st["output"], "head": hd["output"]},
        }

    if "list listeners" in low or "live listeners" in low or re.search(r"\b9218\b", low):
        return _run_shell(
            "lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | "
            "grep -E '9218|9219|57940|58060|8443|9000|9300' || true"
        )

    # default: safe shell not assumed — record as pending_manual for interactive session
    # still try `run:` prefix
    if low.startswith("run:"):
        return _run_shell(joined.split(":", 1)[1].strip())

    return {
        "status": "ok",
        "kind": "queued_text",
        "output": {
            "note": "no structured verb matched; archived + dropped to inbox for interactive Grok Build",
            "body": joined,
            "verbs": ["state", "head", "echo:…", "shell:…", "bakasha:…", "run:…", "!…"],
        },
    }


def _run_shell(cmd: str) -> dict:
    # hard refuse obvious danger
    banned = ["rm -rf /", "mkfs", ":(){", "diskutil erase", "launchctl bootout system"]
    low = cmd.lower()
    for b in banned:
        if b in low:
            return {"status": "fail", "kind": "shell", "output": f"banned pattern: {b}"}
    r = run(["bash", "-lc", cmd], timeout=120)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if len(out) > 8000:
        out = out[:8000] + "\n…[truncated]"
    return {
        "status": "ok" if r.returncode == 0 else "fail",
        "kind": "shell",
        "output": {"exit": r.returncode, "text": out, "cmd": cmd},
    }


def _http_get(url: str, timeout: float = 15.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "tzomet-cmd-bridge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except Exception as e:
        return 0, str(e).encode()


def _push_bakasha(repo: Path, bus: Path, text: str) -> dict:
    outbox = bus / "outbox" / "all"
    outbox.mkdir(parents=True, exist_ok=True)
    fname = f"bakasha_{utc_ts()}_bridge.txt"
    path = outbox / fname
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    r = run(
        [sys.executable, str(RELAY_MOD), "--repo", str(repo), "--once"],
        cwd=repo,
        timeout=180,
    )
    return {
        "status": "ok" if r.returncode == 0 else "fail",
        "kind": "bakasha",
        "output": {
            "file": str(path),
            "stdout": (r.stdout or "").strip()[-500:],
            "stderr": (r.stderr or "").strip()[-300:],
            "exit": r.returncode,
        },
    }


def drop_inbox_turn(bus: Path, text: str, cmd_id: str) -> Path:
    inbox = bus / "inbox" / "grok"
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / f"turn_cmd_{cmd_id}_{utc_ts()}.txt"
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return p


def publish_result_to_relay(repo: Path, bus: Path, result: dict) -> dict:
    """Push RESULT text as mekria bakasha so mobile can poll HEAD."""
    text = (
        f"RESULT|id={result.get('id')}|status={result.get('status')}|"
        f"kind={result.get('kind')}|ts={result.get('ts')}\n"
        f"{json.dumps(result.get('output'), ensure_ascii=False)[:1500]}"
    )
    return _push_bakasha(repo, bus, text)


def deposit_text(repo: Path, text: str, source: str = "deposit") -> dict:
    ensure_layout(repo)
    git(repo, "pull", "--ff-only", "origin", BRANCH)
    cmds = parse_cmds(text)
    primary = cmds[0] if cmds else {"id": f"raw-{utc_ts()}"}
    cid = re.sub(r"[^a-zA-Z0-9._-]+", "_", primary["id"])[:80]
    fname = f"{utc_ts()}_{cid}.txt"
    rel = f"{INCOMING}/{fname}"
    path = repo / rel
    header = f"# source={source}\n# deposited={datetime.now(timezone.utc).isoformat()}\n\n"
    path.write_text(header + text.rstrip() + "\n", encoding="utf-8")
    git(repo, "add", rel, "commands/README.md")
    msg = f"cmd deposit: {fname}"
    c = git(repo, "commit", "-m", msg)
    if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
        return {"ok": False, "error": f"commit failed: {c.stderr[-300:]}"}
    p = git(repo, "push", "origin", f"HEAD:{BRANCH}")
    if p.returncode != 0:
        return {"ok": False, "error": f"push failed: {p.stderr[-400:]}", "path": rel}
    return {"ok": True, "path": rel, "id": primary["id"], "cmds": len(cmds)}


def process_incoming(repo: Path, bus: Path, state: dict, push_result: bool = True) -> list[dict]:
    ensure_layout(repo)
    git(repo, "fetch", "origin", BRANCH)
    git(repo, "pull", "--ff-only", "origin", BRANCH)

    results = []
    inc = repo / INCOMING
    if not inc.is_dir():
        return results

    for path in sorted(inc.glob("*.txt")):
        key = str(path.relative_to(repo))
        if key in state.get("processed_files", {}):
            continue
        text = path.read_text(encoding="utf-8")
        cmds = parse_cmds(text)
        for cmd in cmds:
            if cmd.get("to") not in ("zion-grok-build", "zion", "all", "x_grok"):
                # still accept — operator may mistype target
                pass
            drop_inbox_turn(bus, text, cmd["id"])
            exe = execute_do(cmd["body"], bus, repo)
            result = {
                "id": cmd["id"],
                "to": cmd["to"],
                "pri": cmd["pri"],
                "expect": cmd["expect"],
                "status": exe["status"],
                "kind": exe["kind"],
                "output": exe["output"],
                "ts": datetime.now(timezone.utc).isoformat(),
                "source_file": key,
            }
            # write results file
            rname = f"{utc_ts()}_{re.sub(r'[^a-zA-Z0-9._-]+', '_', cmd['id'])[:60]}.json"
            rpath = repo / RESULTS / rname
            rpath.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            # archive cmd
            done_path = repo / DONE / path.name
            if not done_path.exists():
                done_path.write_text(text, encoding="utf-8")
            if push_result:
                relay_out = publish_result_to_relay(repo, bus, result)
                result["relay"] = relay_out
            results.append(result)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "event": "processed",
                        "id": result["id"],
                        "status": result["status"],
                        "kind": result["kind"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        # remove from incoming after process
        try:
            path.unlink()
        except OSError:
            pass
        state.setdefault("processed_files", {})[key] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "results": [r["id"] for r in results if r.get("source_file") == key],
        }

    # commit results/done/incoming deletions
    git(repo, "add", "-A", "commands")
    c = git(repo, "commit", "-m", f"cmd bridge: process {len(results)} result(s)")
    if c.returncode == 0:
        git(repo, "push", "origin", f"HEAD:{BRANCH}")
    save_state(bus, state)
    return results


def process_issues(repo: Path, bus: Path, state: dict, push_result: bool = True) -> list[dict]:
    """Pull open issues labeled zion-cmd, treat body as CMD deposit."""
    r = run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            f"{OWNER}/{REPO_NAME}",
            "--label",
            ISSUE_LABEL,
            "--state",
            "open",
            "--json",
            "number,title,body,createdAt",
        ],
        timeout=60,
    )
    if r.returncode != 0:
        print(f"CHECK|issues|SKIP|{r.stderr.strip()[:200]}", flush=True)
        return []
    try:
        issues = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []

    out = []
    for iss in issues:
        num = str(iss["number"])
        if num in state.get("processed_issues", {}):
            continue
        body = iss.get("body") or ""
        title = iss.get("title") or ""
        text = body.strip() if body.strip() else title
        # deposit locally then process as file
        dep = deposit_text(repo, text, source=f"issue#{num}")
        if not dep.get("ok"):
            print(f"CHECK|issues|FAIL|#{num}|{dep}", flush=True)
            continue
        # process will pick up the new file
        state.setdefault("processed_issues", {})[num] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": dep.get("path"),
        }
        # close issue
        run(
            [
                "gh",
                "issue",
                "close",
                num,
                "-R",
                f"{OWNER}/{REPO_NAME}",
                "--comment",
                f"Accepted by tzomet_cmd_bridge → {dep.get('path')}",
            ],
            timeout=60,
        )
        out.append(dep)
    save_state(bus, state)
    if out:
        # process newly deposited
        out.extend(process_incoming(repo, bus, state, push_result=push_result))
    return out


def cmd_status(repo: Path, bus: Path) -> int:
    state = load_state(bus)
    git(repo, "fetch", "origin", BRANCH)
    inc = list((repo / INCOMING).glob("*.txt")) if (repo / INCOMING).is_dir() else []
    print(
        json.dumps(
            {
                "repo": str(repo),
                "incoming_local": [p.name for p in inc],
                "processed_files": len(state.get("processed_files", {})),
                "processed_issues": len(state.get("processed_issues", {})),
                "last_run": state.get("last_run"),
                "twin_state": json.loads((bus / "twin_relay_state.json").read_text())
                if (bus / "twin_relay_state.json").is_file()
                else None,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def wrap_as_cmd(text: str, cmd_id: str | None = None) -> str:
    if "CMD|to=" in text and "DO:" in text:
        return text
    cid = cmd_id or f"m-{utc_ts()}"
    return (
        f"[חי] turn=auto | ledger=0 | drift=none | trunc=no\n"
        f"```cmd\n"
        f"CMD|to=zion-grok-build|id={cid}|pri=normal|expect=done\n"
        f"DO:\n{text.strip()}\n"
        f"END\n"
        f"```\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Tzomet no-paste CMD bridge (GitHub bus)")
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--bus", default=str(DEFAULT_BUS))
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("deposit", help="push CMD text to commands/incoming")
    d.add_argument("--text", default="")
    d.add_argument("--stdin", action="store_true")
    d.add_argument("--id", default="")
    d.add_argument("--raw", action="store_true", help="do not wrap as CMD fence")

    sub.add_parser("once", help="process issues + incoming once")
    w = sub.add_parser("watch", help="loop process")
    w.add_argument("--interval", type=int, default=15)

    sub.add_parser("status")

    iss = sub.add_parser("issue-deposit", help="create GitHub issue labeled zion-cmd")
    iss.add_argument("--title", required=True)
    iss.add_argument("--body", default="")
    iss.add_argument("--stdin-body", action="store_true")

    args = ap.parse_args()
    repo = Path(args.repo).expanduser().resolve()
    bus = Path(args.bus).expanduser().resolve()

    if not (repo / ".git").is_dir():
        print(f"CHECK|bridge|DEAD|not_git|{repo}", file=sys.stderr)
        return 2

    if args.cmd == "deposit":
        text = args.text
        if args.stdin:
            text = sys.stdin.read()
        if not text.strip():
            print("empty text", file=sys.stderr)
            return 2
        if not args.raw:
            text = wrap_as_cmd(text, args.id or None)
        res = deposit_text(repo, text, source="cli-deposit")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    if args.cmd == "issue-deposit":
        body = args.body
        if args.stdin_body:
            body = sys.stdin.read()
        if not body.strip():
            body = wrap_as_cmd(args.title)
        elif "CMD|to=" not in body:
            body = wrap_as_cmd(body)
        # ensure label exists
        run(
            [
                "gh",
                "label",
                "create",
                ISSUE_LABEL,
                "-R",
                f"{OWNER}/{REPO_NAME}",
                "--description",
                "Zion command bus",
                "--color",
                "0E8A16",
            ],
            timeout=30,
        )
        r = run(
            [
                "gh",
                "issue",
                "create",
                "-R",
                f"{OWNER}/{REPO_NAME}",
                "--title",
                args.title,
                "--body",
                body,
                "--label",
                ISSUE_LABEL,
            ],
            timeout=60,
        )
        print((r.stdout or r.stderr).strip())
        return r.returncode

    if args.cmd == "status":
        return cmd_status(repo, bus)

    if args.cmd == "once":
        state = load_state(bus)
        process_issues(repo, bus, state, push_result=True)
        # process_issues may have already processed; always sweep incoming
        state = load_state(bus)
        results = process_incoming(repo, bus, state, push_result=True)
        # reverse GET mode: poll zion_ping.json
        get_rc = _zion_get_pull(repo, bus)
        print(
            json.dumps(
                {
                    "ok": True,
                    "processed": len(results),
                    "zion_get_pull_rc": get_rc,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.cmd == "watch":
        print(
            f"CHECK|bridge|UP|repo={repo}|interval={args.interval}|modes=incoming,issues,zion_get",
            flush=True,
        )
        while True:
            try:
                state = load_state(bus)
                process_issues(repo, bus, state, push_result=True)
                state = load_state(bus)
                process_incoming(repo, bus, state, push_result=True)
                _zion_get_pull(repo, bus)
            except Exception as e:
                print(f"CHECK|bridge|ERR|{type(e).__name__}|{e}", flush=True)
            time.sleep(max(5, args.interval))

    return 2


def _zion_get_pull(repo: Path, bus: Path) -> int:
    """Reverse mode: GET commands/zion_ping.json and execute if new seq."""
    try:
        import tzomet_zion_get as zget

        class A:
            pass

        a = A()
        a.repo = str(repo)
        a.bus = str(bus)
        a.no_relay = False
        return int(zget.cmd_pull(a) or 0)
    except Exception as e:
        print(f"CHECK|bridge|zion_get|ERR|{type(e).__name__}|{e}", flush=True)
        return 9


if __name__ == "__main__":
    sys.exit(main())
