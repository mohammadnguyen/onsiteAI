# FOREY — Product Strategy & Validation Charter

**Version:** 1.0
**Status:** SIGNED — Amendments 1–5 incorporated at signing; Amendments 6–9 added 2026-08-09
**Date:** August 2026

---

## Part 0 — How to Read This Document

This Charter is a **decision record, not a permanent constitution**. A decision may be reopened when real product usage, customer evidence, technical constraints or commercial results materially contradict the assumptions recorded here (see §60).

**Authority levels.** Per Amendment 1, every product statement carries exactly one of three levels:

- **DECIDED** — binding on the current build and product architecture. Reopened only via a documented reopen condition or materially contradicting evidence.
- **DIRECTION** — a current preferred direction for a future horizon. Informs architecture where avoiding dead ends is cheap; does **not** authorise implementation; must be reopened and validated when its Horizon becomes active; must not be treated by planning agents or Claude Code as a current build requirement.
- **NOT NOW** — explicitly outside active product scope. NOT NOW does not necessarily mean never.

**Machine interface vs human navigation.** The **Decision Registry (Part A)** is the authoritative binding set and the machine interface: stable Decision IDs, statuses, and hashed decision text. Section numbers and labels in the body (Part B) are human navigation. Where a body label and the Registry conflict, the Registry prevails. Labels changed during sign-off normalization show the original label in parentheses. Coverage rule (Amendment 7): any body heading labelled DECIDED must carry a `Registry:` line mapping it to one or more Registry IDs; the drift check fails on any DECIDED heading without a valid mapping.

**Normative vs explanatory.** Registry entries are normative and hash-protected. Body prose is explanatory context and is not hash-protected. Body DECIDED labels must map to a valid Registry ID, but prose equivalence is not machine-verified: a green check does not mean the body text is semantically unchanged.

**Scope routing (Amendment 9).** Each registry entry may carry an `Applies-To:` line (e.g. global, slice-1, h3, h4, h5). Applies-To is routing metadata, not a fourth authority level — authority remains DECIDED / DIRECTION / NOT NOW. It states when a decision must be acknowledged by the binding implementation document. PRODUCT.md declares its `Binding Scope:`; the drift check's full-coverage mode requires every DECIDED entry whose Applies-To intersects that scope to be referenced there. Applies-To is excluded from the normalized hash, so re-scoping an entry does not invalidate existing acknowledgements — the coverage check itself enforces the consequences of a scope change. A missing Applies-To defaults to global (fail-safe).

**Implementation authority.** `docs/product/PRODUCT.md` is the current implementation authority for Slice 1. This Charter is strategic context. A DIRECTION item enters active build only by explicit promotion in PRODUCT.md.

---

## Part A — Decision Registry

<!-- DECISION-REGISTRY:BEGIN -->

### DEC-ICP-001 — Initial customer profile [DECIDED]
Applies-To: global

Initial ICP: Australian small residential builder (Class 1/1a: new homes, renovations, extensions, wet-area work). The owner works both on site and in administration, has no dedicated full-time office administrator, normally runs approximately 5 active jobs (approaching 10 in busy periods), and requires mobile-first operation. Forey must remain usable without continuous manual system maintenance.

### DEC-LANG-001 — Language position [DECIDED]
Applies-To: global

Mixed Chinese/English site language is a Forey capability, not the customer definition. Forey targets the wider Australian small-builder market. Pure-English input is first-class and must be represented in every evaluation corpus from the first batch.

### DEC-ENTRY-001 — Entry paths [DECIDED]
Applies-To: global

A Job can always be created or imported directly. Quote-first is a supported path but must never become a prerequisite for using Forey.

### DEC-TRUTH-001 — Information spine [DECIDED]
Applies-To: global

All AI-assisted information follows one spine: Evidence, then Candidate, then Confirmation, then Truth. Only confirmed information, explicit human actions, or deterministic system operations based on authoritative data may update project Truth.

### DEC-AI-BOUNDARY-001 — AI cannot write commercial Truth [DECIDED]
Applies-To: global

AI candidates cannot directly write commercial or contractual Truth. The boundary is enforced below the LLM (schema, permissions, credentials), not by prompt instructions alone. AI surfaces information; the builder makes commercial and contractual determinations.

### DEC-EVIDENCE-001 — Evidence and delta retention [DECIDED]
Applies-To: global

Original raw evidence is preserved. Every confirmation retains the full delta: the original evidence reference, the AI candidate as proposed, the human edit, and the final confirmed result.

### DEC-TIME-001 — Two timestamps [DECIDED]
Applies-To: global

occurred_at (when the event actually happened) and created_at (when the Forey record was created) are distinct concepts and both are always stored. A record entered at night about a morning event must not pretend the event occurred at night.

### DEC-JOB-ATTR-001 — Explicit Job attribution [DECIDED]
Applies-To: slice-1

Authoritative Job attribution requires explicit user selection or confirmation in Slice 1. Navigation context, GPS and contextual intelligence may only prefill a suggested default. AI inference alone cannot assign the final Job. Target interaction cost: approximately one tap without sacrificing attribution certainty.

### DEC-ONTOLOGY-001 — Slice-1 ontology [DECIDED]
Applies-To: slice-1

Slice-1 capture ontology is limited to exactly three top-level Candidate types: Site Log Fact, Task, Potential Variation. Issue may be included only if the vertical slice cannot form a coherent workflow without it; no other top-level Candidate type may be added. Progress, Rework, Delay, EOT and further classifications remain excluded as Candidate types (Ontology v2 or later, requiring evidence that the existing capture loop works). Optional classification metadata inside a Site Log Fact (such as a fact_category value) is not a Candidate type and is permitted, provided it creates no separate workflow, module or additional confirmation object.

### DEC-CAPTURE-001 — Low-friction capture wedge [DECIDED]
Applies-To: slice-1

The initial wedge is low-friction capture (voice, text, photo) producing structured Site Truth. One capture may yield multiple candidates; the builder never re-enters the same fact into multiple modules.

### DEC-DETECTION-001 — Active recall, passive precision [DECIDED]
Applies-To: global

When a builder deliberately records a potentially important site event, Forey favours recall: missing a deliberately captured client change is worse than one unnecessary confirmation. Passive background detection over ordinary records favours precision and must not produce a high-volume false-alert inbox.

### DEC-ATTENTION-001 — Attention budget [DECIDED]
Applies-To: global

Human attention is a constrained product resource. Forey must combine review queues, prioritise material items, suppress low-value noise, minimise duplicate confirmation, and operate exception-driven rather than by full-table maintenance. Confirmation burden must not replace the administrative burden it removes.

### DEC-PERSONALIZATION-001 — Personalization boundary [DECIDED]
Applies-To: global

Personalization occurs only at the input-understanding layer (contextual memory: people, trades, suppliers, locations, shorthand, scope items, files, prior confirmations). Truth-layer semantics, the ontology, and commercial rules remain globally consistent and never adapt per user.

### DEC-AUTONOMY-001 — Autonomy earned per action class [DECIDED]
Applies-To: global

Autonomy is granted one action class at a time and gated by explicit, deterministic, auditable conditions; AI confidence scores alone are never sufficient gating criteria. Provisional rule: if post-action human correction or reversal exceeds 2% over a representative sample, autonomous permission for that class is withdrawn and it returns to confirmation-required mode pending review. The threshold is recalibrated before H5 begins.

### DEC-GATE-H1-001 — H1 capture gate [DECIDED]
Applies-To: slice-1

H1 founder test: 3 consecutive weeks on real active projects; behavioural floor of at least 3 meaningful captures per active site day across the portfolio unless the day genuinely produces fewer recordable facts; at least 80% of confirmation items resolved within 48 hours; friction log maintained throughout. Artificial low-value captures do not count. If the founder will not naturally use Capture, that is a product-design failure, not a user-discipline failure.

### DEC-GATE-H2-001 — H2 extraction gate [DECIDED]
Applies-To: slice-1

