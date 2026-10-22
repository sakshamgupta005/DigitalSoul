# Digital Ghost — Shared System Contracts Specification
**Version:** `1.0.0-draft` (Phase 0 / Day 1 Freeze)  
**Target:** Local-first Windows 11 Desktop Agent + Chrome MV3 Extension + Relay + Web / PWA Dashboard  
**Owners:** M4 (Integration Lead & Schema Custodian), with sign-off from M1 (Learning), M2 (Execution & Safety), M3 (Frontend)

---

## 1. Overview & Architectural Principles

The Digital Ghost system coordinates four loosely-coupled subsystems across a strict single-owner directory structure:
* **M1 (Learning):** Extension capture, desktop collector, normalizer, sequence alignment, LLM hypothesis, validation compilation.
* **M2 (Execution & Safety):** Playwright CDP driver, UIA driver, plan interpreter, policy/risk engine, mistake guards, kill switch, relay client, pairing/cryptography.
* **M3 (Frontend):** Local React dashboard, candidate review, plan editor, approvals UI, mistake warnings, remote phone PWA.
* **M4 (Integration & Contracts):** Shared JSON schemas, OpenAPI specifications, type generation (TypeScript + Pydantic), SQLite migrations, bus events, demo applications, safety test harness.

```
                           ┌────────────────────────┐
                           │   Chrome MV3 Extension  │ (M1)
                           └───────────┬────────────┘
                                       │ RawEvent (POST /events/batch)
                                       ▼
┌──────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│   Desktop Collector   │──▶│      Ghost Agent       │◀──│   React Dashboard UI   │ (M3)
│   (pywin32 / UIA)     │   │ (FastAPI / SQLite WAL) │   │ (Localhost :7777 / WS) │
└──────────────────────┘   └─────┬────────────┬─────┘   └────────────────────────┘
         (M1)                    │            │
                                 │            │ HTTP /v1
                                 │            ▼
                                 │ ┌────────────────────────┐
                                 │ │   OmniRoute Gateway    │ (Local / Proxy Router)
                                 │ │ (127.0.0.1:20128/v1)   │ Multi-LLM failover &
                                 │ └────────────────────────┘ analyst prompt routing
                                 │
                                 │ Playwright CDP
                                 ▼
                     ┌────────────────────────┐   ┌────────────────────────┐
                     │ Dedicated Ghost Chrome │   │ Cloud Relay (Fly.io)   │ (M2)
                     │ Profile (Acme Portal & │   │ (WSS / Signed Ed25519) │
                     │ CRM Web Apps)          │   └───────────▲────────────┘
                     └────────────────────────┘               │
                                 (M2)                         │ Phone PWA (M3)
                                                  ┌───────────┴────────────┐
                                                  │ Remote Operator Phone  │
                                                  └────────────────────────┘
```

### Core Tenets
1. **Source Redaction & Data Minimization:** Passwords, payment fields, and non-allowlisted input values are redacted *at the source* (extension or desktop collector) before hitting the API or database.
2. **Deterministic Step Verification:** Execution steps follow an explicit contract of `Precondition -> Resolve Target -> Act -> Postcondition`.
3. **Earned Autonomy & Immutable Risk Classes:** Trust level climbs `observed -> candidate -> validated -> supervised -> trusted`. Destructive actions (`R3`) are blocked unconditionally in MVP.
4. **Offline & Cryptographic Security:** The Agent binds exclusively to `127.0.0.1:7777`, enforces a per-install Bearer token and strict `Origin` headers. Remote commands are signed with non-extractable Ed25519 keys via WebCrypto and verified against a 60-second replay window.
5. **OmniRoute Unified AI Gateway:** The LLM acts purely as an analyst (naming workflows, extracting parameters, proposing decision rules), never driving the mouse. All LLM calls route through OmniRoute's OpenAI/Anthropic compatible gateway (`http://127.0.0.1:20128/v1`), enabling multi-provider failover (Claude, Gemini, OpenAI, DeepSeek, local models), token caching, and automated quota fallback. If OmniRoute is offline or LLM assist is disabled, the agent functions 100% deterministically using local sequence alignment.

---

## 2. Core Data Models & Schemas

All schemas conform to **JSON Schema Draft 2020-12**. Code generation targets Python (Pydantic v2) and TypeScript (zod / pure interfaces).

---

### 2.1 `RawEvent` (Collector / Extension → Agent)

Captured by the Chrome MV3 extension or Windows desktop collector and ingested via `POST /api/v1/events/batch`.

#### JSON Schema
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://digitalghost.local/schemas/raw_event.json",
  "title": "RawEvent",
  "type": "object",
  "required": [
    "event_id",
    "ts",
    "source",
    "kind",
    "app",
    "target",
    "value",
    "page",
    "session_id"
  ],
  "properties": {
    "event_id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique identifier for the captured event."
    },
    "ts": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 UTC timestamp with millisecond precision."
    },
    "source": {
      "type": "string",
      "enum": ["extension", "desktop"],
      "description": "Originating collector sensor."
    },
    "kind": {
      "type": "string",
      "enum": [
        "navigate",
        "click",
        "input_change",
        "submit",
        "focus",
        "window_switch",
        "clipboard_copy"
      ],
      "description": "Semantic category of the interaction."
    },
    "app": {
      "type": "object",
      "required": ["id", "type", "process"],
      "properties": {
        "id": { "type": "string", "example": "acme-crm" },
        "type": { "type": "string", "enum": ["web", "native"] },
        "domain": { "type": ["string", "null"], "example": "crm.acme.local" },
        "process": { "type": "string", "example": "chrome.exe" },
        "window_title": { "type": ["string", "null"], "example": "Acme CRM - Customer 1042" }
      }
    },
    "target": {
      "type": "object",
      "properties": {
        "role": { "type": ["string", "null"], "example": "button" },
        "name": { "type": ["string", "null"], "example": "Save" },
        "testid": { "type": ["string", "null"], "example": "save-btn" },
        "label": { "type": ["string", "null"], "example": "Customer Tag" },
        "css": { "type": ["string", "null"], "example": "form#cust > button.primary" },
        "xpath": { "type": ["string", "null"], "example": "/html/body/div[1]/form/button" },
        "uia": {
          "type": ["object", "null"],
          "properties": {
            "automation_id": { "type": ["string", "null"] },
            "control_type": { "type": ["string", "null"] },
            "name": { "type": ["string", "null"] }
          }
        },
        "bbox": {
          "type": ["array", "null"],
          "items": { "type": "number" },
          "minItems": 4,
          "maxItems": 4,
          "description": "[x, y, width, height] relative to viewport/window."
        }
      }
    },
    "value": {
      "type": "object",
      "required": ["redacted", "shape"],
      "properties": {
        "redacted": { "type": "boolean" },
        "shape": {
          "type": "string",
          "enum": ["none", "text:short", "text:long", "number", "email", "secret_omitted"],
          "example": "text:short"
        },
        "text": {
          "type": ["string", "null"],
          "description": "Populated ONLY if field is explicitly on the user's allowlist (e.g., tag field)."
        }
      }
    },
    "page": {
      "type": "object",
      "required": ["url_pattern"],
      "properties": {
        "url_pattern": { "type": "string", "example": "/customers/:id" },
        "url_params": {
          "type": "object",
          "additionalProperties": { "type": "string" },
          "example": { "id": "1042" }
        }
      }
    },
    "session_id": {
      "type": "string",
      "format": "uuid",
      "description": "Correlates continuous user activity within a window."
    }
  }
}
```

#### TypeScript Definition
```typescript
export type EventSource = 'extension' | 'desktop';

