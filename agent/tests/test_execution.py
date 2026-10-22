import asyncio
import copy

import pytest

from agent.ghost.execution.errors import GhostError
from agent.ghost.execution.expressions import evaluate, render
from agent.ghost.execution.interpreter import compile_workflow
from agent.ghost.execution.runtime import Runtime
from agent.ghost.safety.policy import acme_policy
from .helpers import DemoDriver, finish, runtime, settle, workflow


def test_full_workflow_requires_approvals_and_verifies_results(tmp_path):
    async def check():
        value = runtime(tmp_path)
        initial = await value.start("wf_at_risk_seg", 4, {}, "supervised", "first")
        rid = initial["run_id"]
        run = await finish(value, rid)
        assert run["state"] == "succeeded", run.get("error")
        assert value.driver.records["ACC-1"]["tag"] == "At-risk – High"
        assert value.driver.records["ACC-2"]["tag"] == "At-risk – Watch"
        assert len(value.driver.notes) == 1
        approvals = [e for e in value.events if e["type"] == "run.approval_requested"]
        assert len(approvals) == 6
        assert run["checkpoint"]["intent"] is None
        assert "plan" not in run["parameters"]
        assert value.public(run).get("_plan") is None
        await value.close()
        value.store.close()
    asyncio.run(check())


def test_idempotency_and_single_active_run(tmp_path):
    async def check():
        value = runtime(tmp_path)
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        again = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        assert run["run_id"] == again["run_id"]
        with pytest.raises(GhostError, match="different content"):
            await value.start("wf_at_risk_seg", 4, {}, "normal", "key")
        with pytest.raises(GhostError, match="Another desktop"):
            await value.start("wf_at_risk_seg", 4, {}, "supervised", "other")
        await value.control(run["run_id"], "cancel")
        await value.close()
        value.store.close()
    asyncio.run(check())


def test_changed_target_invalidates_approval(tmp_path):
    async def check():
        value = runtime(tmp_path)
        initial = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        run = await settle(value, initial["run_id"])
        binding = run["pending_approval"]
        value.driver.tag = "user-edited"
        await value.control(run["run_id"], "approve", binding)
        run = await settle(value, run["run_id"])
        assert run["state"] == "paused_needs_user"
        assert run["error"]["code"] == "STALE_APPROVAL"
        assert not any(c["verb"] == "select_option" for c in value.driver.calls)
        await value.close()
        value.store.close()
    asyncio.run(check())


def test_uncertain_write_cannot_be_replayed_after_restart(tmp_path):
    async def check():
        driver = DemoDriver()
        driver.fail_save = True
        value = runtime(tmp_path, driver)
        initial = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        run = await finish(value, initial["run_id"])
        assert run["state"] == "paused_needs_user"
        assert run["checkpoint"]["intent"]["step_id"] == "s5_save_cust"
        with pytest.raises(GhostError, match="reconciliation"):
            await value.control(run["run_id"], "resume")
        restarted = Runtime(driver, value.policy, value.store)
        restarted.register(workflow())
        assert restarted.store.get(run["run_id"])["state"] == "blocked_by_guard"
        assert len([c for c in driver.calls if c["step_id"] == "s5_save_cust"]) == 1
        await restarted.control(run["run_id"], "cancel")
        await restarted.close()
        value.store.close()
    asyncio.run(check())


def test_approval_expiry_and_wrong_iteration(tmp_path):
    async def check():
        value = runtime(tmp_path)
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        run = await settle(value, run["run_id"])
        binding = copy.deepcopy(run["pending_approval"])
        binding["approval_id"] = "wrong-iteration"
        with pytest.raises(GhostError) as exc:
            await value.control(run["run_id"], "approve", binding)
        assert exc.value.code == "STALE_APPROVAL"
        value.clock = lambda: 99999999999
        with pytest.raises(GhostError) as exc:
            await value.control(run["run_id"], "approve", run["pending_approval"])
        assert exc.value.code == "STALE_APPROVAL"
        await value.control(run["run_id"], "cancel")
        value.store.close()
    asyncio.run(check())


