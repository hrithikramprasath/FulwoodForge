#!/usr/bin/env bash
# Safe to invoke from an interactive shell: parse state, never source it.
STATE="${FULWOOD_STATE_DIR:-${HOME}/.local/state/fulwood-forge}"
get_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | head -1; }
applied="$(get_value "$STATE/applied" epoch)"
outcome="$(get_value "$STATE/attempt" outcome)"
if [[ ! "$applied" =~ ^[0-9]+$ ]]; then
  echo 'Fulwood Forge: no verified update has been recorded.'
elif (( $(date +%s) - applied > 3 * 86400 )); then
  echo 'Fulwood Forge: no update applied for over 3 days; inspect the daily rebuild.'
fi
if [[ "$outcome" == failed ]]; then
  echo "Fulwood Forge update failed: $(get_value "$STATE/attempt" detail)"
fi
if [[ -f "$STATE/security-status" ]] && [[ "$(get_value "$STATE/security-status" outcome)" != ok ]]; then
  echo 'Fulwood Forge: security advisory check needs attention; inspect security.log.'
fi
exit 0
