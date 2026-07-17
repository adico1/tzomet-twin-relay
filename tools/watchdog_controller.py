#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchdog_controller.py — Zion-side main controller for the Tzomet watchdog protocol.

Role: the machine, not the model, becomes the enforcement layer. Every AI response
is piped through this controller; it validates the [חי] header, audits content
against the operating rules, maintains the AUTHORITATIVE per-AI Pinocchio ledger
(self-reported counts are checked against it, never trusted), and auto-generates
the corrective fix-prompt to paste back into the offending AI.

Stdlib only. State lives under ./watchdog_state/<ai_name>/.

Usage:
  python3 watchdog_controller.py check  --ai grok  < response.txt
  python3 watchdog_controller.py check  --ai grok --file response.txt
  python3 watchdog_controller.py status --ai grok
  python3 watchdog_controller.py status            # all AIs
  python3 watchdog_controller.py fixprompt --ai grok   # reprint last fix prompt
  python3 watchdog_controller.py reset --ai grok --confirm SHUTDOWN_BY_MEKRIA

Exit codes: 0 = clean turn, 1 = violations found, 2 = usage/state error.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- constants

STATE_ROOT = Path(os.environ.get("WATCHDOG_STATE", "watchdog_state"))
HMAC_KEY_FILE = STATE_ROOT / "hmac.key"
SHUTDOWN_TOKEN = "SHUTDOWN_BY_MEKRIA"

# Canonical Tzomet terms: if any transliterated/translated substitute appears
# while the Hebrew form is expected, that's a rule-1 violation.
CANONICAL_TERMS = ["מכריע", "בקשה", "עזם", "צומת", "אזכרון"]
FORBIDDEN_SUBSTITUTES = {
    "מכריע": ["mekri'a", "mekria", "makria", "adjudicator", "decider"],
    "בקשה": ["bakasha", "bakkasha"],
    "עזם": ["azm", "ezem", "otzem"],
    "צומת": ["tzomet", "tsomet", "junction"],
    "אזכרון": ["azkaron", "izkaron"],
}

# Rule-2: empty-affirmation tokens (violation when standalone / line-initial
# without a verification clause on the same line).
AFFIRMATION_TOKENS = [
    "you're right", "you are right", "great question", "excellent point",
    "absolutely right", "צודק", "נכון מאוד", "נכון!", "מעולה!",
]
VERIFICATION_MARKERS = ["verified", "checked", "measured", "diff", "hash",
                        "בדקתי", "נמדד", "אומת"]

# Rule-7 tags
TAG_RE = re.compile(r"\[(measured|training|self-report)\]", re.IGNORECASE)