export type EventKind =
  | 'navigate'
  | 'click'
  | 'input_change'
  | 'submit'
  | 'focus'
  | 'window_switch'
  | 'clipboard_copy';

export interface UIATarget {
  automation_id: string | null;
  control_type: string | null;
  name: string | null;
}

export interface TargetDescriptor {
  role?: string | null;
  name?: string | null;
  testid?: string | null;
  label?: string | null;
  css?: string | null;
  xpath?: string | null;
  uia?: UIATarget | null;
  bbox?: [number, number, number, number] | null;
}

export interface ValuePayload {
  redacted: boolean;
  shape: 'none' | 'text:short' | 'text:long' | 'number' | 'email' | 'secret_omitted';
  text: string | null;
}

export interface PageDescriptor {
  url_pattern: string;
  url_params?: Record<string, string>;
}

export interface RawEvent {
  event_id: string;
  ts: string; // ISO 8601
  source: EventSource;
  kind: EventKind;
  app: {
    id: string;
    type: 'web' | 'native';
    domain?: string | null;
    process: string;
    window_title?: string | null;
  };
  target: TargetDescriptor;
  value: ValuePayload;
  page: PageDescriptor;
  session_id: string;
}
```

---

### 2.2 `Action` (Normalized Semantic Action)

Normalizer merges bursts of low-level `RawEvent`s (e.g. typing characters, focus shifts) into singular, reproducible actions.

```typescript
export type ActionVerb =
  | 'navigate'
  | 'click'
  | 'set_field'
  | 'read_field'
  | 'select_option'
  | 'copy_selection'
  | 'switch_window';

export interface Action {
  action_id: string;
  session_id: string;
  episode_id?: string | null;
  ts_start: string;
  ts_end: string;
  verb: ActionVerb;
  app: string;
  page_pattern: string;
  target_signature: string; // e.g. "acme-crm:/customers/:id:click:save-btn"
  locators: TargetDescriptor;
  value_shape?: string;
  observed_value?: string | null; // null if redacted
  raw_event_ids: string[];
}
```

---

### 2.3 `WorkflowCandidate` (Mined Pattern & Recommendation)

Extracted across ≥ 3 episodes with sequence alignment score ≥ 0.75.

```typescript
export interface CandidateStepSummary {
  step_index: number;
  verb: ActionVerb;
  app: string;
  page_pattern: string;
  target_name: string;
  is_loop_body: boolean;
}

