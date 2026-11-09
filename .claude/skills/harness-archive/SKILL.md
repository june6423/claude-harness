---
name: harness-archive
description: How the research record is layered (PROJECT_STATE, handover summary, worklog, archive) and how to find and move material between the layers. Load it before you write PROJECT_STATE.md, a worklog summary, or an archive file, and before you look for an old decision, result, or number.
---

# Harness archive

## The four layers

| File | Question it answers | Limit | Who writes it |
|---|---|---|---|
| `state/PROJECT_STATE.md` | What do we believe now, and what must the researcher decide? | `limits.project_state_chars` (default 9000); five fixed sections | DT |
| `worklog/<tower>-summary.md` | What must the next session of this tower know to continue? | `limits.handover_summary_chars` (default 8000) | each tower, rewritten at each rotation |
| `worklog/<tower>.md` | What did this session do, and why? | one session; `harness handover` moves the entries to `archive/<tower>-log-<YYYY-MM>.md` | each tower |
| `archive/<EXPERIMENT-ID>.md`, `archive/<tower>-log-<YYYY-MM>.md` | What happened in a closed experiment, in full? What did each session do? | none | DT (experiment files), `harness handover` (log files) |
| `archive/reports/<YYYY-MM-DD>_<experiment or topic>_<kind>.md` | What did DT tell the researcher (plans, result reports, analyses, decision requests)? | none; Korean | DT (DT rule 15a) |
| `archive/unpublished/<tower>/` | Which record drafts were written but never published? (not evidence) | none | `harness handover` (draft cleanup, DESIGN §29) |

`archive/INDEX.md` has one line for each archive file: `file | experiment or period | dates | result in one sentence | search words`. Records in `records/` stay the evidence. The archive is notes about them.

## PROJECT_STATE.md

Five sections, in this order, each present once. dt_guard refuses another layout.

1. `## Research question` — the question in three sentences or fewer.
2. `## Where we stand` — one paragraph for each line of work, five or fewer: "To learn A, we did B. The result was C. So D." Each paragraph names the verdict that supports it.
3. `## Live hypotheses` — for each: the claim, the evidence in one line with its verdict reference, and the status (supported, not rejected, rejected, untested).
4. `## Decisions waiting for the researcher` — only open decisions, each with the options and the DT recommendation.
5. `## Closed experiments` — one line for each: ID, dates, the result in one sentence, and the archive file.

Numbers, tables, task IDs, and settings go to the archive file, not here.

## Archive file of an experiment

```
# <EXPERIMENT-ID>: <title>

## Question
## Design
(arms, data, metrics, decision rules, budget; researcher decisions verbatim)
## Result
(numbers with their verdict references)
## Interpretation and limits
## Chronology
(the condensed log entries, in date order, moved from the summary)
## Records
(handoff, report, verdict, directive references)
```

Write sections 1 to 4 when the experiment closes. Move the chronology from the summary at the next rotation. Add the INDEX line.

## Handover summary

Rewrite `worklog/<tower>-summary.md` at each rotation. Keep: the current experiment and its state, each running task, each message that waits for an answer, each open item, the next step, and the archive file names for the details. Remove: entries about closed experiments (move them to the archive), record hashes that the archive already holds, and numbers that a record holds. `bin/harness handover` warns when the summary is over the limit; the next session moves the entries first.

## How to find something

1. Read `archive/INDEX.md` for the experiment or period.
2. Run `bin/harness archive search <words>`. It ranks the sections of `archive/**/*.md` (with `archive/reports/`) and the bodies of handoffs, reports, verdicts, and directives (BM25 keyword search, no external tool). Use the words of the records: experiment IDs, gate IDs, metric names, arm names, dates.
3. Read the section the result names. For a number, open the record itself with `bin/harness show <ID>`.

The documents are STE English with a controlled vocabulary, so a keyword search finds what a semantic search would. When a search returns nothing, try the synonyms that the records use, or read the chronology of the experiment file.
