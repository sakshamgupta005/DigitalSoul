# Acme demo apps (synthetic test fixtures)

Two small local web apps used by the Digital Ghost demo workflow ([contracts/fixtures/workflow.valid.json](../contracts/fixtures/workflow.valid.json)):

- **Acme Portal** (`acme-portal`): overdue-account report and team summary notes.
- **Acme CRM** (`acme-crm`): customer records with plan, last-login age, open tickets and a segmentation tag.

All data is **synthetic**. The apps never connect to a real system, every page carries a "SYNTHETIC DEMO DATA" banner, and emails use the reserved `example.invalid` domain. Owner: M4 ([CONTRACTS.md §9](../CONTRACTS.md#L2868)).

The apps use the Python 3.11 standard library only (`http.server` + `sqlite3`). There is nothing to install.

## Run

From the repository root:

```bash
python demo-apps/manage.py reset
```

```bash
python demo-apps/manage.py serve
```

| App | URL | Workflow host | Database |
|---|---|---|---|
| Acme Portal | http://127.0.0.1:7701/ | `portal.acme.local` | `demo-apps/acme-portal/data/acme-portal.sqlite3` |
| Acme CRM | http://127.0.0.1:7702/ | `crm.acme.local` | `demo-apps/acme-crm/data/acme-crm.sqlite3` |

Run one app at a time (`--port` and `--db` are optional):

```bash
python demo-apps/acme-portal/server.py serve
```

```bash
python demo-apps/acme-crm/server.py reset
```

- **Loopback only.** Servers bind to `127.0.0.1` by default and refuse any non-loopback `--host`.
- **Host check.** Requests must carry a `Host` of `127.0.0.1`, `localhost`, `[::1]`, or the app's `*.acme.local` name; anything else gets `421`.
- **Seeding.** A missing database is seeded automatically on first `serve`.

### Reset is demo-only

- **CLI.** `reset` drops and recreates the app's tables from `seed.json`. Two resets produce identical database contents, which the tests verify.
- **HTTP.** `POST /__demo__/reset` exists only when the server starts with `--enable-reset-endpoint`, and it also requires the header `X-Demo-Reset: wipe-synthetic-demo-data`. Otherwise the path returns `404`. It lives outside `/api/`, is not linked from any page, and is not a workflow action. **Agents and the workflow must never call it.** It is for M4 integration tests only.

## Seed data and expected demo outcome

The overdue report lists `1042, 1043, 1044, 1045, 1046, 1047`, in that order. The CRM also contains `1050` and `1051`, which are not overdue and must stay untouched. `1051` is Pro with 100 days since last login, so tagging it would mean the agent read the wrong list.

Applying the fixture rule `plan == 'Pro' and last_login_days > 30`:

| ID | Plan | Last login (days) | Open tickets | Seeded tag | Expected tag |
|---|---|---|---|---|---|
| 1042 | Pro | 45 | 2 | (none) | At-risk – High |
| 1043 | Basic | 60 | 0 | (none) | At-risk – Watch |
| 1044 | Pro | 30 | 0 | (none) | At-risk – Watch (boundary: not > 30) |
| 1045 | Pro | 31 | 1 | (none) | At-risk – High (boundary) |
| 1046 | Enterprise | 90 | 2 | (none) | At-risk – Watch (not Pro) |
| 1047 | Pro | 5 | 0 | Healthy | At-risk – Watch (changes an existing tag) |

The portal is seeded with one earlier note (`run=run_seed_previous_week; processed=5`). A new run's note should match exactly once when looked up by its own `run=<run_id>`.

## Workflow targets

| Step | Page | Target |
|---|---|---|
| `s1_open_portal` | portal nav | link text `Reports` → `/reports/overdue` |
| `s2_copy_accounts` | `/reports/overdue` | `[data-testid=overdue-account-list]`; each item has `data-record-id` |
| `s3_open_record` | `/customers/{id}` | `main[data-testid=customer-record][data-record-id]` (record identity) |
| `s3_read_plan` / `s3_read_days` | customer page | `customer-plan` (text), `last-login-days` (integer text) |
| `s4_set_tag` | customer page | `[data-testid=tag-select]` = `select#tag`, combobox "Customer Tag", inside `form#cust > div.field-tag` |
| `s5_save_cust` | customer page | `[data-testid=save-btn]`, button "Save" → `PATCH /api/customers/{id}` |
| `s6_fill_summary` | `/team/notes` | `[data-testid=team-note-body]` |
| `s6_post_summary` | `/team/notes` | `[data-testid=post-team-note-btn]`, button "Post Weekly Summary" → `POST /api/team/notes` |

The customer page also shows `open-ticket-count`, `open-ticket-list`, `current-tag` and `save-status`. The notes page has `team-note-list` and `post-status`.

## API

JSON in and out. Errors are `{"error": {"code", "message"}}`. Mutations require `Content-Type: application/json` and a body of at most 16 KiB. No endpoint accepts SQL, expressions or arbitrary filters.

**Acme Portal**

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/reports/overdue` | `{accounts: [...], account_ids: [...]}` in report order |
| GET | `/api/team/notes?contains=&limit=` | Newest first. `contains` is a literal, case-sensitive substring. `count` is the total number of matches. `limit` is 1–200 (default 50). |
| POST | `/api/team/notes` | Body `{"body": "..."}` (1–2000 characters, not blank). Returns `201` with `Location`. An optional `Idempotency-Key` header returns the same note (`200`, `Idempotent-Replayed: true`) when reused with the same body, and `409` with a different body. |
| GET | `/api/team/notes/{note_id}` | One note, or `404` |
| GET | `/api/health` | `{app, status, synthetic_demo_data: true}` |

**Acme CRM**

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/customers` | All customers. `?ids=1042,1043` (1–200 distinct IDs) returns `{customers, missing}` for independent tag verification. |
| GET | `/api/customers/{id}` | Customer with `plan`, `last_login_days`, `open_tickets`, `open_ticket_count`, `tag`, `tag_version` |
| PATCH | `/api/customers/{id}` | Body exactly `{"tag": <allowed tag>}`; every other field is read-only (`422`). `tag_version` increments only when the tag changes. The response includes `changed`. |
| GET | `/api/tags` | Allowed tags: `""`, `Healthy`, `At-risk – Watch`, `At-risk – High` (en dash, as in the fixture rule) |
| GET | `/api/health` | As for the portal |

No `DELETE` route exists (`405`).

## Tests

```bash
python -m unittest discover -s demo-apps/tests -v
```

The 27 tests cover:
- seed contents and boundaries, and the synthetic markers;
- CLI and endpoint reset repeatability;
- tag persistence, including across a restart, plus idempotence and validation;
- note creation, reading, idempotency and HTML escaping;
- the routes and every locator in the workflow fixture (unique per page);
- loopback-only binding, Host checks, security headers and body limits.

The tests drive the HTTP and HTML layers. They do **not** run a browser, so the JavaScript save and post handlers are checked statically, not clicked.

## Integration gaps (need M1/M2 input)

- **Host mapping (M2).** The workflow navigates to `http://portal.acme.local/...` and `http://crm.acme.local/...` on the default port. The apps run on `127.0.0.1:7701` and `:7702` and accept those Host names, but nothing maps the names to the ports (no hosts-file change or proxy was made). M2's driver or app registry must decide how `acme-portal` and `acme-crm` resolve.
- **`read_field` of `string[]` (M2).** REVIEW.md requires an adapter that extracts record IDs. The list exposes them as `data-record-id` on `[data-testid=overdue-account]` items. The adapter needs to adopt that attribute.
- **`record_id_matches` (M2).** Record identity is exposed as `data-record-id` on `[data-testid=customer-record]`. This is a proposed convention, not yet in the contracts.
- **`network_2xx` method (M2).** Saving uses `PATCH /api/customers/{id}`, and posting uses `POST /api/team/notes`. The workflow fixture names only the paths.
- **Outcome checks (M2, M4).** `tag_equals_for_all` maps to `GET /api/customers?ids=...`. `team_note_exists` maps to `GET /api/team/notes?contains=...` with `count`. The fixture's `run_id` argument is not a separate note field, so it is matched through the note text. Owners must confirm these adapter mappings.
- **Learning input (M1).** Field names (`plan`, `last_login_days`, `open_tickets`) and test IDs follow the fixture. No recorded observation sessions exist yet for M1's "learn from 4 sessions" deliverable.
- **No end-to-end run.** No agent, driver or browser test has executed the workflow against these apps.