export interface WorkflowCandidate {
  candidate_id: string;
  created_at: string;
  name: string;
  description: string;
  evidence_episode_ids: string[];
  occurrence_count: number;
  alignment_score: number;
  estimated_weekly_savings_minutes: number;
  median_duration_seconds: number;
  steps: CandidateStepSummary[];
  inferred_parameters: Array<{
    name: string;
    suggested_source: string;
    sample_values: string[];
  }>;
  inferred_rules: Array<{
    rule_id: string;
    expression: string;
    supported_evidence_count: number;
    contradicting_evidence_count: number;
  }>;
  status: 'pending' | 'promoted' | 'dismissed';
}
```

---

### 2.4 `Workflow` (Validated Plan & Execution Contract)

The compiled specification reviewed by the user, stored in SQLite, and executed by Playwright/UIA.

#### JSON Schema
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://digitalghost.local/schemas/workflow.json",
  "title": "Workflow",
  "type": "object",
  "required": [
    "workflow_id",
    "version",
    "name",
    "description",
    "trust",
    "provenance",
    "parameters",
    "steps",
    "decision_rules",
    "outcome_checks",
    "remote_allowed"
  ],
  "properties": {
    "workflow_id": { "type": "string" },
    "version": { "type": "integer", "minimum": 1 },
    "name": { "type": "string" },
    "description": { "type": "string" },
    "trust": {
      "type": "string",
      "enum": ["observed", "candidate", "validated", "supervised", "trusted"]
    },
    "provenance": {
      "type": "object",
      "required": ["evidence_episode_ids", "support", "alignment"],
      "properties": {
        "evidence_episode_ids": {
          "type": "array",
          "items": { "type": "string" }
        },
        "support": { "type": "integer", "minimum": 1 },
        "alignment": { "type": "number", "minimum": 0, "maximum": 1 }
      }
    },
    "parameters": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/WorkflowParameter"
      }
    },
    "steps": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/WorkflowStep"
      }
    },
    "decision_rules": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/DecisionRule"
      }
    },
    "outcome_checks": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/OutcomeCheck"
      }
    },
    "remote_allowed": { "type": "boolean" }
  },
  "$defs": {
    "WorkflowParameter": {
      "type": "object",
      "required": ["name", "type", "source", "origin", "confirmed"],
      "properties": {
        "name": { "type": "string" },
        "type": { "type": "string", "enum": ["string", "string[]", "number", "boolean"] },
        "source": { "type": "string", "example": "step:s2.output" },
        "origin": { "type": "string", "enum": ["observed", "inferred", "user_defined"] },
        "confirmed": { "type": "boolean" },
        "default_value": {}
      }
    },
    "StepLocator": {
      "type": "object",
      "required": ["by"],
      "properties": {
        "by": {
          "type": "string",
          "enum": ["testid", "role", "label", "text", "css", "xpath", "uia"]
        },
        "value": { "type": "string" },
        "role": { "type": "string" },
        "name": { "type": "string" },
        "uia_id": { "type": "string" }
      }
    },
    "PrePostCondition": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "url_matches",
            "element_visible",
            "field_equals",
            "network_2xx",
            "record_id_matches"
          ]
        },
        "value": { "type": "string" },
        "locator_ref": { "type": "integer" }
      }
    },
    "WorkflowStep": {
      "type": "object",
      "required": [
        "step_id",
        "kind",
        "verb",
        "app",
        "locators",
        "risk",
        "required",
        "origin",
        "recovery"
      ],
      "properties": {
        "step_id": { "type": "string" },
        "kind": { "type": "string", "enum": ["action", "loop", "decision", "assert"] },
        "verb": {
          "type": "string",
          "enum": [
            "navigate",
            "click",
            "set_field",
            "read_field",
            "select_option",
            "wait_visible",
            "assert_state"
          ]
        },
        "app": { "type": "string" },
        "locators": {
          "type": "array",
          "items": { "$ref": "#/$defs/StepLocator" }
        },
        "input": { "type": ["string", "number", "null"] },
        "loop_iterator": {
          "type": "object",
          "properties": {
            "source_param": { "type": "string" },
            "item_name": { "type": "string" }
          }
        },
        "pre": {
          "type": "array",
          "items": { "$ref": "#/$defs/PrePostCondition" }
        },
        "post": {
          "type": "array",
          "items": { "$ref": "#/$defs/PrePostCondition" }
        },
        "risk": { "type": "string", "enum": ["R0", "R1", "R2", "R3"] },
        "required": { "type": "boolean" },
        "origin": { "type": "string", "enum": ["observed", "inferred"] },
        "allow_input_fallback": { "type": "boolean", "default": false },
        "recovery": {
          "type": "object",
          "required": ["retries", "on_fail"],
          "properties": {
            "retries": { "type": "integer", "minimum": 0 },
            "on_fail": {
              "type": "string",
              "enum": ["pause", "skip", "abort", "retry_then_pause"]
            }
          }
        }
      }
    },
    "DecisionRule": {
      "type": "object",
      "required": ["id", "expr", "then", "else", "origin", "supported_runs", "contradicting_runs"],
      "properties": {
        "id": { "type": "string" },
        "expr": { "type": "string", "example": "plan == 'Pro' and last_login_days > 30" },
        "then": { "type": "string", "example": "At-risk – High" },
        "else": { "type": "string", "example": "At-risk – Watch" },
        "origin": { "type": "string", "enum": ["inferred", "user_defined"] },
        "supported_runs": { "type": "integer" },
        "contradicting_runs": { "type": "integer" }
      }
    },
    "OutcomeCheck": {
      "type": "object",
      "required": ["type", "app", "check", "args"],
      "properties": {
        "type": { "type": "string", "enum": ["app_api", "db_query", "page_assertion"] },
        "app": { "type": "string" },
        "check": { "type": "string" },
        "args": { "type": "object" }
      }
    }
  }
}
```

---

### 2.5 `Run` & Checkpoint State Machine

Execution runs are stateful, checkpointed per loop iteration, and emitted over WebSocket.

```
       ┌───────────┐
       │  queued   │
       └─────┬─────┘
             ▼
       ┌───────────┐
       │ preflight │
       └─────┬─────┘
             ▼
       ┌───────────┐   approval req    ┌───────────────────┐
       │  running  │──────────────────▶│ awaiting_approval │
       └─────┬─────┘◀──────────────────└───────────────────┘
             │          user approves            │
             │                                   │ user rejects
             ├─────────────────┐                 ▼
             │ interrupt/error │            ┌───────────┐
             ▼                 ▼            │ cancelled │
       ┌───────────────────┐               └───────────┘
       │ paused_needs_user │
       └─────────┬─────────┘
                 │ resume
                 ▼ (back to running)
   
   Terminal States:
   * succeeded            (all steps + all independent outcome checks verified green)
   * completed_unverified (all steps ran, but outcome check failed or incomplete)
   * failed               (unhandled error or timeout)
   * blocked_by_guard     (precondition failed, e.g. wrong account record detected)
   * cancelled            (stopped by user or kill switch)
```

#### TypeScript Definition
```typescript
export type RunState =
  | 'queued'
  | 'preflight'
  | 'running'
  | 'awaiting_approval'
  | 'paused_needs_user'
  | 'succeeded'
  | 'completed_unverified'
  | 'failed'
  | 'blocked_by_guard'
  | 'cancelled';

export type RunMode = 'normal' | 'supervised' | 'dry_run';

export interface StepExecutionRecord {
  step_id: string;
  attempt: number;
  status: 'passed' | 'failed' | 'skipped' | 'drift_warning';
  locator_used_index: number;
  duration_ms: number;
  screenshot_path?: string | null;
  error_message?: string | null;
}

export interface RunCheckpoint {
  completed_iterations: number;
  completed_account_ids: string[];
  last_successful_step_id: string;
  context_vars: Record<string, unknown>;
}

export interface Run {
  run_id: string;
  workflow_id: string;
  workflow_version: number;
  mode: RunMode;
  state: RunState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  current_step_id: string | null;
  current_loop_index: number | null;
  total_loop_count: number | null;
  parameters: Record<string, unknown>;
  step_records: StepExecutionRecord[];
  checkpoint: RunCheckpoint | null;
  pending_approval?: {
    step_id: string;
    risk: 'R1' | 'R2';
    description: string;
    preview_data: Record<string, unknown>;
  } | null;
  outcome_results?: {
    verified: boolean;
    checks_passed: number;
    checks_total: number;
    details: Array<{ check: string; passed: boolean; message: string }>;
  } | null;
  initiated_by: {
    channel: 'local_dashboard' | 'remote_phone' | 'cli';
    device_id: string;
  };
}
```

