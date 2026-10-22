/* eslint-disable */
/**
 * GENERATED FILE. Do not edit by hand.
 * Source: contracts/*.schema.json (JSON Schema Draft 2020-12).
 * Regenerate with: python contracts/generate.py
 */

export interface RemoteCommandEnvelope {
  protocol_version: 1
  msg_id: string
  from_device: string
  to_device: string
  kind: "command"
  sig: string
  payload: {
    command_id: string
    device_id: string
    workflow_id?: string
    run_id?: string
    step_id?: string
    approval_id?: string
    type:
      | "run_workflow"
      | "approve_step"
      | "reject_step"
      | "pause_run"
      | "resume_run"
      | "stop_run"
      | "get_status"
      | "list_workflows"
    protocol_version?: 1
    workflow_version?: number
    effect_hash?: string
    params?: {
      [k: string]: unknown
    }
    issued_at: string
    nonce: string
  }
}

export interface GhostError {
  error: {
    code:
      | "VALIDATION_ERROR"
      | "NOT_FOUND"
      | "UNAUTHORIZED"
      | "POLICY_BLOCKED"
      | "APPROVAL_REQUIRED"
      | "LOCATOR_NOT_FOUND"
      | "LOCATOR_AMBIGUOUS"
      | "PRECONDITION_FAILED"
      | "POSTCONDITION_FAILED"
      | "GUARD_TRIGGERED"
      | "APP_UNREACHABLE"
      | "DEVICE_OFFLINE"
      | "SIGNATURE_INVALID"
      | "INTERNAL"
      | "CONFLICT"
      | "STALE_APPROVAL"
      | "REPLAY_DETECTED"
      | "COMMAND_EXPIRED"
      | "RATE_LIMITED"
      | "UNSUPPORTED_VERSION"
    message: string
    retryable: boolean
    run_id?: string | null
    step_id?: string | null
    details?: {
      [k: string]: unknown
    }
    trace_id: string
  }
}

export interface OmniRouteHypothesisResponse {
  /**
   * Human-readable label for the discovered workflow.
   */
  name: string
  /**
   * Summary of the business process and expected input/output.
   */
  description: string
  inferred_parameters: {
    name: string
    type: "string" | "string[]" | "number" | "boolean"
    source: string
    description: string
  }[]
  inferred_decision_rules: {
    id: string
    expr: string
    then: string
    else: string
    explanation: string
  }[]
  inferred_outcome_checks: {
    type: "app_api" | "db_query" | "page_assertion"
    app: string
    check: string
    args: {
      [k: string]: unknown
    }
  }[]
}

export interface RawEvent {
  /**
   * Unique identifier for the captured event.
   */
  event_id: string
  /**
   * ISO 8601 UTC timestamp with millisecond precision.
   */
  ts: string
  /**
   * Originating collector sensor.
   */
  source: "extension" | "desktop"
  /**
   * Semantic category of the interaction.
   */
  kind: "navigate" | "click" | "input_change" | "submit" | "focus" | "window_switch" | "clipboard_copy"
  app: {
    id: string
    type: "web" | "native"
    domain?: string | null
    process: string
    window_title?: string | null
  }
  target: {
    role?: string | null
    name?: string | null
    testid?: string | null
    label?: string | null
    css?: string | null
    xpath?: string | null
    uia?: {
      automation_id?: string | null
      control_type?: string | null
      name?: string | null
    } | null
    /**
     * @minItems 4
     * @maxItems 4
     */
    bbox?: [number, number, number, number] | null
  }
  value: {
    redacted: boolean
    shape: "none" | "text:short" | "text:long" | "number" | "email" | "secret_omitted"
    text: string | null
  }
  page: {
    url_pattern: string
    url_params?: {
      [k: string]: string
    }
  }
  session_id: string
}