The evaluation corpus must mix English, Chinese, mixed Chinese/English, construction shorthand, trade names, supplier names and natural site-language fragments; pure-English samples are mandatory from the first batch. Extraction metrics include: Site Fact recall and precision, Task precision, Potential Variation recall and precision, direct-confirm rate, correction time, wrong person/trade/location rate, and commercially important missed events. Wrong-Job suggestion rate is a capture-attribution telemetry metric under H1/product measurement (per DEC-JOB-ATTR-001) and is not scored by the extraction baseline. Real raw evidence is preserved from the beginning as a reusable evaluation set.

### DEC-GATE-H3-001 — H3 commercial proof gate [DECIDED]
Applies-To: slice-1

The first commercial proof is Variation protection. Run 6 weeks of representative founder usage; if no suitable commercially meaningful event occurs, test at least one seed-builder project for a further 4 weeks; if still no meaningful evidence, reopen the Variation-protection hypothesis. Site Truth alone must not continue indefinitely without commercial validation. Failure leads to re-examining the wedge, not adding features.

### DEC-BOUNDARY-PORTAL-001 — No mandatory external participants [DECIDED]
Applies-To: global

Forey's core workflow never depends on clients creating accounts or on subcontractors adopting Forey. External parties may receive secure documents and experiences without accounts.

### DEC-BOUNDARY-ACCT-001 — Not accounting software [DECIDED]
Applies-To: global

Forey does not become general accounting software: no bank reconciliation, BAS, general ledger, payroll, or full AP/AR accounting. Accounting integrations may be added later where justified; they are not prerequisites.

### DEC-NOTNOW-001 — Active NOT NOW list [DECIDED]
Applies-To: global

The current strategy does not authorise development of: automated drawing takeoff; full estimating database; supplier price-book system; enterprise Gantt/CPM; general accounting; payroll; complete internal chat; mandatory Client Portal; mandatory Subcontractor Portal; automatic contractual adjudication; automatic liability determination; autonomous Variation approval; autonomous commercial pricing; autonomous high-value client invoicing; cross-builder benchmarking; worker ranking; broad autonomous commercial control. These re-enter planning only when evidence justifies reopening.

### DEC-BOUNDARY-EXT-001 — External system compatibility [DECIDED]
Applies-To: global

Forey may provide native quoting, invoicing, signature and document-generation capabilities, but adoption of those native capabilities must never be a prerequisite for using Forey. Builders may continue using established external systems (e.g. Xero, MYOB, existing signature and quoting tools) for accounting, invoicing, signatures and related execution. Forey retains the project and commercial state needed to know what should happen and whether it happened; external systems may remain the authoritative source for their specialist transaction records, and recording an externally issued document in Forey is an explicit human action. Integrations may reduce duplicate administration where commercially justified, but Forey's core workflows must remain functional without paid third-party API integrations.

### DEC-SITELOG-001 — Site Log is the fact/evidence layer [DECIDED]
Applies-To: slice-1

Site Log is Forey's project fact and evidence layer, not a social activity feed. Site facts are captured via text, voice, photographs and attachments, and capturing a site fact must be substantially easier than completing a traditional site diary form.

### DEC-SITELOG-META-001 — Capture-context metadata [DECIDED]
Applies-To: slice-1

Site Log records preserve capture-context metadata where technically appropriate — Job, author, location, attachments and attachment metadata, original content, edit history and audit history — in addition to the timestamp requirements of DEC-TIME-001 and the evidence retention requirements of DEC-EVIDENCE-001.

### DEC-LOCATION-001 — Address vs internal location [DECIDED]
Applies-To: slice-1

Project address and internal site location are distinct concepts. A capture may carry an internal location (e.g. First Floor Ensuite) separate from the project address, enabling later retrieval and analysis by actual work area.

### DEC-TASK-001 — Task as Slice-1 object [DECIDED]
Applies-To: slice-1

Task is a supported Slice-1 Truth object that may be produced as a Candidate from capture, with initial statuses To Do, In Progress, Blocked and Done. A Site Log Event may generate a Task without the user re-entering the same information.

### DEC-GATE-H4-001 — H4 action gate (provisional) [DECIDED]
Applies-To: h4

Once Action/Today is sufficiently implemented, adoption of Forey-recommended actions below approximately 30% over a representative four-week period triggers reconsideration. The exact threshold must be recalibrated before this Horizon begins.

### DEC-GATE-H5-001 — H5 admin-automation gate (provisional) [DECIDED]
Applies-To: h5

When Forey generates commercial documents, at least 50% should be usable directly or with light editing. Poor results trigger redesign of the generated artifact rather than expansion into more document types. The threshold must be recalibrated before this Horizon begins.

### DEC-BUILD-001 — Slice criteria [DECIDED]
Applies-To: global

Every engineering slice must: solve a real user problem independently; preserve the long-term data architecture; be operable by the existing development capacity; have a defined validation result; and have an explicit stopping point.

### DEC-SLICE-001 — First slice definition [DECIDED]
Applies-To: slice-1

The first product slice is the loop: low-friction Capture → Site Log Event → AI Candidate → Human Confirmation → Structured Site Truth. Supporting foundations are limited to what this loop needs: Jobs, project members, project locations, raw evidence, Site Log Events, AI extraction, confirmation, audit history, and basic Tasks or Issues only where the vertical slice requires them.

### DEC-EXISTING-001 — Existing-maintain status [DECIDED]
Applies-To: global

Functionality that was already shipped and relied upon before Charter v1.0 and that maps to DIRECTION or NOT NOW areas holds existing-maintain status. Permitted under the light gate: bug fixes, regression fixes, security and compatibility fixes, test repair, correctness fixes, small UX repairs, necessary maintenance refactors, and the minimal schema changes required to restore existing behaviour. Not permitted as maintenance: new business workflows, domain scope expansion, large new features, whole-module redesign, or removal of existing capability merely because the Charter does not emphasise it. New capability on an existing-maintain surface requires prior promotion through PRODUCT.md or an explicit founder ruling. The Slice-1 repository audit assigns every existing module exactly one status: slice-1-foundation, existing-maintain, untouched-for-now, or conflicts-with-decision. Deprecation or removal of an existing surface is a founder decision, never implied.

<!-- DECISION-REGISTRY:END -->

---

## Part B — Charter Body

# 1. Purpose of This Charter

This document defines the current strategic decisions for Forey:

* who Forey is initially built for;
* what problem Forey exists to solve;
* what the long-term product should become;
* what role AI plays;
* what Forey will and will not build;
* what the first product wedge is;
* how the core product objects connect;
* what must be validated before expanding scope.

This document is a **decision record, not a permanent constitution**.

A decision may be reopened when real product usage, customer evidence, technical constraints or commercial results materially contradict the assumptions recorded here.

---

# 2. Initial Customer Profile — DECIDED

Registry: DEC-ICP-001

Forey's initial customer is an Australian small residential builder operating primarily across Class 1 / Class 1a residential work, including:

* new homes;
* renovations;
* extensions;
* additions;
* structural alteration;
* kitchens and bathrooms;
* wet-area work;
* related residential construction.

The initial ICP:

* has **no dedicated full-time office administrator**;
* has an owner directly involved in both site operations and administration;
* normally manages approximately **5 active projects**;
* may reach approximately **10 concurrent projects** during busy periods;
* personally handles or closely supervises quoting, variations, project progress, client communication and commercial decisions;
* works heavily through subcontractors and suppliers;
* operates using a mixture of phone calls, conversations, email, photographs, invoices, drawings, memory and lightweight software;
* cannot afford a project-management workflow that requires continuous manual system maintenance;
* requires mobile-first operation because commercially important facts frequently originate on site.

Forey must therefore remain usable when one owner is moving between multiple sites and does not have time to maintain hundreds of project fields manually.

The founder can identify at least **3–5 real builders** materially matching this profile. These form the initial seed-user pool. (The named list is private founder material and is **not** stored in this repository.)

---

# 3. Language Position — DECIDED

Registry: DEC-LANG-001

Mixed Chinese/English construction language is a **Forey capability**, not the definition of the customer.

Forey is intended for the wider Australian small-builder market.

Chinese-speaking builders may be an effective early acquisition group because mixed-language capture creates a strong validation environment, but Forey will not position itself as software only for Chinese-speaking builders.