---

### 2.6 Standard Error Format

Used across all HTTP 4xx/5xx responses, WebSocket error notices, and remote command rejections.

#### JSON Schema
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://digitalghost.local/schemas/error.json",
  "title": "GhostError",
  "type": "object",
  "required": ["error"],
  "properties": {
    "error": {
      "type": "object",
      "required": ["code", "message", "retryable", "trace_id"],
      "properties": {
        "code": {
          "type": "string",
          "enum": [
            "VALIDATION_ERROR",
            "NOT_FOUND",
            "UNAUTHORIZED",
            "POLICY_BLOCKED",
            "APPROVAL_REQUIRED",
            "LOCATOR_NOT_FOUND",
            "LOCATOR_AMBIGUOUS",
            "PRECONDITION_FAILED",
            "POSTCONDITION_FAILED",
            "GUARD_TRIGGERED",
            "APP_UNREACHABLE",
            "DEVICE_OFFLINE",
            "SIGNATURE_INVALID",
            "INTERNAL"
          ]
        },
        "message": { "type": "string" },
        "retryable": { "type": "boolean" },
        "run_id": { "type": ["string", "null"] },
        "step_id": { "type": ["string", "null"] },
        "details": { "type": "object" },
        "trace_id": { "type": "string", "format": "uuid" }
      }
    }
  }
}
```

---

### 2.7 Mistake Guard Warning & Feedback

Fired by the live observer or plan interpreter when an action contradicts known patterns.

```typescript
export interface GuardWarning {
  warning_id: string;
  created_at: string;
  source: 'live_observation' | 'execution';
  check_type:
    | 'wrong_record'
    | 'rule_deviation'
    | 'skipped_step'
    | 'destructive_action'
    | 'unusual_volume'
    | 'ui_drift';
  severity: 'warning' | 'blocking';
  confidence: number; // 0.0 - 1.0 (fires warning if >= 0.8)
  title: string;
  message: string;
  evidence: {
    expected: string;
    actual: string;
    context: Record<string, unknown>;
  };
  workflow_id?: string;
  run_id?: string;
}

export interface GuardFeedbackRequest {
  resolution: 'acknowledged' | 'false_positive' | 'rule_updated';
  new_rule_expression?: string;
}
```

---

### 2.8 Audit Ledger Record

Every safety event, observation toggle, command, step execution, and approval is recorded in an append-only, SHA-256 hash-chained SQLite table.

```typescript
export interface AuditRecord {
  id: number; // auto-increment
  ts: string;
  event_type:
    | 'consent_granted'
    | 'observation_paused'
    | 'observation_resumed'
    | 'workflow_validated'
    | 'run_started'
    | 'step_executed'
    | 'approval_granted'
    | 'approval_rejected'
    | 'guard_triggered'
    | 'kill_switch_engaged'
    | 'remote_command_received'
    | 'data_wiped';
  actor: {
    type: 'user_local' | 'user_remote_phone' | 'ghost_agent';
    device_id: string;
  };
  workflow_id?: string | null;
  run_id?: string | null;
  step_id?: string | null;
  details: Record<string, unknown>;
  prev_hash: string; // SHA-256 hex string of row id - 1 (or 64 zeros for genesis)
  current_hash: string; // SHA-256(prev_hash + ts + event_type + JSON(details))
}
```

---

### 2.9 Policy & Enterprise Configuration (`policy.json`)

Stored on the local machine (or distributed via MDM/group policy).

```json
{
  "version": 1,
  "allow_observation": true,
  "allow_remote_control": true,
  "allow_llm_assist": true,
  "omniroute": {
    "enabled": true,
    "base_url": "http://127.0.0.1:20128/v1",
    "api_key_env": "OMNIROUTE_API_KEY",
    "model": "auto/best-analyst",
    "fallback_models": [
      "claude-3-5-sonnet-20241022",
      "gpt-4o",
      "gemini-2.0-flash",
      "deepseek-chat"
    ],
    "timeout_seconds": 15,
    "max_tokens": 1024,
    "prompt_caching": true
  },
  "allowed_domains": [
    "portal.acme.local",
    "crm.acme.local",
    "127.0.0.1"
  ],
  "excluded_domains": [
    "banking.com",
    "payroll.internal"
  ],
  "enforce_killswitch_hotkey": "Ctrl+Alt+Shift+G",
  "max_allowed_risk_level": "R2"
}
```

---

### 2.10 `OmniRoute` LLM Hypothesis Contract (Agent ➔ OmniRoute Gateway)

When candidate workflows reach the mining threshold (≥ 3 repetitions, alignment score ≥ 0.75), the agent queries OmniRoute as an analyst LLM to propose human-readable names, parameter slots, decision rules, and outcome verification checks.

**Wire Protocol:** OpenAI-compatible `/v1/chat/completions` with JSON Object output mode.

#### Request Contract to OmniRoute
```json
{
  "model": "auto/best-analyst",
  "temperature": 0.1,
  "response_format": { "type": "json_object" },
  "messages": [
    {
      "role": "system",
      "content": "You are the Digital Ghost Workflow Analyst. You analyze abstract, redacted user action sequences to propose human-readable workflow titles, parameter bindings, decision rules, and post-run verification checks. NEVER invent raw mouse actions or ask for credentials. Respond strictly in valid JSON."
    },
    {
      "role": "user",
      "content": "Analyze this recurring sequence observed 4 times across Acme Portal and Acme CRM:\n\n[{\"step\": 1, \"app\": \"acme-portal\", \"verb\": \"navigate\", \"target\": \"/reports/overdue\"}, {\"step\": 2, \"app\": \"acme-portal\", \"verb\": \"read_field\", \"target\": \"overdue-account-list\", \"shape\": \"list[string]\"}, {\"step\": 3, \"app\": \"acme-crm\", \"verb\": \"navigate\", \"target\": \"/customers/:id\"}, {\"step\": 4, \"app\": \"acme-crm\", \"verb\": \"set_field\", \"target\": \"tag-select\", \"observed_values\": [\"At-risk – High\", \"At-risk – Watch\"], \"conditions\": [\"plan == Pro and last_login_days > 30 -> High\"]}, {\"step\": 5, \"app\": \"acme-crm\", \"verb\": \"click\", \"target\": \"save-btn\"}, {\"step\": 6, \"app\": \"acme-portal\", \"verb\": \"click\", \"target\": \"post-team-note-btn\"}]"
    }
  ]
}
```

#### Expected Structured Output from OmniRoute
```json
{
  "name": "Weekly at-risk customer segmentation",
  "description": "Reads overdue accounts from Acme Portal, inspects CRM history, applies segmentation tags based on login freshness and open tickets, and posts summary note.",
  "inferred_parameters": [
    {
      "name": "account_ids",
      "type": "string[]",
      "source": "step:s2_copy_accounts.output",
      "description": "List of account numbers extracted from the overdue portal table."
    }
  ],
  "inferred_decision_rules": [
    {
      "id": "segment",
      "expr": "plan == 'Pro' and last_login_days > 30",
      "then": "At-risk – High",
      "else": "At-risk – Watch",
      "explanation": "Customers on Pro plans inactive for over 30 days are categorized as High risk."
    }
  ],
  "inferred_outcome_checks": [
    {
      "type": "app_api",
      "app": "acme-crm",
      "check": "tag_equals_for_all",
      "args": { "ids": "{{account_ids}}" }
    },
    {
      "type": "app_api",
      "app": "acme-portal",
      "check": "team_note_exists",
      "args": { "containing": "Weekly at-risk segmentation complete" }
    }
  ]
}
```

#### Offline & Fallback Circuit Breaker
* **Timeout:** 15 seconds max. If OmniRoute is offline, rate-limited, or unreachable, the circuit breaker opens.
* **Deterministic Fallback:** The Learning Engine defaults to:
  * `name`: `"Discovered Workflow #" + candidate_id[:6]`
  * `parameters`: `["param_1", "param_2"]` mapped directly from differing input slots.
  * `decision_rules`: Local statistical frequency table of observed values.
  * No user-blocking exception is thrown. Candidates still appear in the dashboard for manual user naming and review.

---

## 3. Local REST API Specification

**Base URL:** `http://127.0.0.1:7777/api/v1`  
**Security:**  
1. Bound strictly to `127.0.0.1`.
2. Header: `Authorization: Bearer <local_agent_token>` (read from local environment / DPAPI).
3. Header: `Origin: http://localhost:7777` or `chrome-extension://<id>`. Foreign origin requests return HTTP 403 `POLICY_BLOCKED`.

