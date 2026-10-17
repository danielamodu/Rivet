"""Trusted application workflow for verified, human-approved repair proposals.

Coordinates the pure reconciliation engine and independent AEGIS verifier.
Execution is delegated to an injected sandbox adapter; this module does not
connect to Stripe, execute generated code, or perform financial mutations.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .aegis import verify_reconciliation
from .reconciliation import ReconciliationInput, reconcile


class WorkflowError(RuntimeError):
    """Base class for rejected workflow operations."""


class InvalidTransitionError(WorkflowError):
    """A repair task cannot make the requested state transition."""


class AuthorizationError(WorkflowError):
    """A principal is not allowed to perform an operation."""


class IdempotencyConflictError(WorkflowError):
    """An idempotency key was reused for a different request."""


class EvidenceIntegrityError(WorkflowError):
    """Persisted execution evidence no longer matches its digest."""


class TaskState(str, Enum):
    PROPOSED = "PROPOSED"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PrincipalRole(str, Enum):
    SYSTEM = "SYSTEM"
    HUMAN = "HUMAN"
    WORKER = "WORKER"


_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PROPOSED: frozenset({TaskState.VERIFIED, TaskState.REJECTED}),
    TaskState.VERIFIED: frozenset({TaskState.APPROVED, TaskState.REJECTED}),
    TaskState.APPROVED: frozenset({TaskState.EXECUTING}),
    TaskState.EXECUTING: frozenset({TaskState.SUCCEEDED, TaskState.FAILED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.REJECTED: frozenset(),
    TaskState.FAILED: frozenset(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Principal:
    principal_id: str
    role: PrincipalRole

    def __post_init__(self) -> None:
        if not self.principal_id.strip():
            raise ValueError("principal_id must be non-empty")


@dataclass(frozen=True)
class RepairTask:
    task_id: str
    submission_id: str
    finding_id: str
    finding_code: str
    subject_id: str
    input_sha256: str


@dataclass(frozen=True)
class TaskRecord:
    task: RepairTask
    state: TaskState
    finding: Mapping[str, Any]
    proposer_id: str
    proposer_role: PrincipalRole
    approved_by: str | None
    worker_id: str | None
    attempt_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SubmissionRecord:
    submission_id: str
    idempotency_key_hash: str
    request_sha256: str
    input: Mapping[str, Any]
    result: Mapping[str, Any]
    verification: Mapping[str, Any] | None
    outcome: str | None
    created_at: str


@dataclass(frozen=True)
class ExecutionResult:
    """Control-plane result returned by a trusted sandbox adapter."""

    exit_code: int | None
    stdout: str
    stderr: str
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionEvidence:
    task_id: str
    attempt_id: str
    result: ExecutionResult
    sha256: str


class SandboxAdapter(Protocol):
    """Adapter to a configured sandbox; never forward unrestricted secrets."""

    def execute(self, task: RepairTask, worker: Principal) -> ExecutionResult:
        """Execute one proposal in the approved sandbox boundary."""


class WorkflowStore(Protocol):
    """Persistence contract; production implementations may use PostgreSQL."""

    def find_submission(self, idempotency_key_hash: str) -> SubmissionRecord | None: ...
    def create_submission(self, submission: SubmissionRecord, tasks: Sequence[TaskRecord]) -> None: ...
    def finalize_verification(self, submission_id: str, report: Mapping[str, Any], accepted: bool) -> None: ...
    def get_submission(self, submission_id: str) -> SubmissionRecord: ...
    def list_tasks(self, submission_id: str) -> tuple[TaskRecord, ...]: ...
    def get_task(self, task_id: str) -> TaskRecord: ...
    def approve_task(self, task_id: str, approver: Principal) -> None: ...
    def begin_attempt(self, task_id: str, worker: Principal) -> tuple[str, int]: ...
    def finish_attempt(self, task_id: str, attempt_id: str, outcome: TaskState, result: ExecutionResult) -> None: ...
    def get_evidence(self, task_id: str) -> tuple[ExecutionEvidence, ...]: ...


class SQLiteWorkflowStore:
    """Local/test adapter using SQLite; not the production database schema."""

    def __init__(self, database: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                submission_id TEXT PRIMARY KEY,
                idempotency_key_hash TEXT NOT NULL UNIQUE,
                request_sha256 TEXT NOT NULL,
                input_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                verification_json TEXT,
                outcome TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS findings (
                task_id TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
                finding_id TEXT NOT NULL,
                finding_json TEXT NOT NULL,
                UNIQUE(submission_id, finding_id)
            );
            CREATE TABLE IF NOT EXISTS verification_results (
                submission_id TEXT PRIMARY KEY REFERENCES submissions(submission_id),
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repair_tasks (
                task_id TEXT PRIMARY KEY REFERENCES findings(task_id),
                state TEXT NOT NULL,
                proposer_id TEXT NOT NULL,
                proposer_role TEXT NOT NULL,
                approved_by TEXT,
                worker_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES repair_tasks(task_id),
                approver_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repair_attempts (
                attempt_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES repair_tasks(task_id),
                attempt_number INTEGER NOT NULL,
                worker_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                failure_type TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(task_id, attempt_number)
            );
            CREATE TABLE IF NOT EXISTS execution_evidence (
                evidence_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES repair_tasks(task_id),
                attempt_id TEXT NOT NULL REFERENCES repair_attempts(attempt_id),
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def find_submission(self, idempotency_key_hash: str) -> SubmissionRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM submissions WHERE idempotency_key_hash = ?", (idempotency_key_hash,)
            ).fetchone()
        return self._submission(row) if row else None

    def create_submission(self, submission: SubmissionRecord, tasks: Sequence[TaskRecord]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission.submission_id, submission.idempotency_key_hash,
                    submission.request_sha256, _json(submission.input), _json(submission.result),
                    None, None, submission.created_at,
                ),
            )
            for record in tasks:
                self._db.execute(
                    "INSERT INTO findings VALUES (?, ?, ?, ?)",
                    (record.task.task_id, record.task.submission_id, record.task.finding_id, _json(record.finding)),
                )
                self._db.execute(
                    "INSERT INTO repair_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.task.task_id, record.state.value, record.proposer_id,
                        record.proposer_role.value, None, None, 0,
                        record.created_at, record.updated_at,
                    ),
                )

    def finalize_verification(self, submission_id: str, report: Mapping[str, Any], accepted: bool) -> None:
        state = TaskState.VERIFIED if accepted else TaskState.REJECTED
        outcome = "PASS" if accepted else "REJECT"
        with self._lock, self._db:
            submission = self._db.execute(
                "SELECT outcome FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
            if submission is None:
                raise WorkflowError("unknown submission")
            if submission["outcome"] is not None:
                if submission["outcome"] != outcome:
                    raise WorkflowError("submission already has a different verification outcome")
                return
            now = _now()
            self._db.execute(
                "INSERT INTO verification_results VALUES (?, ?, ?)", (submission_id, _json(report), now)
            )
            self._db.execute(
                "UPDATE submissions SET verification_json = ?, outcome = ? WHERE submission_id = ?",
                (_json(report), outcome, submission_id),
            )
            self._db.execute(
                "UPDATE repair_tasks SET state = ?, updated_at = ? WHERE task_id IN "
                "(SELECT task_id FROM findings WHERE submission_id = ?) AND state = ?",
                (state.value, now, submission_id, TaskState.PROPOSED.value),
            )

    def get_submission(self, submission_id: str) -> SubmissionRecord:
        with self._lock:
            row = self._db.execute("SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)).fetchone()
        if row is None:
            raise WorkflowError("unknown submission")
        return self._submission(row)

    def list_tasks(self, submission_id: str) -> tuple[TaskRecord, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT f.task_id, f.submission_id, f.finding_id, f.finding_json, t.state, "
                "t.proposer_id, t.proposer_role, t.approved_by, t.worker_id, t.attempt_count, "
                "t.created_at, t.updated_at FROM findings f JOIN repair_tasks t USING(task_id) "
                "WHERE f.submission_id = ? ORDER BY f.finding_id", (submission_id,)
            ).fetchall()
        return tuple(self._task(row) for row in rows)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            row = self._db.execute(
                "SELECT f.task_id, f.submission_id, f.finding_id, f.finding_json, t.state, "
                "t.proposer_id, t.proposer_role, t.approved_by, t.worker_id, t.attempt_count, "
                "t.created_at, t.updated_at FROM findings f JOIN repair_tasks t USING(task_id) "
                "WHERE f.task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise WorkflowError("unknown repair task")
        return self._task(row)

    def approve_task(self, task_id: str, approver: Principal) -> None:
        if approver.role != PrincipalRole.HUMAN:
            raise AuthorizationError("only a human principal may approve a repair task")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT state, proposer_id FROM repair_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise WorkflowError("unknown repair task")
            if approver.principal_id == row["proposer_id"]:
                raise AuthorizationError("a proposer cannot approve their own repair task")
            self._transition(TaskState(row["state"]), TaskState.APPROVED)
            now = _now()
            changed = self._db.execute(
                "UPDATE repair_tasks SET state = ?, approved_by = ?, updated_at = ? "
                "WHERE task_id = ? AND state = ?",
                (TaskState.APPROVED.value, approver.principal_id, now, task_id, TaskState.VERIFIED.value),
            ).rowcount
            if changed != 1:
                raise InvalidTransitionError("task state changed during approval")
            self._db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), task_id, approver.principal_id, now),
            )

    def begin_attempt(self, task_id: str, worker: Principal) -> tuple[str, int]:
        if worker.role != PrincipalRole.WORKER:
            raise AuthorizationError("execution requires a worker principal")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT state, approved_by, proposer_id, attempt_count FROM repair_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise WorkflowError("unknown repair task")
            self._transition(TaskState(row["state"]), TaskState.EXECUTING)
            if row["approved_by"] is None:
                raise InvalidTransitionError("execution requires a recorded human approval")
            if worker.principal_id in {row["proposer_id"], row["approved_by"]}:
                raise AuthorizationError("worker must be distinct from proposer and approver")
            number = int(row["attempt_count"]) + 1
            attempt_id, started = str(uuid.uuid4()), _now()
            changed = self._db.execute(
                "UPDATE repair_tasks SET state = ?, worker_id = ?, attempt_count = ?, updated_at = ? "
                "WHERE task_id = ? AND state = ?",
                (TaskState.EXECUTING.value, worker.principal_id, number, started, task_id, TaskState.APPROVED.value),
            ).rowcount
            if changed != 1:
                raise InvalidTransitionError("task state changed during execution start")
            self._db.execute(
                "INSERT INTO repair_attempts VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (attempt_id, task_id, number, worker.principal_id, "RUNNING", None, started),
            )
        return attempt_id, number

    def finish_attempt(self, task_id: str, attempt_id: str, outcome: TaskState, result: ExecutionResult) -> None:
        if outcome not in {TaskState.SUCCEEDED, TaskState.FAILED}:
            raise ValueError("execution outcome must be SUCCEEDED or FAILED")
        expected_outcome = TaskState.SUCCEEDED if result.exit_code == 0 else TaskState.FAILED
        if outcome != expected_outcome:
            raise ValueError("execution outcome must match the returned exit code")
        payload = _json(result.to_dict())
        payload_hash = _sha256(payload.encode("utf-8"))
        with self._lock, self._db:
            row = self._db.execute("SELECT state FROM repair_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise WorkflowError("unknown repair task")
            self._transition(TaskState(row["state"]), outcome)
            finished, evidence_id = _now(), str(uuid.uuid4())
            failure_type = result.metadata.get("failure_type") if outcome == TaskState.FAILED else None
            changed = self._db.execute(
                "UPDATE repair_attempts SET outcome = ?, failure_type = ?, finished_at = ? "
                "WHERE attempt_id = ? AND task_id = ? AND outcome = 'RUNNING'",
                (outcome.value, failure_type, finished, attempt_id, task_id),
            ).rowcount
            if changed != 1:
                raise WorkflowError("unknown or already completed repair attempt")
            self._db.execute(
                "INSERT INTO execution_evidence VALUES (?, ?, ?, ?, ?)",
                (evidence_id, task_id, attempt_id, payload, payload_hash),
            )
            self._db.execute(
                "UPDATE repair_tasks SET state = ?, updated_at = ? WHERE task_id = ? AND state = ?",
                (outcome.value, finished, task_id, TaskState.EXECUTING.value),
            )

    def get_evidence(self, task_id: str) -> tuple[ExecutionEvidence, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM execution_evidence WHERE task_id = ? ORDER BY rowid", (task_id,)
            ).fetchall()
        records: list[ExecutionEvidence] = []
        for row in rows:
            raw = row["payload_json"]
            digest = _sha256(raw.encode("utf-8"))
            if digest != row["payload_sha256"]:
                raise EvidenceIntegrityError("execution evidence digest mismatch")
            payload = json.loads(raw)
            records.append(
                ExecutionEvidence(
                    task_id, row["attempt_id"],
                    ExecutionResult(payload["exit_code"], payload["stdout"], payload["stderr"], payload["metadata"]),
                    digest,
                )
            )
        return tuple(records)
    @staticmethod
    def _submission(row: sqlite3.Row) -> SubmissionRecord:
        return SubmissionRecord(
            row["submission_id"], row["idempotency_key_hash"], row["request_sha256"],
            json.loads(row["input_json"]), json.loads(row["result_json"]),
            json.loads(row["verification_json"]) if row["verification_json"] else None,
            row["outcome"], row["created_at"],
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRecord:
        finding = json.loads(row["finding_json"])
        task = RepairTask(
            row["task_id"], row["submission_id"], row["finding_id"], finding["code"],
            finding["subject_id"], finding.get("input_sha256", ""),
        )
        return TaskRecord(
            task, TaskState(row["state"]), finding, row["proposer_id"],
            PrincipalRole(row["proposer_role"]), row["approved_by"], row["worker_id"],
            row["attempt_count"], row["created_at"], row["updated_at"],
        )

    @staticmethod
    def _transition(current: TaskState, target: TaskState) -> None:
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"invalid repair task transition: {current.value} -> {target.value}")


@dataclass(frozen=True)
class SubmissionResult:
    submission: SubmissionRecord
    tasks: tuple[TaskRecord, ...]


class RivetOrchestrator:
    """Trusted orchestration entrypoint; task state changes stay in this layer."""

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    def submit(
        self,
        data: ReconciliationInput,
        idempotency_key: str,
        proposer: Principal,
        candidate_result: Mapping[str, Any] | None = None,
    ) -> SubmissionResult:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        raw_input = data.to_dict()
        engine_result = reconcile(data).to_dict()
        result = dict(candidate_result) if candidate_result is not None else engine_result
        request_digest = _sha256(_json({"input": raw_input, "result": result}).encode("utf-8"))
        key_hash = _sha256(idempotency_key.encode("utf-8"))
        existing = self._store.find_submission(key_hash)
        if existing is not None:
            if existing.request_sha256 != request_digest:
                raise IdempotencyConflictError("idempotency key was already used for another request")
            if existing.verification is not None:
                return SubmissionResult(existing, self._store.list_tasks(existing.submission_id))
            submission = existing
        else:
            submission_id, created_at = str(uuid.uuid4()), _now()
            submission = SubmissionRecord(
                submission_id, key_hash, request_digest, raw_input, result, None, None, created_at
            )
            self._store.create_submission(submission, self._proposed_tasks(submission, result, proposer))

        report = verify_reconciliation(raw_input, result).to_dict()
        accepted = report["outcome"] == "PASS" and report["financial_action_authorized"] is False
        self._store.finalize_verification(submission.submission_id, report, accepted)
        finalized = self._store.get_submission(submission.submission_id)
        return SubmissionResult(finalized, self._store.list_tasks(finalized.submission_id))

    def approve(self, task_id: str, approver: Principal) -> TaskRecord:
        if approver.role != PrincipalRole.HUMAN:
            raise AuthorizationError("only a human principal may approve a repair task")
        task = self._store.get_task(task_id)
        if task.state != TaskState.VERIFIED:
            raise InvalidTransitionError("only independently verified tasks may be approved")
        if approver.principal_id == task.proposer_id:
            raise AuthorizationError("a proposer cannot approve their own repair task")
        self._store.approve_task(task_id, approver)
        return self._store.get_task(task_id)

    def execute(self, task_id: str, worker: Principal, sandbox: SandboxAdapter) -> TaskRecord:
        if worker.role != PrincipalRole.WORKER:
            raise AuthorizationError("execution requires a worker principal")
        record = self._store.get_task(task_id)
        if record.state != TaskState.APPROVED:
            raise InvalidTransitionError("execution requires prior human approval")
        if worker.principal_id in {record.proposer_id, record.approved_by}:
            raise AuthorizationError("worker must be distinct from proposer and approver")
        attempt_id, _ = self._store.begin_attempt(task_id, worker)
        try:
            result = sandbox.execute(record.task, worker)
            if not isinstance(result, ExecutionResult):
                raise TypeError("sandbox adapter returned an invalid result type")
            _json(result.to_dict())
        except Exception as exc:
            result = ExecutionResult(None, "", "", {"failure_type": type(exc).__name__})
        outcome = TaskState.SUCCEEDED if result.exit_code == 0 else TaskState.FAILED
        self._store.finish_attempt(task_id, attempt_id, outcome, result)
        return self._store.get_task(task_id)

    @staticmethod
    def _proposed_tasks(
        submission: SubmissionRecord, result: Mapping[str, Any], proposer: Principal
    ) -> tuple[TaskRecord, ...]:
        findings = result.get("findings", [])
        if not isinstance(findings, list):
            return ()
        now, records = _now(), []
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            finding_id, code, subject = finding.get("finding_id"), finding.get("code"), finding.get("subject_id")
            if not all(isinstance(value, str) and value for value in (finding_id, code, subject)):
                continue
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"rivet:{submission.submission_id}:{finding_id}"))
            stored_finding = dict(finding)
            input_digest = submission.result.get("input_sha256", "")
            stored_finding["input_sha256"] = input_digest
            task = RepairTask(task_id, submission.submission_id, finding_id, code, subject, input_digest)
            records.append(
                TaskRecord(task, TaskState.PROPOSED, stored_finding, proposer.principal_id,
                           proposer.role, None, None, 0, now, now)
            )
        return tuple(records)
