# Contract freeze review: owner sign-off

Status: **not ready to freeze.** Contracts are `1.1.0-draft`. Prepared by M4 on 2026-10-22 against the working tree of branch `m4/foundation` (base `20809e7`, uncommitted). This document records an audit. It changes no contract, schema, fixture, OpenAPI definition or generated model.

Authority is unchanged: the standalone JSON Schemas define wire shapes, and [REVIEW.md](REVIEW.md) defines runtime semantics and overrides conflicting legacy examples in [CONTRACTS.md](../CONTRACTS.md). A schema or fixture pass is **not** evidence of runtime safety.

## 1. Audit summary

1. **Endpoint coverage is complete but not specified.** All 27 operations in the [endpoint table](../CONTRACTS.md#L2024) exist in [openapi.yaml](openapi.yaml), with matching methods, paths and success codes. 13 request/response types used by those operations have no JSON Schema (§4). Every M2 run/approval endpoint returns an unspecified `Run`.
2. **Generated Pydantic models are not a validator.** They drop every `if/then` rule and both custom format rules. A probe of 21 schema-invalid mutations found **20 accepted by Pydantic** and rejected by the JSON Schema. Accepted cases include `redacted: true` with plaintext `text`, an `approve_step` command with no `approval_id`, and an R2 step with `retries: 1` and no postcondition (§3.2). If an agent route validates input only through these models, it accepts data the contract forbids.
3. **Illustrative spec types contradict REVIEW.md.**
   - `Run.pending_approval` ([CONTRACTS.md:1721](../CONTRACTS.md#L1721)) has no `approval_id`, `effect_hash` or expiry. REVIEW.md §Approvals 4–5 requires all three.
   - `RunCheckpoint` ([:1699](../CONTRACTS.md#L1699)) lacks the plan hash, frozen items, loop cursor and effect receipts that REVIEW.md §Workflow compilation requires.
   - `openapi.yaml` allows only `mode: supervised`; `RunMode` ([:1687](../CONTRACTS.md#L1687)) is `normal | supervised | dry_run`.
4. **The validator's sync check has a blind spot.** The legacy §5.2 command example ([CONTRACTS.md:2186](../CONTRACTS.md#L2186)) is never compared, because the indented fence of the pairing block above it breaks the regex in [validate.py](validate.py). That example fails `command.schema.json` with 6 errors. REVIEW.md overrides it, so the contract is not ambiguous, but the spec document is stale.
5. **CI had a real cross-OS defect, now fixed minimally.** `datamodel-codegen` writes CRLF on Windows and LF on Linux. Without `.gitattributes`, a contributor with `core.autocrlf=false` commits CRLF and the Linux drift check fails. I added a 3-line root [.gitattributes](../.gitattributes) for the generated paths and verified it (§7). GitHub and Linux execution remain **unverified**.
6. **The "13 gap types" figure is correct, but the earlier description was loose.** The 13 are the non-schema type names in the endpoint table (§4). 11 of them are marked `x-spec-gap` in `openapi.yaml`. The other 2, `StartRunRequest` and `ApproveStepRequest`, were drafted inline from REVIEW.md fields and are still unsigned.
7. **M3 confirmation for [web/src/types/contracts.ts](../web/src/types/contracts.ts) is pending.** M3 has not approved the file's location or content. The path is in M3-owned `web/` ([CONTRACTS.md:2867](../CONTRACTS.md#L2867)) but is named as an M4 output in [§8](../CONTRACTS.md#L2843).

## 2. Endpoint coverage matrix

Base `http://127.0.0.1:7777/api/v1`. Every operation declares Bearer auth (global `security`) and has 401/403 `GhostError` responses.
- **Table vs API:** success code from the spec table / codes declared in `openapi.yaml`.
- **Body / params:** request body or parameters in `openapi.yaml`.
- **Gap:** an unschematized type, or ⚠ a contradiction with the table or another source.

| Method | Path | Owner | operationId | Table vs API | Body / params | Gap |
|---|---|---|---|---|---|---|
| POST | /events/batch | M1 | ingestEvents | 200 / 200,409,422 | `RawEvent[]` 1–200 | ⚠ response `{ingested,duplicates}` follows REVIEW.md, not table `{ingested}`; no status for >1 MiB |
| GET | /settings/observation | M1 | getObservationSettings | 200 / 200 | — | `ObservationSettings` |
| PUT | /settings/observation | M1 | updateObservationSettings | 200 / 200,422 | `UpdateObservationSettings` | `UpdateObservationSettings` |
| GET | /candidates | M1 | listCandidates | 200 / 200 | **no** limit/cursor | `WorkflowCandidate`; ⚠ REVIEW.md says lists are paginated |
| POST | /candidates/{id}/dismiss | M1 | dismissCandidate | 200 / 200,404 | id | — |
| POST | /candidates/{id}/promote | M1 | promoteCandidate | 201 / 201,404,409 | id | — |
| GET | /workflows | M1 | listWorkflows | 200 / 200,422 | limit, cursor | cursor transport (A2) |
| GET | /workflows/{id} | M1 | getWorkflow | 200 / 200,404 | id | — |
| PUT | /workflows/{id} | M1 | updateWorkflow | 200 / 200,404,409,422 | `Workflow`; `If-Match` | — |
| POST | /workflows/{id}/validate | M1 | validateWorkflow | 200 / 200,404,422 | id | — |
| POST | /workflows/{id}/runs | M2 | startRun | 201 / 201,404,409,422 | `StartRunRequest`; `Idempotency-Key` | `StartRunRequest`, `Run`; ⚠ mode enum |
| GET | /runs | M2 | listRuns | 200 / 200,422 | status (string), limit, cursor | `Run`; `status` not tied to `RunState` |
| GET | /runs/{id} | M2 | getRun | 200 / 200,404 | id | `Run` |
| POST | /runs/{id}/approve | M2 | approveStep | 200 / 200,404,409,422 | `ApproveStepRequest` | `ApproveStepRequest`, `Run` |
| POST | /runs/{id}/reject | M2 | rejectStep | 200 / 200,404,409,422 | `ApproveStepRequest` | ⚠ table lists no body; REVIEW.md §Approvals 5 requires binding fields |
| POST | /runs/{id}/pause | M2 | pauseRun | 200 / 200,404,409 | id | `Run` |
| POST | /runs/{id}/resume | M2 | resumeRun | 200 / 200,404,409 | id | `Run` |
| POST | /runs/{id}/cancel | M2 | cancelRun | 200 / 200,404,409 | id | `Run` |
| GET | /guard/warnings | M2 | listGuardWarnings | 200 / 200 | **no** limit/cursor | `GuardWarning`; ⚠ pagination |
| POST | /guard/warnings/{id}/feedback | M2 | recordGuardFeedback | 200 / 200,404,422 | `GuardFeedbackRequest` | `GuardFeedbackRequest` |
| POST | /remote/pair/start | M2 | startPairing | 201 / 201 | — | `PairingSession` |
| GET | /remote/devices | M2 | listPairedDevices | 200 / 200,422 | limit, cursor | `PairedDevice` |
| DELETE | /remote/devices/{id} | M2 | revokePairedDevice | 204 / 204,404 | id | — |
| GET | /audit | M4 | queryAudit | 200 / 200,422 | from, to, limit, cursor | `AuditResponse` |
| POST | /data/delete | M4 | deleteData | 200 / 200,422 | `DataDeleteRequest` | `DataDeleteRequest` |
| POST | /stream/tickets | M4 | createStreamTicket | 201 / 201 | — (`Cache-Control: no-store`) | — |
| GET | /health | M4 | getHealth | 200 / 200 | — | `HealthStatus`; ⚠ auth assumed (A8) |

**Behavior required by REVIEW.md or CONTRACTS.md §4–5 with no endpoint:**
- **A1** WebSocket upgrade `GET /stream?ticket=` and the stream envelope fields `event_id` and `sequence` ([REVIEW.md §Local HTTP](REVIEW.md#local-http-streams-privacy-and-storage), [CONTRACTS.md:2120](../CONTRACTS.md#L2120)). No schema exists for the envelope or for any event payload.
- **A5** Desktop confirmation of a pairing claim. Steps 4–5 at [CONTRACTS.md:2163](../CONTRACTS.md#L2163) and REVIEW.md §Approvals 7 require it.
- **A6** Undocumented status codes: oversize batch (> 1 MiB) and rate limiting (`RATE_LIMITED` exists in the `GhostError` enum but has no HTTP status code).
- **A7** Origin rule. [CONTRACTS.md:2020](../CONTRACTS.md#L2020) allows `http://localhost:7777`; REVIEW.md requires the exact `http://127.0.0.1:7777`, with `localhost` only if configured. REVIEW.md wins, but §3 is stale.

I invented none of these. Each needs an owner decision (§5).

## 3. Generated model integrity

Outputs: [agent/ghost/contracts/](../agent/ghost/contracts/) (Pydantic v2) and [web/src/types/contracts.ts](../web/src/types/contracts.ts), both generated from the five schemas by [generate.py](generate.py). The 13 gap types are **not** generated (§4).

### 3.1 Correspondence

A mechanical walk of all 13 object schemas found property names, `required` lists and `additionalProperties: false` (as `extra='forbid'`) preserved, with 0 mismatches. Enum values and the TypeScript output were checked by reading. The previous pass reported that all 8 fixtures validate or fail as expected. That test only checks whole fixtures and missed the conditional-rule loss below.

### 3.2 Defects: Pydantic accepts data the schema rejects

Probe: 21 targeted mutations of the valid fixtures, each checked with `Draft202012Validator` (using `validate.py`'s format checker) and with the generated model.

| Lost rule (schema source) | Example accepted by Pydantic | Severity |
|---|---|---|
| Redaction invariants (`raw_event` `value.allOf`) | `redacted: true` with `text: "secret"`; `secret_omitted` with `redacted: false`; `shape: none` with text | **High**: privacy |
| Per-command required/allowed fields (`command` `payload.allOf`) | `approve_step` with no `approval_id`; `run_workflow` with no `workflow_id`; `get_status` with `run_id` | **High**: approval binding |
| Workflow safety conditionals (`WorkflowStep.allOf`) | R2 step with `retries: 1` or `post: []`; required step with `on_fail: skip`; loop with no `body` or with a `verb`; non-read step with `output_name` | **High**: execution safety |
| Locator and parameter conditionals | `by: testid` with no `value`; `string` parameter with numeric `default_value` | Medium |
| UTC `Z` timestamps (`validate.py` format; `command` `issued_at` pattern `Z$`) | `+05:30` and `+00:00` offsets (`AwareDatetime`) | Medium: replay window and ordering |
| `format: uuid` hyphenated form | 32 hex digits without hyphens | Low |
| Optional is not nullable | `verb: null`, `allow_input_fallback: null`, `url_params: null` | Low–medium |

Only `params: {"secret": ...}` was rejected by both, because Pydantic enforces `maxProperties: 0` through `max_length=0`.

**Not a generator bug to patch here.** `datamodel-codegen` cannot express `if/then` as field types. The resolution is a boundary rule (gate G3): validate untrusted input with the JSON Schema and `validate.py`'s format semantics, then use the models for typing.

### 3.3 Serialization pitfalls

- `DecisionRule.else` becomes `else_`. A default `model_dump()` emits `else_`, which does not match the wire key. `by_alias=True` restores an exact round trip of `workflow.valid.json` (verified).
- `WorkflowStep.input: str | float | bool | None` re-serializes integer `30` as `30.0`. The workflow hash and `effect_hash` must be computed with RFC 8785 JCS over the received JSON, not over `json.dumps(model_dump())`.

### 3.4 TypeScript output

`tsc --strict` passed in the previous pass; I did not rerun it. The conditional rules are lost here too: `WorkflowStep` is a 4-way intersection of identical object types and `PrePostCondition` a 2-way one, both `json2ts` artifacts of `allOf/if/then`. These are type-equivalent and only a stylistic concern. The TypeScript types are compile-time only and validate nothing at runtime. M3 must not treat them as input validation.

### 3.5 Stylistic only

The following are not defects; fixing them is optional cleanup.
- **Pydantic names:** `Type`, `Type1`, `Type2`, `Origin1`, `Origin2` and `OutputType.string__` come from untitled nested schemas. Fixing them needs `title`s in the schemas, which is a schema change requiring owner sign-off, or generator naming options.
- **Enum bases:** `command.py` uses `Enum` while the other files use `StrEnum`.

## 4. Gap register (the 13 types)

**How the list was derived.** I parsed the type names from the request and response columns of the [endpoint table](../CONTRACTS.md#L2024), lines 2026–2052, and excluded `RawEvent` and `Workflow`, which have schemas. That yields exactly 13. It matches the 11 `x-spec-gap` components in `openapi.yaml` plus the 2 inline drafts. No type was added to reach the count.

Evidence levels:
- **TS** = illustrative TypeScript in the spec.
- **Ex** = example JSON only.
- **Name** = name only, no shape anywhere.

| # | Type | Evidence | Owner (approver) | Cross-owner | Unresolved decision |
|---|---|---|---|---|---|
| 1 | ObservationSettings | Name ([:2027](../CONTRACTS.md#L2027)); partial payload in `observation.status` event ([:2138](../CONTRACTS.md#L2138)) | M1 | M3 (onboarding UI) | State, allowlist (origins, fields, values), excluded apps, clipboard default. Observation consent must be a separate field from LLM-upload consent (REVIEW.md §Local HTTP). |
| 2 | UpdateObservationSettings | Name ([:2028](../CONTRACTS.md#L2028)) | M1 | M3 | Pause/resume only, or also allowlist edits; whether updates are partial; rejecting unknown fields. |
| 3 | WorkflowCandidate | TS ([:465](../CONTRACTS.md#L465)) | M1 | M3 | Convert to schema. Field names differ from the OmniRoute schema (`expression` vs `expr`); `ActionVerb` needs a schema; status enum. |
| 4 | StartRunRequest | Ex ([:2058](../CONTRACTS.md#L2058)); REVIEW.md §Workflow compilation; inline in OpenAPI | M2 | M3 | ⚠ Allowed `mode` values: OpenAPI allows `supervised` only, `RunMode` lists `normal`/`dry_run`, §6 says "Trusted mode". |
| 5 | Run (+ `RunState`, `RunMode`, checkpoint, pending approval) | TS ([:1706](../CONTRACTS.md#L1706)), Ex ([:2070](../CONTRACTS.md#L2070)) | M2 | **M3** (console), M4 (store) | ⚠ `pending_approval` lacks binding fields and expiry; checkpoint lacks the fields REVIEW.md requires; how loop path is represented in step records. |
| 6 | ApproveStepRequest | Name ([:2039](../CONTRACTS.md#L2039)); fields in REVIEW.md §Approvals 4–5; inline in OpenAPI | M2 | **M3** (approval UI) | Confirm the 4 fields and their reuse for `reject`. The `effect_preview` object needs its own schema (A3). |
| 7 | GuardWarning | TS ([:1831](../CONTRACTS.md#L1831)) | M2 | M3 | Schema; `evidence.context` must not carry secrets (redaction rule); confidence threshold semantics. |
| 8 | GuardFeedbackRequest | TS ([:1855](../CONTRACTS.md#L1855)) | M2 | **M1** (rules) | Whether `rule_updated` + `new_rule_expression` may change a decision rule. If so, it must use the restricted grammar and reset trust (REVIEW.md §Workflow compilation). |
| 9 | PairingSession | Ex ([:2169](../CONTRACTS.md#L2169)) | M2 | **M3** (PWA) | ⚠ The example's QR payload carries only a 6-digit `code`. REVIEW.md §Approvals 7 requires a high-entropy one-use capability; the code is only a display hint. |
| 10 | PairedDevice | Name ([:2047](../CONTRACTS.md#L2047)) | M2 | M3 | Fields: device id, name, key fingerprint, paired/last-seen times, revocation state. |
| 11 | AuditResponse | Name ([:2049](../CONTRACTS.md#L2049)); `AuditRecord` TS ([:1868](../CONTRACTS.md#L1868)) | M4 | M3 (explorer) | Wrapper fields (records, chain status, head, cursor). `current_hash` uses non-canonical `JSON(details)` ([:1893](../CONTRACTS.md#L1893)): specify JCS. Must not claim tamper-proofing (REVIEW.md). |
| 12 | DataDeleteRequest | Name ([:2050](../CONTRACTS.md#L2050)) | M4 | **M2** (stop work, invalidate approvals), M3 (consent UI) | Scope union (all / workflow / day); local-consent proof; which audit records are retained or minimized. |
| 13 | HealthStatus | Ex ([:2092](../CONTRACTS.md#L2092)) | M4 | **M1, M2** (subsystem fields) | Schema; whether the response exposes OmniRoute `base_url`; whether `/health` requires auth (A8). |

**Additional contract gaps (not among the 13):**
- **A1** Stream envelope and event payloads. Owner M4 (`bus.py`); producers M1/M2; consumer M3.
- **A2** Pagination cursor transport: the response arrays have nowhere to carry a next cursor. Owner M4; all members are affected.
- **A3** `effect_preview` schema. Owner M2, with M3.
- **A4** Signed desktop response envelope (REVIEW.md §Approvals 6). Owner M2.
- **A5** Pairing confirmation endpoint. Owner M2, with M3.
- **A6** Status codes for oversize batches and rate limiting. Owner M4.
- **A7** Stale Origin line in §3. Owner M4.
- **A8** Health authentication. Owner M4.

For every row in this section, the common requirements are:
- **Proposed resolution:** a standalone `*.schema.json` with `additionalProperties: false`, plus one valid and one invalid fixture. Then replace the OpenAPI `x-spec-gap` component with a `$ref`.
- **Compatibility impact:** additive for the wire. Breaking only against the illustrative TypeScript where marked ⚠, and nothing consumes those types yet.
- **Tests:** new fixtures picked up by `validate.py`; regenerated models; a drift-check pass.

## 5. Recommended resolution order

**Blocking contract decisions** (cross-owner items first; each needs the named approvers):

1. **G3 validation-boundary rule** (M4 proposes; M1 and M2 approve). Agent routes validate untrusted bodies with JSON Schema plus `validate.py` formats, not generated models alone. This is the cheapest fix for the High items in §3.2.
2. **Run family**: #5, `RunState`/`RunMode`, #4 mode decision (M2 + M3). This unblocks the execution console and every run endpoint.
3. **Approval binding**: #6, A3 `effect_preview`, and the `run.approval_requested` payload ([:2143](../CONTRACTS.md#L2143)) (M2 + M3).
4. **Stream and pagination**: A1, A2 (M4 + all).
5. **Pairing**: #9, #10, A5, A4 (M2 + M3).
6. **Capture surfaces**: #1, #2, #3 (M1 + M3).
7. **Guard**: #7, #8 (M2 + M3, M1 for rule updates).
8. **Platform**: #11, #12, #13, A6, A8 (M4 + M2/M3/M1 as listed).

**Non-blocking cleanup** (M4, after the decisions):
- Add `limit`/`cursor` to `/candidates` and `/guard/warnings` once A2 is decided.
- Type `/runs?status=` as `RunState`.
- Fix the `validate.py` fence regex to handle indented blocks, then update the stale §5.2 example and the §3 Origin line ([CONTRACTS.md](../CONTRACTS.md) is M4-owned; changes go through review).
- Document `model_dump(by_alias=True)` and the JCS hashing rule for model users.
- Optional: improve generated names via schema titles; pin transitive codegen dependencies (`jinja2`, `genson`, `inflect` are unpinned); make `generate.py` remove stale outputs.

## 6. Freeze gates

The contracts can be called frozen only when all of these hold:

- **G1 Gap register closed.** Each of the 13 types and A1–A8 is either a signed schema with valid and invalid fixtures, or explicitly deferred out of 1.1 by its approvers. `openapi.yaml` has zero `x-spec-gap` components for in-scope endpoints.
- **G2 Contradictions resolved in writing.** Covers the ⚠ rows in §2 and §4 (batch response, reject body, run mode, pending-approval binding, pairing capability, Origin), with REVIEW.md or the spec updated through review.
- **G3 Boundary validation agreed.** Every §3.2 mutation is rejected by the agent's validation path, proven by a test that M4 adds under `tests/` once the agent exists.
- **G4 CI green on GitHub.** Both jobs pass on a pull request with the generated outputs committed. This is the first real Linux run.
- **G5 Ownership confirmed.** M3 acknowledges the `web/src/types/contracts.ts` location and that M4 regenerates it. All four owners sign §7 of the checklist below.

The runtime gates listed in [REVIEW.md §Verification](REVIEW.md#verification-and-remaining-implementation-gates) apply after freeze. They cover crypto vectors, nonce races, approval expiry, redaction, checkpoint recovery and similar behavior. None of them is passed or tested by anything in this repository.

### Runtime requirements: where each is represented

- **Contract:** a schema or `openapi.yaml` expresses it.
- **Static:** `validate.py` checks it today.
- **Runtime:** implementation and tests are required. None exist yet.

| REVIEW.md requirement | Contract | Static | Runtime |
|---|---|---|---|
| Risk: R3 blocked, R2 approval, R1 supervised approval, effective risk = max | Step `risk` enum; R1/R2 need pre/post; R2/R3 `retries: 0` | Partly (schema conditionals + regressions) | Classification, policy, R3 block, child risk under loops |
| Approval binding, 60 s expiry, single use, invalidation | Command schema binding fields; `ApproveStepRequest` (unsigned) | Shape only | `effect_hash` computation, expiry, atomic consumption, `STALE_APPROVAL` |
| Command auth: JCS + Ed25519, 60 s skew, 128-bit nonce, `device_id == from_device` | Hex lengths, `Z` pattern, `protocol_version` | Shape only; the equality check cannot be expressed in the schema | Signature verification, durable nonce store, revocation, signed responses |
| Event redaction and dedup | `redacted ⇒ text null` etc.; batch 1–200; 409 | Yes for the invariants | Secret detection in URLs, titles, labels and screenshots; dedup by ID; 1 MiB limit |
| Execution checkpoints | None (`Run` unschematized) | No | Intent and receipt persistence, crash reconciliation, frozen items |
| Outcome verification, `expected_tags` | `OutcomeCheck` shape; `outcome_checks` minItems 1 | Template scope (`expected_tags` only in outcomes) | Registered read-only adapters; `db_query` never SQL; independent reads |
| Expressions and templates | `expr` string | Restricted AST, scope, references | Operand typing, URL allowlist |
| Local HTTP: Bearer, Origin, Host, stream tickets | `bearerAuth`, `/stream/tickets` + `no-store` | No | Origin/Host/DNS-rebinding checks, single-use ticket consumption |
| Idempotency-Key, If-Match | Required headers in OpenAPI | No | 409 semantics, no double execution |

## 7. Owner sign-off checklist

No owner has signed. Nothing here records an approval.

| Owner | Must approve | Status |
|---|---|---|
| M1 Learning | #1–3, G3; consulted on #8, #13, A1 | Pending |
| M2 Execution & Safety | #4–10, A3–A5, G3; consulted on #12, #13, A1 | Pending |
| M3 Frontend | Consumer review of #1–7, #9–12, A1, A2; `web/src/types/contracts.ts` location (G5) | **Pending, not given** |
| M4 Integration | #11–13, A1, A2, A6–A8, G3 proposal, G4 | Pending |

## 8. Checks executed for this review

The following were run in this session on Windows 11 with Python 3.11.9 and Node v24.16.0.

| Check | Result |
|---|---|
| Structural walk of schemas vs Pydantic fields (properties, required, `extra=forbid`) | 13 object schemas, 0 mismatches |
| `git status`, `git log`, `git rev-parse HEAD origin/main` | Branch `m4/foundation`, `HEAD = origin/main = 20809e7`; all prior work untracked; no tracked file modified |
| Count `x-spec-gap` components in `openapi.yaml` | 11 components (a 12th grep hit is description text) |
| Parse the type names in the endpoint table | 13 non-schema types (§4) |
| Script comparing the table (27 rows) with `openapi.yaml` operations | 27/27 present, none extra, success codes match; differences as listed in §2 |
| Probe: 21 mutations, JSON Schema vs generated Pydantic (script kept in the session scratchpad, not the repo) | 20 diverge (schema rejects, Pydantic accepts), 1 agrees |
| Round trip of `workflow.valid.json` through `Workflow` | Equal only with `by_alias=True`; default dump has `else_`; integer `30` → `30.0` |
| Scan of embedded ```` ```json ```` blocks in CONTRACTS.md, using the validator's regex | 17 blocks, 16 parseable; §5.2 example (line 2186) not compared; it fails `command.schema.json` with 6 errors |
| Byte count of line endings in generated files | Pydantic files CRLF on Windows; TypeScript LF |
| `git hash-object --path` with `core.autocrlf=false`, before and after `.gitattributes` | Before: CRLF blob would be committed for `workflow.py`. After: all 6 generated files store LF |
| Linux availability: `wsl -l -v`; Docker or Podman on PATH | No WSL distribution, no Docker/Podman: **Linux generation not tested** |

Not rerun this pass, since nothing they cover changed: `npm ci`, `python contracts/generate.py`, `python contracts/validate.py`, `tsc`. Their last results come from the previous pass and are reported, not re-verified. GitHub Actions has **never run**. The CI drift job will fail until the generated files are committed.
