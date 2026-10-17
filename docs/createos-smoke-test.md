# CreateOS Sandbox smoke test

## Status

**PASSED.** One `s-1vcpu-256mb` sandbox was created using `devbox:1`; its
loopback-only egress rule was confirmed in the CreateOS response. All test
commands ran inside that sandbox, file transfer passed, and the sandbox was
destroyed. No credentials were passed into the guest.

Two earlier create requests were rejected by API validation because the
temporary sandbox name exceeded the 22-character limit. The API returned HTTP
400 and the cleanup lookup found no matching resource for either attempt. The
corrected script disables create retries and the successful run created one
sandbox.

## Verified locally

- Installed package: `createos-sandbox==0.1.3` in the project `.venv`.
- `CREATEOS_API_KEY` authentication succeeded using `Client().who_am_i()`.
- The read-only identity response reported zero active sandboxes.
- The live shape catalog includes `s-1vcpu-256mb`.
- The live root-filesystem catalog includes `devbox:1`.
- No sandbox is created by the read-only preflight checks.

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

`scripts/createos_smoke_test.py` created one `s-1vcpu-256mb` sandbox using
`devbox:1`, ran Python code in the sandbox, captured success and deliberate
exception results, tested upload and download, and destroyed the sandbox in
`finally`. The create request set the egress allowlist to `127.0.0.1`, and the
CreateOS response reported that same rule. The test did not probe external
connectivity, so egress enforcement was not independently tested.

The script emits only test output and sanitized API error fields; it does not
print or serialize the API token. SDK retries are disabled for the sandbox
creation request.

## Actual execution results

All three executions were returned by `SandboxInstance.run_command`:

| Execution | Exit code | stdout | stderr |
| --- | ---: | --- | --- |
| Successful Python | 0 | `RIVET_CREATEOS_SMOKE_OK\n` | empty |
| Deliberate exception | 1 | empty | See traceback below |
| Guest file creation | 0 | `RIVET_FILE_EXECUTION_OK\n` | empty |

Actual stderr from the deliberate exception:

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    raise RuntimeError('RIVET_INTENTIONAL_FAILURE')
RuntimeError: RIVET_INTENTIONAL_FAILURE
```

File transfer passed: 20 bytes were uploaded, Python inside the sandbox read
the uploaded file and created an output file, and the SDK downloaded
`RIVET_FILE_CREATED:RIVET_UPLOAD_PAYLOAD`.

Cleanup returned `destroyed` for one matching sandbox. The measured end-to-end
wall-clock lifecycle was 2.058 seconds.

The read-only account metrics query for the surrounding 10-minute window
reported one sandbox spawned and one destroyed, with aggregate lifetime
1.627944 seconds, `1.627944` vCPU-seconds, `416.753664` MiB-seconds of memory,
and `16670.14656` MiB-seconds of disk. These are account-wide metrics, not a
per-sandbox invoice. The metrics response exposed no price, charge, or credit
fields, so the actual monetary/credit charge is unavailable from the API data
queried. The wall-clock duration is not presented as a billing duration.

## Billing and next action

CreateOS documents sandbox billing by vCPU, memory, and storage runtime; a
running sandbox is billable until destroyed. Its documentation also describes
free credits for new accounts, but the account's balance was not verified. See
[Sandbox limits and defaults](https://createos.sh/docs/Sandbox/Limits/) and
[CreateOS pricing](https://createos.sh/docs/Account-Billing/Pricing/).

Run from WSL in the project directory:

```bash
source $HOME/.config/rivet/createos.env
.venv/bin/python scripts/createos_smoke_test.py --confirm-billable-sandbox
```

The script records observed wall-clock lifecycle duration. The installed SDK
does not return a per-run charge. CreateOS's account metrics report aggregate
resource usage, not an invoice amount; see [Sandbox metrics](https://createos.sh/docs/Sandbox/REST-API/Metrics/).