---

# 4. Core Problem

Small residential builders do not primarily lack software.

They lack a reliable connection between:

> **what actually happens on a project**

and

> **what the management system knows.**

Important project facts currently live in:

* conversations;
* phone calls;
* voice messages;
* photographs;
* emails;
* drawings;
* supplier documents;
* handwritten notes;
* individual memory;
* disconnected software.

Traditional project-management systems often require the builder to stop working and manually maintain the software.

For Forey's ICP, this maintenance burden is one of the reasons project data becomes incomplete.

The result is not merely poor administration.

It can lead to:

* missed tasks;
* team members operating from different information;
* incomplete site records;
* delayed identification of problems;
* missed or late variations;
* weak evidence;
* inaccurate progress;
* delayed invoicing;
* poor cost visibility;
* margin leakage.

---

# 5. Product Thesis — DECIDED

Registry: DEC-CAPTURE-001, DEC-AI-BOUNDARY-001

Forey reverses the traditional PM software model.

> **Capture what happens. Keep the team aligned. Turn project facts into action.**

The user should be able to record what happened naturally using:

* voice;
* text;
* photographs;
* lightweight actions.

Forey then performs the administrative work of:

* structuring;
* categorising;
* linking;
* summarising;
* retrieving;
* preparing;
* recommending.

The builder retains commercial and project judgement.

The governing AI principle is:

> **Forey does the paperwork. The builder makes the decision.**

---

# 6. Information Spine — DECIDED

Registry: DEC-TRUTH-001

All AI-assisted information follows one common architecture:

> **Evidence → Candidate → Confirmation → Truth**

## Evidence

The original information entering Forey.

Examples:

* voice recording;
* typed note;
* photograph;
* uploaded document;
* site action;
* completed checklist;
* project file.

Original evidence is preserved.

## Candidate

AI may interpret the evidence and propose structure.

Examples:

* possible progress update;
* possible issue;
* suggested task;
* potential variation;
* possible rework;
* possible delay event.

A Candidate is not project truth.

## Confirmation

An authorised human:

* accepts;
* edits;
* rejects;

the Candidate.

Forey retains the delta between:

* original evidence;
* AI candidate;
* human correction;
* final confirmed result.

## Truth

Only confirmed information, explicit human actions or deterministic system operations based on authoritative data may update project Truth.

AI does not silently rewrite commercial or contractual records.

---

# 7. Forey Product Lifecycle — DIRECTION (original: TARGET END STATE)

Forey should eventually connect the residential builder's operational and commercial lifecycle:

> **Estimate → Quote → Client Acceptance → Job → Scope → Budget → Schedule → Payment Schedule → Site Activity → Progress → Variation / Rework → Forecast → Claim / Invoice → Payment → Project Outcome**

The core product principle is:

> **Information created upstream should flow downstream without being manually recreated.**

Forey is therefore not intended to become a collection of unrelated modules.

Its value comes from the relationships between them.

---

# 8. Entry Into Forey — DECIDED

Registry: DEC-ENTRY-001

A Job does **not** require a Forey Quote to exist.

Forey supports two valid entry paths.

## Path A — Quote-first

Estimate
→ Quote
→ Client acceptance/signature
→ Create Job

## Path B — Existing project

Directly create or import a Job.

This is necessary because a builder adopting Forey may already have several active projects.

Quote-first provides richer structured data, but it is not a prerequisite for using Forey.

---

# 9. Lightweight Estimating — DIRECTION (original: DECIDED)

Forey should eventually provide lightweight internal estimating.

Builders may enter or upload:

* trade quotations;
* subcontractor costs;
* supplier quotations;
* material costs;
* labour allowances;
* preliminaries;
* PC items;
* provisional sums;
* contingencies;
* other project costs.

Forey may then support:

* cost calculation;
* markup;
* margin;
* GST;
* sell price.

The goal is not initially to reproduce a large specialist estimating platform.

Forey does **not initially require**:

* huge price libraries;
* complex assemblies;
* estimating recipes;
* supplier catalogue databases;
* sophisticated productivity calculations.

---

# 10. Quoting — DIRECTION (original: DECIDED)

Forey should have its own Quote / Proposal workflow.

A Forey Quote may contain:

* builder details;
* client details;
* site address;
* scope;
* scope sections;
* line items;
* inclusions;
* exclusions;
* allowances;
* Prime Cost items;
* Provisional Sums;
* internal estimated costs;
* markup / margin;
* GST;
* sell price;
* commercial terms;
* attachments;
* quote version history.

Forey should support:

> Draft → Sent → Viewed → Accepted / Declined → Signed

The builder should eventually be able to send the Quote directly from Forey.

---

# 11. Signature — DIRECTION (original: DECIDED DIRECTION)

Forey should ultimately have its own secure electronic-signature capability.

This is a long-term product capability, not an immediate build requirement.

Initial implementation may continue to use an external signature provider while Forey owns:

* document state;
* commercial data;
* version history;
* signature status;
* final signed document reference.

Forey should only implement its own legally relied-upon signature workflow once:

* identity verification;
* consent;
* document integrity;
* audit trail;
* evidence retention;
* security;
* applicable legal requirements;

have been properly designed and reviewed.

---

# 12. Quote → Job — DIRECTION (original: DECIDED)

A signed or accepted Forey Quote may be converted into a Job.

This should not require rebuilding the project from scratch.

Relevant information should flow into the Job, including where applicable:

* client;
* project address;
* contract / sell value;
* Scope Items;
* initial Budget;
* allowances;
* project documentation;
* payment structure.

---

# 13. Scope Items — DIRECTION (original: DECIDED)

Meaningful quotation scope should be represented by persistent internal Scope Items.

Example:

* Bathroom demolition
* Plumbing rough-in
* Waterproofing
* Tiling
* Shower screen

Each receives a stable internal identifier.

The client does not need to see the identifier.

When the Quote becomes a Job, these Scope Items become the starting Scope Baseline.

This allows later information to reference the same original scope.

For example:

> Client requests an additional shower niche.

Forey can locate related Scope Items and supporting documents and assist the builder in determining whether the request is:

* Included;
* Excluded;
* Not Mentioned;
* Ambiguous.

AI surfaces information.

The builder makes the commercial or contractual determination.

---

# 14. Automated Drawing Takeoff — NOT NOW

Forey will not initially attempt to become a specialist drawing takeoff platform.

Not-now examples include automatically measuring from drawings:

* m²;
* lineal metres;
* concrete volume;
* fixture counts;
* wall quantities;
* material quantities;
* complete Bills of Quantities.

AI understanding of drawings may still be useful later for:

* finding information;
* identifying drawings;
* understanding notes;
* comparing revisions;
* linking drawings to Scope;
* linking drawings to Issues or Variations.

Automated authoritative quantity takeoff remains a separate future capability.

---

# 15. Job as the Core Workspace — DIRECTION (original: DECIDED; Jobs themselves are active Slice-1 foundation)

Once a project exists, its Job becomes the shared internal project workspace.

Conceptually a Job contains:

## Overview

Current high-level project state.

## Site Log

What actually happened.

## Tasks

What needs to happen and who owns it.

## Progress

What was planned and where the project currently stands.

## Commercial

The project's money and commercial cases.

## Files

The project document system.

These are domain concepts.

They do not all have to become separate mobile navigation tabs.

Mobile UX must remain substantially simpler than the underlying data model.

---

# 16. Site Log — DECIDED (original: CORE CAPABILITY)

Registry: DEC-SITELOG-001

Site Log is not merely a social activity feed.

It is Forey's **project fact and evidence layer**.

Users may record site activity through:

* text;
* voice;
* photographs;
* attachments.

Forey should make capturing a site fact substantially easier than completing a traditional site diary form.

---

# 17. Site Log Metadata — DECIDED

Registry: DEC-SITELOG-META-001, DEC-TIME-001, DEC-EVIDENCE-001

Where technically appropriate, Forey should automatically preserve contextual metadata such as:

* Job;
* project address;
* date;
* time;
* timezone;
* author;
* created timestamp;
* event timestamp;
* optional location/GPS verification;
* weather snapshot;
* weather source;
* attachments;
* attachment metadata;
* original content;
* edit history;
* audit history.

Two time concepts must remain distinct:

## `occurred_at`

When the event actually happened.

## `created_at`

When the Forey record was created.

A record entered at night describing something that happened that morning must not pretend that the event occurred at night.

---

# 18. Weather and Site Conditions — DIRECTION (original: DECIDED)

Weather should form part of the Site Log context because it may later help explain:

* delay;
* lost working time;
* site access problems;
* blocked work;
* Extension of Time review;
* project disputes.

Automatic weather data alone is not sufficient to establish construction impact.

Forey should allow project facts to connect:

> Weather / Condition → Affected Work → Delay / Blocker → Schedule Impact → Potential Commercial or EOT Review

The builder remains responsible for the final contractual assessment.

---

# 19. Site Location — DECIDED

Registry: DEC-LOCATION-001

Project address and internal site location are separate concepts.

Example:

**Project:**
18 Smith Street, Burwood

**Location:**
First Floor Ensuite

Location should eventually support project-specific areas such as:

* Ground Floor;
* First Floor;
* Kitchen;
* Ensuite;
* Roof;
* Rear Yard;
* Garage.

This allows later retrieval and analysis by actual work area.

---

# 20. Site Log Events and Daily Site Log — Events: DECIDED / Daily Log assembly: DIRECTION (original: DECIDED)

Registry: DEC-CAPTURE-001, DEC-SITELOG-001

Forey supports two layers.

## Site Log Event

A granular project event.

Examples:

* concrete delivered;
* plumber did not attend;
* client requested change;
* waterproofing completed;
* inspection passed;
* rain stopped roofing work.

A project may have many events in one day.

## Daily Site Log / Site Diary

Forey should automatically assemble that day's relevant project information into a structured Daily Site Log.

Possible sections include:

* date;
* project address;
* weather;
* site conditions;
* people / trades on site;
* work performed;
* progress;
* delays;
* blockers;
* client instructions;
* variations;
* issues;
* inspections;
* deliveries;
* tasks;
* photographs;
* other evidence.

An authorised user may:

> Review → Finalise Daily Log

Once finalised, the record should not be silently rewritten.

Corrections should preserve amendment history.

---

# 21. Team Visibility — DIRECTION (original: DECIDED; downgraded per Amendment 7)

Internal project team members should be able to access a common view of relevant project information.

Forey should reduce situations where:

* one person knows an issue;
* another person has the photograph;
* someone else received the instruction;
* the task exists only in memory.

The operating principle is:

> **If information has continuing project value, Forey should make it possible to preserve and share it.**

This does not mean every conversation must happen inside Forey.

---

# 22. Tasks — DECIDED (original: CORE CAPABILITY)

Registry: DEC-TASK-001, DEC-ONTOLOGY-001

Forey should support project Tasks.

A Task may contain:

* title;
* description;
* Job;
* location;
* assignee;
* due date;
* status;
* source Site Log Event;
* related Issue;
* attachments;
* comments.

Initial Task statuses:

> **To Do → In Progress → Blocked → Done**

A Site Log Event should be able to generate a Task without requiring the user to re-enter the same information.

Example:

Site capture:

> Window flashing hasn't been installed. John needs to arrange it tomorrow.

Forey may propose:

**Task:** Arrange/install window flashing
**Assignee:** John
**Due:** Tomorrow

The user confirms.

---

# 23. Comments — DIRECTION (original: YES)

Tasks and other project objects may support contextual comments.

This provides necessary project discussion while retaining structured context.

---

# 24. Internal Chat — NOT NOW

Forey will not initially become a general-purpose company chat system.

Full internal chat creates a separate product surface involving:

* channels;
* direct messages;
* read states;
* mentions;
* chat search;
* threads;
* notifications;
* file sharing;
* message moderation.

Forey should first determine whether object-based communication through:

* Tasks;
* Issues;
* Site Logs;
* Comments;

is sufficient.

Internal Chat may be reconsidered later.

---

# 25. File Management — DIRECTION (original: CORE CAPABILITY; downgraded per Amendment 7)

Evidence attachment storage itself remains DECIDED via DEC-EVIDENCE-001; the document-management module described below is future scope.

Every Job requires project file management.

Forey should support project documents such as:

* Drawings;
* Contracts;
* Specifications;
* Quotes;
* Variations;
* Invoices;
* Certificates;
* Inspections;
* Reports;
* Photos;
* Other files.

File records should support relevant metadata including:

* uploader;
* upload time;
* type;
* Job;
* revision where applicable;
* current/superseded status where applicable.

---

# 26. Drawing Revision Control — DIRECTION (original: DIRECTION DECIDED)

For drawing-type files, Forey should eventually distinguish:

> **CURRENT**

from:

> **SUPERSEDED**

This becomes important for:

* site work;
* scope interpretation;
* variation evidence;
* defects;
* disputes;
* AI retrieval.

A file should also be linkable to:

* Task;
* Issue;
* Scope Item;
* Variation;
* Rework;
* Site Log;
* other project objects.

---

# 27. Schedule — DIRECTION (original: CORE CAPABILITY; downgraded per Amendment 1 beyond active-slice requirements)

Forey requires native project scheduling because:

* Progress requires a plan baseline;
* payment milestones depend on work completion;
* delays affect downstream work;
* Variations may affect time;
* forecasting may depend on project duration.

However:

> **Forey is not initially building Primavera or MS Project.**

---

# 28. Operational Schedule V1 — DIRECTION (original: DECIDED; downgraded per Amendment 1)

The initial scheduling hierarchy is:

> **Stage → Work Package → Task**

A Task may contain:

* planned start;
* planned finish;
* Trade;
* Location;
* assignee;
* status;
* blocker;
* simple predecessor/dependency;
* actual start;
* actual finish.

Initial statuses may include:

* Not Ready;
* Ready;
* In Progress;
* Blocked;
* Complete.

V1 does not require:

* Critical Path Method;
* float;
* resource levelling;
* complex multi-calendar scheduling;
* sophisticated automatic rescheduling;
* enterprise-scale Gantt functionality.

A richer visual Gantt may be considered after the operational model is proven.

---

# 29. Schedule + Site Truth — DIRECTION (original: DECIDED)

The product should connect:

> **What was supposed to happen**

with:

> **What actually happened.**

Example:

Schedule:

> Waterproofing planned Monday.

Site Log:

> Plumbing issue prevented waterproofing from starting.

Forey may propose:

> Mark Waterproofing as Blocked?

The authorised user confirms.

The user should not have to manually maintain both a Site Diary and an entirely separate Schedule whenever the same site event affects both.

---

# 30. Progress — DIRECTION (original: DECIDED DIRECTION)

Forey may use confirmed:

* Site Logs;
* Tasks;
* Checklists;
* inspections;
* photographs;
* labour records;

to suggest project progress.

AI may suggest:

> Plumbing rough-in appears complete.

AI does not automatically make authoritative completion decisions where the decision matters commercially.

An authorised user confirms progress.

---

# 31. Payment Schedule — DIRECTION (original: CORE CAPABILITY; downgraded per Amendment 1)

Each Job may have a Payment Schedule based on the relevant contract/commercial arrangement.

Examples:

* Deposit;
* Demolition;
* Frame;
* Lock-up;
* Fixing;
* Completion.

A Payment Milestone may link to:

* Stage;
* Work Packages;
* Tasks;
* evidence;
* inspections;
* other completion conditions.

---

# 32. Progress → Invoice — DIRECTION (original: TARGET WORKFLOW; downgraded per Amendment 1)

The long-term administrative flow should become:

> Site Fact → Progress Confirmed → Payment Milestone Recognised → Invoice / Claim Prepared → Authorised Send

This replaces unnecessary administrative handoff such as:

> Site manager reports progress → office administrator interprets report → invoice manually recreated.

Initial product behaviour:

> **Prepare where explicit deterministic conditions are met; require human confirmation before sending material commercial documents.** (Gate conditions per Amendment 5 — deterministic, not confidence scores.)

