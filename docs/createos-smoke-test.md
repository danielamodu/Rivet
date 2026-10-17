# CreateOS Sandbox smoke test

## Status

**Prepared, not executed.** Read-only authentication and catalog calls succeeded,
but sandbox creation was not attempted because the account's billing-credit
balance is not exposed by the installed SDK's `who_am_i()` response. CreateOS
documents per-second resource billing. The test script requires an explicit
`--confirm-billable-sandbox` flag before it can create a sandbox.

No stdout, stderr, execution exit codes, file-transfer result, or cleanup result
are reported as test outcomes because no sandbox was created.

## Verified locally

- Installed package: `createos-sandbox==0.1.3` in the project `.venv`.
- `CREATEOS_API_KEY` authentication succeeded using `Client().who_am_i()`.
- The read-only identity response reported zero active sandboxes.
- The live shape catalog includes `s-1vcpu-256mb`.
- The live root-filesystem catalog includes `devbox:1`.
- No sandbox, database, or other remote resource was created.

## SDK methods confirmed in the installed package

The smoke script uses these methods and models from the installed SDK:

- Authentication: `Client()` reads `CREATEOS_API_KEY`; `Client.who_am_i()`.
- Creation: `Client.create_sandbox(CreateSandboxRequest(...))`.
- Execution: `SandboxInstance.run_command(RunCommandRequest(...))`; results
  contain `standard_output`, `standard_error`, and `exit_code`.
- Upload: `sandbox.files.upload(absolute_path, bytes)`.
- Download: `sandbox.files.download(absolute_path)` returns a readable stream.
- Cleanup: `sandbox.destroy()` followed by
  `sandbox.wait_until_destroyed(WaitOptions(timeout=120.0))`.

These names and signatures were inspected from the installed `createos` Python
package. The Python SDK overview documents the same create/run/destroy workflow
and file transfer patterns: [CreateOS Sandbox SDKs](https://createos.sh/docs/Sandbox/SDK/Overview/).

## Test design

When authorized, `scripts/createos_smoke_test.py` creates one
`s-1vcpu-256mb` sandbox using `devbox:1`, runs Python code in that sandbox,
captures the success and deliberate-exception results, tests upload and
download, and destroys the sandbox in `finally`. It does not pass the API key or
other environment variables to the guest. The script does not configure or test
egress and makes no isolation claim.

The script emits only sanitized test output and exception class names; it does
not print or serialize the API token.

## Billing and next action

CreateOS documents sandbox billing by vCPU, memory, and storage runtime; a
running sandbox is billable until destroyed. Its documentation also describes
free credits for new accounts, but the account's balance was not verified. See
[Sandbox limits and defaults](https://createos.sh/docs/Sandbox/Limits/) and
[CreateOS pricing](https://createos.sh/docs/Account-Billing/Pricing/).

After approval to create one short-lived sandbox, run from WSL in the project
directory:

```bash
source $HOME/.config/rivet/createos.env
.venv/bin/python scripts/createos_smoke_test.py --confirm-billable-sandbox
```

Then replace this status section with the actual sanitized output and cleanup
result. Do not infer successful execution from the installed SDK or docs alone.
