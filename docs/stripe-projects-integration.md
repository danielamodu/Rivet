# Stripe Projects and PostgreSQL integration — Phase 2

Last verified: 2026-10-18

## Status

The Projects plugin and Stripe CLI authentication are verified, and the live Stripe Projects catalog was queried. The user authorized Neon PostgreSQL Free at $0, but no Projects app or database has been initialized or provisioned. Initialization is blocked because the active Stripe CLI context is Test Mode and the installed Projects plugin requires live-mode credentials to create the project. The user has explicitly chosen to retain Test Mode. Database connectivity is therefore untested.

## Latest initialization result and mode blocker

After the user authorized the Stripe CLI in the **Test mode** sandbox, the CLI was run once with the approved manual-init options:

```bash
stripe projects init Rivet --mode manual --skip-skills --json --yes
```

It returned `PROJECTS_CONTEXT_MISMATCH` (retryable). The exact message was: “The Stripe CLI's active context is test mode, but Stripe Projects needs live-mode credentials.” The CLI suggested `stripe switch context <authorized-account-id> --live`, then retrying initialization. The account identifier is intentionally omitted here. This was a context error, not an authentication failure; the response said not to run `stripe login` or `stripe projects init` again until the context changes.

A separate preflight attempt with `--testmode` and no import source returned `INVALID_ARGUMENT`: `--testmode` on `stripe projects init` requires `--from` in manual mode. This confirms the flag is not a standalone way to initialize a new manual project in Test Mode.

### What the mode requirement means

| Route | Authentication context and command | Financial / security implications | Status |
|---|---|---|---|
| Keep the current Test Mode context | Authentication: existing Test Mode sandbox. Command attempted: `stripe projects init Rivet --mode manual --skip-skills --json --yes`; it failed with `PROJECTS_CONTEXT_MISMATCH`, and no state was created. | Preserves the Test Mode boundary, but cannot initialize a new Projects app through this CLI context. | **VERIFIED blocker** |
| Switch the CLI to Live Mode, then initialize | Authentication: existing Stripe account in Live Mode. Commands documented in local CLI help and suggested by the actual error: `stripe switch context <authorized-account-id> --live`, then `stripe projects init Rivet --mode manual --skip-skills --json --yes`. Neither command was run. | The switch command is documented as selecting the account's Live Mode context. Inference from that command description: the switch changes local CLI context and is not itself a payment request. It gives later CLI commands access to real account data. Stripe documents that live-mode API requests use real account data, unlike Test Mode. Projects docs say `init` creates a Stripe-backed project record, but do not explicitly guarantee that live-mode init is free of financial effects or payment-method requirements; those are **UNVERIFIED**. This route crosses the user's Test Mode-only boundary and requires explicit approval. | **DOCUMENTED command; financial safety of init UNVERIFIED** |
| Import a shared stack with `--testmode` | Authentication: would still need an authorized Stripe account; whether an existing Test Mode context can complete init is unverified. Installed `stripe projects init --help` documents `--from <share-url>` for importing a shared stack and says `--testmode` creates provisioned resources in Stripe Test Mode. Candidate syntax: `stripe projects init Rivet --mode manual --skip-skills --from <share-url> --testmode --json --yes`. This combination was not run. | The local help documents Test Mode for provisioned resources, but does not say this bypasses the Live Mode requirement for creating the Projects account/project. Stripe's public hackathon guide does not document a Test-Mode-only fresh-init workflow or this combination. Do not treat it as a verified workaround. | **CLI options DOCUMENTED; Test-Mode-only initialization UNVERIFIED** |
| Provision Neon outside Stripe Projects | Authentication: separate Neon account credentials; no Stripe CLI context. No command was selected or run. Neon is listed in the live Stripe Projects catalog, but direct provisioning would bypass the requested Projects workflow. | Could avoid switching Stripe CLI context, but requires separate provider setup and credential management. The $0 result would depend on staying within Neon Free limits/terms. This is not an official Stripe Projects alternative. | **Possible operational alternative; not performed** |

Stripe's official Test Mode documentation states that Test Mode API requests do not affect live data or interact with banking networks, and that API key mode determines which environment is accessed. This supports keeping Rivet's payment simulation in Test Mode; it does not establish that Stripe Projects project initialization itself is Test-Mode-compatible.