Fully automatic sending is an autonomy decision that must be earned later.

---

# 33. Commercial Cost Control — DIRECTION (original: TARGET CAPABILITY)

Forey should eventually connect:

* Budget;
* Expenses;
* Labour;
* accepted trade/supplier costs;
* ETC;
* Forecast;
* Variations;
* Rework.

The product should ultimately answer:

* What have we spent?
* What have we already committed to spend?
* What remains?
* What is the expected final cost?
* What margin remains?
* Which numbers are unreliable?

---

# 34. Cost Packages — DIRECTION (original: DIRECTION DECIDED; downgraded per Amendment 1)

Forey requires a residential construction cost/work-package structure broad enough for Class 1 / Class 1a projects.

This should cover major areas such as:

* Preliminaries;
* consultants and approvals;
* demolition;
* excavation;
* drainage;
* foundations;
* concrete;
* structural steel;
* masonry;
* framing;
* carpentry;
* roofing;
* roof plumbing;
* external envelope;
* windows and doors;
* plumbing;
* electrical;
* mechanical;
* waterproofing;
* plasterboard / linings;
* tiling;
* joinery;
* stone;
* painting;
* flooring;
* fixtures;
* appliances;
* external works;
* landscaping;
* cleaning;
* defects;
* rework;
* contingency;
* unclassified.

The final detailed taxonomy is an implementation decision, not a Charter-level requirement.

---

# 35. Accepted / Committed Cost — DIRECTION (original: TARGET CAPABILITY)

Forey should record major project costs that the builder has already accepted or ordered even when the full supplier/subcontractor invoice has not yet arrived.

Example:

Plumbing quote accepted: $25,000
Expenses received so far: $12,000
Remaining accepted cost: $13,000

This helps prevent the project from appearing artificially profitable simply because an invoice has not arrived.

---

# 36. ETC and Forecast — DIRECTION (original: TARGET CAPABILITY; downgraded per Amendment 1)

Eventually the base project forecast should conceptually derive from:

> **Actual Cost + Remaining Accepted/Committed Cost + Remaining Uncommitted ETC**

ETC maintenance should be exception-driven rather than requiring the owner to re-enter every project category continuously.

Forecast should retain historical snapshots so Forey can later compare:

* previous prediction;
* later prediction;
* final outcome;
* reason for change.

---

# 37. Data Confidence — DIRECTION (original: PRODUCT PRINCIPLE; downgraded per Amendment 7)

Forey must not present uncertain financial information with false precision.

Forecasts may use states such as:

* Reliable;
* Qualified;
* Low Confidence;
* Unavailable.

Confidence should reflect conditions such as:

* missing cost information;
* stale ETC;
* unmatched items;
* incomplete Budget;
* unresolved Variations.

Confidence is not merely a dashboard feature.

It determines how financial information is rendered and interpreted.

---

# 38. Variation — DECIDED (Potential Variation concept and builder-determination principle; full lifecycle: DIRECTION) (original: CORE COMMERCIAL CASE)

Registry: DEC-ONTOLOGY-001, DEC-AI-BOUNDARY-001

Forey should help prevent additional work from being:

* forgotten;
* poorly documented;
* priced late;
* approved late;
* completed without sufficient commercial follow-up.

Variation workflow may eventually include:

> Potential → Scope Review → Draft → Ready to Send → Sent → Signed → Work Authorised → Invoiced → Paid → Closed

AI may identify a **Potential Variation**.

The builder determines:

* whether it is actually a Variation;
* what it should cost;
* contractual entitlement;
* whether to proceed.

---

# 39. Rework — DIRECTION (original: SEPARATE COMMERCIAL CASE; Ontology v2+ per Amendment 2)

Rework is not simply another Variation status.

Rework answers different questions:

* What went wrong?
* Who performed the original work?
* What caused the problem?
* Who is responsible?
* What did rectification cost?
* Is there a Backcharge?
* Was the Backcharge recovered?
* What cost did the builder ultimately absorb?

Forey should be capable of linking:

* Site Log;
* Issue;
* Photos;
* Files;
* Labour;
* Expenses;
* rectification evidence;

into a Rework / Backcharge case.

Responsibility remains subject to human confirmation.

---

# 40. Late Capture — DIRECTION (original: IMPORTANT METRIC; capture-timing recording is active via §43)

If a commercial change is identified after:

* work has started;
* work has completed;
* cost has already been incurred;

Forey should preserve that fact.

The objective is not simply to count Variations.

Over time Forey should help improve:

* pre-cost capture;
* approval-before-work;
* speed from event to commercial action;
* recovery of additional work.

---

# 41. Files + AI — DIRECTION (original: FUTURE INTELLIGENCE)

Once Forey contains project files, AI may eventually answer questions such as:

> What is the kitchen window size?

or:

> Is the shower niche mentioned in the accepted scope?

Where practical, AI-generated answers should show source evidence.

Forey should prefer:

> **"Here is what the relevant document says."**

over:

> **"AI has decided the contractual answer."**

---

# 42. Initial Wedge — DECIDED (subordinate to §62 per Amendment 2)

Registry: DEC-CAPTURE-001

The initial wedge is:

> **Low-friction Site Capture → Structured Site Truth**

This is broader than "Variation Capture" but remains a narrow product-behaviour test.

The first question Forey must answer is:

> Can builders capture real site facts naturally enough that Forey becomes a reliable source of project truth?

Slice-1 confirmed outputs from one Capture are limited by Amendment 2 to:

* Site Log Fact;
* Task;
* Potential Variation;
* (Issue only if required for a coherent vertical workflow).

The builder should not be required to manually enter the same fact into multiple modules.

---

# 43. First Commercial Proof — DECIDED

Registry: DEC-GATE-H3-001

Although the Wedge is Site Truth, the first primary commercial proof remains:

> **Variation Protection**

Forey should demonstrate that natural site capture can surface a commercially material potential Variation that had a reasonable chance of being:

* forgotten;
* delayed;
* poorly documented;
* commercially mishandled;

without Forey.

No arbitrary fixed AUD amount is currently required.

Real cases should record:

* estimated value;
* quoted value;
* approved value;
* actual cost;
* recovered value;
* capture timing.

The first several cases will establish the appropriate commercial benchmark.

---

# 44. Active vs Passive AI Detection — DECIDED

Registry: DEC-DETECTION-001

When a builder deliberately records a potentially important site event, Forey may favour **recall**.

Missing a deliberately captured client change is worse than asking for one unnecessary confirmation.

For passive background detection over ordinary project records, Forey should favour **precision**.

Passive AI must not produce a high-volume false-alert inbox.

---

# 45. Project Memory — DIRECTION (original: DIRECTION DECIDED; the personalization boundary itself is DECIDED via DEC-PERSONALIZATION-001)

Forey should become better at understanding each project and organisation over time.

This initially means contextual memory rather than per-user model retraining.

Relevant context may include:

* people;
* trades;
* suppliers;
* locations;
* common shorthand;
* recurring terminology;
* known Scope Items;
* project files;
* previous confirmations.

Example:

> "chippy说 waterproofing 要等 Reece 的货"

Forey may understand the sentence better because:

* chippy has known meaning;
* Reece is a known supplier;
* waterproofing is a known Work Package;
* the project Schedule provides context.

---

# 46. Attention Budget — DECIDED (original: PRODUCT CONSTRAINT)

Registry: DEC-ATTENTION-001

Forey cannot solve admin burden by creating a new confirmation burden.

The ICP commonly manages approximately five concurrent Jobs and may approach ten.

Therefore:

> **Human attention is a constrained product resource.**

Forey should:

* combine review queues;
* prioritise material items;
* suppress low-value noise;
* minimise duplicate confirmation;
* use exceptions rather than full-table maintenance.

The owner should not need to spend the day maintaining Forey for Forey to remain accurate.

---

# 47. Action / Today — DIRECTION (original: TARGET END STATE)

As Forey earns enough structured project truth, it should progressively reduce information into:

> **Today — the things that need you.**

Examples:

* overdue Task;
* blocked Work Package;
* Variation awaiting pricing;
* Variation awaiting client approval;
* payment milestone ready for review;
* stale Forecast input;
* critical project document requiring attention.