### Endpoint Summary

| Method | Path | Summary | Owner | Request Body | Response (Success) |
|---|---|---|---|---|---|
| `POST` | `/events/batch` | Ingest batch of captured events (≤ 200) | M1 | `RawEvent[]` | `200 { ingested: number }` |
| `GET` | `/settings/observation` | Read observation state & allowlist | M1 | — | `200 ObservationSettings` |
| `PUT` | `/settings/observation` | Update observation state (pause/resume) | M1 | `UpdateObservationSettings` | `200 ObservationSettings` |
| `GET` | `/candidates` | List mined candidates + recommendations | M1 | — | `200 WorkflowCandidate[]` |
| `POST` | `/candidates/{id}/dismiss` | Dismiss a candidate permanently | M1 | — | `200 { dismissed: true }` |
| `POST` | `/candidates/{id}/promote` | Convert candidate to draft workflow | M1 | — | `201 Workflow` |
| `GET` | `/workflows` | List all workflows | M1 | — | `200 Workflow[]` |
| `GET` | `/workflows/{id}` | Read workflow details | M1 | — | `200 Workflow` |
| `PUT` | `/workflows/{id}` | Update draft workflow | M1 | `Workflow` | `200 Workflow` |
| `POST` | `/workflows/{id}/validate` | Finalize & compile execution plan | M1 | — | `200 Workflow` |
| `POST` | `/workflows/{id}/runs` | Start a workflow run | M2 | `StartRunRequest` | `201 Run` |
| `GET` | `/runs` | List execution runs (pagination) | M2 | Query: `limit, status` | `200 Run[]` |
| `GET` | `/runs/{id}` | Get run details + step breakdown | M2 | — | `200 Run` |
| `POST` | `/runs/{id}/approve` | Approve gated step (R1/R2) | M2 | `ApproveStepRequest` | `200 Run` |
| `POST` | `/runs/{id}/reject` | Reject gated step | M2 | — | `200 Run` |
| `POST` | `/runs/{id}/pause` | Pause active run | M2 | — | `200 Run` |
| `POST` | `/runs/{id}/resume` | Resume paused run | M2 | — | `200 Run` |
| `POST` | `/runs/{id}/cancel` | Abort execution | M2 | — | `200 Run` |
| `GET` | `/guard/warnings` | Fetch active mistake guard warnings | M2 | — | `200 GuardWarning[]` |
| `POST` | `/guard/warnings/{id}/feedback` | Report false positive / rule update | M2 | `GuardFeedbackRequest` | `200 { status: "recorded" }` |
| `POST` | `/remote/pair/start` | Begin phone pairing (produces QR payload) | M2 | — | `201 PairingSession` |
| `GET` | `/remote/devices` | List paired phones/devices | M2 | — | `200 PairedDevice[]` |
| `DELETE` | `/remote/devices/{id}` | Revoke paired device | M2 | — | `204 No Content` |
| `GET` | `/audit` | Query audit log with hash-chain check | M4 | Query: `from, to, limit` | `200 AuditResponse` |
| `POST` | `/data/delete` | Wipe data (all, per-workflow, or per-day) | M4 | `DataDeleteRequest` | `200 { deleted: true }` |
| `GET` | `/health` | Heartbeat & subsystem connectivity | M4 | — | `200 HealthStatus` |

---

### Detailed Endpoint Contracts

