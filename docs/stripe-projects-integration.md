# Stripe Projects and PostgreSQL integration — Phase 2

Date: 2026-10-17

## Status

The Projects plugin and Stripe CLI authentication are verified. The live Stripe Projects catalog is available. No Projects app or database has been initialized or provisioned; database connectivity is therefore untested. Provisioning is awaiting explicit approval for a specific provider and tier.

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

The catalog response reported `project_initialized: false` and `authenticated: false` in its metadata. This is distinct from the successful `stripe whoami` check; no Projects project was initialized during this inspection.

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
- [Stripe Projects participant guide](https://projects.dev/hackathon-participants) documents project initialization, service catalog, credential sync, and spend commands.
- [Neon Free plan update](https://neon.com/blog/neon-free-plan-1-gb-per-project) provides current Neon Free database limits.

Request explicit approval before initializing the Stripe Projects app and adding the Neon free PostgreSQL service. After approval, if the CLI presents a payment-method requirement, paid upgrade, or charge, stop and report before accepting it. Only after database provisioning is authorized should credentials be synced (never printed) and a trusted local Python check run for `SELECT 1`, PostgreSQL version, transactions, JSONB, and large integer storage.

No database, payment/customer record, or financial mutation was created. No provider charge was incurred by catalog inspection.
