---
title: Playwright `page.clock.install()` does not freeze time
category: testing
tags: [playwright, fake-timers, flaky-test, ci]
signal_ref: 0a87e62; web/e2e/replay.spec.ts "stop replay ..." test
date: 2026-09-30
---

## Problem
A replay test passed locally and failed on GitHub Actions even after a retry: the pipeline
replay had already finished before the assertion that a node was still "Queued".

## Cause
`page.clock.install()` installs fake timers but time keeps flowing. The test assumed a frozen
clock; on a slower runner the replay (default 3×) advanced during the assertions.

## Fix
Call `page.clock.pauseAt(await page.evaluate(() => Date.now() + 50))` before starting the
behaviour under test, then advance only with `page.clock.runFor(ms)`. Verified by adding a real
4 s wait after the click: the test still passes.

## Applies when
Any Playwright test that relies on fake timers to hold UI state still.