#### `POST /api/v1/workflows/{id}/runs`
**Request Body:**
```json
{
  "mode": "supervised",
  "parameters": {
    "account_ids": ["ACC-1042", "ACC-1088", "ACC-1102"]
  }
}
```
**Response `201 Created`:**
```json
{
  "run_id": "run_01j9b2e4t3n2y",
  "workflow_id": "wf_at_risk_seg",
  "workflow_version": 3,
  "mode": "supervised",
  "state": "preflight",
  "created_at": "2026-10-22T09:30:00.000Z",
  "started_at": null,
  "finished_at": null,
  "current_step_id": null,
  "current_loop_index": 0,
  "total_loop_count": 3,
  "parameters": {
    "account_ids": ["ACC-1042", "ACC-1088", "ACC-1102"]
  },
  "step_records": [],
  "checkpoint": null,
  "pending_approval": null,
  "initiated_by": {
    "channel": "local_dashboard",
    "device_id": "desktop_primary"
  }
}
```

#### `GET /api/v1/health`
**Response `200 OK`:**
```json
{
  "status": "healthy",
  "agent_version": "1.0.0",
  "uptime_seconds": 1420,
  "drivers": {
    "ghost_chrome_profile": "connected",
    "playwright_cdp": "attached",
    "windows_uia": "ready"
  },
  "subsystems": {
    "database": "connected_wal",
    "learning_engine": "idle",
    "omniroute_gateway": {
      "status": "connected",
      "base_url": "http://127.0.0.1:20128/v1",
      "active_model": "auto/best-analyst",
      "latency_ms": 42
    },
    "relay_client": "connected"
  }
}
```

---

## 4. WebSocket Event Bus Specification

**Connection URL:** `ws://127.0.0.1:7777/api/v1/stream`  
**Authentication:** Pass header `Authorization: Bearer <token>` during WebSocket handshake.

All frames are JSON objects matching this envelope:
```json
{
  "type": "run.step_updated",
  "ts": "2026-10-22T09:31:12.450Z",
  "data": {}
}
```

### Event Catalog

| Event Type | Producer | Emitted When | Payload Structure (`data`) |
|---|---|---|---|
| `observation.status` | M1 | Capture toggled, paused on lock/app exclusion | `{ observing: boolean, reason?: string, allowlist_count: number }` |
| `candidate.created` | M1 | Sequence mining identifies ≥ 3 repetitions | `WorkflowCandidate` |
| `candidate.updated` | M1 | LLM completes hypothesis or candidate status changes | `WorkflowCandidate` |
| `run.state_changed` | M2 | Any transition in the run state machine | `{ run_id: string, from_state: RunState, to_state: RunState, reason?: string }` |
| `run.step_updated` | M2 | Step completes, drifts, or retries | `{ run_id: string, step_id: string, attempt: number, status: string, duration_ms: number }` |
| `run.approval_requested` | M2 | Run hits R1 (supervised) or R2 step | `{ run_id: string, step_id: string, risk: "R1"|"R2", description: string, preview: object }` |
| `guard.warning` | M2 | Live check or execution guard deviation detected | `GuardWarning` |
| `remote.device_status` | M2 | Remote phone connects, disconnects, or pairs | `{ device_id: string, name: string, status: "online"|"offline"|"paired" }` |

---

## 5. Remote Relay & Cryptographic Protocol

Connects the desktop agent to the operator’s phone PWA without exposing incoming firewall ports.
* **Desktop:** Maintains persistent outbound WSS to `wss://relay.digitalghost.io/agent`.
* **Phone:** Connects to `wss://relay.digitalghost.io/phone`.
* **Relay Server:** Blind routing switch. It does not store workflows, field values, or private keys.

```
┌──────────────┐         Outbound WSS         ┌──────────────┐         Outbound WSS         ┌──────────────┐
│ Phone PWA    │─────────────────────────────▶│  Relay Hub   │◀─────────────────────────────│ Desktop      │
│ (WebCrypto)  │   Signed Ed25519 Payload     │  (Render)    │   Signed Ed25519 Payload     │ Agent (DPAPI)│
└──────────────┘                              └──────────────┘                              └──────────────┘
```

### 5.1 Pairing Handshake Contract

1. Desktop calls `POST /api/v1/remote/pair/start`.
2. Agent generates ephemeral pairing session:
   ```json
   {
     "pairing_id": "pair_99a81c",
     "pairing_code": "849-211",
     "expires_at": "2026-10-22T09:35:00Z",
     "desktop_pubkey": "d4e2c8...32bytes_hex",
     "qr_payload": "ghostpair:v1?code=849211&host=relay.digitalghost.io&pub=d4e2c8..."
   }
   ```
3. Phone scans QR code, generates local Ed25519 keypair in IndexedDB, and sends pairing claim to Relay.
4. Desktop prompts user: *"Allow iPhone 15 Pro (Fingerprint 849-211) to control this desktop?"*
5. On desktop approval, phone public key is saved into agent database.

### 5.2 Command Envelope Specification

Every control message crossing the relay is signed by the phone's non-extractable private key.

```json
{
  "msg_id": "msg_01j9b311fa89",
  "from_device": "phone_usr_iphone15",
  "to_device": "desktop_primary",
  "kind": "command",
  "sig": "3a9f1c...64bytes_hex_signature",
  "payload": {
    "command_id": "cmd_88192a",
    "device_id": "phone_usr_iphone15",
    "type": "approve_step",
    "workflow_id": "wf_at_risk_seg",
    "run_id": "run_01j9b2e4t3n2y",
    "step_id": "s6_post_summary",
    "params": {},
    "issued_at": "2026-10-22T09:32:00.100Z",
    "nonce": "9af182ca33de4401"
  }
}
```

#### Allowed Command Types
* `run_workflow` (Only if `remote_allowed == true`)
* `approve_step`
* `reject_step`
* `pause_run`
* `resume_run`
* `stop_run`
* `get_status`
* `list_workflows`

#### Signature Verification Rules (Agent-side)
1. `from_device` must match an active key in `remote_devices`.
2. `sig` must verify `ed25519_verify(pubkey, canonical_json(payload), sig)`.
3. `|now() - issued_at| <= 60` seconds (Clock skew & freshness check).
4. `nonce` has not been seen in the last 10 minutes (Replay prevention).

