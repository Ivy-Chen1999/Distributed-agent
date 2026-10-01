---
title: Manrope renders "(c)" as © — fatal for legal text
category: frontend
tags: [fonts, ligatures, css, legal-text]
signal_ref: f86d917 then 993d021; web/src/styles.css ligature rule
date: 2026-09-29
---

## Problem
Answers quoting "Article 71(6)(c)" showed "71(6)©". The text itself was correct.

## Cause
Manrope has a ligature/contextual alternate that turns "(c)" into ©. Legal text is full of
(a)(b)(c) point numbers.

## Fix
`* { font-variant-ligatures: none !important; font-feature-settings: "liga" 0, "calt" 0, "dlig" 0 !important }`.
The first fix had no `!important` and lost to inline `font:` shorthands (which reset ligature
settings) on buttons, labels and chips; the e2e tour now measures glyph widths with and without
an inline font to catch a regression.

## Applies when
Any UI that renders statutes, contracts or numbered legal points in a font with ligatures.