### Supported commands and current decision

Commands documented by the installed CLI and official Projects materials:

```bash
# Normal documented project initialization (blocked in the current Test Mode context)
stripe projects init Rivet --mode manual --skip-skills --json --yes

# CLI-documented context switch suggested by the actual error (NOT approved or run)
stripe switch context <authorized-account-id> --live

# Retry only after an explicitly approved context change
stripe projects init Rivet --mode manual --skip-skills --json --yes

# CLI help advertises this only with a shared-stack import; workaround is unverified
stripe projects init Rivet --mode manual --skip-skills --from <share-url> --testmode --json --yes
```

**Recommended next step:** retain Test Mode and do not retry initialization. Ask Stripe Projects support whether a fresh project can be initialized against a Test Mode sandbox, or obtain explicit user approval for a narrowly scoped Live Mode CLI context after accepting that the no-financial-effect guarantee for `init` is not documented. Do not run `stripe projects add` or provision Neon until that decision is made. Neon Free remains the approved provider/tier candidate; no payment-method requirement has been tested.

## Verified environment and CLI

| Capability | Status | Evidence |
|---|---|---|
| WSL project location and branch | VERIFIED | `/mnt/c/Users/USER/Desktop/Rivet`, branch `main`, upstream `origin/main` |
| Stripe CLI | VERIFIED | `/home/xbt/.local/bin/stripe`, version `1.54.0` |
| Stripe CLI authentication | VERIFIED | `stripe whoami` exited successfully; output was suppressed to avoid recording account identifiers |
| Projects plugin | VERIFIED | User's interactive install displayed `installation of v0.50.0 complete`; subsequent Projects help showed commands |
| Projects command workflows | VERIFIED | `stripe projects --help` listed `init`, `catalog`, `search`, `add`, `env`, `billing`, `spend`, and related commands |
| Git ignore for `.env` | VERIFIED | `git check-ignore -q .env` succeeded |
| Projects local-state ignore rules | VERIFIED | `.gitignore` now excludes `.projects/cache`, `.projects/vault`, and local/test state files; these changes appeared after Projects setup |
| Credential keyring | UNAVAILABLE | Install screenshot warned system keyring was unavailable and CLI credentials were stored unencrypted under the WSL home directory; credential contents were not inspected or exposed |

The catalog response reported `project_initialized: false` and `authenticated: false` in its metadata. The later browser authorization succeeded for the Test Mode sandbox, but the subsequent init was rejected for mode mismatch. No Projects project was initialized.

## Live provider catalog

`stripe projects catalog --json` returned catalog data from the API, timestamped `2026-10-17T22:38:27.870Z`. Subsequent filtered provider queries used that same cached catalog timestamp. Relevant available PostgreSQL entries:

| Provider / service | Live catalog tier and limits | Price and notes |
|---|---|---|
| **Neon / `postgres`** | `Neon Free` plan is available. Catalog describes serverless Postgres but does not include quotas. Current Neon official information: 1 GB database storage per project, 100 CU-hours per project/month, autoscaling up to 2 CU (~8 GB RAM), 10 branches, and a 6-hour instant restore window. | Free plan: $0. Catalog also lists a paid `launch` parent option with usage-based compute/storage. No paid upgrade selected. |
| Prisma / `database` | Free plan: 100k queries and 0.5 GiB-month storage per billing cycle; hard limits, no paid overages. Region defaults to `us-east-1`. | $0 on the listed free plan. |
| Render / `postgres` | Free instance: 256 MB RAM, 0.1 CPU, 1 GB storage. | Paid catalog options: $6/month (256 MB), $19/month (1 GB RAM), $75/month (4 GB RAM); storage $0.30/GB/month on paid tiers. |
| Railway / `postgres` | Free provider plan: 500 compute hours, 512 MB RAM, shared CPU, 1 GB disk. | PostgreSQL is free under the free plan; paid plans are usage based: compute $0.000463/min/vCPU, memory $0.000231/min/GB, volume storage $0.25/GB/month. |
| ClickHouse / `postgres` | Managed Postgres Scale marked “Free during preview.” Catalog exposes region/VM selections but does not give preview resource quotas. | Catalog marks free during preview; limits and preview duration are not specified. |
| PlanetScale / `postgresql` | No free tier listed. Lowest listed PS-5 is 1/16 vCPU, 512 MB RAM. | Starts at $5/node/month; 10 GB storage and 10 GB egress included in the least expensive US regions, with regional differences. |
| Fly.io / `mpg` | Managed Postgres; listed plans start with `basic`. | Basic: $38/month, starting at 10 GB storage; storage $0.28/GB/month. Bandwidth is region-dependent. |

