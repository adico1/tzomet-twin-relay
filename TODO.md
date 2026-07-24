# Tracked TODOs

## Post-route-restore: watchdog HMAC Keychain custody

- Keep `watchdog_state/hmac.key` frozen and excluded from Git and every
  `ai_logs_backup` sweep during route restoration.
- After the route is green, migrate the existing value into the adapter
  Keychain layer without rotating it.
- Verify the Keychain read-back matches the frozen file without printing
  either value.
- Preserve historical HMAC verification.
- Delete the file only after explicit deletion approval and a passing
  Keychain-equivalence check.

`watchdog_state/*/state.json` and `watchdog_state/*/ledger.jsonl` are
continuity/log-class data and belong in the `ai_logs_backup` sweep.
