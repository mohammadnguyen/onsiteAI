# PRODUCT.md — Slice-1 Implementation Authority

**Status:** ACTIVE — this document is the current implementation authority.
**Strategic context:** `docs/product/forey-charter-v1.0.md` (Charter v1.0, SIGNED).
**Governance:** `docs/decisions/ADR-001-automation-and-agent-architecture.md`.

Binding Scope: global, slice-1

The scope line above is machine-read by the drift check: every DECIDED registry entry whose `Applies-To` intersects this scope must be acknowledged in this document. Out-of-scope entries (currently the H4/H5 gates) enter this document only when their horizon is promoted — extending the Binding Scope then forces their acknowledgement.

How to read this file: every binding section carries a `Source:` (stable Decision ID in the Charter registry) and an `Ack:` (the normalized hash of that decision as acknowledged here). `scripts/check_decision_drift.py` is a CI gate: if the Charter decision changes, the check fails until this file is reviewed and the Ack updated **in the same commit**. Charter DIRECTION items are **not buildable** unless explicitly promoted into this file first.

---

## 1. What Slice 1 is

One vertical loop, nothing more:

> **Low-friction Capture (voice / text / photo) → Site Log Event with preserved raw Evidence → AI Candidate(s) → Human Confirmation → Structured Site Truth**

Source: DEC-CAPTURE-001 (Charter §42, §62, Amendment 2)
Ack: d15a6ca6c986

Source: DEC-SLICE-001 (Charter §62)
Ack: 6418cdfc4da9

Supporting foundations are limited to what this loop needs: Jobs, project members, project locations, raw evidence storage, Site Log Events, extraction, confirmation, audit history, and basic Tasks where the vertical slice requires them (Charter §62). Nothing downstream (schedule, payments, forecast, quoting) is in Slice 1.

---

## 2. Binding decisions

### ICP constraints that shape design

Owner-operated Australian small residential builder; no full-time office admin; ~5 active Jobs, up to ~10; mobile-first because facts originate on site. Design consequence: every Slice-1 interaction must survive one person moving between multiple sites; no workflow may assume desk time or continuous field maintenance.

Source: DEC-ICP-001 (Charter §2)
Ack: 6d3b0899a7d3

### Language

Mixed ZH/EN is a capability, not the customer definition. Pure-English input is first-class everywhere: prompts, UX copy assumptions, and **every eval corpus batch from the first batch**.

Source: DEC-LANG-001 (Charter §3)
Ack: 64ab1b10d48b

### Entry paths

Direct Job creation/import must always work. No feature may make a Forey Quote a prerequisite.

Source: DEC-ENTRY-001 (Charter §8)
Ack: 77a759bea271

### Information spine

Evidence → Candidate → Confirmation → Truth. Candidates live in candidate state/tables; Truth is written only by human confirmation, explicit human action, or deterministic operations on authoritative data.

Source: DEC-TRUTH-001 (Charter §6)
Ack: a347d0f0c005

### AI boundary is below the LLM

The extraction path must be **schema/permission-incapable** of writing Truth-designated records (e.g. variation status, confirmed events). Prompt instructions are not a boundary. Code review must reject any path where model output flows into Truth writes without a confirmation step.

Source: DEC-AI-BOUNDARY-001 (Charter §6, Amendment 5)
Ack: 6770e87d0196

### Evidence and delta retention

Raw evidence (audio, text, image) is stored and never destroyed by extraction. Confirmation records store the full delta: evidence ref, candidate-as-proposed, human edit, final confirmed result. This is a schema requirement from day one — retrofitting is an order of magnitude more expensive.

Source: DEC-EVIDENCE-001 (Charter §6, §17)
Ack: 3f991e60d3e5

### Two timestamps

`occurred_at` and `created_at` are separate columns on event-like records, both always stored. At the raw Evidence layer occurred_at is nullable — unknown stays NULL rather than being manufactured from upload time; a confirmed Site Log Fact must carry occurred_at or an explicit unknown/approximate marker (capture-slice concern).