export type WorkflowParameter = {
  name: string
  type: "string" | "string[]" | "number" | "boolean"
  source: string
  origin: "observed" | "inferred" | "user_defined"
  confirmed: boolean
  default_value?: unknown
}
export type WorkflowStep = {
  step_id: string
  kind: "action" | "loop" | "decision" | "assert"
  verb?: "navigate" | "click" | "set_field" | "read_field" | "select_option" | "wait_visible" | "assert_state"
  app: string
  locators: StepLocator[]
  input?: string | number | boolean | null
  loop_iterator?: {
    source_param: string
    item_name: string
    max_iterations: number
  }
  pre: PrePostCondition[]
  post: PrePostCondition[]
  risk: "R0" | "R1" | "R2" | "R3"
  required: boolean
  origin: "observed" | "inferred"
  allow_input_fallback?: boolean
  recovery: {
    retries: number
    on_fail: "pause" | "skip" | "abort" | "retry_then_pause"
  }
  /**
   * @minItems 1
   */
  body?: [WorkflowStep, ...WorkflowStep[]]
  rule_id?: string
  output_name?: string
  output_type?: "string" | "string[]" | "number" | "boolean"
  timeout_ms?: number
} & {
  step_id: string
  kind: "action" | "loop" | "decision" | "assert"
  verb?: "navigate" | "click" | "set_field" | "read_field" | "select_option" | "wait_visible" | "assert_state"
  app: string
  locators: StepLocator[]
  input?: string | number | boolean | null
  loop_iterator?: {
    source_param: string
    item_name: string
    max_iterations: number
  }
  pre: PrePostCondition[]
  post: PrePostCondition[]
  risk: "R0" | "R1" | "R2" | "R3"
  required: boolean
  origin: "observed" | "inferred"
  allow_input_fallback?: boolean
  recovery: {
    retries: number
    on_fail: "pause" | "skip" | "abort" | "retry_then_pause"
  }
  /**
   * @minItems 1
   */
  body?: [WorkflowStep, ...WorkflowStep[]]
  rule_id?: string
  output_name?: string
  output_type?: "string" | "string[]" | "number" | "boolean"
  timeout_ms?: number
} & {
  step_id: string
  kind: "action" | "loop" | "decision" | "assert"
  verb?: "navigate" | "click" | "set_field" | "read_field" | "select_option" | "wait_visible" | "assert_state"
  app: string
  locators: StepLocator[]
  input?: string | number | boolean | null
  loop_iterator?: {
    source_param: string
    item_name: string
    max_iterations: number
  }
  pre: PrePostCondition[]
  post: PrePostCondition[]
  risk: "R0" | "R1" | "R2" | "R3"
  required: boolean
  origin: "observed" | "inferred"
  allow_input_fallback?: boolean
  recovery: {
    retries: number
    on_fail: "pause" | "skip" | "abort" | "retry_then_pause"
  }
  /**
   * @minItems 1
   */
  body?: [WorkflowStep, ...WorkflowStep[]]
  rule_id?: string
  output_name?: string
  output_type?: "string" | "string[]" | "number" | "boolean"
  timeout_ms?: number
} & {
  step_id: string
  kind: "action" | "loop" | "decision" | "assert"
  verb?: "navigate" | "click" | "set_field" | "read_field" | "select_option" | "wait_visible" | "assert_state"
  app: string
  locators: StepLocator[]
  input?: string | number | boolean | null
  loop_iterator?: {
    source_param: string
    item_name: string
    max_iterations: number
  }
  pre: PrePostCondition[]
  post: PrePostCondition[]
  risk: "R0" | "R1" | "R2" | "R3"
  required: boolean
  origin: "observed" | "inferred"
  allow_input_fallback?: boolean
  recovery: {
    retries: number
    on_fail: "pause" | "skip" | "abort" | "retry_then_pause"
  }
  /**
   * @minItems 1
   */
  body?: [WorkflowStep, ...WorkflowStep[]]
  rule_id?: string
  output_name?: string
  output_type?: "string" | "string[]" | "number" | "boolean"
  timeout_ms?: number
}
export type StepLocator = {
  by: "testid" | "role" | "label" | "text" | "css" | "xpath" | "uia"
  value?: string
  role?: string
  name?: string
  uia_id?: string
}
export type PrePostCondition = {
  type: "url_matches" | "element_visible" | "field_equals" | "network_2xx" | "record_id_matches"
  value?: string
  locator_ref?: number
} & {
  type: "url_matches" | "element_visible" | "field_equals" | "network_2xx" | "record_id_matches"
  value?: string
  locator_ref?: number
}

export interface Workflow {
  workflow_id: string
  version: number
  name: string
  description: string
  trust: "observed" | "candidate" | "validated" | "supervised" | "trusted"
  provenance: {
    evidence_episode_ids: string[]
    support: number
    alignment: number
  }
  parameters: WorkflowParameter[]
  /**
   * @minItems 1
   */
  steps: [WorkflowStep, ...WorkflowStep[]]
  decision_rules: DecisionRule[]
  /**
   * @minItems 1
   */
  outcome_checks: [OutcomeCheck, ...OutcomeCheck[]]
  remote_allowed: boolean
}
export interface DecisionRule {
  id: string
  expr: string
  then: string
  else: string
  origin: "inferred" | "user_defined"
  supported_runs: number
  contradicting_runs: number
  confirmed: boolean
}
export interface OutcomeCheck {
  type: "app_api" | "db_query" | "page_assertion"
  app: string
  check: string
  args: {
    [k: string]: unknown
  }
}