def test_loop_limit_blocks_before_any_customer_write(tmp_path):
    async def check():
        value = runtime(tmp_path)
        value.driver.ids = ["ACC-1"] * 101
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        run = await settle(value, run["run_id"])
        assert run["state"] == "failed"
        assert not any(c["app"] == "acme-crm" for c in value.driver.calls)
        await value.close()
        value.store.close()
    asyncio.run(check())


def test_cancel_propagates_to_inflight_driver(tmp_path):
    async def check():
        value = runtime(tmp_path)
        value.driver.block = asyncio.Event()
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        await asyncio.sleep(0.02)
        await value.control(run["run_id"], "cancel")
        assert value.tasks[run["run_id"]].cancelled()
        assert value.store.get(run["run_id"])["state"] == "cancelled"
        value.store.close()
    asyncio.run(check())


def test_dry_run_does_not_touch_apps(tmp_path):
    async def check():
        value = runtime(tmp_path)
        run = await value.start("wf_at_risk_seg", 4, {}, "dry_run", "key")
        run = await settle(value, run["run_id"])
        assert run["state"] == "completed_unverified"
        assert not value.driver.calls
        value.store.close()
    asyncio.run(check())


def test_read_only_reconciliation_resumes_without_duplicate_save(tmp_path):
    async def check():
        driver = DemoDriver()
        driver.fail_save = True
        value = runtime(tmp_path, driver)
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        run = await finish(value, run["run_id"])
        assert run["checkpoint"]["intent"]
        reconciled = await value.reconcile(run["run_id"])
        assert reconciled["checkpoint"]["intent"] is None
        driver.fail_save = False
        await value.control(run["run_id"], "resume")
        run = await finish(value, run["run_id"])
        assert run["state"] == "succeeded", run.get("error")
        saves = [c for c in driver.calls if c["step_id"] == "s5_save_cust"]
        assert len(saves) == 2  # One save per account, including the reconciled write.
        await value.close()
        value.store.close()
    asyncio.run(check())


def test_emergency_stop_latches_before_worker_starts(tmp_path):
    async def check():
        value = runtime(tmp_path)
        run = await value.start("wf_at_risk_seg", 4, {}, "supervised", "key")
        await value.emergency_stop()
        assert value.store.get(run["run_id"])["state"] == "cancelled"
        assert not value.driver.calls
        with pytest.raises(GhostError, match="Kill switch"):
            await value.start("wf_at_risk_seg", 4, {}, "supervised", "next")
        value.store.close()
    asyncio.run(check())


def test_policy_blocks_disguised_or_unregistered_actions():
    plan = workflow()
    plan["steps"][-1]["risk"] = "R0"
    policy = acme_policy()
    assert policy.classify(plan["steps"][-1]) == "R2"
    plan["steps"][-1]["locators"] = [{"by": "testid", "value": "delete-everything"}]
    with pytest.raises(GhostError):
        compile_workflow(plan, policy)
    with pytest.raises(GhostError):
        policy.url("acme-crm", "http://evil.example/customers/ACC-1")


def test_restricted_expressions_and_templates():
    assert evaluate("plan == 'Pro' and days > 30", {"plan": "Pro", "days": 45})
    assert render("{{ids}}", {"ids": ["a", "b"]}) == ["a", "b"]
    assert render("/customers/{{item}}", {"item": "a/b"}, url=True) == "/customers/a%2Fb"
    for expression in ("__import__('os')", "user.secret", "items[0]", "days + 1", "1e999 == 1e999"):
        with pytest.raises(GhostError):
            evaluate(expression, {})
    with pytest.raises(GhostError):
        evaluate("days > 3", {"days": "45"})
    with pytest.raises(GhostError):
        render("{{missing}}", {})
