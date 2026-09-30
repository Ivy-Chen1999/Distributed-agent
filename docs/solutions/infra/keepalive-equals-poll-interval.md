---
title: ECONNRESET when the client polls at the server's keep-alive timeout
category: infra
tags: [uvicorn, keep-alive, polling, playwright]
signal_ref: e9127fe; Dockerfile CMD --timeout-keep-alive 30; web/e2e/live.spec.ts poll
date: 2026-09-30
---

## Problem
The live e2e spec polled `GET /runs/{id}` every 5 s and failed with `read ECONNRESET` while the
server and the run were healthy.

## Cause
uvicorn closes idle keep-alive connections after 5 s by default. A client reusing the pooled
connection exactly at that moment races the server's close.

## Fix
- Server: `--timeout-keep-alive 30` (well above any client poll interval).
- Pollers: treat a transport error as "not finished yet" and keep polling; only the final status
  decides pass/fail.

## Applies when
Any HTTP poller whose interval is close to the server's idle timeout (uvicorn default 5 s).