**Recommended candidate for approval:** Neon `postgres` on `Neon Free`, expected service cost **$0** while kept on that plan. The current official Neon free-plan limits above come from [Neon's Oct. 2, 2026 update](https://neon.com/blog/neon-free-plan-1-gb-per-project). The Stripe Projects catalog does not state whether a payment method is required to add the free tier; this remains **UNVERIFIED**. If the CLI requests a payment method, paid tier, or any nonzero commitment, stop and ask before proceeding. No automatic paid upgrade has been authorized.

Other current Neon limits documented by Neon include scale-to-zero behavior; the exact result of exceeding included free usage was not established for this Stripe Projects integration. Do not infer that overages are impossible from the catalog's free label.

## Commands executed

Commands were run from WSL, with the project kept at its existing path. Outputs containing account identifiers or credentials were suppressed or not requested.

```bash
cd /mnt/c/Users/USER/Desktop/Rivet
/home/xbt/.local/bin/stripe --version
/home/xbt/.local/bin/stripe plugin list
/home/xbt/.local/bin/stripe projects --help
/home/xbt/.local/bin/stripe whoami  # output suppressed; exit status checked
/usr/bin/timeout 25s /home/xbt/.local/bin/stripe plugin install projects  # initial attempt failed
/usr/bin/npm install --global --prefix /home/xbt/.local @stripe/cli@latest  # EEXIST: binary already exists
/usr/bin/npm install --global --prefix /home/xbt/.local/share/rivet-stripe-cli @stripe/cli@latest
/home/xbt/.local/share/rivet-stripe-cli/bin/stripe --version
/usr/bin/timeout 55s /home/xbt/.local/share/rivet-stripe-cli/bin/stripe plugin install projects
/home/xbt/.local/bin/stripe projects catalog --json
/home/xbt/.local/bin/stripe projects catalog database --json
/home/xbt/.local/bin/stripe projects catalog Railway --json
/home/xbt/.local/bin/stripe projects catalog Neon --json
git check-ignore -q .env
git diff --check
```

The initial install attempt returned `could not install plugin 'projects': context canceled`. The user later completed the interactive official plugin install successfully (v0.50.0); CLI help and catalog commands were then verified. The npm package installed to the separate user-local prefix reports CLI v1.54.0. Projects local-state ignore rules appeared in `.gitignore` after setup and are included to avoid tracking local vault/cache/state. Temporary filtered catalog JSON was written under `/tmp`, not into the repository.

## Documentation and next step

- [Stripe Projects developer setup](https://projects.dev/developers/) documents `stripe plugin install projects` and `stripe projects catalog` / `add` workflows.
- [Stripe Projects participant guide](https://projects.dev/hackathon-participants) documents normal project initialization, account setup, service catalog, credential sync, and spend commands; it does not describe a Test-Mode-only init path.
- [Stripe CLI documentation](https://docs.stripe.com/stripe-cli) and the installed `stripe switch context --help` describe the CLI; the `--live` switch behavior was verified from local help.
- [Stripe API reference](https://docs.stripe.com/api) and [API keys documentation](https://docs.stripe.com/keys) distinguish Test Mode from Live Mode and describe which account data each mode accesses.
- [Neon Free plan update](https://neon.com/blog/neon-free-plan-1-gb-per-project) provides current Neon Free database limits.

The user approved initialization and exactly one Neon Free PostgreSQL resource at $0, subject to keeping Test Mode and stopping if any payment method, paid upgrade, or financial commitment is required. That approval does not authorize switching the CLI to Live Mode. If the user later authorizes a mode change, still stop before any payment method, paid tier, nonzero commitment, or paid add-on. Only after safe provisioning should credentials be synced (never printed) and a trusted local Python check run for `SELECT 1`, PostgreSQL version, transactions, JSONB, and large integer storage.

No database, payment/customer record, or financial mutation was created. No provider charge was incurred by catalog inspection.
