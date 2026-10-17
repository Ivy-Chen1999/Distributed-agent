# Contributing expert assessments

The answer keys come from the Commission's own impact assessments, and the Regulatory Scrutiny
Board's opinions are now fetched automatically (see [README.md](README.md)). Other independent
experts have assessed many of the same proposals; their assessments can become further
must-mention points. Gathering them needs no code.

## What counts

- **Official and independent** of the Commission's own report: for example the European
  Parliament Research Service's *Initial Appraisal of a European Commission Impact Assessment*
  (most proposals have one), assessments by Parliament committees, the European Economic and
  Social Committee or the Committee of the Regions, or Council analyses.
- **About the proposal or its impact assessment**, not about the final law.
- **Public**, with a stable link (a PDF or an official page).

Law-firm notes, blog posts and news articles do not count: they vary in scope, are often paid or
copyrighted, and the AI may already have seen them.

## What to record

One row per source, in a shared sheet you send to the owner privately (not in this repository):

| Column | Example |
|---|---|
| Law | Data Act |
| Type | EPRS initial appraisal |
| Link | the URL of the document |
| Date | 2022-05-30 |
| Language | EN |
| Note | optional: which points look most relevant |

Use the practice laws listed in [README.md](README.md). If you find something for a law not in
that list, send it too; the owner sorts it.

## Why a private sheet

The repository is public. A list of sources per law, kept next to the cases, would show which
documents back the sealed cases. The owner adds the useful sources to the pipeline after
checking them.

## What happens next

Each source is fetched, and the points it raises are drafted into the cases the same way as the
RSB points: the AI drafts, three AI judges check, an AI tie-break settles disagreements, and
people check the new points before they count.