---

## 6. Action Risk Classification & Policy Rules

Every step is evaluated against its risk class before execution.

| Risk Class | Description | Examples | Supervised Mode | Trusted Mode | Remote Phone Mode |
|---|---|---|---|---|---|
| **R0** | Read-Only | Navigate, open record, read field, assert visible | Auto-allow | Auto-allow | Auto-allow |
| **R1** | Reversible Write | Set tag, edit draft text field | Requires 1-click confirmation | Auto-allow (audited) | Auto-allow |
| **R2** | External Effect | Post team note, send message, commit changes | Requires explicit approval | Requires batch approval | Requires signed approval from phone |
| **R3** | Destructive / Irreversible | Delete customer, purge records, change permissions | **BLOCKED** (Never executed) | **BLOCKED** | **BLOCKED** |

### Automated Heuristics for R3 Classification
The agent marks a target element `R3` if:
* Button text / accessible name matches regex: `/(delete|remove|purge|drop|wipe|destroy|terminate)/i`.
* Target action emits HTTP `DELETE` method.
* Target triggers an irreversible browser modal (`window.confirm`).
* An LLM suggestion attempts to lower risk: **Rejected** (LLM can raise risk class, but cannot lower it).

---

## 7. Concrete Test Fixtures

---

### 7.1 `RawEvent` Fixtures

#### Valid Fixture 1: Semantic CRM Tag Selection (Web)
```json
{
  "event_id": "b1a2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "ts": "2026-10-22T09:14:03.120Z",
  "source": "extension",
  "kind": "click",
  "app": {
    "id": "acme-crm",
    "type": "web",
    "domain": "crm.acme.local",
    "process": "chrome.exe",
    "window_title": "Acme CRM - Customer 1042"
  },
  "target": {
    "role": "combobox",
    "name": "Customer Tag",
    "testid": "tag-select",
    "label": "Tag",
    "css": "form#cust > div.field-tag > select",
    "xpath": "/html/body/div[1]/form/div[2]/select",
    "uia": null,
    "bbox": [812, 640, 140, 36]
  },
  "value": {
    "redacted": false,
    "shape": "text:short",
    "text": "At-risk – High"
  },
  "page": {
    "url_pattern": "/customers/:id",
    "url_params": {
      "id": "1042"
    }
  },
  "session_id": "99998888-7777-6666-5555-444433332222"
}
```

#### Valid Fixture 2: Native Window Switch (Desktop Collector)
```json
{
  "event_id": "a9b8c7d6-e5f4-3a2b-1c0d-fe9876543210",
  "ts": "2026-10-22T09:14:15.000Z",
  "source": "desktop",
  "kind": "window_switch",
  "app": {
    "id": "acme-portal",
    "type": "web",
    "domain": "portal.acme.local",
    "process": "chrome.exe",
    "window_title": "Acme Internal Portal - Overdue Invoices"
  },
  "target": {
    "role": "window",
    "name": "Chrome - Acme Portal",
    "testid": null,
    "label": null,
    "css": null,
    "xpath": null,
    "uia": {
      "automation_id": "Chrome_WidgetWin_1",
      "control_type": "UIA_WindowControlTypeId",
      "name": "Acme Internal Portal"
    },
    "bbox": [0, 0, 1920, 1080]
  },
  "value": {
    "redacted": true,
    "shape": "none",
    "text": null
  },
  "page": {
    "url_pattern": "/reports/overdue",
    "url_params": {}
  },
  "session_id": "99998888-7777-6666-5555-444433332222"
}
```

#### Invalid Fixture: Unredacted Secret in Password Input
```json
{
  "event_id": "invalid-uuid-format",
  "ts": "not-a-timestamp",
  "source": "unknown_sensor",
  "kind": "input_change",
  "app": {
    "id": "acme-portal",
    "type": "web",
    "process": "chrome.exe"
  },
  "target": {
    "role": "textbox",
    "name": "Password",
    "testid": "pwd-input"
  },
  "value": {
    "redacted": false,
    "shape": "secret_omitted",
    "text": "plaintext_password_is_illegal"
  },
  "page": {
    "url_pattern": "/login"
  },
  "session_id": "1234"
}
```
*Validation Rejection Reason:* Violates `format: uuid`, `format: date-time`, enum `source`, and schema privacy rule prohibiting plaintext in non-allowlisted credentials.

---

### 7.2 `Workflow` Fixture (Demo Task: "Weekly At-Risk Customer Segmentation")

