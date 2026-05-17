# Integration Guide: AGENTS.md + Jules System Prompt

## Overview

Two complementary documents have been created to ensure AI-assisted development on your eToro Nautilus platform maintains quality, safety, and consistency:

1. **AGENTS.md** (7,200+ lines) — The authoritative codebase reference
2. **JULES_SYSTEM_PROMPT.md** (900+ lines) — Instructions for Jules on how to use and maintain AGENTS.md

---

## File Purposes

### AGENTS.md — The Single Source of Truth

**What it contains:**
- Complete system architecture (18 sections)
- Full adapter and strategy documentation
- eToro REST + WebSocket API protocol reference
- Order lifecycle and reconciliation flow
- Step-by-step guides for adding new instruments/strategies
- Safety constraints and risk controls
- 10 documented pitfalls with root causes
- Code conventions and style guidelines
- Agent-maintained changelog

**Who uses it:**
- Jules (AI coding agent) — reads relevant sections before every task
- You (as developer) — reference guide for understanding the system
- Anyone reviewing code — knows exactly where to find the rules

**Accuracy level:**
- Verified against actual code files
- Contains real endpoint URLs, exact parameter names, and actual timing values
- Intentionally includes file paths and code snippets that must match the repo

### JULES_SYSTEM_PROMPT.md — The Operational Protocol

**What it contains:**
- Part 1: How to use AGENTS.md in your work (3 steps: identify → re-read → verify)
- Part 2: Verification checklist (30+ items Jules should validate)
- Part 3: How to improve AGENTS.md (4 types: accuracy, clarity, coverage, pitfalls)
- Part 4: Changelog entry format and rules
- Part 5: Integration scenarios (adding strategies, debugging, etc.)
- Part 6: Quality standards for AGENTS.md documentation
- Part 7: Red flags that require immediate updates
- Part 8: Pre-submission checklist for code changes
- Part 9: Monthly maintenance audit procedure

**Who uses it:**
- Jules (AI coding agent) — operational instructions for every task
- You (as developer) — understand how Jules will approach your codebase

**Enforcement level:**
- Non-negotiable constraints (e.g., "Never use `sys.exit()` where `os._exit(1)` is documented")
- Mandatory verification steps (e.g., "Run verification checklist for affected sections")
- Required changelog discipline (every change to AGENTS.md must have an entry)

---

## Workflow: How Jules Will Work With These Files

### Before Starting Any Task

```
1. Jules reads the System Prompt (Part 1)
2. Jules identifies relevant AGENTS.md sections
3. Jules re-reads those sections in AGENTS.md
4. Jules checks current code against documented behavior
5. If discrepancy found → Jules updates AGENTS.md FIRST
6. Jules proceeds with actual task using patterns from AGENTS.md
```

### During Task Execution

```
- Jules follows exact patterns (boilerplate, naming, logging)
- Jules avoids anything not documented in AGENTS.md
- Jules validates constraints (e.g., size_precision=0 is immutable)
- Jules tests against documented edge cases (Section 16 pitfalls)
```

### After Task Completion

```
1. Jules checks the pre-submission checklist (Part 8)
2. Jules adds changelog entries to AGENTS.md Section 18
3. Jules verifies cross-references (if Section 5.2 changed, check Section 9)
4. Jules ensures every AGENTS.md change has a changelog entry
```

---

## Key Integration Points

### 1. Safety Constraints (Non-Negotiable)

When AGENTS.md says **"do NOT do X"**, this is binding on Jules:

| Constraint | Doc Location | Why Critical |
|-----------|--------------|--------------|
| Never use `sys.exit()` where `os._exit(1)` is specified | Section 12 + 5.1 + 5.2 | Conflicts with systemd restart logic |
| Never reduce `_poll_for_fill` from 20×5s to less | Section 5.2 + 9 | eToro takes up to 90s to settle |
| Never change `size_precision` from 0 to non-zero | Section 5.1 + 6 | Breaks eToro USD-amount calculation |
| Never weaken `_check_live_safety_interlock()` | Section 12 | Prevents accidental real trading |
| Never add in-process WebSocket reconnection | Section 12 + 16 | systemd handles restarts; in-process caused state corruption |

If Jules is tempted to violate any of these, the System Prompt requires Jules to:
1. Document the reason for the change
2. Update the relevant AGENTS.md section
3. Add a detailed changelog entry
4. Request human (you) approval before proceeding

### 2. Documentation-Driven Updates

When Jules discovers a discrepancy:

**Scenario:** Code does something AGENTS.md doesn't mention.

```
Jules action:
1. Re-reads both AGENTS.md and code carefully
2. Determines: "Is AGENTS.md wrong, or is the code wrong?"
3. If AGENTS.md is incomplete:
   - Updates AGENTS.md first
   - Adds changelog entry
   - Then uses the correct behavior
4. If code is wrong:
   - Documents the bug
   - Adds to Section 16 (Pitfalls)
   - Fixes the code
   - Adds changelog entry
```

