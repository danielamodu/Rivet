#!/usr/bin/env python3
"""Minimal CreateOS Sandbox integration smoke test for Rivet.

Sandbox creation consumes CreateOS resources. Pass --confirm-billable-sandbox
only after confirming that a short run is acceptable for this account.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

from createos import (
    Client,
    APIError,
    CreateSandboxRequest,
    ListSandboxesOptions,
    RequestOptions,
    RetryOptions,
    RunCommandRequest,
    WaitOptions,
)


SHAPE = "s-1vcpu-256mb"
ROOTFS = "devbox:1"
EGRESS_RULES = ["127.0.0.1"]
REMOTE_INPUT = "/tmp/rivet-smoke-input.txt"
REMOTE_OUTPUT = "/tmp/rivet-smoke-output.txt"
INPUT_CONTENT = b"RIVET_UPLOAD_PAYLOAD"


def result_record(response) -> dict[str, object]:
    result = response.result
    return {
        "stdout": result.standard_output,
        "stderr": result.standard_error,
        "exit_code": result.exit_code,
    }


def safe_error_record(exc: Exception) -> dict[str, object]:
    secret = os.environ.get("CREATEOS_API_KEY", "")
    message = str(exc)
    if secret:
        message = message.replace(secret, "[REDACTED]")
    record: dict[str, object] = {
        "type": type(exc).__name__,
        "message": message,
    }
    if isinstance(exc, APIError):
        record["http_status"] = exc.status_code
        record["api_code"] = exc.code
        if exc.request_id:
            record["request_id"] = exc.request_id
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-billable-sandbox",
        action="store_true",
        help="confirm authorization to create and run one billable sandbox",
    )
    args = parser.parse_args()

    if not os.environ.get("CREATEOS_API_KEY"):
        print("CREATEOS_API_KEY is not set", file=sys.stderr)
        return 2
    if not args.confirm_billable_sandbox:
        print(
            "Sandbox creation is gated: obtain billing approval, then pass "
            "--confirm-billable-sandbox.",
            file=sys.stderr,
        )
        return 2

    report: dict[str, object] = {
        "authentication": "not checked",
        "sandbox": {"shape": SHAPE, "rootfs": ROOTFS},
        "executions": {},
        "file_transfer": {"status": "not tested"},
        "cleanup": {"status": "not started"},
    }
    client = Client(retry=RetryOptions(max_retries=0))
    sandbox = None
    sandbox_name = f"rivet-smk-{uuid.uuid4().hex[:10]}"
    create_attempted = False
    creation_started = None
    passed = False

    try:
        client.who_am_i()
        report["authentication"] = "verified"

        # No environment variables or credentials are passed into the sandbox.
        # Only loopback is allowlisted; the test makes no isolation claim.
        creation_started = time.monotonic()
        create_attempted = True
        sandbox = client.create_sandbox(
            CreateSandboxRequest(
                shape=SHAPE,
                rootfs=ROOTFS,
                name=sandbox_name,
                egress_rules=EGRESS_RULES,
            ),
            RequestOptions(disable_retry=True),
        )
        sandbox_record = report["sandbox"]
        assert isinstance(sandbox_record, dict)
        sandbox_record["status"] = str(sandbox.status)
        sandbox_record["egress_rules"] = list(sandbox.data.egress_rules)
        if sandbox.data.egress_rules != EGRESS_RULES:
            raise RuntimeError("server did not confirm the requested egress rules")

        success = sandbox.run_command(
            RunCommandRequest(
                command="python3",
                arguments=[
                    "-c",
                    "print('RIVET_CREATEOS_SMOKE_OK')",
                ],
            )
        )
        failure = sandbox.run_command(
            RunCommandRequest(
                command="python3",
                arguments=[
                    "-c",
                    "raise RuntimeError('RIVET_INTENTIONAL_FAILURE')",
                ],
            )
        )

        sandbox.files.upload(REMOTE_INPUT, INPUT_CONTENT)
        file_script = (
            "from pathlib import Path; "
            f"source=Path({REMOTE_INPUT!r}); "
            f"target=Path({REMOTE_OUTPUT!r}); "
            "target.write_bytes(b'RIVET_FILE_CREATED:' + source.read_bytes()); "
            "print('RIVET_FILE_EXECUTION_OK')"
        )
        file_execution = sandbox.run_command(
            RunCommandRequest(command="python3", arguments=["-c", file_script])
        )
        with sandbox.files.download(REMOTE_OUTPUT) as remote_file:
            downloaded = remote_file.read()

        executions = {
            "success": result_record(success),
            "deliberate_exception": result_record(failure),
            "file_creation": result_record(file_execution),
        }
        report["executions"] = executions
        report["file_transfer"] = {
            "status": (
                "passed"
                if downloaded == b"RIVET_FILE_CREATED:" + INPUT_CONTENT
                else "failed"
            ),
            "upload_bytes": len(INPUT_CONTENT),
            "downloaded_content": downloaded.decode("utf-8", errors="replace"),
        }
        report["egress"] = {
            "requested_rules": EGRESS_RULES,
            "server_reported_rules": list(sandbox.data.egress_rules),
            "external_connectivity_probe": "not performed",
        }

        success_result = success.result
        failure_result = failure.result
        file_result = file_execution.result
        passed = (
            success_result.exit_code == 0
            and success_result.standard_output.strip() == "RIVET_CREATEOS_SMOKE_OK"
            and failure_result.exit_code != 0
            and "RIVET_INTENTIONAL_FAILURE" in failure_result.standard_error
            and file_result.exit_code == 0
            and file_result.standard_output.strip() == "RIVET_FILE_EXECUTION_OK"
            and downloaded == b"RIVET_FILE_CREATED:" + INPUT_CONTENT
        )
        report["overall"] = "passed" if passed else "failed"
    except Exception as exc:  # Keep SDK exceptions from exposing request data.
        report["overall"] = "failed"
        report["error"] = safe_error_record(exc)
    finally:
        cleanup_targets = [sandbox] if sandbox is not None else []
        if sandbox is None and create_attempted:
            try:
                cleanup_targets = [
                    candidate
                    for candidate in client.list_sandboxes(
                        ListSandboxesOptions(limit=500)
                    )
                    if candidate.name == sandbox_name
                ]
            except Exception as exc:
                report["cleanup"] = {
                    "status": "lookup_failed",
                    "error": safe_error_record(exc),
                }
                passed = False
                report["overall"] = "failed"

        if cleanup_targets:
            try:
                for target in cleanup_targets:
                    target.destroy()
                    target.wait_until_destroyed(WaitOptions(timeout=120.0))
                report["cleanup"] = {
                    "status": "destroyed",
                    "matching_sandboxes": len(cleanup_targets),
                }
                if creation_started is not None:
                    report["lifecycle_wall_seconds"] = round(
                        time.monotonic() - creation_started, 3
                    )
            except Exception as exc:
                report["cleanup"] = {
                    "status": "failed",
                    "error": safe_error_record(exc),
                }
                passed = False
                report["overall"] = "failed"
        elif report["cleanup"].get("status") != "lookup_failed":
            report["cleanup"] = {"status": "no sandbox created"}
        client.close()

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
