#!/usr/bin/env bash
# PreToolUse/Bash guard — keeps raw git/gh out of the main coding agent.
#
# Policy (see CLAUDE.md "Git & Branching Rules"):
#   - The main agent must NOT run git/gh directly. It delegates to the
#     git-flow-manager subagent, which opts in with the CLAUDE_GIT_OK=1 marker.
#   - Pushing to `main` is never allowed, even with the marker — merges go
#     through `gh pr merge` after review.
#
# Reads the PreToolUse hook JSON on stdin; emits a permissionDecision.
set -euo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // ""')"

deny() {
  # $1 = reason shown to the model
  jq -cn --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Only police commands that actually invoke git or gh.
if ! printf '%s' "$cmd" | grep -Eq '(^|[;&|(]|[[:space:]])(git|gh)([[:space:]]|$)'; then
  exit 0
fi

# Hard guard: never push to main, regardless of who is running.
if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push' \
   && printf '%s' "$cmd" | grep -Eq '(^|[[:space:]:])main([[:space:]]|:|$)'; then
  deny "Pushing to main is never allowed. Land changes via 'gh pr merge' after review (see CLAUDE.md Git & Branching Rules)."
fi

# Allowance: the git-flow-manager subagent opts in with this marker.
if printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])CLAUDE_GIT_OK=1([[:space:]])'; then
  exit 0
fi

# Default: block raw git/gh for the main agent.
deny "Raw git/gh is disabled in this session. Delegate all Git work to the git-flow-manager subagent (.claude/agents/git-flow-manager.md) — it is the only sanctioned Git operator. Do not run git/gh yourself."
