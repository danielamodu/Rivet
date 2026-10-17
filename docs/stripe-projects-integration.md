# Stripe Projects and PostgreSQL integration — Phase 2

Date: 2026-10-17

## Status

**Phase blocked before catalog inspection.** Stripe CLI authentication is verified, but the Projects plugin did not install. The live provider catalog therefore remains unavailable; no provider or tier was selected, no resource was provisioned, and no database connection was attempted.

## Results

| Capability | Status | Evidence |
|---|---|---|
| WSL project location and branch | VERIFIED | `/mnt/c/Users/USER/Desktop/Rivet`, branch `main`, clean before this report; upstream `origin/main` |
| Stripe CLI | VERIFIED | `/home/xbt/.local/bin/stripe`, version `1.54.0` |
| Stripe CLI authentication | VERIFIED | `stripe whoami` exited successfully; output was suppressed, so account identifiers were not recorded |
| Projects plugin install | UNAVAILABLE | Install attempts did not leave the plugin installed |
| Projects command set | UNAVAILABLE | `stripe projects --help` showed generic help only; `stripe projects catalog` reported that the plugin is required |
| Account provider catalog | UNVERIFIED | Catalog could not run without the plugin |
| PostgreSQL provider/tier, quotas, and cost | UNVERIFIED | No live catalog data was returned |
| Database provisioning and connectivity | UNAVAILABLE | No resource was provisioned and no credentials were created |
| `.env` Git ignore rule | VERIFIED | `git check-ignore -q .env` succeeded |

## Commands run

All development commands ran through WSL from the existing project directory.

```bash
cd /mnt/c/Users/USER/Desktop/Rivet
/home/xbt/.local/bin/stripe --version
/home/xbt/.local/bin/stripe plugin list
/home/xbt/.local/bin/stripe projects --help
/home/xbt/.local/bin/stripe whoami  # output redirected/suppressed for account privacy
/usr/bin/timeout 25s /home/xbt/.local/bin/stripe plugin install projects
/usr/bin/npm install --global --prefix /home/xbt/.local @stripe/cli@latest
/usr/bin/npm install --global --prefix /home/xbt/.local/share/rivet-stripe-cli @stripe/cli@latest
/home/xbt/.local/share/rivet-stripe-cli/bin/stripe --version
/usr/bin/timeout 55s /home/xbt/.local/share/rivet-stripe-cli/bin/stripe plugin install projects
/home/xbt/.local/share/rivet-stripe-cli/bin/stripe plugin list
/home/xbt/.local/share/rivet-stripe-cli/bin/stripe projects --help
/home/xbt/.local/share/rivet-stripe-cli/bin/stripe projects catalog
git status --short --branch
git rev-parse --abbrev-ref --symbolic-full-name '@{u}'
git check-ignore -q .env
```

The first plugin install produced the error:

```text
installing 'projects' v0.50.0...
could not install plugin 'projects': context canceled
context canceled
```

The standard npm install into `/home/xbt/.local` failed with `EEXIST` because `/home/xbt/.local/bin/stripe` already exists. Installing the official npm package in the separate prefix succeeded, but it still reported Stripe CLI `1.54.0`; the subsequent plugin attempt did not install Projects. The catalog invocation then stated that the Projects plugin is required. No untrusted binary was downloaded or run.

## Stripe Projects documentation

The [official Stripe Projects developer setup](https://projects.dev/developers/) documents the Linux installation workflow as installing `@stripe/cli` from npm and then running `stripe plugin install projects`. The [participant guide](https://projects.dev/hackathon-participants) lists the catalog and service workflow (`stripe projects catalog`, `stripe projects add <provider>/<service>`, and `stripe projects env --pull`). Those documented workflows were not verified against this account because the plugin is unavailable locally.

## Outstanding work and safety

1. Resolve the official plugin installation failure in WSL, then verify the available Projects commands.
2. Run the live catalog and report actual PostgreSQL providers, tiers, limits, payment requirements, and costs.
3. Obtain explicit approval for one exact provider/tier and its cost before provisioning.
4. Only after provisioning approval, sync credentials without displaying them and run the requested PostgreSQL checks (`SELECT 1`, server version, transaction, JSONB, and large integer storage).

No Stripe resources, database records, credentials, or financial mutations were created. No cost was incurred by this integration attempt.
