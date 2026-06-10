# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# Git & Branching Rules (for Claude)

## Absolutes
- **Never run raw Git operations as the "main" coding agent.** Delegate all Git work to the `git-flow-manager` subagent (`.claude/agents/git-flow-manager.md`) — the main agent must not invoke `git` directly. This prevents agents from stepping on each other's branches and accidentally touching `main`.
- **Use one Git worktree per ticket** so each session is isolated and Git state is deterministic. Claude Code has first-class worktree support — start the ticket session with the worktree flag (`--worktree` / `-w`); do not reuse a worktree across tickets.
  - Example: starting `claude -w feat/TKT-1234-delete-ads` creates a worktree at `.claude/worktrees/feat-TKT-1234-delete-ads/`.
- Never commit or merge directly to `main`.
- All work happens on ticket branches created from `main`.
- Branch naming: `{type}/{ticket}-{slug}`, where type ∈ {feat, fix, chore}.
  - Example: `feat/TKT-1234-delete-ads`.

## Ticket lifecycle (MUST follow in order)
1. When I say: `start ticket TKT-1234 delete ads`, do:
   - Create or use a worktree for this ticket from `main`.
   - Create a branch `feat/TKT-1234-delete-ads` in that worktree.
   - Ensure this session is **locked** to that worktree/branch.

2. While implementing:
   - Make **small, atomic commits** with Conventional Commit style messages.
   - Run tests before opening a PR using the repo's test command.

3. When I say: `open PR for this ticket`:
   - Push the branch.
   - Open a PR targeting `main`.
   - Use a structured PR description:
     - Summary
     - Motivation / context
     - Implementation details
     - Tests
     - Risks / rollout

4. Before you ask me to merge:
   - Perform a **self-review** on the PR.
   - List any concerns or potential regressions.

5. When I say: `finish ticket TKT-1234`:
   - If PR is approved and checks are green:
     - Merge the PR into `main`.
     - Delete the remote branch.
     - Delete the local worktree and branch.
     - Fetch and fast-forward local `main`.
   - If PR is not mergeable, tell me why and do not merge.

## Stacked PRs / Dependencies
- If a ticket depends on another ticket's branch:
  - Explicitly indicate the parent branch in PR description.
  - Do NOT merge parent PRs unless I explicitly say: `finish stack root for TKT-xxxx`.
- When I say "merge" without specifying PR:
  - Ask which PR number and show the base branch to avoid ambiguity.