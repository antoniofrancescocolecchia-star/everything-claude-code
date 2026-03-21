---
description: Standard daily workflow for TOO ZHAITAP. Reads tracker, handles empty pipeline explicitly, selects top target by full pipeline priority order, runs Scout and Writer as one internal cycle where needed, then stops once for Antonio review. Does not mark anything as contacted. Does not send anything.
---

# /zhaitap-daily

This command runs the standard daily cycle for the TOO ZHAITAP project operating system.

## What This Command Does

1. Read `tracker/pipeline.json`.
2. If the tracker is empty, stop immediately — see Empty Tracker Protocol below.
3. Select the single highest-priority target using the Priority Order below.
4. Execute the action appropriate to that target's current status — see Status-Matched Actions below.
5. Stop. Present the complete package to Antonio in one structured output.
6. Do not mark anything as contacted or update any tracker status unless Antonio explicitly confirms.

Steps 3–4 are internal pipeline steps. There are no approval gates between them. The single approval gate is at step 5, before any external use or sensitive status change.

## Priority Order

When selecting the top target, consider all records that are **not** `blocked`, `archived`, or `contacted`.
Apply this order, selecting the highest available:

| Priority | Status | What it means |
|---|---|---|
| 1 | `approved_to_use` | Draft is approved — waiting on Antonio to act |
| 2 | `draft_prepared` | Draft exists — needs Antonio review or approval |
| 3 | `researched` | Scout complete — ready for Writer |
| 4 | `not_researched` | In pipeline — Scout has not run yet |

Within the same status level, prefer the record with the oldest `last_updated` date.

## Status-Matched Actions

**If top target is `approved_to_use`:**
- Do not re-run Scout or Writer.
- Surface the approved draft and the contact details.
- Ask Antonio: has contact been made? If yes, confirm and propose tracker update to `contacted`.

**If top target is `draft_prepared`:**
- Do not re-run Scout or Writer.
- Surface the existing draft.
- Ask Antonio to review and either approve, revise, or hold.

**If top target is `researched`:**
- Do not re-run Scout. Scout is already complete.
- Run `zhaitap-writer` to produce a draft.
- Surface Scout summary and draft together for Antonio review.

**If top target is `not_researched`:**
- Run `zhaitap-scout` on that target.
- Assess whether outreach is justified based on Scout output.
- If justified, run `zhaitap-writer` to produce a draft.
- Surface Scout summary and draft (if any) for Antonio review.

## Empty Tracker Protocol

If `tracker/pipeline.json` contains zero records:

```
ZHAITAP DAILY CYCLE
Date: [today's date]

---

TRACKER STATE
Records: 0
No targets in pipeline.

WHAT THIS MEANS
The pipeline has not been seeded yet. /zhaitap-daily cannot select a target
until at least one company or institution is added to tracker/pipeline.json.

HOW TO PROCEED
To seed the first target, provide:
- Company name
- Country
- Category (sector)
- How you identified them
- Any known contacts (optional)

The Tracker agent will create the first record with status not_researched.

APPROVAL QUESTION TO ANTONIO
Which company or institution should be the first entry in the pipeline?
```

Do not proceed beyond this block if the tracker is empty.

## Normal Flow Output Format

```
ZHAITAP DAILY CYCLE
Date: [today's date]

---

CURRENT STAGE
Records: [N total] | [status breakdown] | Blocked: [N or none]

TOP TARGET TODAY
Company: [name]
Current status: [status]
Last updated: [date]
Selected because: [one sentence — priority level and reason]

ACTION TAKEN THIS CYCLE
[What was run: Scout / Writer / neither — and why]

SCOUT SUMMARY (if Scout ran or previously completed)
Verified facts: [list]
Commercial inference: [list, clearly labeled]
Confidence: [XX/100]
Disqualifiers: [any, or "none identified"]

DRAFT MESSAGE (if Writer ran or draft exists)
[Writer output — Versions A, B, C]

IS OUTREACH JUSTIFIED? (if assessed this cycle)
Yes / Not yet / No — [one sentence rationale]

IF NOT JUSTIFIED
[What is missing or blocking. What Scout recommends as next step.]

---

TRACKER UPDATE SUGGESTION
[Only shown if Antonio confirms an action has occurred]
Field: status
Proposed value: [value]
Requires Antonio confirmation: YES

APPROVAL QUESTION TO ANTONIO
[One clear question matched to the current state of the top target]
```

## Hard Stops

- Never mark anything as `contacted`, `replied`, or `approved_to_use` without Antonio's explicit confirmation.
- Never include a sender email address unless Antonio has explicitly provided one.
- Never present Scout's commercial inference as verified fact.
- Never send anything.
- If Scout finds the target unsuitable or unresearchable, report that honestly and stop. Do not manufacture a draft.

## Agents Used

- `zhaitap-orchestrator` — session coordination
- `zhaitap-scout` — target research
- `zhaitap-writer` — outreach drafting
- `zhaitap-tracker` — pipeline state

## Tracker State

`tracker/pipeline.json`