Source: DEC-TIME-001 (Charter §17)
Ack: 79e4d0c77e8c

### Job attribution is explicit

The authoritative Job on a Capture is set by explicit user selection or confirmation. Navigation context / GPS / contextual intelligence may only prefill a suggestion. Being inside a Job Workspace counts as explicit context. AI inference alone never assigns the final Job. Target: ~one tap.

Source: DEC-JOB-ATTR-001 (Charter Amendment 3)
Ack: 7a156a02619d

### Slice-1 ontology (closed list)

Extraction may propose only three top-level Candidate types: **Site Log Fact, Task, Potential Variation** (Issue only if the vertical slice cannot cohere without it). Anything else (Progress, Rework, Delay, EOT, Forecast…) is Ontology v2+ **as a Candidate type**: not in prompts, not in schema enums as extractable candidate types. Optional classification metadata inside a Site Log Fact (`fact_category`, closed 12-value list, see annotation-schema-v0.2) is not a Candidate type and is permitted; it creates no separate workflow, module or confirmation object. One Raw Evidence item may produce zero or more Facts, Tasks and Potential Variations. Expense and Labour remain outside this ontology in their existing structured modules.

Source: DEC-ONTOLOGY-001 (Charter Amendment 2, Amendment 10)
Ack: 1c2f7be526d1

### Site Log is the fact/evidence layer

Site Log is the slice's core surface: facts captured by text, voice, photo and attachment; capturing a fact must be substantially easier than filling a site-diary form.

Source: DEC-SITELOG-001 (Charter §16)
Ack: b8e132b6c1ca

### Capture-context metadata

Site Log records carry Job, author, location, attachments (with metadata), original content, edit history and audit history where technically appropriate — on top of DEC-TIME-001 timestamps and DEC-EVIDENCE-001 retention. Schema requirement from the first migration that touches these records.

Source: DEC-SITELOG-META-001 (Charter §17)
Ack: 00c2965fdb42

### Address vs internal location

Project address and internal site location are distinct fields. A capture may carry an internal location (e.g. "First Floor Ensuite") separate from the Job address.

Source: DEC-LOCATION-001 (Charter §19)
Ack: 66d5de582714

### Tasks

Task is a Slice-1 Truth object producible as a Candidate from capture; initial statuses To Do / In Progress / Blocked / Done; a Site Log Event generates a Task without re-entry of the same information.

Source: DEC-TASK-001 (Charter §22)
Ack: 4a8f0c5c6672

### Active recall / passive precision

Deliberate captures: tune for recall (a missed client change is worse than one extra confirmation). Passive background detection (when it exists): tune for precision; no false-alert inbox.

Source: DEC-DETECTION-001 (Charter §44)
Ack: 78ead8fb8a09

### Attention budget

Confirmation UX must batch/queue, prioritise material items, and avoid duplicate confirmations. A capture yielding multiple candidates is confirmed in one flow, not N separate interruptions.

Source: DEC-ATTENTION-001 (Charter §46, Amendment 2)
Ack: 1c1fea3cb498

### Personalization boundary

Context injection (people, trades, suppliers, locations, shorthand, prior confirmations) improves input understanding per project/org. Ontology, commercial semantics and Truth-layer rules are global and never adapt per user.

Source: DEC-PERSONALIZATION-001 (Charter §45 + registry)
Ack: be2d49a7782b

### Autonomy (forward constraint)

No autonomous commercial action exists in Slice 1. Any future gate must be deterministic and auditable; confidence scores are never sufficient. Provisional withdrawal threshold 2% correction/reversal.

Source: DEC-AUTONOMY-001 (Charter §58, Amendment 5)
Ack: 6f1265241b91

### Boundaries

No client accounts required; no subcontractor adoption required.

Source: DEC-BOUNDARY-PORTAL-001 (Charter §48, §49)
Ack: c94b9972e91c

Not accounting software.