### 3. Pattern Reuse

When implementing something new (e.g., new strategy):

```
Jules action:
1. Reads Section 6 (Strategy Layer) in AGENTS.md
2. Reads Section 11 (Adding New Strategies) in AGENTS.md
3. Finds real example in strategies/ folder
4. Copies the exact pattern from AGENTS.md boilerplate
5. Fills in strategy-specific logic
6. Never improvises beyond documented patterns
```

### 4. Changelog Discipline

Every change to AGENTS.md must have a corresponding entry:

```markdown
| 2026-05-15 | Added clarification on limit order StopLossRate requirement | Section 5.2 |
| 2026-05-15 | Fixed _poll_for_fill timing documentation (20→30 attempts) | Section 5.2, Section 9 |
| 2026-05-16 | Documented PnL envelope unwrapping in Real API | Section 8, Section 16 |
```

This creates a **audit trail** of what changed and why.

---

## How You (As Developer) Should Use These Files

### On Day 1 (Setup)

1. **Read AGENTS.md in full** — understand the architecture and constraints
2. **Review JULES_SYSTEM_PROMPT.md** — understand how Jules will work
3. **Place both files in the repo root** (or `/docs`) for Jules to reference
4. **Create a `.jules.config` or similar** that points Jules to these files

### Before Asking Jules to Work on Something

1. **Verify AGENTS.md is current** — run Part 2 (Verification Checklist) yourself
2. **If you know of any inaccuracies, fix them first**
3. **Provide Jules with the System Prompt** in the task description
4. **Reference the relevant AGENTS.md sections** in your task

**Example task for Jules:**
```
Task: Add support for instrument "XYZ"

Use the System Prompt in JULES_SYSTEM_PROMPT.md (Parts 1-4).
Follow AGENTS.md Section 10 (Adding New Instruments) exactly.
Verify your changes against the checklist in Section 10.
Add a changelog entry to AGENTS.md Section 18.
```

### When Reviewing Jules's Work

1. **Check the changelog entry** in AGENTS.md Section 18 — is it clear and specific?
2. **Spot-check the code** against AGENTS.md patterns
3. **Verify constraints weren't violated** (Section 12 + Section 16)
4. **If AGENTS.md was updated**, verify the changes are accurate to the code

---

## Monthly Audit Checklist (For You)

**Every 4 weeks, spend 1 hour on this:**

- [ ] Read the AGENTS.md changelog (Section 18) since last month
- [ ] Verify 3 entries from the changelog — do the changes still apply?
- [ ] Pick 1 pitfall from Section 16 — check if it still exists in the code
- [ ] Review Part 9 of the System Prompt (Monthly Maintenance)
- [ ] Run Part 2 (Verification Checklist) on 2–3 critical sections
- [ ] Check that all files in `/adapters` still follow conventions from AGENTS.md
- [ ] Verify that new strategies (if any) still match Section 6 boilerplate
- [ ] Update AGENTS.md if you find anything stale

---

## Preventing Common Mistakes

### Mistake 1: AGENTS.md Gets Out of Sync

**Prevention:**
- Jules adds changelog entries for every change (System Prompt Part 4)
- You audit monthly (Part 9)
- Part 2 (Verification Checklist) catches discrepancies

### Mistake 2: Jules Ignores Documentation

**Prevention:**
- System Prompt Part 1 requires Jules to read AGENTS.md first
- Part 8 is a pre-submission checklist
- Red flags (Part 7) stop Jules if something seems wrong

### Mistake 3: Constraints Get Violated

**Prevention:**
- Part 7 lists red flags that require AGENTS.md updates before changes
- Section 12 (Safety) is explicit and non-negotiable
- Section 16 (Pitfalls) explains why constraints exist

### Mistake 4: No One Knows Why A Decision Was Made

**Prevention:**
- Changelog entries include rationale (not just "changed X to Y")
- AGENTS.md explanation sections (not just lists) justify constraints
- Code comments link to AGENTS.md when necessary

---

## File Placement

**Recommended structure:**
```
etoro_nautilus/
├── AGENTS.md                    # Main reference (v1.0 from this task)
├── JULES_SYSTEM_PROMPT.md       # System prompt for AI agents
├── docs/
│   ├── ARCHITECTURE.md          # (optional) High-level overview
│   └── MAINTENANCE_LOG.txt      # (optional) Track audits
├── adapters/
├── strategies/
├── config/
└── dev_scripts/
```

Or if you prefer:
```
etoro_nautilus/
├── .agents/                     # Hidden directory for AI-specific docs
│   ├── AGENTS.md
│   ├── JULES_SYSTEM_PROMPT.md
│   ├── Integration_Guide.md
│   ├── API_docs_etoro.md
│   └── testing.md
└── ... (rest of repo)
```

