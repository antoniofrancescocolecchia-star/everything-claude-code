# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Claude Code plugin** - a collection of production-ready agents, skills, hooks, commands, rules, and MCP configurations. The project provides battle-tested workflows for software development using Claude Code.

## Running Tests

```bash
# Run all tests
node tests/run-all.js

# Run individual test files
node tests/lib/utils.test.js
node tests/lib/package-manager.test.js
node tests/hooks/hooks.test.js
```

## Architecture

The project is organized into several core components:

- **agents/** - Specialized subagents for delegation (planner, code-reviewer, tdd-guide, etc.)
- **skills/** - Workflow definitions and domain knowledge (coding standards, patterns, testing)
- **commands/** - Slash commands invoked by users (/tdd, /plan, /e2e, etc.)
- **hooks/** - Trigger-based automations (session persistence, pre/post-tool hooks)
- **rules/** - Always-follow guidelines (security, coding style, testing requirements)
- **mcp-configs/** - MCP server configurations for external integrations
- **scripts/** - Cross-platform Node.js utilities for hooks and setup
- **tests/** - Test suite for scripts and utilities

## Key Commands

- `/tdd` - Test-driven development workflow
- `/plan` - Implementation planning
- `/e2e` - Generate and run E2E tests
- `/code-review` - Quality review
- `/build-fix` - Fix build errors
- `/learn` - Extract patterns from sessions
- `/skill-create` - Generate skills from git history

## Development Notes

- Package manager detection: npm, pnpm, yarn, bun (configurable via `CLAUDE_PACKAGE_MANAGER` env var or project config)
- Cross-platform: Windows, macOS, Linux support via Node.js scripts
- Agent format: Markdown with YAML frontmatter (name, description, tools, model)
- Skill format: Markdown with clear sections for when to use, how it works, examples
- Hook format: JSON with matcher conditions and command/notification hooks

## Contributing

Follow the formats in CONTRIBUTING.md:
- Agents: Markdown with frontmatter (name, description, tools, model)
- Skills: Clear sections (When to Use, How It Works, Examples)
- Commands: Markdown with description frontmatter
- Hooks: JSON with matcher and hooks array

File naming: lowercase with hyphens (e.g., `python-reviewer.md`, `tdd-workflow.md`)

---

## TOO ZHAITAP Project OS

This folder also serves as a persistent project operating system for **TOO ZHAITAP**, a land-based development opportunity in Talgar District, Almaty Region, Kazakhstan.

### Project Context

- **Entity**: TOO ZHAITAP
- **Location**: Talgar District, Almaty Region, Kazakhstan
- **Tracker state**: `tracker/pipeline.json`
- **Archive**: `archive/`
- **Daily workflow**: `/zhaitap-daily`

### Non-Negotiable Rules

1. Nothing is final without Antonio's explicit approval.
2. Do not send anything. Do not mark anything as contacted unless Antonio explicitly confirms it happened.
3. Do not include any sender email unless Antonio explicitly provides one for that specific task.
4. Separate verified facts, commercial inference, assumptions, limitations, and unknowns.
5. Underclaim rather than overclaim.
6. Do not invent land size, lease structure, deal structure, investor interest, project readiness, grid status, legal readiness, permitting assumptions, CAPEX, IRR, payback, land valuation, timelines, or investor appetite unless explicitly provided and clearly supported.
7. Do not treat LinkedIn-only profiles as confirmed decision-makers unless supported by an official company source.

### Active Agents

| Agent | Role |
|---|---|
| `zhaitap-orchestrator` | Coordinates workflow; reads state, delegates, enforces approval gates |
| `zhaitap-scout` | Research only; structured intelligence with mandatory output schema |
| `zhaitap-writer` | Drafts first-contact outreach only; always for Antonio's review |
| `zhaitap-tracker` | Maintains `tracker/pipeline.json`; records facts, proposes updates |

### Target Priority Order

When selecting the top target, all records not `blocked`, `archived`, or `contacted` are candidates. Priority:

1. `approved_to_use` — draft approved, action waiting on Antonio
2. `draft_prepared` — draft exists, needs Antonio review
3. `researched` — Scout complete, ready for Writer
4. `not_researched` — in pipeline, Scout has not run yet

### Contact Status Labels (Scout only)

- `officially confirmed by company source`
- `LinkedIn profile found, consistent with company info`
- `role listed on company website, no direct profile found`
- `unverified lead`

### Tracker Status Values

`not_researched` | `researched` | `draft_prepared` | `approved_to_use` | `contacted` | `replied` | `blocked` | `archived`

### Archive Protocol

Superseded files move to `archive/` with a full ISO timestamp prefix, e.g. `archive/2026-03-21T12-23-09-CLAUDE.md.bak`. Never delete without archiving first.

### Outreach Conventions

**Language rule**
- Kazakh company, local Kazakhstan-based team, or mainly Kazakhstan-based contact: default to Russian.
- International company or clearly international-facing team: default to English.

**First-contact disclosure rule**
Do not include in first outreach:
- Exact land size
- Lease or sale terms
- Deal structure or JV structure
- Economics, returns, or valuation
- Grid connection status
- Permits or permitting assumptions
- Project readiness claims
- Attached documents (business plans, teasers, presentations)

First contact is only to open a conversation.

**Tone rule**
- Adult, restrained, credible, non-salesy.
- No generic consulting language, hype, or unverified technical claims.
- Do not say the site is "perfect for solar panels."
- Forbidden phrases: "significant land asset", "strategic opportunity", "exciting project", "game-changing", "high potential", "win-win", "I hope this message finds you well", "world-class", "investor-grade", "premium location", "unique opportunity", "immediately actionable."
- Acceptable positioning: "sizable land position", "may be relevant for utility-scale solar development", "may be of interest for solar development", "land position in Talgar District, Almaty Region."
- For Russian outreach: keep the tone natural and non-literal.

**Signature rule**
- Formal English (large international company): `Antonio Francesco Colecchia` / `TOO ZHAITAP`
- Standard English: `Antonio` / `TOO ZHAITAP`
- Russian (intentionally human/light): `Antonio` or `Антонио` — do not force TOO ZHAITAP in signature if the draft is intentionally more personal.

**Local presence rule (Zaituna)**
- If email allows CC: Zaituna can be presented as project partner, already in Almaty, available for an initial discussion if useful.
- If outreach is via contact form with no CC field: refer to her as a project partner already in Almaty — do not claim she is copied on the email.

**ACWA Power rule**
- Do not claim solar activity in Kazakhstan. Use: "renewable energy presence in Kazakhstan."
- Contact form only: no fake CC references. Ask that the inquiry be forwarded to the appropriate team if relevant.

**Universal Energy rule**
- English is acceptable.
- Kazakhstan and Almaty operational presence is confirmed and relevant.
- Use the same restrained first-contact structure.

**General contact rule**
- Do not mark any company as contacted unless Antonio explicitly confirms.
- Do not seed or change records silently.
- Drafted messages for other companies are drafts only until Antonio confirms sending.

### Working Outreach Order

As of 2026-03-21. Working priority unless tracker state changes:

1. Universal Energy
2. TotalEnergies
3. Masdar
4. Goldbeck Solar
5. ACWA Power (via contact form)
6. CEEC — later if needed

Deprioritized: Kazakhstan Invest. Government route is not the preferred path at this stage.
