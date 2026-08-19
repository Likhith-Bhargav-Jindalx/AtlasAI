# Atlas Recording Form — v1 Specification

**Purpose:** the metadata an ops SME provides around each workflow recording in Atlas, so the library is queryable and can later feed the automation backlog.

**Design constraints for v1**
- Two branching keys only: `lob` → `action`
- Maximum 10 fields on any path
- Vocabulary matches the existing Project Codex planner, so the current rows migrate without re-tagging

**Sequence:** Section A before recording starts, Section B after the SME stops.

---

## Section A — Identify

Always shown. 7 fields.

| ID | Label | Type | Req | Options / source |
|---|---|---|---|---|
| `lob` | Line of business | select | Y | AR · EV · PA · PP · Reporting-Admin — **branch key 1** |
| `category` | Category | select | Y | Process (how to operate a system) · Scenario (how to handle a case) |
| `action` | Action | select | Y | option set determined by `lob`, see A.1 — **branch key 2** |
| `system` | System recorded in | select | Y | system master — see A.2 |
| `client_scope` | Applies to | multi-select | Y | client master + "Generic (all clients)" |
| `reuse_scope` | Reuse scope | select | Y | Client-specific · Generic (reusable across clients) |
| `workflow_name` | Title | text, auto-suggested | Y | pattern `ACTION – SYSTEM – vN`, editable |

### A.1 — `action` option sets

| `lob` | `action` options |
|---|---|
| AR | Status Check · Status Follow-up · Denial · Rejection Check · Payment Research |
| EV | Portal Verification · IVR Verification · Rep Call · Mismatch Resolution |
| PA | Auth Submission · Auth Follow-up · Clinical Upload · Auth Entry in PMS |
| PP | ERA Posting · EOB Manual Posting · Denial Posting · Unapplied / Recoupment |
| Reporting-Admin | Report Download · Report Generation · File Transfer · Master Data Update |

### A.2 — `system` master (seed values)

PMS/EHR: Exa · eCW · Athena
Clearinghouse: Waystar · Stedi
Multi-payer portals: Availity
Payer portals: Cigna · UHC · UMR · Integranet · Humana Military · Tricare For Life · Community Health Choice
Other: Medicare IVR · NPPES · Payer phone (no portal)

`system` is the thing a person or bot logs into. It is not the payer.

---

## Section B — Context

Conditional. Each field appears only when its show-when condition is true. Maximum 6 conditional fields on any single path.

| ID | Label | Type | Req | Show when | Options / notes |
|---|---|---|---|---|---|
| `payer_scope` | Payer scope | select | Y | `lob` ∈ {AR, EV, PA, PP} | Payer-specific · Multi-payer portal · All payers |
| `payers` | Payer(s) covered | multi-select | Y | `payer_scope` ≠ All payers | payer master; multiple allowed |
| `other_systems` | Other systems touched | multi-select | N | `lob` ∈ {AR, EV, PA, PP} | system master, e.g. Exa + Waystar on a timely-filing check |
| `code` | Denial / rejection code | select | Y | `action` ∈ {Denial, Rejection Check, Denial Posting} | CARC/RARC master; free text allowed for clearinghouse rejections |
| `sub_reason` | Sub-reason | select | Y | `code` is a multi-use code (e.g. CO-16) | Missing NDC · Invalid taxonomy · Missing CLIA · Missing provider details · Missing description · Invalid group practice · Other |
| `resolution_path` | Resolution path | multi-select | Y | `action` ∈ {Denial, Rejection Check, Payment Research} | Corrected claim · Appeal · Refile to correct payer · Payer call · Write-off / adjustment · Bill to patient · Escalate internally · No action (wait) |
| `evidence_required` | Documents needed | multi-select | Y | `resolution_path` ∈ {Appeal, Corrected claim} | Medical records · Auth number · EOB · Proof of timely filing · W9 · None |
| `report_id` | Report / file name in source system | text | Y | `action` ∈ {Report Download, Report Generation} | |
| `date_param_logic` | Date range, relative to run date | text | Y | `action` ∈ {Report Download, Report Generation} | e.g. "prior calendar month" — **absolute dates rejected** |
| `output_destination` | Where the output lands | text | Y | `lob` = Reporting-Admin | path / SFTP / distribution list |
| `post_processing` | Manual work after download | text | Y | `lob` = Reporting-Admin | explicit "None" checkbox required if none |
| `scenario_description` | Scenario description | text | Y | always | one or two lines, as in the current planner |

---

## Field count by path

| Path | A | B | Total |
|---|---|---|---|
| AR – Status Check (Availity) | 7 | 4 | 11 |
| AR – Denial (CO-16 NDC) | 7 | 6 | 13 |
| AR – Rejection Check (Waystar) | 7 | 5 | 12 |
| EV – Portal Verification | 7 | 4 | 11 |
| Reporting – Report Download | 7 | 5 | 12 |

Sections are shown at different times; no single screen exceeds 7 fields.

---

## Migration from the current planner

| Existing column | Maps to |
|---|---|
| Video Title | `workflow_name` |
| Client | `client_scope` |
| LOB | `lob` |
| Category | `category` |
| Type | `action` — "Scenario" splits into **Status Follow-up** and **Payment Research** |
| Payor Name | `payers`, plus new `payer_scope` and `system` |
| Scenario description | `scenario_description` |
| — | new: `system`, `payer_scope`, `reuse_scope`, `code`, `sub_reason`, `resolution_path`, `evidence_required` |

Two clean-ups this forces on the existing 58 rows:

1. **Portal vs payer.** The Availity row covers nine payers on one portal; rows 1–2 are single-payer portals; the Waystar rejection rows have no payer at all. `system` + `payer_scope` + `payers` separates these. Blank Payor cells become impossible.
2. **"Scenario" is overloaded.** Rows 11–14 are status follow-ups; rows 38–46 are payment research. Different work, different automation path.

---

## Implementation notes

**Branching.** Only `lob` and `action` control visibility. No third level — if a case seems to need one, it is a new `action` value, not a nested question.

**Master data, not free text.** `system`, `payers`, `client_scope`, `code` resolve to IDs. This is what lets you ask "everything that touches Availity" when a portal change breaks a set of bots, or "everything for Molina" when a payer's rules change.

**Progressive disclosure.** Section A is a modal before the recorder starts. Section B opens on stop, while the workflow is fresh.

**Validation.**
- `payers` required unless `payer_scope` = All payers
- `sub_reason` required when `code` is on the multi-use list
- `date_param_logic` rejects absolute dates
- `post_processing` requires text or an explicit "None"

**Record states.** Draft → Published. Only Published feeds Codex.

---

## Deferred to v2 (do not build)

- Fork / decision tagging during recording
- Exceptions, volume, AHT and definition-of-done capture
- EV benefit-field checklist and PMS mismatch rules
- Trigger and write-back fields
- Reviewer / QC sign-off and Superseded state
- Parent-child variant linking (e.g. CO-29 recorded on Aetna with a TFL grid embedded)