Either way, **make sure Jules knows where to find these files** (specify in system instructions).

---

## Integration Example: Adding a New Strategy

Here's how the system should work end-to-end:

### Step 1: You Give Jules a Task
```
"Add a new strategy called 'MomentumClasher' using the template in AGENTS.md Section 6.
Use System Prompt Part 1–4 to guide your work.
Reference Section 11 for the checklist."
```

### Step 2: Jules Reads & Verifies
- Jules reads JULES_SYSTEM_PROMPT.md Part 1 (how to use AGENTS.md)
- Jules reads AGENTS.md Section 6 + Section 11
- Jules verifies the strategy boilerplate in existing strategies/ files
- If anything seems wrong, Jules updates AGENTS.md first

### Step 3: Jules Implements
- Jules copies the exact pattern from AGENTS.md Section 6 boilerplate
- Jules names files, methods, variables according to documented conventions
- Jules logs in German (adapters/strategies) or English (dev_scripts)
- Jules uses `self.create_task()` not `asyncio.create_task()`
- Jules verifies pattern matches by comparing to existing strategy

### Step 4: Jules Registers & Documents
- Jules adds entry to STRATEGY_REGISTRY in run_bot.py
- Jules adds bot config to ACTIVE_BOTS in config/setups.py
- Jules adds changelog entry to AGENTS.md Section 18:
  ```
  | 2026-05-20 | Implemented MomentumClasher strategy | Section 6, Section 11, strategies/ |
  ```

### Step 5: You Review
- You check the changelog entry — is it clear?
- You compare strategy code to AGENTS.md Section 6 boilerplate
- You verify constraint compliance (size_precision, position tracking, etc.)
- You approve or request changes

---

## FAQ

**Q: What if Jules finds AGENTS.md is wrong?**
A: Jules updates AGENTS.md first, adds a changelog entry, then proceeds. Part 7 (Red Flags) requires this.

**Q: Do we need both AGENTS.md and JULES_SYSTEM_PROMPT.md?**
A: Yes.
- AGENTS.md is a reference (what to do)
- JULES_SYSTEM_PROMPT.md is a process (how to verify and improve)
- Together they prevent drift and ensure quality

**Q: Can I just give Jules one file?**
A: Not recommended. JULES_SYSTEM_PROMPT.md contains critical operational steps (Part 2 verification, Part 8 checklist, Part 7 red flags) that AGENTS.md alone doesn't enforce.

**Q: What if AGENTS.md gets really long?**
A: Split it into multiple files when it exceeds 10,000 lines. Update the table of contents with cross-links. Both AGENTS.md and JULES_SYSTEM_PROMPT.md are designed to handle a larger codebase over time.

**Q: How often should Jules update AGENTS.md?**
A: Every time Jules makes changes to the codebase. If the change warrants it, Jules also updates AGENTS.md. See System Prompt Part 3 (How to Improve).

**Q: Can Jules improve AGENTS.md without code changes?**
A: Absolutely. If Jules finds an unclear section, Jules can improve the documentation (Part 3A–C) without touching code. Always add changelog entries.

---

## Quick Reference: Where to Find Everything

| Need | Location |
|------|----------|
| System architecture | AGENTS.md Section 3 |
| How to write a strategy | AGENTS.md Section 6 + Section 11 |
| REST API endpoints | AGENTS.md Section 8 + Section 5.2 |
| eToro WebSocket protocol | AGENTS.md Section 8 + Section 5.1 |
| Known pitfalls | AGENTS.md Section 16 |
| Safety constraints | AGENTS.md Section 12 |
| How Jules should work | JULES_SYSTEM_PROMPT.md all parts |
| Verification checklist for Jules | JULES_SYSTEM_PROMPT.md Part 2 |
| Changelog format | JULES_SYSTEM_PROMPT.md Part 4 + AGENTS.md Section 18 |

---

## Next Steps

1. **Place both files in your repo** (root or `/docs` or `.agents/`)
2. **Update AGENTS.md Section 18** with your initial dates if not already done
3. **Share JULES_SYSTEM_PROMPT.md with Jules** whenever you request work
4. **Bookmark Part 2 of the System Prompt** — run it monthly
5. **Keep the changelog (AGENTS.md Section 18) current** — Jules will do this, but verify

---

**Files Ready to Use:**
- ✅ AGENTS.md — 18 sections, 7,300+ lines, fully cross-referenced
- ✅ JULES_SYSTEM_PROMPT.md — 9 parts, operational instructions for AI agents
- ✅ This integration guide — explains how they work together

**Status:** Ready for production use. AGENTS.md is a living document; expect it to grow and improve as you use the system.
