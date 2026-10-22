# DigitalSoul (Digital Ghost)

DigitalSoul learns a user's repeated desktop work from consented observations, proposes workflows for review, and runs reviewed workflows deterministically on the user's Windows 11 machine. The system is local-first: a loopback agent, a Chrome MV3 observer, a local and phone dashboard, and a signed relay for remote control.

This repository is currently in the foundation stage. It contains the wire contracts, fixtures, the contract validator, and CI. The agent, dashboard, relay, and demo apps are not implemented yet.

## Documentation

- [CONTRACTS.md](CONTRACTS.md): system specification and ownership matrix (§9).
- [contracts/README.md](contracts/README.md): contract layout and validation.
- [contracts/REVIEW.md](contracts/REVIEW.md): runtime requirements that override conflicting legacy examples.
- [AGENTS.md](AGENTS.md) and [BLINKY_REFERENCE.md](BLINKY_REFERENCE.md): project context and the Blinky technical reference. Blinky is a reference only, not a runtime dependency.

Contract versions are `1.1.0-draft`. They are not frozen or deployed.

## Ownership

Each directory has exactly one owner. Shared files are owned by M4 and change only through reviewed pull requests.

| Area | Owner |
|---|---|
| `extension/`, `agent/ghost/collector/`, `agent/ghost/learning/`, `agent/ghost/api/learn_routes.py` | M1 Learning Engine |
| `agent/ghost/execution/`, `drivers/`, `safety/`, `remote/`, `api/run_routes.py`, `api/remote_routes.py`, `relay/` | M2 Execution & Safety |
| `web/` (dashboard and phone PWA) | M3 Frontend & Remote UX |
| `contracts/`, `agent/ghost/app.py`, `config.py`, `bus.py`, `db/`, `audit/`, `demo-apps/`, `tests/e2e/`, `tests/safety/`, `.github/`, `ci/` | M4 Integration Lead |

M2 owns the runtime safety code, and M4 owns the adversarial tests in `tests/safety/`. Neither edits the other's files.

## Setup

Python 3.11 is used in CI. From the repository root:

```bash
python -m pip install -r contracts/requirements-dev.txt
```

## Contract validation

```bash
python contracts/validate.py
```

The validator checks JSON Schema validity, positive and negative fixtures, static workflow references and scopes, restricted decision syntax, synchronization of embedded JSON in `CONTRACTS.md`, and targeted regression cases. CI runs the same command on every pull request and on pushes to `main`.

It does **not** verify:

- runtime behavior of an agent, driver, relay, or dashboard;
- Ed25519 signatures, canonicalization (JCS), replay or nonce handling, or pairing;
- approval, risk, or policy enforcement at execution time;
- checkpoint recovery or external-effect reconciliation;
- that `contracts/openapi.yaml` covers every endpoint in the specification (see the gaps below).

Fixture shape validation does not prove that a command is safe to execute. See [contracts/REVIEW.md](contracts/REVIEW.md) for the required integration gates.

## Contract generation

[contracts/openapi.yaml](contracts/openapi.yaml) is the hand-written API description. The standalone JSON Schemas remain the authority for wire shapes; generated code is never edited by hand.

One-time Node setup (from the repository root):

```bash
npm ci --prefix contracts
```

Regenerate models and validate the OpenAPI document (Python 3.11):

```bash
python -m pip install -r contracts/requirements-codegen.txt
python contracts/generate.py
```

This writes `agent/ghost/contracts/*.py` (Pydantic v2 models for each schema) and `web/src/types/contracts.ts` (TypeScript types), then typechecks the TypeScript output and validates `openapi.yaml`. Generation is deterministic; CI regenerates and fails if the committed output differs.

Known gaps:

- Types named in the specification's endpoint table without a JSON schema (for example `Run`, `ObservationSettings`, `PairingSession`, `AuditResponse`, `HealthStatus`) are marked `x-spec-gap` in the OpenAPI document and are **not** generated. They need owner sign-off per [contracts/REVIEW.md](contracts/REVIEW.md) before they are frozen.
- The cursor mechanism for paginated responses is not yet specified.
