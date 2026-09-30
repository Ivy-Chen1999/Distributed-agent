---
title: Isolating `claude -p` subprocesses without losing subscription auth
category: llm-backends
tags: [claude-code, subprocess, isolation, oauth]
signal_ref: 9a87d45 (merge U5); src/womm/llm/claude_code.py ISOLATION_FLAGS / FORBIDDEN_FLAGS
date: 2026-09-29
---

## Problem
Pipeline roles run through the local Claude Code subscription (`claude -p`). A plain call inherits
the user's hooks, plugins, skills, `~/.claude/rules` and CLAUDE.md (observed: 15 hook events,
32 plugins, 376 skills, a canary CLAUDE.md leaked into answers, ~35-52 s per call).

## What worked
- `--tools "" --strict-mcp-config --no-session-persistence --restricted --setting-sources ""
  --disable-slash-commands --system-prompt <p>`, user content on stdin, a fresh temp cwd per
  call, and an env allowlist (drop `ANTHROPIC_*`, `LANGSMITH_*`, `CC_LANGSMITH_*`,
  `TRACE_TO_LANGSMITH`, `OPENAI_*`). Calls dropped to ~3 s.
- A fail-closed self-check: run once with `--output-format stream-json --verbose
  --include-hook-events`, assert the init event lists no tools / MCP / memory / user plugins /
  skills and no hook events, plus a canary CLAUDE.md in the parent dir that must not appear.

## Gotchas
- **Never `--bare`**: it disables OAuth/keychain reads, so the subscription can no longer be used.
- `--setting-sources ""` alone still left `memory_paths.auto` set; `--restricted` cleared it.
- A leftover `ANTHROPIC_API_KEY` in the parent env silently switches billing to the API key.
- Built-in plugins (`agents-md@builtin`, `telemetry@builtin`) still load; the empty temp cwd
  gives `agents-md` nothing to read.

## Applies when
Any tool shells out to `claude -p` for structured output on a user's machine.