This screen is an earned outcome of reliable project data.

It should not be simulated before the underlying information is trustworthy.

---

# 48. Client Portal — NOT NOW (the "not required" boundary is DECIDED via DEC-BOUNDARY-PORTAL-001)

Registry: DEC-BOUNDARY-PORTAL-001

Forey's core workflow must not depend on clients creating accounts.

Clients may receive secure external experiences for:

* Quotes;
* signatures;
* Variations;
* invoices;
* documents.

A richer Client Portal may be reconsidered later.

---

# 49. Subcontractor Portal — DECIDED (not required) (original: NOT REQUIRED)

Registry: DEC-BOUNDARY-PORTAL-001

Forey's core product should not depend on subcontractors adopting Forey.

The builder must be able to operate Forey while subcontractors continue using their existing communication and invoicing methods.

---

# 50. Accounting — DECIDED boundary (original: PRODUCT BOUNDARY)

Registry: DEC-BOUNDARY-ACCT-001

Forey does not become general accounting software.

It should not initially attempt to reproduce:

* bank reconciliation;
* BAS;
* general ledger;
* payroll;
* full accounts payable;
* full accounts receivable accounting.

Accounting integrations may be added later where economically and operationally justified.

They are not prerequisites for Forey to work.

---

# 51. Full Gantt / CPM — NOT NOW

Forey requires scheduling.

Forey does **not initially require**:

* Primavera-level planning;
* enterprise Critical Path analysis;
* detailed resource levelling;
* complex baseline management.

The strategy is:

> **Low-maintenance operational scheduling first.**

Detailed scheduling capability may increase only if actual users need it.

---

# 52. Explicit Not-Now List — NOT NOW

The current strategy does not authorise immediate development of:

* automated drawing takeoff;
* full estimating database;
* supplier price-book system;
* enterprise Gantt / CPM;
* general accounting;
* payroll;
* complete internal chat;
* mandatory Client Portal;
* mandatory Subcontractor Portal;
* automatic contractual adjudication;
* automatic liability determination;
* autonomous Variation approval;
* autonomous commercial pricing;
* autonomous high-value client invoicing;
* cross-builder benchmarking;
* worker ranking;
* broad autonomous commercial control.

These items may only re-enter active planning when evidence justifies reopening them.

---

# 53. Validation Hypothesis H1 — Capture — DECIDED (as amended by Amendment 4)

Registry: DEC-GATE-H1-001

Forey must first prove that low-friction Capture fits real construction behaviour.

Initial test:

* use across real active projects;
* approximately 3 consecutive weeks;
* no artificial test-only behaviour;
* behavioural floor: at least 3 meaningful Captures per active site day across the portfolio, unless the day genuinely produces fewer recordable project facts;
* confirmation items handled within 48 hours at an initial target rate of at least 80%.

The Capture-count metric is a behavioural floor, not a productivity target. Artificial low-value Captures created only to satisfy the metric do not count. The friction log remains more important than forcing usage.

Track friction such as:

* chose not to record;
* started then abandoned;
* AI correction too difficult;
* wrong Job;
* wrong person/trade/location;
* repeated confirmation became annoying;
* easier to remember than use Forey.

If the founder will not naturally use Capture, the problem is treated as a product-design failure, not lack of user discipline.

---

# 54. Validation Hypothesis H2 — Structured Site Truth — DECIDED (as amended by Amendment 4)

Registry: DEC-GATE-H2-001

Forey must demonstrate that real:

* Chinese;
* English;
* mixed Chinese/English;
* construction shorthand;
* supplier names;
* trade terminology;

can be transformed into useful structured records.

Because mixed-language support is a product capability rather than the ICP definition, pure-English samples are mandatory in the evaluation corpus.

Evaluation should measure more than transcription accuracy.

Important measures include:

* Site Fact recall;
* Site Fact precision;
* Task precision;
* Potential Variation recall;
* Potential Variation precision;
* Direct Confirm Rate;
* Correction Time;
* wrong location/trade/person rate;
* commercially important missed-event rate.

Wrong-Job **suggestion** rate is measured in capture attribution telemetry (H1/product measurement, per DEC-JOB-ATTR-001); it is not scored by the extraction baseline.

Real Raw Evidence should be preserved from the beginning as a reusable evaluation set.

---

# 55. Validation Hypothesis H3 — Commercial Proof — DECIDED (as amended by Amendment 4)

Registry: DEC-GATE-H3-001

Forey should demonstrate at least one real commercially meaningful event where:

> low-friction capture materially improved the handling of a potential Variation.

Founder environment: 6 weeks of representative usage. If no suitable commercially meaningful Variation event occurs, this alone does not falsify the wedge because Variation frequency may be sparse.

Seed-user protection: then test on at least one appropriate seed-builder project for a further 4 weeks.

If no meaningful evidence appears across both environments, reopen the Variation-protection commercial hypothesis.

Do not allow Site Truth alone to continue indefinitely without commercial validation.

Failure should lead to re-examining the Wedge, not immediately adding more features.

---

# 56. Validation Hypothesis H4 — Action — DECIDED (provisional threshold; recalibrate before this Horizon begins)

Registry: DEC-GATE-H4-001

Once Action is sufficiently implemented, track whether Forey-recommended actions are actually useful.

An initial provisional threshold is:

> less than approximately 30% adoption/action over a representative four-week period triggers reconsideration.

The exact threshold must be recalibrated before this Horizon begins.

---

# 57. Validation Hypothesis H5 — Admin Automation — DECIDED (provisional threshold; recalibrate before this Horizon begins)

Registry: DEC-GATE-H5-001

When Forey starts generating:

* Quotes;
* Variation drafts;
* Site Logs;
* invoices;
* other commercial documents;

the majority should require minimal rewriting.

Initial provisional criterion:

> at least 50% should be usable directly or with light editing.

Poor results should trigger redesign of the generated artifact rather than automatic expansion into more document types.

---

# 58. Autonomy — EARNED, NOT ASSUMED — DECIDED (as amended by Amendment 5)

Registry: DEC-AUTONOMY-001

Forey does not begin with broad autonomous commercial actions.

Future autonomy should be granted one action category at a time.

Autonomy requires:

* reliable underlying Truth;
* deterministic conditions where appropriate;
* adequate evidence;
* explicit user permission;
* strong audit history;
* low correction/reversal rate (provisional threshold: below 2% per Amendment 5).

Humans should be able to override system Gates with a recorded reason where operationally necessary.

---

# 59. Evidence Register

## Claim

Small builders lose meaningful money because Variations are missed, documented late or commercially mishandled.

**Current status:** Founder firsthand evidence; broader market prevalence still requires validation.

## Claim

Small builders resist continuously maintaining traditional PM software.

**Current status:** Founder firsthand experience + anecdotal evidence; broader market requires validation.

## Claim

Mixed Chinese/English capture creates meaningful differentiation.

**Current status:** Hypothesis.

## Claim

Builders will pay for margin protection and reduction of administrative effort.

**Current status:** Hypothesis.

These assumptions define the first external customer validation agenda.

---

# 60. Strategic Reopen Conditions

This Charter may be reopened when evidence shows, for example:

* builders naturally prefer structured manual forms over Capture;
* Site Capture does not produce commercially useful Truth;
* Variation protection is too infrequent to generate sufficient value;
* users will not maintain even the lightweight Schedule;
* Quote-first workflow materially outperforms direct Job creation;
* customers strongly require detailed Gantt capability;
* clients or subcontractors must participate directly for the workflow to succeed;
* human confirmation cost becomes unsustainable;
* document-generation acceptance remains poor;
* integrations become more valuable than native functionality;
* another commercial problem consistently dominates Variation leakage.

Reopening a decision is not failure.

Continuing to defend a disproven decision is failure.

---

# 61. Build Principle After Charter Sign-off — DECIDED

Registry: DEC-BUILD-001

Once this Charter is signed:

> **Stop expanding the Vision and move into product architecture and sequencing.**

Do not attempt to build the complete Target End State.

Every engineering slice must:

1. solve a real user problem independently;
2. preserve the long-term data architecture;
3. be operable by the existing development capacity;
4. have a defined validation result;
5. have an explicit stopping point.

---

# 62. First Engineering Focus — DECIDED

Registry: DEC-SLICE-001

The first product slice should centre on:

> **Low-friction Capture → Site Log Event → AI Candidate → Human Confirmation → Structured Site Truth**

Supporting foundations should include only what is necessary for this loop, such as:

* Jobs;
* project members;
* project locations;
* raw text / voice / image evidence;
* Site Log Events;
* AI extraction;
* confirmation;
* audit history;
* basic Tasks / Issues where required by the vertical slice.

The first release does **not** need to implement every downstream object described in this Charter.

---

# 63. Product End-State in One View — DIRECTION (vision)

Forey ultimately aims to turn this:

> scattered conversations + memory + photos + paperwork + disconnected systems

into this:

> **one connected project truth**

where:

**Quote defines the original promise.**

**Scope defines what was included.**

**Schedule defines what was supposed to happen.**

**Site Log records what actually happened.**

**Tasks define what needs action.**

**Files preserve project evidence.**

**Progress connects reality to the plan.**

**Variation and Rework explain commercial changes.**

**Costs and Forecast explain where the money is going.**

**Payment Schedule connects delivered work to revenue.**

**AI reduces the administration required to keep all of this current.**

The desired operating outcome is:

> **Capture what happens. Keep the team aligned. Turn project facts into action.**

And the governing principle remains:

> **Forey does the paperwork. The builder makes the decision.**

---

## Part C — Final Sign-off Amendments (v1.0)

The Charter is approved subject to the following final amendments. Where an amendment conflicts with the body, the amendment prevails; the Decision Registry reflects post-amendment state.

### Amendment 1 — Authority Levels

All product statements in the Charter use only three authority levels: **DECIDED**, **DIRECTION**, **NOT NOW** (definitions in Part 0).

Authority corrections — downgraded from DECIDED to DIRECTION:

* Lightweight Estimating;
* Forey-native Quoting / Proposal;
* Forey-native Electronic Signature;
* Operational Schedule structure beyond requirements needed by the active slice;
* Payment Schedule;
* Cost Package taxonomy;
* detailed Forecast / ETC implementation;
* Progress-to-Invoice automation.

The following remain DECIDED because they constrain current architecture: ICP; direct Job creation must remain possible; Quote-first must never become a prerequisite; Evidence → Candidate → Confirmation → Truth; human confirmation for material commercial truth; low-friction Capture; Site Log as project fact/evidence layer; occurred_at and created_at remain distinct; original evidence and confirmation deltas are retained; Job attribution is explicitly confirmed; Client Portal is not required; Subcontractor participation is not required; Forey does not become accounting software; automated Drawing Takeoff is NOT NOW; enterprise Gantt / CPM is NOT NOW; broad commercial autonomy is NOT NOW.

Future capability descriptions remain in the Charter as strategic reference, but their authority is determined by these labels.

### Amendment 2 — Initial Ontology Narrowed

Section 42 is subordinate to the Slice-1 definition in Section 62. The initial Capture ontology is intentionally narrow.

Slice-1 confirmed outputs — a Capture may initially produce only: **Site Log Fact; Task; Potential Variation.** Issue may be included only if required to complete a coherent vertical workflow without duplicating another object.

Explicitly Ontology v2 or later: Progress Candidate; Rework Candidate; Delay Candidate; EOT Candidate; Forecast Candidate; other commercial/event classifications.

Reasons: (1) **Human attention** — with approximately five normal active Jobs and periods approaching ten, every additional Candidate class increases possible confirmation volume; the product must not solve administrative burden by creating a classification-review burden. (2) **Evaluation cost** — every Candidate class creates a new annotation problem (definition; positive examples; negative examples; ambiguous cases; gold labels; precision/recall measurement; correction tracking). Ontology expansion requires evidence that the existing Capture loop is working.

The active Slice remains: **Raw Capture → Site Log Fact → optional Task / Potential Variation → Human Confirmation → Truth.**

### Amendment 3 — Job Attribution Is Explicit in Slice 1

Wrong-Job attribution is a trust-critical failure. In Slice 1, Forey must not rely on AI to determine authoritative Job attribution.

Capture behaviour: Forey may preselect a likely Job using current navigation context; may suggest a Job using location/GPS where permission exists; may later use contextual intelligence to improve the suggested default. However, **the authoritative Job must be explicitly selected or confirmed by the user.** AI inference alone cannot assign the final Job in Slice 1. Where the user is already inside a Job Workspace, that Job may act as the explicit context. Objective: approximately one tap where possible without sacrificing attribution certainty.

### Amendment 4 — Validation Time Boxes Restored

**H1 — Capture Behaviour.** Duration: 3 consecutive weeks; environment: real active projects; normal expected scale: approximately 5 active Jobs where available; minimum activity target: at least 3 meaningful Captures per active site day across the portfolio, unless the day genuinely produces fewer recordable project facts; at least 80% of confirmation items resolved within 48 hours. The Capture-count metric is a behavioural floor, not a productivity target; artificial low-value Captures do not count; the friction log remains more important than forcing usage.

**H2 — Extraction / Structured Site Truth.** The baseline evaluation corpus must not validate only the founder's personal speech patterns. It must contain a meaningful mixture of English; Chinese; mixed Chinese/English; construction shorthand; trade names; supplier names; natural site-language fragments. Because mixed-language support is a product capability rather than the ICP definition, **pure-English samples are mandatory.** Evaluation includes: Site Fact recall; Site Fact precision; Task precision; Potential Variation recall; Potential Variation precision; Direct Confirm Rate; Correction Time; Wrong Job rate; wrong location/trade/person rate; commercially important missed-event rate.

**H3 — First Commercial Proof.** The commercial proof remains Variation protection. Founder environment: 6 weeks of representative usage; if no suitable commercially meaningful Variation event occurs, this alone does not falsify the wedge because Variation frequency may be sparse. Seed-user protection: then test on at least one appropriate seed-builder project for a further 4 weeks. If no meaningful evidence appears across both environments, reopen the Variation-protection commercial hypothesis. Do not allow Site Truth alone to continue indefinitely without commercial validation. The private list of 3–5 seed builders must not be stored in the repository; it is maintained separately as private founder/customer-development material.

### Amendment 5 — Autonomy and Deterministic Gates

Any future material automated action must be governed by explicit, auditable conditions. **AI confidence scores alone are not sufficient gating criteria.** For example, a future Ready to Claim state should rely on conditions such as: required milestone confirmed; required evidence attached; relevant human confirmation completed; no defined blocking condition remains. Forey may use AI to surface supporting information, but the gate itself should be deterministic wherever the underlying business rule allows it.

Provisional autonomy threshold: for any action class allowed to execute autonomously, post-action human correction / reversal rate must remain below **2%**. If the rate exceeds 2% over a representative sample: autonomous permission for that action class is withdrawn; the action returns to confirmation-required mode; the failure is reviewed before autonomy may be restored. The 2% threshold is provisional and must be recalibrated before H5 begins.

### Amendment 6 — External System Compatibility (post-signing, 2026-08-09)

**DECIDED principle** (registry: DEC-BOUNDARY-EXT-001):

Forey may provide native quoting, invoicing, signature and document-generation capabilities, but adoption of those native capabilities must never be a prerequisite for using Forey. Builders may continue using established external systems for accounting, invoicing, signatures and related execution. Forey retains the project and commercial state needed to know what should happen and whether it happened; external systems may remain the authoritative source for their specialist transaction records. Integrations may reduce duplicate administration where commercially justified, but Forey's core workflows must remain functional without paid third-party API integrations.

Positioning consequence: Forey does not become the centre by replacing every existing system; it becomes the centre by connecting project facts, actions and commercial state. This amendment generalises DEC-ENTRY-001 (Quote-first is never a prerequisite) and reinforces DEC-BOUNDARY-ACCT-001.

**DIRECTION design notes** (recorded to avoid future dead ends; not buildable until their Horizon opens):