```json
{
  "workflow_id": "wf_at_risk_seg",
  "version": 3,
  "name": "Weekly at-risk customer segmentation",
  "description": "Reads overdue accounts from Acme Portal, inspects CRM history, applies segmentation tags based on login freshness and open tickets, and posts summary note.",
  "trust": "validated",
  "provenance": {
    "evidence_episode_ids": ["ep_01", "ep_04", "ep_07", "ep_09"],
    "support": 4,
    "alignment": 0.88
  },
  "parameters": [
    {
      "name": "account_ids",
      "type": "string[]",
      "source": "step:s2_copy_accounts.output",
      "origin": "inferred",
      "confirmed": true,
      "default_value": []
    }
  ],
  "steps": [
    {
      "step_id": "s1_open_portal",
      "kind": "action",
      "verb": "navigate",
      "app": "acme-portal",
      "locators": [
        { "by": "text", "value": "Reports" }
      ],
      "input": "http://portal.acme.local/reports/overdue",
      "pre": [],
      "post": [
        { "type": "url_matches", "value": "/reports/overdue" }
      ],
      "risk": "R0",
      "required": true,
      "origin": "observed",
      "recovery": { "retries": 1, "on_fail": "pause" }
    },
    {
      "step_id": "s2_copy_accounts",
      "kind": "action",
      "verb": "read_field",
      "app": "acme-portal",
      "locators": [
        { "by": "testid", "value": "overdue-account-list" }
      ],
      "input": null,
      "pre": [
        { "type": "element_visible", "locator_ref": 0 }
      ],
      "post": [],
      "risk": "R0",
      "required": true,
      "origin": "observed",
      "recovery": { "retries": 2, "on_fail": "pause" }
    },
    {
      "step_id": "s3_loop_crm",
      "kind": "loop",
      "verb": "navigate",
      "app": "acme-crm",
      "locators": [],
      "input": null,
      "loop_iterator": {
        "source_param": "account_ids",
        "item_name": "item"
      },
      "pre": [],
      "post": [],
      "risk": "R0",
      "required": true,
      "origin": "inferred",
      "recovery": { "retries": 0, "on_fail": "abort" }
    },
    {
      "step_id": "s4_set_tag",
      "kind": "action",
      "verb": "set_field",
      "app": "acme-crm",
      "locators": [
        { "by": "testid", "value": "tag-select" },
        { "by": "role", "role": "combobox", "name": "Customer Tag" },
        { "by": "css", "value": "select#tag" }
      ],
      "input": "{{decision.segment}}",
      "pre": [
        { "type": "url_matches", "value": "/customers/{{item}}" },
        { "type": "record_id_matches", "value": "{{item}}" }
      ],
      "post": [
        { "type": "field_equals", "locator_ref": 0, "value": "{{decision.segment}}" }
      ],
      "risk": "R1",
      "required": true,
      "origin": "observed",
      "recovery": { "retries": 1, "on_fail": "pause" }
    },
    {
      "step_id": "s5_save_cust",
      "kind": "action",
      "verb": "click",
      "app": "acme-crm",
      "locators": [
        { "by": "testid", "value": "save-btn" },
        { "by": "role", "role": "button", "name": "Save" }
      ],
      "input": null,
      "pre": [
        { "type": "element_visible", "locator_ref": 0 }
      ],
      "post": [
        { "type": "network_2xx", "value": "/api/customers/:id" }
      ],
      "risk": "R1",
      "required": true,
      "origin": "observed",
      "recovery": { "retries": 1, "on_fail": "pause" }
    },
    {
      "step_id": "s6_post_summary",
      "kind": "action",
      "verb": "click",
      "app": "acme-portal",
      "locators": [
        { "by": "testid", "value": "post-team-note-btn" },
        { "by": "role", "role": "button", "name": "Post Weekly Summary" }
      ],
      "input": "Processed {{account_ids.length}} accounts.",
      "pre": [
        { "type": "url_matches", "value": "/team/notes" }
      ],
      "post": [
        { "type": "element_visible", "locator_ref": 0 }
      ],
      "risk": "R2",
      "required": true,
      "origin": "observed",
      "recovery": { "retries": 0, "on_fail": "pause" }
    }
  ],
  "decision_rules": [
    {
      "id": "segment",
      "expr": "plan == 'Pro' and last_login_days > 30",
      "then": "At-risk – High",
      "else": "At-risk – Watch",
      "origin": "inferred",
      "supported_runs": 4,
      "contradicting_runs": 0
    }
  ],
  "outcome_checks": [
    {
      "type": "app_api",
      "app": "acme-crm",
      "check": "tag_equals_for_all",
      "args": {
        "ids": "{{account_ids}}"
      }
    },
    {
      "type": "app_api",
      "app": "acme-portal",
      "check": "team_note_exists",
      "args": {
        "containing": "Weekly at-risk segmentation complete"
      }
    }
  ],
  "remote_allowed": true
}
```

---

### 7.3 Remote Command & Replay Test Fixtures

#### Valid Command Fixture: Approve Step from Phone
```json
{
  "msg_id": "msg_f9e8d7c6-b5a4-3210-9876-543210fedcba",
  "from_device": "device_iphone_15_pro_sahil",
  "to_device": "desktop_win11_sahil",
  "kind": "command",
  "sig": "e9821a4f0012bc556281736450192837465019283746501928374650192837461234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
  "payload": {
    "command_id": "cmd_01j9b44g8",
    "device_id": "device_iphone_15_pro_sahil",
    "type": "approve_step",
    "workflow_id": "wf_at_risk_seg",
    "run_id": "run_01j9b2e4t3n2y",
    "step_id": "s6_post_summary",
    "params": {},
    "issued_at": "2026-10-22T09:32:00.100Z",
    "nonce": "77af32b901fc88a1"
  }
}
```

#### Invalid Command Fixture: Replay / Stale Timestamp
```json
{
  "msg_id": "msg_stale_123",
  "from_device": "device_iphone_15_pro_sahil",
  "to_device": "desktop_win11_sahil",
  "kind": "command",
  "sig": "valid_sig_on_old_payload",
  "payload": {
    "command_id": "cmd_stale",
    "device_id": "device_iphone_15_pro_sahil",
    "type": "run_workflow",
    "workflow_id": "wf_at_risk_seg",
    "params": {},
    "issued_at": "2026-10-20T08:00:00.000Z",
    "nonce": "old_nonce_already_logged"
  }
}
```
*Validation Rejection Reason:* `issued_at` delta exceeds the 60-second freshness window; `nonce` collision triggers immediate rejection with `SIGNATURE_INVALID`.

---

## 8. Day-1 Contract Sign-Off & Verification Checklist

To freeze Version 1.0.0, each owner validates their integration points against these schemas:

- [ ] **M1 (Learning):** Extension batch poster matches `POST /events/batch` with `RawEvent` schema; Normalizer generates valid `Action` records; Compilation outputs valid `Workflow` JSON.
- [ ] **M2 (Execution & Safety):** Playwright interpreter takes `Workflow` fixture and correctly processes ranked locators; Pre/post condition parser supports all 5 condition types; Risk classifier blocks `R3` buttons; Ed25519 verifier accepts Phone PWA signatures.
- [ ] **M3 (Frontend):** React dashboard loads `Workflow` fixture and renders steps, parameters, and decision rules; WebSocket client consumes `ws://127.0.0.1:7777/api/v1/stream` events; Phone PWA generates non-extractable Ed25519 keys via WebCrypto.
- [ ] **M4 (Integration Lead):** Pydantic models generated into `agent/ghost/contracts/`; TypeScript types generated into `web/src/types/contracts.ts`; e2e test verifies that `make reset` seeds both Acme Portal and CRM with fixture accounts.