Source: DEC-BOUNDARY-ACCT-001 (Charter §50)
Ack: be1d249c590b

External-system compatibility (forward guardrail): native capability adoption is never a prerequisite; core workflows must function without paid third-party API integrations. No Slice-1 build impact — recorded so nothing in Slice 1 or its schema assumes native-only issuance downstream.

Source: DEC-BOUNDARY-EXT-001 (Charter Amendment 6)
Ack: 2bc188a64bef

Active NOT NOW list applies (takeoff, estimating DB, price books, enterprise Gantt/CPM, accounting/payroll, internal chat, mandatory portals, contractual adjudication, liability determination, autonomous approval/pricing/invoicing, benchmarking, worker ranking, broad autonomy).

Source: DEC-NOTNOW-001 (Charter §52)
Ack: 705ac9c0dee5

### Existing surfaces (existing-maintain)

Expenses, labour, budget and related cost views shipped before the Charter hold existing-maintain status: keep them working under the light gate — bug fixes, regression fixes, security and compatibility fixes, test repair, correctness fixes, small UX repairs, necessary maintenance refactors, minimal schema changes required to restore existing behaviour. Do not grow them as "maintenance": no new business workflows, no domain scope expansion, no large new features, no whole-module redesign, and never remove existing capability merely because the Charter does not emphasise it. New capability requires promotion through this document first. The Slice-1 audit assigns every module one of: slice-1-foundation / existing-maintain / untouched-for-now / conflicts-with-decision.

Source: DEC-EXISTING-001 (Charter Amendment 8)
Ack: 4c16d5631ca6

---

## 3. Validation gates for this phase

### H1 — Capture behaviour

3 consecutive weeks, real projects, ≥3 meaningful captures per active site day (floor, not target), ≥80% confirmations resolved within 48h, friction log maintained (`docs/product/FOUNDER_FRICTION_LOG.md`). Founder non-use = product-design failure.

Source: DEC-GATE-H1-001 (Charter §53, Amendment 4)
Ack: 2d12f6e2e943

### H2 — Extraction quality

Corpus mixes EN / ZH / mixed / shorthand / trade & supplier names; pure-English mandatory from batch 1. Extraction metrics: fact recall & precision, Task precision, PV recall & precision, direct-confirm rate, correction time, wrong person/trade/location rate, commercially-important misses. Wrong-Job suggestion rate is capture-attribution telemetry (H1/product measurement, per DEC-JOB-ATTR-001), not an extraction metric. Raw evidence preserved as reusable eval set. See `evals/extraction/annotation-schema-v0.1.md`.

Source: DEC-GATE-H2-001 (Charter §54, Amendment 4)
Ack: 6a617ad8020d

### H3 — Commercial proof

Variation protection: 6 weeks founder usage; if no event, +4 weeks on ≥1 seed-builder project; still nothing → reopen the hypothesis. Seed-builder list is private, never committed.

Source: DEC-GATE-H3-001 (Charter §55, Amendment 4)
Ack: 6a5f8a38e45f

---

## 4. Explicitly out of Slice 1

Everything the Charter marks DIRECTION (quoting, estimating, signature, schedule beyond slice needs, payments, cost control, forecast, daily-log assembly, comments, weather chains, scope items, Today screen) and everything in DEC-NOTNOW-001. If work seems to need one of these, STOP and surface it — do not build it "small" as a side effect.

---

## 5. Changing this document

1. Change the Charter (registry entry and/or body) via its own PR.
2. Update the affected PRODUCT.md section and its `Ack:` in the same commit.
3. `python scripts/check_decision_drift.py` must pass in CI.

Slice planning follows the five criteria of DEC-BUILD-001: independent user value, long-term data architecture preserved, operable by existing capacity, defined validation result, explicit stopping point.

Source: DEC-BUILD-001 (Charter §61)
Ack: 8bbd36e32015

Promotion of a DIRECTION item into the active slice requires: a Charter registry entry (or status change to DECIDED), a new binding section here, and a sceptic-review record for the decision.
