import asyncio
import copy
import time
from uuid import uuid4

from ..safety.approvals import digest, make_approval, matches, now
from ..safety.guard import require_unchanged
from ..safety.killswitch import KillSwitch
from ..safety.risk import needs_approval
from .context import ExecutionContext, typed
from .errors import GhostError
from .expressions import evaluate, render
from .interpreter import bind_parameters, compile_workflow, resolved_step
from .verification import verify

TERMINAL = {"succeeded", "completed_unverified", "failed", "cancelled"}


class Runtime:
    def __init__(self, driver, policy, store, clock=time.time, event_sink=None):
        self.driver, self.policy, self.store, self.clock = driver, policy, store, clock
        self.workflows = {}
        self.tasks = {}
        self.kill_switch = KillSwitch()
        self.events = []
        self.sequence = 0
        self.event_sink = event_sink
        self.device_check = None
        self._lock = asyncio.Lock()
        # Recovery never automatically replays a mutation after a crash.
        for run in store.all():
            if run["state"] not in TERMINAL:
                run["state"] = "blocked_by_guard" if run["checkpoint"]["intent"] else "paused_needs_user"
                run["pending_approval"] = None
                run["_approved"] = None
                run["_restore_required"] = True
                store.save(run)

    def register(self, workflow, *, trusted=False):
        """Local integration hook only. Never expose registration to a remote caller.

        M4/M1 must call this only after review; the workflow's trust string alone
        cannot grant permission. The separate trusted argument is server-owned.
        """
        compiled = compile_workflow(workflow, self.policy)
        if compiled["trust"] not in ("validated", "supervised", "trusted"):
            raise GhostError("POLICY_BLOCKED", "Plan must be reviewed before registration", 403)
        self.workflows[(compiled["workflow_id"], compiled["version"])] = (compiled, trusted)

    def emit(self, kind, data):
        self.sequence = self.store.next_sequence()
        event = {"event_id": str(uuid4()), "sequence": self.sequence, "type": kind, "ts": now(), "data": data}
        self.events.append(event)
        del self.events[:-500]
        if self.event_sink:
            try:
                self.event_sink(copy.deepcopy(event))
            except Exception:
                # Event delivery cannot change the outcome of a committed action.
                # Consumers recover by reading authoritative REST snapshots.
                pass

    def public(self, run):
        return copy.deepcopy({k: v for k, v in run.items() if not k.startswith("_")})

    def save(self, run, state=None):
        old = run["state"]
        if state:
            run["state"] = state
            if state in TERMINAL:
                run["finished_at"] = now()
                run["pending_approval"] = None
                run["_approved"] = None
        self.store.save(run)
        if old != run["state"]:
            self.emit("run.state_changed", {"run_id": run["run_id"], "from_state": old, "to_state": run["state"]})

    async def start(self, workflow_id, version, parameters, mode, key, *, remote_device=None):
        if mode not in ("normal", "supervised", "dry_run") or not key or len(key) > 128:
            raise GhostError("VALIDATION_ERROR", "Invalid mode or idempotency key", 422)
        fingerprint = digest({"workflow_id": workflow_id, "version": version, "parameters": parameters, "mode": mode, "remote_device": remote_device})
        async with self._lock:
            previous = self.store.by_key(key)
            if previous:
                if fingerprint != previous[0]:
                    raise GhostError("CONFLICT", "Idempotency key was used with different content")
                return self.public(previous[1])
            if (workflow_id, version) not in self.workflows:
                raise GhostError("NOT_FOUND", "Reviewed workflow version not registered", 404)
            plan, trusted = self.workflows[(workflow_id, version)]
            parameters = bind_parameters(plan, parameters)
            if self.kill_switch.engaged:
                raise GhostError("POLICY_BLOCKED", "Kill switch is engaged", 403)
            if any(r["state"] not in TERMINAL for r in self.store.all()):
                raise GhostError("CONFLICT", "Another desktop run requires completion or cancellation")
            rid = "run_" + uuid4().hex
            run = {
                "run_id": rid, "workflow_id": workflow_id, "workflow_version": version,
                "workflow_hash": digest(plan), "mode": mode, "state": "queued", "created_at": now(),
                "started_at": None, "finished_at": None, "current_step_id": None,
                "current_loop_index": None, "total_loop_count": None, "parameters": parameters,
                "step_records": [], "pending_approval": None, "outcome_results": None,
                "initiated_by": {"channel": "local_dashboard", "device_id": "desktop_primary"},
                "checkpoint": {"completed": {}, "loops": {}, "loop_path": [], "intent": None, "expected_tags": {}},
                "_plan": copy.deepcopy(plan), "_trusted": trusted, "_approved": None,
                "_remote_device": remote_device, "_approval_device": None,
                "_drafts": {}, "_restore_required": False,
            }
            self.store.create(run, key, fingerprint)
            self.spawn(rid)
            return self.public(run)

    def spawn(self, rid):
        if rid in self.tasks and not self.tasks[rid].done():
            raise GhostError("CONFLICT", "Run worker is still active")
        task = asyncio.create_task(self.execute(rid))
        self.tasks[rid] = task
        self.kill_switch.track(task)

    async def execute(self, rid):
        run = self.store.get(rid)
        try:
            run["started_at"] = run["started_at"] or now()
            self.save(run, "preflight")
            registration = self.workflows.get((run["workflow_id"], run["workflow_version"]))
            if not registration or digest(registration[0]) != run["workflow_hash"]:
                raise GhostError("POLICY_BLOCKED", "Reviewed workflow changed or is unavailable", 403)
            compile_workflow(run["_plan"], self.policy)
            if run.get("_restore_required"):
                navigation = {}
                for completed in run["checkpoint"]["completed"].values():
                    if "navigation" in completed:
                        navigation.update(completed["navigation"])
                if navigation:
                    if not hasattr(self.driver, "restore_navigation"):
                        raise GhostError("APP_UNREACHABLE", "Driver requires local recovery initialization")
                    await asyncio.wait_for(self.driver.restore_navigation(navigation), 30)
                run["_restore_required"] = False
                self.save(run)
            self.save(run, "running")
            if run["mode"] == "dry_run":
                # Static admission only: do not navigate, read, or mutate an app.
                self.save(run, "completed_unverified")
                return
            context = ExecutionContext({**run["parameters"], "run_id": rid})
            await self.walk(run, run["_plan"]["steps"], context)
            results = []
            for check in run["_plan"]["outcome_checks"]:
                resolved = render(check, {**context.values, "expected_tags": run["checkpoint"]["expected_tags"]})
                passed = await asyncio.wait_for(self.driver.outcome(resolved), timeout=10)
                results.append({"check": check["check"], "passed": passed})
            verified = all(r["passed"] for r in results)
            run["outcome_results"] = {"verified": verified, "checks_passed": sum(r["passed"] for r in results),
                                      "checks_total": len(results), "details": results}
            self.save(run, "succeeded" if verified else "completed_unverified")
        except asyncio.CancelledError:
            # Controls persist their chosen state after awaiting cancellation.
            if self.kill_switch.engaged:
                run["pending_approval"] = None
                run["_approved"] = None
                self.save(run, "cancelled")
            raise
        except GhostError as exc:
            run["error"] = {"code": exc.code, "message": exc.message}
            state = "blocked_by_guard" if exc.code in ("POLICY_BLOCKED", "GUARD_TRIGGERED") else "paused_needs_user"
            if run["state"] == "awaiting_approval":
                state = "awaiting_approval"
            elif state == "paused_needs_user" and self.failure_mode(run) == "abort":
                state = "failed"
            self.save(run, state)
        except Exception:
            run["error"] = {"code": "INTERNAL", "message": "Driver or verification failed; inspect locally before resuming"}
            self.save(run, "paused_needs_user")

    async def walk(self, run, steps, context):
        rules = {r["id"]: r for r in run["_plan"]["decision_rules"]}
        cp = run["checkpoint"]
        for step in steps:
            if self.kill_switch.engaged:
                raise GhostError("POLICY_BLOCKED", "Kill switch is engaged", 403)
            sid = step["step_id"]
            instance = digest({"step": sid, "loop": context.loop_path})
            cp["loop_path"] = copy.deepcopy(context.loop_path)
            run["current_step_id"] = sid
            run["current_loop_index"] = context.loop_path[-1]["index"] if context.loop_path else None
            if instance in cp["completed"]:
                record = cp["completed"][instance]
                if "output_name" in record:
                    context.values[record["output_name"]] = record["output"]
                continue
            if step["kind"] == "loop":
                iterator = step["loop_iterator"]
                items = typed(context.values.get(iterator["source_param"]), "string[]")
                if len(items) > iterator["max_iterations"] or len(set(items)) != len(items):
                    raise GhostError("VALIDATION_ERROR", "Loop limit or unique record constraint failed", 422)
                frozen = cp["loops"].setdefault(instance, items)
                if frozen != items:
                    raise GhostError("GUARD_TRIGGERED", "Loop input changed during recovery")
                run["total_loop_count"] = len(frozen)
                self.save(run)
                for index, item in enumerate(frozen):
                    await self.walk(run, step["body"], context.child(sid, index, item, iterator["item_name"]))
            elif step["kind"] == "decision":
                rule = rules[step["rule_id"]]
                output = rule["then"] if evaluate(rule["expr"], context.values) else rule["else"]
                name = "decision." + rule["id"]
                context.values[name] = output
                cp["completed"][instance] = {"output_name": name, "output": output}
                if rule["id"] == "segment" and context.loop_path:
                    cp["expected_tags"][context.loop_path[-1]["item"]] = output
            else:
                resolved = resolved_step(step, context.values)
                if resolved.get("verb") == "click":
                    resolved["_expected_fields"] = copy.deepcopy(run.get("_drafts", {}).get(resolved["app"], {}))
                if resolved["app"] == "acme-crm" and resolved.get("verb") in ("set_field", "select_option", "click"):
                    resolved["_expected_record"] = {k: context.values[k] for k in ("plan", "last_login_days") if k in context.values}
                retries = step["recovery"]["retries"] if step.get("verb") in ("navigate", "read_field", "wait_visible") else 0
                for attempt in range(retries + 1):
                    try:
                        result = await asyncio.wait_for(self.action(run, resolved), step.get("timeout_ms", 10000) / 1000)
                        break
                    except (GhostError, TimeoutError) as exc:
                        # Approval/policy/guard failures must never be retried.
                        safe_error = not isinstance(exc, GhostError) or exc.code in ("APP_UNREACHABLE", "LOCATOR_NOT_FOUND", "PRECONDITION_FAILED")
                        if not safe_error or attempt == retries:
                            raise
                if step.get("verb") == "read_field":
                    result = typed(result, step["output_type"])
                    context.values[step["output_name"]] = result
                    cp["completed"][instance] = {"output_name": step["output_name"], "output": result}
                elif run.get("_last_receipt"):
                    cp["completed"][instance] = {"receipt": run.pop("_last_receipt")}
                if step.get("verb") == "navigate":
                    cp["completed"][instance] = {"navigation": {step["app"]: resolved["input"]}}
                if step.get("verb") in ("set_field", "select_option"):
                    for locator in resolved["locators"]:
                        if locator["by"] == "testid":
                            run.setdefault("_drafts", {}).setdefault(step["app"], {})[locator["value"]] = str(resolved["input"])
                            break
            cp["completed"].setdefault(instance, {})
            cp["loop_path"] = copy.deepcopy(context.loop_path)
            run["step_records"].append({"step_id": sid, "loop_path": copy.deepcopy(context.loop_path), "attempt": 1, "status": "passed"})
            self.save(run)
            self.emit("run.step_updated", {"run_id": run["run_id"], **run["step_records"][-1]})

    async def action(self, run, step):
        self.check_devices(run)
        risk = self.policy.classify(step)
        if step.get("verb") == "navigate":
            self.policy.url(step["app"], step["input"])
        await verify(self.driver, step, "pre")
        target = await self.driver.prepare(step)
        mutating = risk in ("R1", "R2")
        if needs_approval(risk, run["mode"], run["_trusted"]):
            approved = run["_approved"]
            if not approved:
                pending = make_approval(run, step, target, risk, self.policy.app(step["app"]).origin,
                                        self.policy.generation, self.clock)
                run["pending_approval"] = pending
                self.save(run, "awaiting_approval")
                self.emit("run.approval_requested", {"run_id": run["run_id"], **pending})
                raise GhostError("APPROVAL_REQUIRED", "Explicit approval is required")
            matches(approved, approved, self.clock)
            preview = approved["preview_data"]
            require_unchanged(preview["target"], target)
            require_unchanged(preview["arguments"], {"verb": step.get("verb"), "input": step.get("input")})
            if (preview["step_id"] != step["step_id"] or preview["loop_path"] != run["checkpoint"]["loop_path"]
                    or preview["policy_generation"] != self.policy.generation or preview["risk"] != risk):
                raise GhostError("STALE_APPROVAL", "Approval scope or policy changed")
            run["_approved"] = None
            run["pending_approval"] = None
            self.save(run)
        # Fresh context after consent, immediately before the input boundary.
        await verify(self.driver, step, "pre")
        require_unchanged(target, await self.driver.prepare(step))
        if self.kill_switch.engaged:
            raise GhostError("POLICY_BLOCKED", "Kill switch is engaged", 403)
        self.check_devices(run)
        if mutating:
            run["checkpoint"]["intent"] = {"step_id": step["step_id"], "loop_path": copy.deepcopy(run["checkpoint"]["loop_path"]), "target": target, "step": step}
            self.save(run)
        result = await self.driver.perform(step, target)
        await verify(self.driver, step, "post")
        if mutating:
            # Completed action and cleared intent are persisted together by walk().
            run["checkpoint"]["intent"] = None
            run["_last_receipt"] = {"step_id": step["step_id"], "verified_at": now(), "target": target}
        return result

    def check_devices(self, run):
        for device in (run.get("_remote_device"), run.get("_approval_device")):
            if device:
                if self.device_check is None:
                    raise GhostError("POLICY_BLOCKED", "Remote authorization is unavailable", 403)
                self.device_check(device)

    def failure_mode(self, run):
        def find(steps):
            for step in steps:
                if step["step_id"] == run["current_step_id"]:
                    return step["recovery"]["on_fail"]
                if step["kind"] == "loop":
                    mode = find(step["body"])
                    if mode:
                        return mode
            return None
        return find(run["_plan"]["steps"])

    async def emergency_stop(self):
        self.kill_switch.engage()
        for run in self.store.all():
            if run["state"] not in TERMINAL:
                await self.control(run["run_id"], "cancel")

    async def reconcile(self, rid):
        """Local-only, read-only reconciliation. Never replays uncertain input."""
        async with self._lock:
            run = self.store.get(rid)
            intent = run["checkpoint"]["intent"]
            if run["state"] not in ("paused_needs_user", "blocked_by_guard") or not intent:
                raise GhostError("CONFLICT", "Run has no uncertain effect to reconcile")
            if self.tasks.get(rid) and not self.tasks[rid].done():
                raise GhostError("CONFLICT", "Wait for the execution worker to stop")
            if not hasattr(self.driver, "reconcile") or not await asyncio.wait_for(self.driver.reconcile(intent), 10):
                raise GhostError("GUARD_TRIGGERED", "Independent read could not confirm the effect")
            instance = digest({"step": intent["step_id"], "loop": intent["loop_path"]})
            run["checkpoint"]["completed"][instance] = {"receipt": {"reconciled": True, "verified_at": now(), "target": intent["target"]}}
            run["checkpoint"]["intent"] = None
            run["pending_approval"] = None
            run["_approved"] = None
            run.pop("error", None)
            self.save(run, "paused_needs_user")
            return self.public(run)

    async def control(self, rid, operation, binding=None, *, approval_device=None):
        async with self._lock:
            run = self.store.get(rid)
            if run["state"] in TERMINAL:
                raise GhostError("CONFLICT", "Run is already terminal")
            if operation in ("approve", "reject"):
                if run["state"] != "awaiting_approval":
                    raise GhostError("STALE_APPROVAL", "Run is not awaiting approval")
                matches(run["pending_approval"], binding or {}, self.clock)
                task = self.tasks.get(rid)
                if task and not task.done():
                    await task
                if operation == "reject":
                    self.save(run, "cancelled")
                else:
                    run["_approved"] = run["pending_approval"]
                    run["_approval_device"] = approval_device
                    run["pending_approval"] = None
                    self.save(run, "queued")
                    self.spawn(rid)
            elif operation in ("pause", "cancel"):
                task = self.tasks.get(rid)
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                run = self.store.get(rid)
                run["pending_approval"] = None
                run["_approved"] = None
                self.save(run, "cancelled" if operation == "cancel" else "paused_needs_user")
            elif operation == "resume":
                if run["state"] != "paused_needs_user" or run["checkpoint"]["intent"]:
                    raise GhostError("CONFLICT", "Run requires reconciliation or is not paused")
                if self.kill_switch.engaged:
                    raise GhostError("POLICY_BLOCKED", "Kill switch is engaged", 403)
                run["_approved"] = None
                run["pending_approval"] = None
                run.pop("error", None)
                self.save(run, "queued")
                self.spawn(rid)
            else:
                raise GhostError("VALIDATION_ERROR", "Unknown control operation", 422)
            return self.public(self.store.get(rid))

    async def close(self):
        for rid, task in list(self.tasks.items()):
            if not task.done():
                await self.control(rid, "pause")
        await self.driver.close()
