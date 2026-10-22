# Digital Ghost — Shared System Contracts Specification
**Version:** `1.1.0-draft` (reviewed; runtime gates still required)
**Target:** Local-first Windows 11 Desktop Agent + Chrome MV3 Extension + Relay + Web / PWA Dashboard  
**Owners:** M4 (Integration Lead & Schema Custodian), with sign-off from M1 (Learning), M2 (Execution & Safety), M3 (Frontend)

> Normative corrections and runtime requirements: [contracts/REVIEW.md](contracts/REVIEW.md). These take precedence over legacy illustrative TypeScript and protocol examples below. JSON schema files are authoritative for wire shapes. This is a breaking draft revision, not a frozen or deployed implementation.

> Previous-product reference: the user identified Blinky at `C:\Projects\Blinky` as a closely related product. Consult [BLINKY_REFERENCE.md](BLINKY_REFERENCE.md) for inspected source paths and adaptation notes when implementing capture, targeting, execution and remote control. DigitalSoul's schemas and runtime requirements govern integration.

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
      "enum": [
        "extension",
        "desktop"
      ],
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
      "required": [
        "id",
        "type",
        "process"
      ],
      "properties": {
        "id": {
          "type": "string"
        },
        "type": {
          "type": "string",
          "enum": [
            "web",
            "native"
          ]
        },
        "domain": {
          "type": [
            "string",
            "null"
          ]
        },
        "process": {
          "type": "string"
        },
        "window_title": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "additionalProperties": false
    },
    "target": {
      "type": "object",
      "properties": {
        "role": {
          "type": [
            "string",
            "null"
          ]
        },
        "name": {
          "type": [
            "string",
            "null"
          ]
        },
        "testid": {
          "type": [
            "string",
            "null"
          ]
        },
        "label": {
          "type": [
            "string",
            "null"
          ]
        },
        "css": {
          "type": [
            "string",
            "null"
          ]
        },
        "xpath": {
          "type": [
            "string",
            "null"
          ]
        },
        "uia": {
          "type": [
            "object",
            "null"
          ],
          "properties": {
            "automation_id": {
              "type": [
                "string",
                "null"
              ]
            },
            "control_type": {
              "type": [
                "string",
                "null"
              ]
            },
            "name": {
              "type": [
                "string",
                "null"
              ]
            }
          },
          "additionalProperties": false
        },
        "bbox": {
          "type": [
            "array",
            "null"
          ],
          "items": {
            "type": "number"
          },
          "minItems": 4,
          "maxItems": 4
        }
      },
      "additionalProperties": false
    },
    "value": {
      "type": "object",
      "required": [
        "redacted",
        "shape",
        "text"
      ],
      "properties": {
        "redacted": {
          "type": "boolean"
        },
        "shape": {
          "type": "string",
          "enum": [
            "none",
            "text:short",
            "text:long",
            "number",
            "email",
            "secret_omitted"
          ]
        },
        "text": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "redacted": {
                "const": true
              }
            },
            "required": [
              "redacted"
            ]
          },
          "then": {
            "properties": {
              "text": {
                "type": "null"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "shape": {
                "const": "secret_omitted"
              }
            },
            "required": [
              "shape"
            ]
          },
          "then": {
            "properties": {
              "redacted": {
                "const": true
              },
              "text": {
                "type": "null"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "shape": {
                "const": "none"
              }
            },
            "required": [
              "shape"
            ]
          },
          "then": {
            "properties": {
              "text": {
                "type": "null"
              }
            }
          }
        }
      ]
    },
    "page": {
      "type": "object",
      "required": [
        "url_pattern"
      ],
      "properties": {
        "url_pattern": {
          "type": "string"
        },
        "url_params": {
          "type": "object",
          "additionalProperties": {
            "type": "string"
          }
        }
      },
      "additionalProperties": false
    },
    "session_id": {
      "type": "string",
      "format": "uuid"
    }
  },
  "additionalProperties": false
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
    "workflow_id": {
      "type": "string"
    },
    "version": {
      "type": "integer",
      "minimum": 1
    },
    "name": {
      "type": "string",
      "minLength": 1
    },
    "description": {
      "type": "string"
    },
    "trust": {
      "type": "string",
      "enum": [
        "observed",
        "candidate",
        "validated",
        "supervised",
        "trusted"
      ]
    },
    "provenance": {
      "type": "object",
      "required": [
        "evidence_episode_ids",
        "support",
        "alignment"
      ],
      "properties": {
        "evidence_episode_ids": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "support": {
          "type": "integer",
          "minimum": 1
        },
        "alignment": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        }
      },
      "additionalProperties": false
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
      },
      "minItems": 1
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
      },
      "minItems": 1
    },
    "remote_allowed": {
      "type": "boolean"
    }
  },
  "$defs": {
    "WorkflowParameter": {
      "type": "object",
      "required": [
        "name",
        "type",
        "source",
        "origin",
        "confirmed"
      ],
      "properties": {
        "name": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "type": {
          "type": "string",
          "enum": [
            "string",
            "string[]",
            "number",
            "boolean"
          ]
        },
        "source": {
          "type": "string"
        },
        "origin": {
          "type": "string",
          "enum": [
            "observed",
            "inferred",
            "user_defined"
          ]
        },
        "confirmed": {
          "type": "boolean"
        },
        "default_value": {}
      },
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "type": {
                "const": "string"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "properties": {
              "default_value": {
                "type": "string"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "string[]"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "properties": {
              "default_value": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "number"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "properties": {
              "default_value": {
                "type": "number"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "boolean"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "properties": {
              "default_value": {
                "type": "boolean"
              }
            }
          }
        }
      ]
    },
    "StepLocator": {
      "type": "object",
      "required": [
        "by"
      ],
      "properties": {
        "by": {
          "type": "string",
          "enum": [
            "testid",
            "role",
            "label",
            "text",
            "css",
            "xpath",
            "uia"
          ],
          "minLength": 1
        },
        "value": {
          "type": "string",
          "minLength": 1
        },
        "role": {
          "type": "string",
          "minLength": 1
        },
        "name": {
          "type": "string",
          "minLength": 1
        },
        "uia_id": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "by": {
                "const": "testid"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "text"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "label"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "css"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "xpath"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "role"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "role",
              "name"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "by": {
                "const": "uia"
              }
            },
            "required": [
              "by"
            ]
          },
          "then": {
            "required": [
              "uia_id"
            ]
          }
        }
      ]
    },
    "PrePostCondition": {
      "type": "object",
      "required": [
        "type"
      ],
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
        "value": {
          "type": "string"
        },
        "locator_ref": {
          "type": "integer",
          "minimum": 0
        }
      },
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "type": {
                "const": "url_matches"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "record_id_matches"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "network_2xx"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "required": [
              "value"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "element_visible"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "required": [
              "locator_ref"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "type": {
                "const": "field_equals"
              }
            },
            "required": [
              "type"
            ]
          },
          "then": {
            "required": [
              "locator_ref",
              "value"
            ]
          }
        }
      ]
    },
    "WorkflowStep": {
      "type": "object",
      "required": [
        "step_id",
        "kind",
        "app",
        "locators",
        "risk",
        "required",
        "origin",
        "recovery",
        "pre",
        "post"
      ],
      "properties": {
        "step_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "kind": {
          "type": "string",
          "enum": [
            "action",
            "loop",
            "decision",
            "assert"
          ]
        },
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
        "app": {
          "type": "string"
        },
        "locators": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/StepLocator"
          }
        },
        "input": {
          "type": [
            "string",
            "number",
            "boolean",
            "null"
          ]
        },
        "loop_iterator": {
          "type": "object",
          "properties": {
            "source_param": {
              "type": "string"
            },
            "item_name": {
              "type": "string"
            },
            "max_iterations": {
              "type": "integer",
              "minimum": 1,
              "maximum": 1000
            }
          },
          "additionalProperties": false,
          "required": [
            "source_param",
            "item_name",
            "max_iterations"
          ]
        },
        "pre": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/PrePostCondition"
          }
        },
        "post": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/PrePostCondition"
          }
        },
        "risk": {
          "type": "string",
          "enum": [
            "R0",
            "R1",
            "R2",
            "R3"
          ]
        },
        "required": {
          "type": "boolean"
        },
        "origin": {
          "type": "string",
          "enum": [
            "observed",
            "inferred"
          ]
        },
        "allow_input_fallback": {
          "type": "boolean",
          "default": false
        },
        "recovery": {
          "type": "object",
          "required": [
            "retries",
            "on_fail"
          ],
          "properties": {
            "retries": {
              "type": "integer",
              "minimum": 0,
              "maximum": 3
            },
            "on_fail": {
              "type": "string",
              "enum": [
                "pause",
                "skip",
                "abort",
                "retry_then_pause"
              ]
            }
          },
          "additionalProperties": false
        },
        "body": {
          "type": "array",
          "minItems": 1,
          "items": {
            "$ref": "#/$defs/WorkflowStep"
          }
        },
        "rule_id": {
          "type": "string",
          "minLength": 1
        },
        "output_name": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "output_type": {
          "enum": [
            "string",
            "string[]",
            "number",
            "boolean"
          ]
        },
        "timeout_ms": {
          "type": "integer",
          "minimum": 1,
          "maximum": 60000
        }
      },
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "kind": {
                "const": "loop"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "required": [
              "body",
              "loop_iterator"
            ],
            "not": {
              "anyOf": [
                {
                  "required": [
                    "verb"
                  ]
                },
                {
                  "required": [
                    "rule_id"
                  ]
                }
              ]
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "decision"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "required": [
              "rule_id"
            ],
            "not": {
              "required": [
                "verb"
              ]
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "action"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "required": [
              "verb"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "assert"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "required": [
              "verb"
            ],
            "properties": {
              "verb": {
                "const": "assert_state"
              },
              "post": {
                "minItems": 1
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "verb": {
                "const": "read_field"
              }
            },
            "required": [
              "verb"
            ]
          },
          "then": {
            "required": [
              "output_name",
              "output_type"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "verb": {
                "const": "navigate"
              }
            },
            "required": [
              "verb"
            ]
          },
          "then": {
            "required": [
              "input"
            ],
            "properties": {
              "input": {
                "type": "string",
                "minLength": 1
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "verb": {
                "const": "click"
              }
            },
            "required": [
              "verb"
            ]
          },
          "then": {
            "properties": {
              "input": {
                "type": "null"
              },
              "recovery": {
                "properties": {
                  "retries": {
                    "const": 0
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "required": {
                "const": true
              }
            },
            "required": [
              "required"
            ]
          },
          "then": {
            "properties": {
              "recovery": {
                "properties": {
                  "on_fail": {
                    "enum": [
                      "pause",
                      "abort",
                      "retry_then_pause"
                    ]
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "enum": [
                  "action",
                  "assert"
                ]
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "body"
                  ]
                },
                {
                  "required": [
                    "loop_iterator"
                  ]
                },
                {
                  "required": [
                    "rule_id"
                  ]
                }
              ]
            }
          }
        },
        {
          "if": {
            "properties": {
              "verb": {
                "enum": [
                  "click",
                  "set_field",
                  "select_option",
                  "read_field",
                  "wait_visible"
                ]
              }
            },
            "required": [
              "verb"
            ]
          },
          "then": {
            "properties": {
              "locators": {
                "minItems": 1
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "risk": {
                "enum": [
                  "R1",
                  "R2"
                ]
              }
            },
            "required": [
              "risk"
            ]
          },
          "then": {
            "properties": {
              "pre": {
                "minItems": 1
              },
              "post": {
                "minItems": 1
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "decision"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "body"
                  ]
                },
                {
                  "required": [
                    "loop_iterator"
                  ]
                }
              ]
            }
          }
        },
        {
          "if": {
            "properties": {
              "verb": {
                "enum": [
                  "set_field",
                  "select_option"
                ]
              }
            },
            "required": [
              "verb"
            ]
          },
          "then": {
            "required": [
              "input"
            ],
            "properties": {
              "input": {
                "type": [
                  "string",
                  "number",
                  "boolean"
                ]
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "risk": {
                "enum": [
                  "R2",
                  "R3"
                ]
              }
            },
            "required": [
              "risk"
            ]
          },
          "then": {
            "properties": {
              "recovery": {
                "properties": {
                  "retries": {
                    "const": 0
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "not": {
              "properties": {
                "verb": {
                  "const": "read_field"
                }
              },
              "required": [
                "verb"
              ]
            }
          },
          "then": {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "output_name"
                  ]
                },
                {
                  "required": [
                    "output_type"
                  ]
                }
              ]
            }
          }
        }
      ]
    },
    "DecisionRule": {
      "type": "object",
      "required": [
        "id",
        "expr",
        "then",
        "else",
        "origin",
        "supported_runs",
        "contradicting_runs",
        "confirmed"
      ],
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "expr": {
          "type": "string"
        },
        "then": {
          "type": "string"
        },
        "else": {
          "type": "string"
        },
        "origin": {
          "type": "string",
          "enum": [
            "inferred",
            "user_defined"
          ]
        },
        "supported_runs": {
          "type": "integer",
          "minimum": 0
        },
        "contradicting_runs": {
          "type": "integer",
          "minimum": 0
        },
        "confirmed": {
          "type": "boolean"
        }
      },
      "additionalProperties": false
    },
    "OutcomeCheck": {
      "type": "object",
      "required": [
        "type",
        "app",
        "check",
        "args"
      ],
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "app_api",
            "db_query",
            "page_assertion"
          ]
        },
        "app": {
          "type": "string"
        },
        "check": {
          "type": "string"
        },
        "args": {
          "type": "object"
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
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
  "required": [
    "error"
  ],
  "properties": {
    "error": {
      "type": "object",
      "required": [
        "code",
        "message",
        "retryable",
        "trace_id"
      ],
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
            "INTERNAL",
            "CONFLICT",
            "STALE_APPROVAL",
            "REPLAY_DETECTED",
            "COMMAND_EXPIRED",
            "RATE_LIMITED",
            "UNSUPPORTED_VERSION"
          ]
        },
        "message": {
          "type": "string"
        },
        "retryable": {
          "type": "boolean"
        },
        "run_id": {
          "type": [
            "string",
            "null"
          ]
        },
        "step_id": {
          "type": [
            "string",
            "null"
          ]
        },
        "details": {
          "type": "object"
        },
        "trace_id": {
          "type": "string",
          "format": "uuid"
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
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
| `POST` | `/stream/tickets` | Mint single-use stream ticket | M4 | - | `201 { ticket: string, expires_at: string }` |
| `GET` | `/health` | Heartbeat & subsystem connectivity | M4 | — | `200 HealthStatus` |

---

### Detailed Endpoint Contracts

#### `POST /api/v1/workflows/{id}/runs`
**Request Body:**
```json
{
  "mode": "supervised",
  "workflow_version": 4,
  "parameters": {}
}
```
**Response `201 Created`:**
```json
{
  "run_id": "run_01j9b2e4t3n2y",
  "workflow_id": "wf_at_risk_seg",
  "workflow_version": 4,
  "mode": "supervised",
  "state": "preflight",
  "created_at": "2026-10-22T09:30:00.000Z",
  "started_at": null,
  "finished_at": null,
  "current_step_id": null,
  "current_loop_index": 0,
  "total_loop_count": 0,
  "parameters": {},
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
**Authentication:** Browser clients obtain a single-use, 30-second stream ticket via authenticated `POST /api/v1/stream/tickets`, then connect to `/api/v1/stream?ticket=...`. Consume the ticket atomically, redact it from logs, and verify Origin. Browser WebSocket constructors cannot set an Authorization header. Native clients may use the Bearer header.

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
* **Relay Server:** Routing service that can read signed payloads. WSS and signatures do not provide end-to-end confidentiality. Do not send field values through this MVP relay; end-to-end encryption requires a separate reviewed protocol.

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
4. Desktop prompts user: *"Allow iPhone 15 Pro (public-key fingerprint shown on both devices) to control this desktop?"*
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
2. `sig` must verify `ed25519_verify(pubkey, UTF8("digitalghost.command.v1\n") + JCS(envelope_without_sig), sig)`.
3. `|now() - issued_at| <= 60` seconds (Clock skew & freshness check).
4. `nonce` has not been seen in the last 10 minutes (Replay prevention).

---

## 6. Action Risk Classification & Policy Rules

Every step is evaluated against its risk class before execution.

| Risk Class | Description | Examples | Supervised Mode | Trusted Mode | Remote Phone Mode |
|---|---|---|---|---|---|
| **R0** | Read-Only | Navigate, open record, read field, assert visible | Auto-allow | Auto-allow | Auto-allow |
| **R1** | Reversible Write | Set tag, edit draft text field | Requires 1-click confirmation | Auto-allow (audited) | Same trust/approval rules as local |
| **R2** | External Effect | Post team note, send message, commit changes | Requires explicit approval | Requires explicit approval bound to exact effects | Requires signed approval from phone |
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
  "version": 4,
  "name": "Weekly at-risk customer segmentation",
  "description": "Reads overdue accounts, classifies each by plan and login age, saves tags, and posts a run-specific summary.",
  "trust": "validated",
  "provenance": {
    "evidence_episode_ids": [
      "ep_01",
      "ep_04",
      "ep_07",
      "ep_09"
    ],
    "support": 4,
    "alignment": 0.88
  },
  "parameters": [
    {
      "name": "account_ids",
      "type": "string[]",
      "source": "step:s2_copy_accounts.output",
      "origin": "inferred",
      "confirmed": true
    }
  ],
  "steps": [
    {
      "step_id": "s1_open_portal",
      "kind": "action",
      "verb": "navigate",
      "app": "acme-portal",
      "locators": [
        {
          "by": "text",
          "value": "Reports"
        }
      ],
      "input": "http://portal.acme.local/reports/overdue",
      "pre": [],
      "post": [
        {
          "type": "url_matches",
          "value": "/reports/overdue"
        }
      ],
      "risk": "R0",
      "required": true,
      "origin": "observed",
      "recovery": {
        "retries": 1,
        "on_fail": "pause"
      }
    },
    {
      "step_id": "s2_copy_accounts",
      "kind": "action",
      "verb": "read_field",
      "app": "acme-portal",
      "locators": [
        {
          "by": "testid",
          "value": "overdue-account-list"
        }
      ],
      "input": null,
      "pre": [
        {
          "type": "element_visible",
          "locator_ref": 0
        }
      ],
      "post": [],
      "risk": "R0",
      "required": true,
      "origin": "observed",
      "recovery": {
        "retries": 2,
        "on_fail": "pause"
      },
      "output_name": "account_ids",
      "output_type": "string[]"
    },
    {
      "step_id": "s3_loop_crm",
      "kind": "loop",
      "app": "acme-crm",
      "locators": [],
      "input": null,
      "loop_iterator": {
        "source_param": "account_ids",
        "item_name": "item",
        "max_iterations": 100
      },
      "pre": [],
      "post": [],
      "risk": "R0",
      "required": true,
      "origin": "inferred",
      "recovery": {
        "retries": 0,
        "on_fail": "abort"
      },
      "body": [
        {
          "step_id": "s3_open_record",
          "kind": "action",
          "verb": "navigate",
          "app": "acme-crm",
          "locators": [],
          "input": "http://crm.acme.local/customers/{{item}}",
          "pre": [],
          "post": [
            {
              "type": "record_id_matches",
              "value": "{{item}}"
            }
          ],
          "risk": "R0",
          "required": true,
          "origin": "observed",
          "recovery": {
            "retries": 0,
            "on_fail": "pause"
          }
        },
        {
          "step_id": "s3_read_plan",
          "kind": "action",
          "verb": "read_field",
          "app": "acme-crm",
          "locators": [
            {
              "by": "testid",
              "value": "customer-plan"
            }
          ],
          "input": null,
          "pre": [
            {
              "type": "record_id_matches",
              "value": "{{item}}"
            }
          ],
          "post": [],
          "risk": "R0",
          "required": true,
          "origin": "observed",
          "recovery": {
            "retries": 0,
            "on_fail": "pause"
          },
          "output_name": "plan",
          "output_type": "string"
        },
        {
          "step_id": "s3_read_days",
          "kind": "action",
          "verb": "read_field",
          "app": "acme-crm",
          "locators": [
            {
              "by": "testid",
              "value": "last-login-days"
            }
          ],
          "input": null,
          "pre": [
            {
              "type": "record_id_matches",
              "value": "{{item}}"
            }
          ],
          "post": [],
          "risk": "R0",
          "required": true,
          "origin": "observed",
          "recovery": {
            "retries": 0,
            "on_fail": "pause"
          },
          "output_name": "last_login_days",
          "output_type": "number"
        },
        {
          "step_id": "s3_decide",
          "kind": "decision",
          "app": "acme-crm",
          "locators": [],
          "pre": [],
          "post": [],
          "risk": "R0",
          "required": true,
          "origin": "inferred",
          "recovery": {
            "retries": 0,
            "on_fail": "abort"
          },
          "rule_id": "segment"
        },
        {
          "step_id": "s4_set_tag",
          "kind": "action",
          "verb": "select_option",
          "app": "acme-crm",
          "locators": [
            {
              "by": "testid",
              "value": "tag-select"
            },
            {
              "by": "role",
              "role": "combobox",
              "name": "Customer Tag"
            },
            {
              "by": "css",
              "value": "select#tag"
            }
          ],
          "input": "{{decision.segment}}",
          "pre": [
            {
              "type": "url_matches",
              "value": "/customers/{{item}}"
            },
            {
              "type": "record_id_matches",
              "value": "{{item}}"
            }
          ],
          "post": [
            {
              "type": "field_equals",
              "locator_ref": 0,
              "value": "{{decision.segment}}"
            }
          ],
          "risk": "R1",
          "required": true,
          "origin": "observed",
          "recovery": {
            "retries": 1,
            "on_fail": "pause"
          }
        },
        {
          "step_id": "s5_save_cust",
          "kind": "action",
          "verb": "click",
          "app": "acme-crm",
          "locators": [
            {
              "by": "testid",
              "value": "save-btn"
            },
            {
              "by": "role",
              "role": "button",
              "name": "Save"
            }
          ],
          "input": null,
          "pre": [
            {
              "type": "element_visible",
              "locator_ref": 0
            },
            {
              "type": "record_id_matches",
              "value": "{{item}}"
            }
          ],
          "post": [
            {
              "type": "network_2xx",
              "value": "/api/customers/{{item}}"
            }
          ],
          "risk": "R2",
          "required": true,
          "origin": "observed",
          "recovery": {
            "retries": 0,
            "on_fail": "pause"
          }
        }
      ]
    },
    {
      "step_id": "s6_open_notes",
      "kind": "action",
      "verb": "navigate",
      "app": "acme-portal",
      "locators": [],
      "input": "http://portal.acme.local/team/notes",
      "pre": [],
      "post": [
        {
          "type": "url_matches",
          "value": "/team/notes"
        }
      ],
      "risk": "R0",
      "required": true,
      "origin": "observed",
      "recovery": {
        "retries": 0,
        "on_fail": "pause"
      }
    },
    {
      "step_id": "s6_fill_summary",
      "kind": "action",
      "verb": "set_field",
      "app": "acme-portal",
      "locators": [
        {
          "by": "testid",
          "value": "team-note-body"
        }
      ],
      "input": "Weekly at-risk segmentation complete; run={{run_id}}; processed={{account_ids.length}}",
      "pre": [
        {
          "type": "url_matches",
          "value": "/team/notes"
        }
      ],
      "post": [
        {
          "type": "field_equals",
          "locator_ref": 0,
          "value": "Weekly at-risk segmentation complete; run={{run_id}}; processed={{account_ids.length}}"
        }
      ],
      "risk": "R1",
      "required": true,
      "origin": "observed",
      "recovery": {
        "retries": 0,
        "on_fail": "pause"
      }
    },
    {
      "step_id": "s6_post_summary",
      "kind": "action",
      "verb": "click",
      "app": "acme-portal",
      "locators": [
        {
          "by": "testid",
          "value": "post-team-note-btn"
        },
        {
          "by": "role",
          "role": "button",
          "name": "Post Weekly Summary"
        }
      ],
      "input": null,
      "pre": [
        {
          "type": "url_matches",
          "value": "/team/notes"
        }
      ],
      "post": [
        {
          "type": "network_2xx",
          "value": "/api/team/notes"
        }
      ],
      "risk": "R2",
      "required": true,
      "origin": "observed",
      "recovery": {
        "retries": 0,
        "on_fail": "pause"
      }
    }
  ],
  "decision_rules": [
    {
      "id": "segment",
      "expr": "plan == 'Pro' and last_login_days > 30",
      "then": "At-risk \u2013 High",
      "else": "At-risk \u2013 Watch",
      "origin": "inferred",
      "supported_runs": 4,
      "contradicting_runs": 0,
      "confirmed": true
    }
  ],
  "outcome_checks": [
    {
      "type": "app_api",
      "app": "acme-crm",
      "check": "tag_equals_for_all",
      "args": {
        "ids": "{{account_ids}}",
        "expected_by_id": "{{expected_tags}}"
      }
    },
    {
      "type": "app_api",
      "app": "acme-portal",
      "check": "team_note_exists",
      "args": {
        "containing": "Weekly at-risk segmentation complete; run={{run_id}}; processed={{account_ids.length}}",
        "run_id": "{{run_id}}",
        "expected_count": 1
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
  "sig": "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
  "payload": {
    "command_id": "cmd_01j9b44g8",
    "device_id": "device_iphone_15_pro_sahil",
    "type": "approve_step",
    "workflow_id": "wf_at_risk_seg",
    "run_id": "run_01j9b2e4t3n2y",
    "step_id": "s6_post_summary",
    "params": {},
    "issued_at": "2026-10-22T09:32:00.100Z",
    "nonce": "77af32b901fc88a177af32b901fc88a1",
    "workflow_version": 4,
    "approval_id": "approval_demo_01",
    "effect_hash": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "protocol_version": 1
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
  },
  "protocol_version": 1
}
```
*Validation Rejection Reason:* This fixture has malformed signature and nonce fields and fails schema validation. A correctly signed but stale command must return `COMMAND_EXPIRED`; a consumed nonce must return `REPLAY_DETECTED`. See REVIEW.md for runtime gates. The valid fixture is shape-only and has a zero placeholder signature.

---

## 8. Day-1 Contract Sign-Off & Verification Checklist

To freeze Version 1.1.0, each owner validates their integration points against these schemas:

- [ ] **M1 (Learning):** Extension batch poster matches `POST /events/batch` with `RawEvent` schema; Normalizer generates valid `Action` records; Compilation outputs valid `Workflow` JSON.
- [ ] **M2 (Execution & Safety):** Playwright interpreter takes `Workflow` fixture and correctly processes ranked locators; Pre/post condition parser supports all 5 condition types; Risk classifier blocks `R3` buttons; Ed25519 verifier accepts Phone PWA signatures.
- [ ] **M3 (Frontend):** React dashboard loads `Workflow` fixture and renders steps, parameters, and decision rules; WebSocket client consumes `ws://127.0.0.1:7777/api/v1/stream` events; Phone PWA generates non-extractable Ed25519 keys via WebCrypto.
- [ ] **M4 (Integration Lead):** Pydantic models generated into `agent/ghost/contracts/`; TypeScript types generated into `web/src/types/contracts.ts`; e2e test verifies that `make reset` seeds both Acme Portal and CRM with fixture accounts.

---

## 9. Four-Person Responsibility Matrix & Ownership Boundaries

Every directory in the monorepo has **exactly one owner**. Shared files (`contracts/`, `agent/ghost/db/`, `agent/ghost/app.py`, `config.py`, and `bus.py`) are owned by M4 and can only be modified through reviewed Pull Requests.

```
ghost/
  contracts/        JSON Schemas, OpenAPI, fixtures, generated TS + Pydantic types   (M4)
  agent/ghost/
    app.py, config.py, bus.py, db/ (models, migrations)                            (M4)
    collector/      window + UIA observer, extension ingest endpoint               (M1)
    learning/       normalize, abstract, segment, mine, describe, compile          (M1)
    api/learn_routes.py                                                            (M1)
    execution/      interpreter, plan runtime, recovery, verification              (M2)
    drivers/        browser.py, uia.py, input_fallback.py                          (M2)
    safety/         policy.py, risk.py, guard.py, killswitch.py, tray.py           (M2)
    remote/         relay_client.py, signing.py                                     (M2)
    api/run_routes.py, api/remote_routes.py                                        (M2)
    audit/          audit.py (append-only log)                                     (M4)
  extension/        Chrome MV3 observer                                            (M1)
  relay/            cloud relay service                                            (M2)
  web/              dashboard + phone PWA                                          (M3)
  demo-apps/        acme-portal, acme-crm, seed/reset scripts                      (M4)
  tests/e2e/, tests/safety/, ci/                                                   (M4)
```

### Detailed Role Matrix

| Dimension | **M1 — Learning Engine** | **M2 — Execution & Safety** | **M3 — Frontend & Remote UX** | **M4 — Integration Lead** |
|---|---|---|---|---|
| **Role Name** | Learning & Extraction Lead | Execution & Safety Architect | Frontend & PWA Engineer | Integration Lead & Repo Custodian |
| **Owned Directories** | `extension/`<br>`agent/ghost/collector/`<br>`agent/ghost/learning/`<br>`agent/ghost/api/learn_routes.py` | `agent/ghost/execution/`<br>`agent/ghost/drivers/`<br>`agent/ghost/safety/`<br>`agent/ghost/remote/`<br>`agent/ghost/api/run_routes.py`<br>`agent/ghost/api/remote_routes.py`<br>`relay/` | `web/` (Local React Dashboard + Remote Phone PWA) | `contracts/`<br>`agent/ghost/app.py`<br>`agent/ghost/config.py`<br>`agent/ghost/bus.py`<br>`agent/ghost/db/`<br>`agent/ghost/audit/`<br>`demo-apps/`<br>`tests/e2e/`<br>`tests/safety/`<br>`ci/` |
| **Core Responsibilities** | Consented capture, redaction at source, action normalization, episode segmentation, sequence alignment, OmniRoute LLM hypothesis prompts, validation compilation, recommendation scoring. | Plan interpreter, locator resolution & fallback, pre/postcondition evaluation, loop checkpoint resume, policy engine & risk classifier, mistake guards, kill switch thread & tray icon, relay service & WebCrypto Ed25519 verification. | Consent/onboarding UX, observation controls, candidate + evidence review, workflow editor, execution console, step approvals, live guard warnings, audit explorer + data wipe, phone PWA. | Schema custodianship, type generation, FastAPI skeleton & origin checks, SQLite WAL migrations, seeded demo web apps with reset APIs, CI pipelines, daily e2e smoke tests, independent safety test suite. |
| **Key Deliverables** | Learns demo workflow from 4 recorded sessions; emits valid `Workflow` JSON. | Runs demo workflow end-to-end to `succeeded` status; blocks `R3` actions; executes signed commands from phone. | All screens responsive and functional against mock fixtures, then live WebSocket / REST API. | Frozen Day-1 contracts; green e2e test suite; repeatable demo apps (`make reset`); final rehearsal runbook. |
| **Depends On** | Contracts & fixtures (M4), Demo apps (M4). | Contracts (M4), Demo apps (M4), Validated workflow (M1; fixture until Phase 2). | Contracts & fixtures (M4), Live REST & WS APIs (M1, M2). | Inputs and pull requests from M1, M2, and M3. |
| **Provides To** | Workflows ➔ M2;<br>Candidates & Workflows API ➔ M3. | Run status & approvals API ➔ M3;<br>Policy rules ➔ M4 safety tests. | Validated workflows & user edits ➔ M1. | Stable base, database schemas, fixtures, and CI validation to all members. |

### Ways of Working & Collaboration Protocols
1. **Protected `main` Branch:** Direct pushes to `main` are disabled. All changes require an approved Pull Request with passing CI (schema validation, unit tests, and e2e smoke).
2. **Fixture-First Development:** M2 and M3 build and test exclusively against static JSON fixtures in `contracts/fixtures/` until Integration Checkpoints wire in live modules.
3. **Daily 15-Minute Integration Standup:** M4 leads daily syncs to evaluate checkpoint gates (CP0 through CP4). A phase is completed only when its checkpoint script passes on a clean machine.
4. **Independent Safety Verification:** M2 authors the runtime safety and guard code (`agent/ghost/safety/`), while M4 authors the adversarial test suite (`tests/safety/`) that attempts to bypass it (e.g. injecting R3 buttons, spoofing timestamps, foreign Origin requests). Neither edits the other's test or enforcement files.
