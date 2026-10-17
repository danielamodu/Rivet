import hashlib
import json

import pytest

from rivet.application import (
    AuthorizationError,
    ExecutionResult,
    EvidenceIntegrityError,
    IdempotencyConflictError,
    InvalidTransitionError,
    Principal,
    PrincipalRole,
    RivetOrchestrator,
    SQLiteWorkflowStore,
    TaskState,
)
from rivet.reconciliation import ReconciliationInput, UsageRecord


class ResultSandbox:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, task, worker):
        self.calls.append((task, worker))
        return self.result


def fixture():
    store = SQLiteWorkflowStore()
    app = RivetOrchestrator(store)
    data = ReconciliationInput(usage_records=(UsageRecord("usage-1", "customer-1", 3),))
    proposer = Principal("reconciler-worker", PrincipalRole.WORKER)
    submitted = app.submit(data, "request-1", proposer)
    assert len(submitted.tasks) == 1
    return store, app, data, proposer, submitted


def test_reconciliation_to_aegis_verification_creates_unapproved_task():
    store, app, _, _, submitted = fixture()

    assert submitted.submission.outcome == "PASS"
    assert submitted.submission.verification["verification_scope"] == "evidence_consistency_only"
    task = submitted.tasks[0]
    assert task.state == TaskState.VERIFIED
    assert task.approved_by is None
    assert task.finding["code"] == "DROPPED_USAGE_SYNC"
    assert store.get_submission(submitted.submission.submission_id).verification["outcome"] == "PASS"


def test_aegis_rejected_findings_cannot_be_approved_or_executed():
    store = SQLiteWorkflowStore()
    app = RivetOrchestrator(store)
    data = ReconciliationInput(usage_records=(UsageRecord("usage-1", "customer-1", 3),))
    bad_result = {
        "schema_version": 1,
        "input_sha256": "fabricated",
        "findings": [{
            "finding_id": "fabricated-finding",
            "code": "DROPPED_USAGE_SYNC",
            "subject_id": "usage-1",
            "severity": "high",
            "summary": "fabricated",
            "evidence": {},
            "calculation": {},
        }],
        "summary": {"finding_count": 1, "counts_by_code": {"DROPPED_USAGE_SYNC": 1}},
    }
    result = app.submit(
        data, "tampered-request", Principal("worker-1", PrincipalRole.WORKER), bad_result
    )

    assert result.submission.outcome == "REJECT"
    assert result.tasks[0].state == TaskState.REJECTED
    with pytest.raises(InvalidTransitionError):
        app.approve(result.tasks[0].task.task_id, Principal("human-1", PrincipalRole.HUMAN))
    sandbox = ResultSandbox(ExecutionResult(0, "", "", {}))
    with pytest.raises(InvalidTransitionError):
        app.execute(result.tasks[0].task.task_id, Principal("worker-2", PrincipalRole.WORKER), sandbox)
    assert sandbox.calls == []


def test_execution_requires_approval_and_workers_cannot_approve():
    store, app, _, proposer, submitted = fixture()
    task_id = submitted.tasks[0].task.task_id
    sandbox = ResultSandbox(ExecutionResult(0, "ok", "", {}))

    with pytest.raises(InvalidTransitionError):
        app.execute(task_id, Principal("worker-2", PrincipalRole.WORKER), sandbox)
    with pytest.raises(AuthorizationError):
        app.approve(task_id, proposer)
    assert sandbox.calls == []
    assert store.get_task(task_id).state == TaskState.VERIFIED


def test_proposer_cannot_self_approve_and_state_cannot_be_approved_twice():
    store, app, _, _, submitted = fixture()
    task_id = submitted.tasks[0].task.task_id

    with pytest.raises(AuthorizationError):
        app.approve(task_id, Principal("reconciler-worker", PrincipalRole.HUMAN))
    approved = app.approve(task_id, Principal("human-approver", PrincipalRole.HUMAN))
    assert approved.state == TaskState.APPROVED
    with pytest.raises(InvalidTransitionError):
        app.approve(task_id, Principal("second-human", PrincipalRole.HUMAN))
    assert store.get_task(task_id).approved_by == "human-approver"


def test_execution_records_success_result_and_integrity_checked_evidence():
    store, app, _, _, submitted = fixture()
    task_id = submitted.tasks[0].task.task_id
    app.approve(task_id, Principal("human-approver", PrincipalRole.HUMAN))
    sandbox = ResultSandbox(ExecutionResult(0, "sandbox stdout", "", {"runtime_ms": 18}))

    finished = app.execute(task_id, Principal("sandbox-worker", PrincipalRole.WORKER), sandbox)
    evidence = store.get_evidence(task_id)

    assert finished.state == TaskState.SUCCEEDED
    assert finished.attempt_count == 1
    assert len(evidence) == 1
    assert evidence[0].task_id == task_id
    assert evidence[0].result.stdout == "sandbox stdout"
    expected = hashlib.sha256(
        json.dumps(evidence[0].result.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert evidence[0].sha256 == expected
    assert sandbox.calls[0][0].task_id == task_id


def test_failed_execution_preserves_exit_code_stderr_and_evidence():
    store, app, _, _, submitted = fixture()
    task_id = submitted.tasks[0].task.task_id
    app.approve(task_id, Principal("human-approver", PrincipalRole.HUMAN))
    sandbox = ResultSandbox(ExecutionResult(7, "partial", "ValueError: bad input", {}))

    failed = app.execute(task_id, Principal("sandbox-worker", PrincipalRole.WORKER), sandbox)
    evidence = store.get_evidence(task_id)

    assert failed.state == TaskState.FAILED
    assert evidence[0].result.exit_code == 7
    assert evidence[0].result.stderr == "ValueError: bad input"


def test_execution_evidence_tampering_is_detected():
    store, app, _, _, submitted = fixture()
    task_id = submitted.tasks[0].task.task_id
    app.approve(task_id, Principal("human-approver", PrincipalRole.HUMAN))
    app.execute(
        task_id,
        Principal("sandbox-worker", PrincipalRole.WORKER),
        ResultSandbox(ExecutionResult(0, "original", "", {})),
    )
    store._db.execute("UPDATE execution_evidence SET payload_json = ?", ('{"stdout":"tampered"}',))
    store._db.commit()

    with pytest.raises(EvidenceIntegrityError):
        store.get_evidence(task_id)


def test_duplicate_submission_is_idempotent_and_conflicting_payload_is_rejected():
    store, app, data, proposer, first = fixture()

    repeated = app.submit(data, "request-1", proposer)
    assert repeated.submission.submission_id == first.submission.submission_id
    assert [row.task.task_id for row in repeated.tasks] == [row.task.task_id for row in first.tasks]
    with pytest.raises(IdempotencyConflictError):
        app.submit(ReconciliationInput(), "request-1", proposer)
    assert len(store.list_tasks(first.submission.submission_id)) == 1