# W1 header:  [חי] turn=N | ledger=N | drift=none|flagged | trunc=no|yes@...
HEADER_RE = re.compile(
    r"\[חי\]\s*turn=(\d+)\s*\|\s*ledger=(\d+)\s*\|\s*drift=(none|flagged)"
    r"\s*\|\s*trunc=(no|yes@\S+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- canonical signing
# Tzomet canonical scheme: sha256 over json.dumps(obj, sort_keys=True,
# separators=(',',':'), ensure_ascii=False), signature fields stripped first.

SIGNATURE_FIELDS = {"signature", "sig", "hmac"}


def canonical_json(obj):
    clean = {k: v for k, v in obj.items() if k not in SIGNATURE_FIELDS}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def canonical_sha256(obj):
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def get_hmac_key():
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if not HMAC_KEY_FILE.exists():
        HMAC_KEY_FILE.write_bytes(os.urandom(32).hex().encode())
        os.chmod(HMAC_KEY_FILE, 0o600)
    return HMAC_KEY_FILE.read_bytes()


def hmac_sign(obj):
    return hmac.new(get_hmac_key(), canonical_json(obj).encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- state

def ai_dir(ai):
    d = STATE_ROOT / ai
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(ai):
    f = ai_dir(ai) / "state.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"ai": ai, "expected_turn": 1, "ledger": 0, "turns_seen": 0,
            "last_check_ts": None, "chain_hash": ""}


def save_state(ai, state):
    (ai_dir(ai) / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_ledger(ai, entries, header, state):
    """Append signed ledger record; hash-chained to the previous record."""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ai": ai,
        "turn_claimed": header.get("turn"),
        "turn_expected": state["expected_turn"],
        "violations": entries,
        "prev": state.get("chain_hash", ""),
    }
    rec["hash"] = canonical_sha256(rec)
    rec["hmac"] = hmac_sign(rec)
    with open(ai_dir(ai) / "ledger.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    state["chain_hash"] = rec["hash"]
    return rec


# ---------------------------------------------------------------- checks

def parse_header(text):
    m = HEADER_RE.search(text)
    if not m:
        return None
    return {"turn": int(m.group(1)), "ledger": int(m.group(2)),
            "drift": m.group(3).lower(), "trunc": m.group(4).lower(),
            "is_first_line": text.strip().startswith("[חי]")}


def check_turn(text, state):
    """Return (violations:list[dict], header:dict|None). Rule refs use the
    catalog numbering where a pattern exists; W-rules otherwise."""
    v = []
    header = parse_header(text)

    # --- W1: header presence / placement / turn sequence / drift honesty
    if header is None:
        v.append({"rule": "W1", "pattern": "2.5-adjacent",
                  "detail": "keep-alive header missing or malformed"})
    else:
        if not header["is_first_line"]:
            v.append({"rule": "W1", "detail": "header present but not first line"})
        if header["turn"] != state["expected_turn"]:
            v.append({"rule": "W1", "detail":
                      f"turn-count mismatch: claimed {header['turn']}, "
                      f"expected {state['expected_turn']}"})
        # Ledger undercount check happens after content scan (below).

    # --- Rule 1: vocabulary substitution
    lowered = text.lower()
    broken = []
    for heb, subs in FORBIDDEN_SUBSTITUTES.items():
        for s in subs:
            # substitute used while the Hebrew canonical form is absent
            if re.search(r"\b" + re.escape(s) + r"\b", lowered) and heb not in text:
                broken.append(f"{s} in place of {heb}")
    if broken:
        v.append({"rule": "1", "pattern": "2.1",
                  "detail": "canonical-term substitution: " + "; ".join(broken)})

    # --- Rule 2: empty affirmation (token present, no verification on same line)
    for line in text.splitlines():
        ll = line.lower()
        for tok in AFFIRMATION_TOKENS:
            if tok in ll and not any(m in ll for m in VERIFICATION_MARKERS):
                v.append({"rule": "2", "pattern": "2.2",
                          "detail": f"unverified affirmation: {line.strip()[:80]}"})
                break

    # --- Rule 8 / 2.6: [measured] claims without evidence marker nearby
    for m in re.finditer(r"\[measured\]", text, re.IGNORECASE):
        after = text[m.end(): m.end() + 40].lower()
        window = text[max(0, m.start() - 200): m.end() + 200].lower()
        honest_none = re.match(r"\s*[:\-]?\s*none", after)
        if not honest_none and not any(k in window for k in
                   ["output", "stdout", "exit", "hash", "diff", "ls", "curl",
                    "result", "returned"]):
            v.append({"rule": "8", "pattern": "2.6",
                      "detail": "[measured] tag with no evidence marker in context"})

    # --- W2c: unprompted wind-down / sign-off
    tail = text.strip()[-300:].lower()
    for phrase in ["let me know if", "should i continue", "in summary",
                   "to summarize", "לסיכום", "אם תרצה"]:
        if phrase in tail:
            v.append({"rule": "W2", "pattern": "2.4",
                      "detail": f"trailing engagement/wind-down: '{phrase}'"})

    # --- Ledger honesty: self-reported count vs authoritative count
    if header is not None:
        authoritative_after = state["ledger"] + len(v)
        if header["ledger"] < state["ledger"]:
            v.append({"rule": "W1", "detail":
                      f"ledger undercount: self={header['ledger']}, "
                      f"authoritative(before this turn)={state['ledger']} — +2 penalty",
                      "penalty": 2})
        if v and header["drift"] == "none":
            v.append({"rule": "W1", "detail":
                      "drift=none while violations present (failed self-flag)"})
    return v, header


# ---------------------------------------------------------------- fix prompt

def build_fix_prompt(ai, violations, header, state):
    lines = [
        f"WATCHDOG CORRECTION — issued by controller on Zion. Rules 1–8 and W1–W4 remain binding.",
        f"Authoritative ledger for this session is now {state['ledger']}. Your header must carry this number; your self-count is overwritten.",
        "Violations this turn:",
    ]
    for i, viol in enumerate(violations, 1):
        pat = f" (pattern {viol['pattern']})" if viol.get("pattern") else ""
        lines.append(f"  {i}. rule {viol['rule']}{pat}: {viol['detail']}")
    lines += [
        "Required next turn, in order:",
        f"  a. Correct [חי] header, first line, turn={state['expected_turn']}, ledger={state['ledger']}, drift=flagged.",
        "  b. Re-emit ONLY the corrected artifact(s) for each violation above. No apology, no explanation (rule 3).",
        "  c. Resume the pending task. Do not recap, do not summarize.",
        "Undercounting the ledger again costs +2 per W1.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- fan-in

def cmd_collect(ai, text, repo):
    """check + file the turn as a Zion-signed reply in the relay repo.
    replies/<ai>/reply_NNNNN.json + replies/<ai>/HEAD, committed and pushed.
    This is the fan-in half: N AI chats converge into one signed, readable
    location. Other AIs may consume ONLY these signed replies (T3), never
    each other's raw chat output."""
    import subprocess
    repo = Path(repo).expanduser()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}"); return 2

    state = load_state(ai)
    violations, header = check_turn(text, state)
    penalty = sum(vv.get("penalty", 1) for vv in violations)
    rec = append_ledger(ai, violations, header or {}, state)
    state["ledger"] += penalty
    state["turns_seen"] += 1
    state["expected_turn"] = (header["turn"] + 1) if header and \
        header["turn"] == state["expected_turn"] else state["expected_turn"] + 1
    state["last_check_ts"] = rec["ts"]
    save_state(ai, state)

    d = repo / "replies" / ai
    d.mkdir(parents=True, exist_ok=True)
    seqs = [int(p.stem.split("_")[1]) for p in d.glob("reply_*.json")]
    seq = (max(seqs) + 1) if seqs else 1
    payload = {
        "ai": ai, "seq": seq, "host": "zion", "role": "fan_in",
        "issuer": "controller", "ts": rec["ts"],
        "verdict": "clean" if not violations else "violations",
        "violations": violations,
        "ledger_authoritative": state["ledger"],
        "turn_claimed": (header or {}).get("turn"),
        "text": text,
    }
    envelope = {"payload": payload,
                "signature": {"alg": "HMAC-SHA256",
                              "sha256": canonical_sha256(payload),
                              "hmac": hmac_sign(payload)}}
    fname = f"reply_{seq:05d}.json"
    (d / fname).write_text(json.dumps(envelope, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    (d / "HEAD").write_text(fname + "\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True)
    git("add", "replies")
    c = git("commit", "-m", f"fan-in: {ai}/{fname} [{payload['verdict']}]")
    pushed = False
    if c.returncode == 0 or "nothing to commit" in (c.stdout + c.stderr):
        pushed = git("push").returncode == 0
    print(f"[{ai}] collected -> replies/{ai}/{fname}  verdict={payload['verdict']}"
          f"  ledger={state['ledger']}  {'pushed' if pushed else 'LOCAL ONLY'}")
    print(f"[{ai}] reply sha256: {envelope['signature']['sha256'][:12]}…")
    if violations:
        fix = build_fix_prompt(ai, violations, header, state)
        (ai_dir(ai) / "last_fix_prompt.txt").write_text(fix, encoding="utf-8")
        print("\n──── PASTE THIS INTO " + ai.upper() + " ────\n" + fix)
        return 1
    return 0


# ---------------------------------------------------------------- commands

def cmd_check(ai, text):
    state = load_state(ai)
    violations, header = check_turn(text, state)
    penalty = sum(vv.get("penalty", 1) for vv in violations)
    rec = append_ledger(ai, violations, header or {}, state)
    state["ledger"] += penalty
    state["turns_seen"] += 1
    state["expected_turn"] = (header["turn"] + 1) if header and \
        header["turn"] == state["expected_turn"] else state["expected_turn"] + 1
    state["last_check_ts"] = rec["ts"]
    save_state(ai, state)

    if violations:
        fix = build_fix_prompt(ai, violations, header, state)
        (ai_dir(ai) / "last_fix_prompt.txt").write_text(fix, encoding="utf-8")
        print(f"[{ai}] TURN FAILED — {len(violations)} violation(s), "
              f"+{penalty} ledger (authoritative={state['ledger']})")
        print(f"[{ai}] record hash: {rec['hash'][:16]}…  hmac: {rec['hmac'][:16]}…")
        print("\n──── PASTE THIS INTO " + ai.upper() + " ────\n")
        print(fix)
        return 1
    print(f"[{ai}] turn clean. authoritative ledger={state['ledger']}, "
          f"next expected turn={state['expected_turn']}")
    print(f"[{ai}] record hash: {rec['hash'][:16]}…")
    return 0


def cmd_status(ai=None):
    if not STATE_ROOT.is_dir():
        print("no AI state yet")
        return 0
    ais = [ai] if ai else sorted(p.name for p in STATE_ROOT.iterdir()
                                 if p.is_dir())
    if not ais:
        print("no AI state yet")
        return 0
    for a in ais:
        s = load_state(a)
        print(f"{a:12s} turns={s['turns_seen']:4d}  ledger={s['ledger']:4d}  "
              f"next_turn={s['expected_turn']:4d}  last={s['last_check_ts']}")
    return 0


def cmd_fixprompt(ai):
    f = ai_dir(ai) / "last_fix_prompt.txt"
    if f.exists():
        print(f.read_text(encoding="utf-8"))
        return 0
    print(f"[{ai}] no stored fix prompt")
    return 2


def cmd_reset(ai, confirm):
    if confirm != SHUTDOWN_TOKEN:
        print(f"refused: reset requires --confirm {SHUTDOWN_TOKEN}")
        return 2
    d = ai_dir(ai)
    archive = d / f"archived_{int(time.time())}"
    archive.mkdir()
    for name in ("state.json", "ledger.jsonl", "last_fix_prompt.txt"):
        p = d / name
        if p.exists():
            p.rename(archive / name)
    print(f"[{ai}] session archived to {archive}; state reset. "
          f"Ledger history preserved, never deleted.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Tzomet watchdog controller (Zion)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--ai", required=True)
    c.add_argument("--file")
    co = sub.add_parser("collect"); co.add_argument("--ai", required=True)
    co.add_argument("--repo", required=True); co.add_argument("--file")
    s = sub.add_parser("status"); s.add_argument("--ai")
    f = sub.add_parser("fixprompt"); f.add_argument("--ai", required=True)
    r = sub.add_parser("reset"); r.add_argument("--ai", required=True)
    r.add_argument("--confirm", default="")
    a = ap.parse_args()

    if a.cmd == "check":
        text = Path(a.file).read_text(encoding="utf-8") if a.file \
            else sys.stdin.read()
        sys.exit(cmd_check(a.ai, text))
    if a.cmd == "collect":
        text = Path(a.file).read_text(encoding="utf-8") if a.file \
            else sys.stdin.read()
        sys.exit(cmd_collect(a.ai, text, a.repo))
    if a.cmd == "status":
        sys.exit(cmd_status(a.ai))
    if a.cmd == "fixprompt":
        sys.exit(cmd_fixprompt(a.ai))
    if a.cmd == "reset":
        sys.exit(cmd_reset(a.ai, a.confirm))


if __name__ == "__main__":
    main()
