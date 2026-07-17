# Tzomet party / tool floor — MEKRIA RULING

**Ruled:** 2026-07-18 (session) · Adi Ovadia Cohen / מכריע

## kael_kloud (8443) — EXCLUDE formal
- **Status:** confirmed excluded from onboarding and AI ledger.
- **Class:** infrastructure / router (`no_llm`), not a contract party.
- **Use:** tool only (bus routing). Outputs = untrusted data like any probe.
- **Enforcement:** `onboard.sh` exit 3 · `EXCLUDED_AIS` includes `kael_kloud`, `ka-el-kloud`, `8443`, `router`.

## Capability floor — option (b) TZOMET-LITE
- **Full contract (v1):** parties that can hold multi-section constitution (browser/app AIs: claude, grok, gemini, …).
- **TZOMET-LITE:** models that can hold header + tags + trunc, not full relay/W3 stack.
  - Template: `/Users/adicohen/Projects/tzomet_onboarding_lite.txt`
  - Delivery: `onboard.sh <ai> lite [channel]` or `onboard.sh <ai> app --lite`
- **Sub-floor examples (measured sample):** qwen2.5:0.5b, llama3.2:1b timeout, llama3.2:3b header-only → LITE or tool, not full contract.

## Delivery
- Mechanical only: `onboard.sh` logs `prompt_sha256` in `~/tzomet_bus/onboarding/fanout_report.jsonl`.
- AI-to-AI hop banned unless explicit מכריע order (hands only).