* Forey distinguishes two objects. The **Billing Requirement / Claim** is Forey's own commercial-state object (payment stage, amount, ready-to-claim per deterministic conditions, per Amendment 5). The **Issued Invoice** is the financial document actually issued — Forey-native or external, carrying provider and external reference (e.g. Xero INV number). The two are never silently synchronised; external issuance is recorded by explicit human action, with `Mark as invoiced externally` as the minimum viable version.
* The same pattern applies to Variations (approved / invoiced / paid / outstanding tracked in Forey regardless of where the invoice was issued) and to Quotes (an existing signed quote or contract may be uploaded at Job creation; any scope extraction from such documents is its own Candidate → Confirmation problem with its own evaluation cost, and is not part of the Slice-1 extraction scope).
* Integration ladder: **Level 1** manual recording (baseline; must always work) → **Level 2** import/link (document, CSV or email in; Forey proposes a match to a stage or variation; the user confirms — the Evidence → Candidate → Confirmation spine applied to external documents) → **Level 3** API integration (only where commercially justified; reduces clicks, never determines whether the product works).
* Boundary watch: Forey records the builder's confirmation of commercial events (issued, paid) as project facts. It never performs bank reconciliation or derives payment state from financial feeds — that remains accounting territory per DEC-BOUNDARY-ACCT-001.

### Amendment 7 — Registry Coverage Rule (post-signing, 2026-08-09)

**Rule:** any Charter body heading labelled DECIDED must carry a `Registry:` mapping line naming one or more Decision Registry IDs. A DECIDED label without a registry mapping is invalid. The drift check enforces this (body-coverage check), closing the gap where body text labelled DECIDED could drift outside machine enforcement while CI stayed green.

**New registry entries created for current-phase coverage:** DEC-SITELOG-001, DEC-SITELOG-META-001, DEC-LOCATION-001, DEC-TASK-001, DEC-GATE-H4-001, DEC-GATE-H5-001, DEC-BUILD-001, DEC-SLICE-001. (H4/H5 are registered as signed provisional gates; §61/§62 are registered because they are the most operative sections of the current phase.)

**Downgraded to DIRECTION (not registered, not current-phase binding):** §21 Team Visibility; §25 File Management (evidence attachment storage itself remains DECIDED via DEC-EVIDENCE-001); §37 Data Confidence (the principle attaches to financial surfaces, which are all DIRECTION).

**H2 metric relocation:** wrong-Job rate is removed from the extraction-baseline metric set. Under DEC-JOB-ATTR-001 the extractor consumes the user-confirmed Job as context and never predicts it, so the extraction evaluation structurally cannot measure wrong-Job. The metric becomes **wrong-Job suggestion rate**, measured in capture attribution telemetry (H1/product measurement). DEC-GATE-H2-001's registry text is revised accordingly and re-acknowledged in PRODUCT.md through the drift mechanism.

**Mappings to existing entries** (rather than minting duplicates): §5 Thesis → DEC-CAPTURE-001 + DEC-AI-BOUNDARY-001; §20 Events → DEC-CAPTURE-001 + DEC-SITELOG-001; §38 Variation → DEC-ONTOLOGY-001 + DEC-AI-BOUNDARY-001. All mappings are visible as `Registry:` lines throughout Part B.

### Amendment 8 — Existing-Maintain Status for Pre-Charter Functionality (post-signing, 2026-08-09)

Context: the Charter was drafted greenfield, but the repository already ships working functionality (expenses, labour, budget, GST and margin views) that real users rely on and that maps to areas the Charter records as DIRECTION or NOT NOW. Read literally, the conflict rule would turn every bugfix on those surfaces into a governance STOP, producing either stop-loops or silent erosion of the rule — both worse than deciding explicitly. This was a missing state, not a strategy error.

**Rule (registry: DEC-EXISTING-001):** shipped, user-relied surfaces in DIRECTION / NOT NOW areas are maintained, not extended. Permitted under the light gate: bug fixes, regression fixes, security and compatibility fixes, test repair, correctness fixes, small UX repairs, necessary maintenance refactors, and minimal schema changes required to restore existing behaviour. Not permitted as maintenance: new business workflows, domain scope expansion, large new features, whole-module redesign, or removal of existing capability merely because the Charter does not emphasise it. New capability requires prior promotion through PRODUCT.md or an explicit founder ruling. The audit assigns every module exactly one of: slice-1-foundation, existing-maintain, untouched-for-now, conflicts-with-decision.

Naming note: the status is deliberately called existing-maintain rather than legacy-maintain — "legacy" invites agents to assume scheduled deprecation. Deprecation is a founder decision, never implied.

Boundary note: existing-maintain is not promotion. GST and margin views remain project cost views; DEC-BOUNDARY-ACCT-001 continues to forbid drift into general accounting. Worked examples: an Expenses bug → fix under light gate; a Labour iOS regression → fix under light gate; adding a full AP approval workflow to Expenses → requires promotion; adding a resource-scheduling system to Labour → requires promotion.


### Amendment 9 — Scoped Coverage / Applies-To (post-signing, 2026-08-09)

Full-coverage enforcement (Amendment 7 follow-up; review finding G1) is scope-aware, not global. Requiring PRODUCT.md to acknowledge every DECIDED entry regardless of horizon would gradually rebuild the Charter inside PRODUCT.md and destroy the Charter/PRODUCT layering — the Charter is strategic source of truth; PRODUCT.md is the implementation authority of the active slice only.

Mechanism: every registry entry carries `Applies-To:` routing metadata (global, slice-1, h3, h4, h5, …). Applies-To is not an authority level and is excluded from the normalized hash; a missing Applies-To defaults to global. PRODUCT.md declares `Binding Scope: global, slice-1`. CI runs the drift check with --require-full-coverage: any in-scope DECIDED entry unreferenced in PRODUCT.md fails the gate; out-of-scope entries (currently DEC-GATE-H4-001, DEC-GATE-H5-001) are deferred until their horizon is promoted, at which point extending the Binding Scope forces their acknowledgement.

Deliberate scoping call: DEC-GATE-H3-001 is scoped slice-1, not deferred — Amendment 4 defines H1→H2→H3 as one continuous validation arc and H3's clock runs during Slice-1 usage.

Registry IDs freeze at repository landing. The rename in Amendment 8 (draft DEC-LEGACY-001 → DEC-EXISTING-001) was the last free in-place rename; after landing, a rename requires a new entry plus a tombstone note.

### Amendment 10 — Fact-category metadata inside the closed ontology (2026-08-15)

DEC-ONTOLOGY-001's registry text is amended to state explicitly that the closed list applies to **top-level Candidate types**, and that optional classification metadata inside a Site Log Fact (a `fact_category` value such as attendance, progress, site_condition, quality, delivery, inspection, safety, delay, incident, instruction, weather, other) is **not** a Candidate type and is permitted, provided it creates no separate workflow, module or additional confirmation object. Rationale: Amendment 2's constraints target Candidate classes because each class multiplies confirmation volume and annotation cost; a tag on an already-confirmed atomic fact does neither. A complex capture is split into atomic facts rather than given a compound category; generic `issue` is not a category because quality/safety/delivery/delay issues would overlap. Expense and Labour remain outside the Slice-1 Candidate ontology in their existing structured modules. The amended registry text is re-acknowledged in PRODUCT.md through the drift mechanism in the same commit.

### Repository Authority Split

The full Charter is committed as a strategic reference document at `docs/product/forey-charter-v1.0.md`. It contains DECIDED constraints, future DIRECTION, NOT NOW boundaries, hypotheses, validation criteria and reopen conditions. It is not itself the implementation specification.

A separate binding product document, `docs/product/PRODUCT.md`, contains only the active Slice-1 decisions. Claude Code and planning/review agents treat **PRODUCT.md as current implementation authority** and **the Charter as strategic context**, unless PRODUCT.md explicitly promotes a DIRECTION item into the active Slice.

---

**SIGNED — v1.0.** Strategy expansion pauses. The next question is no longer "What else could Forey build?" but: **Can Forey reliably turn low-friction real site input into trustworthy Site Truth without creating more administrative work than it removes?**