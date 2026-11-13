# ADR-004: Environments and shared VM

- **Status:** draft, waiting for Martins' decision (Phase 0, November 2026)
- **Related:** ADR-001 (tools and data sizing: the VM's size and free capacity)

## Context

**Four environments.** Every component reads `CECL_ENV` and stops if it is missing
([cecl/environment.py](../../cecl/environment.py)):

| | local | ci | dev | prod |
|---|---|---|---|---|
| Where | laptop | GitHub Actions | Docker on Martins' PC | the Oracle VM shared with Bank Health |
| Data | synthetic fixtures (DuckDB) | synthetic fixtures (DuckDB) | Databricks `cecl_lab_dev` | Databricks `cecl_lab` |
| May write prod, publish, move `@champion` | no | no | no | yes |

**The shared VM.** Prod has to run somewhere that is always on. The only such machine is the Oracle
Cloud VM that already runs Bank Health, a live project:

- Oracle Cloud, US Midwest region, shape VM.Standard.A1.Flex: 4 OCPU, 24 GB RAM, **Arm (aarch64)**,
  Ubuntu 24.04, 100 GB boot volume. The account is Pay As You Go: going over the free allowance costs
  money, so nothing here adds or resizes resources without Martins' approval.
- Bank Health runs Airflow in Docker under `/srv/bank-health/` (app, secrets, var), UI on VM port
  8080 (tunnel to local 8090).
- Its weekly prod run starts **Monday 06:00 Chicago and must finish by Monday noon**.
- Unattended upgrades with an **automatic reboot at 03:00**.
- A self-hosted GitHub Actions runner deploys Bank Health.
- **Bank Health must never be changed, restarted or slowed by CECL work.**

The VM is reached only through Martins' SSH aliases. Its address, hostname, OCIDs and user names
never appear in this repository (placeholder: `<vm-host>`).

**The shared Databricks workspace.** Bank Health uses the same Free Edition workspace, with catalogs
`bank_health` and `bank_health_dev`, so new catalogs appear to be allowed. Bank Health uses one
personal access token for everything.

## Options

### A. Where prod Airflow runs

| Option | For | Against |
|---|---|---|
| **A1. Own Airflow stack on the shared VM** | No extra cost; same proven Docker pattern as Bank Health; full separation of containers, metadata database, ports and failures | Uses some of the VM's RAM, CPU and disk; needs caps and schedule rules so Bank Health is never slowed |
| A2. CECL DAGs inside Bank Health's Airflow | Least RAM and disk | Ties the projects together: a CECL dependency, upgrade or crash can break Bank Health; one metadata database; one set of secrets; breaks "never change Bank Health" |
| A3. A second VM | Complete isolation | Costs money on a Pay As You Go account; another machine to patch and secure |
| A4. Prod on the PC | No VM work | Not always on; the PC is dev |

### B. How A1 is isolated (if chosen)

| Layer | Plan | Note |
|---|---|---|
| Linux user | the role user `cecl`, owning `/srv/cecl/{app,secrets,var}`; it can't read `/srv/bank-health/`, and Bank Health's users can't read `/srv/cecl/` | A user in the root `docker` group can mount any host folder into a container, which is root-level access in practice and would bypass these folder permissions. So `cecl` is **not** added to the `docker` group (see next row) |
| Docker daemon | **rootless Docker** for `cecl` (set up in Phase 1): its own Docker daemon running as `cecl`, so CECL containers have only `cecl`'s rights on the host and can't reach Bank Health's files, containers or daemon | Resource caps (`mem_limit`, `cpus`) under rootless Docker need **cgroup v2 with delegation** of the memory and cpu controllers to the `cecl` user (a systemd setting). Without delegation the caps are silently ignored. Phase 1 checks this with `docker info` and a test container before the first deploy |
| Docker | Compose project `cecl`: own containers, network, volumes, Airflow metadata database; `restart: unless-stopped` so it returns after the 03:00 reboot | names never collide with Bank Health's |
| Ports | VM port 8081 bound to 127.0.0.1; SSH tunnel to local 8091; dev on the PC stays on 8082 | nothing opened to the internet |
| Resources | Docker caps on every container: about 5.75 GB RAM and 2.0 CPUs in total ([airflow/docker-compose.yaml](../../airflow/docker-compose.yaml)) | leaves 2 OCPU and about 18 GB for Bank Health and the OS; to be confirmed against `docker stats` (ADR-001) |
| Schedules | no run starts on a Monday before 13:00 Chicago, or 02:30 to 04:00 any day; weekly health check on Wednesday | enforced twice: CI rejects bad schedules; every task checks the clock ([cecl/scheduling.py](../../cecl/scheduling.py)) |
| Disk | only code, logs and small outputs on the VM; Freddie files go to Databricks / cloud storage | log rotation needed (Phase 1) |
| Image | built on the VM, so native Arm; CI builds linux/arm64 too | every Python dependency needs an Arm wheel; modelling libraries run on Databricks, not the VM |
| Alerts | separate CECL dev and prod channels, messages prefixed `[CECL]` / `[CECL dev]` | never mixed with Bank Health's alerts |

The 13:00 Monday limit leaves an hour after Bank Health's noon deadline. A task that starts before
02:30 and is still running at 03:00 would be killed by the reboot. Every task is idempotent (safe to
re-run), so the next run or a manual clear recovers it.

### C. Databricks layout

Martins' stated direction: **C1 first; C2 only if Free Edition can't create catalogs.** A case where
exactly one new catalog can be created is not planned for.

| Option | For | Against |
|---|---|---|
| **C1. Two catalogs: `cecl_lab_dev` (dev) and `cecl_lab` (prod)**, the same pattern as Bank Health | Clear separation; same schema names in both | Needs permission to create catalogs |
| C2. Fallback if catalogs can't be created: the workspace's default catalog, environment in the schema name (`workspace.cecl_lab_dev_gold`, `workspace.cecl_lab_gold`) | Works with no new catalogs | Longer names; separation by naming only |

Both are built in (`CECL_DATABRICKS_LAYOUT=catalogs` or `schema_prefix`); C2 is a configuration
switch, not a rewrite.

### D. Dev and prod credentials on Free Edition

A user can create several personal access tokens, but each token has **all of that user's
permissions**. A "dev token" made by the same user can still write the prod catalog. Real separation
needs a service principal (a non-human identity) with grants on `cecl_lab_dev` only. Whether Free
Edition offers service principals is **not yet verified** (Martins checks in Phase 1).

- If it does: a dev identity with no rights on `cecl_lab`, and a prod identity.
- If it doesn't: record the limitation here. Separation then rests on code guards only (the
  environment module, the dbt environment macro, fixed catalog names) and on keeping the prod token
  only on the VM.

### E. How prod is deployed to the VM

Martins' stated direction: **E2, manual deploys**, written up as a runbook in Phase 1 (`docs/runbook.md`):
SSH in as `cecl`, `git pull`, rebuild, smoke check. No self-hosted runner for CECL. E1 is kept
here as the considered alternative.

| Option | For | Against |
|---|---|---|
| E1. A second self-hosted GitHub runner, registered only to the CECL repo, running as `cecl` | Same pattern as Bank Health; deploys are logged in GitHub; repeatable | Another service on the VM using RAM. **On a public repo, a self-hosted runner can be made to run code from a fork's pull request**: it would have to run only on pushes to `main` / manual dispatch, behind a protected environment, and never for pull requests. A runner bug or compromise could reach the VM |
| **E2. Manual deploy over SSH** (pull, rebuild, smoke check, per the runbook) | Nothing extra running on the VM; nothing a fork can trigger; simple | Manual steps; each deploy must be written in the run log by hand; Martins does every deploy |

**To-do for Martins (Bank Health, outside this repo):** confirm that Bank Health's self-hosted
runner only runs workflows on push to `main` or manual dispatch, behind its protected environment,
and never on pull requests. The fork-PR risk above applies to it today.

## Consequences

- CECL can be started, stopped, upgraded or broken without touching Bank Health's containers,
  database, ports or secrets.
- Bank Health keeps priority: CECL's caps and blocked windows mean its Monday run has the VM's
  remaining capacity to itself.
- Some CECL runs wait for an allowed window, and a run interrupted by the 03:00 reboot is re-run.
- The VM's free disk and memory must be confirmed before the first deploy (ADR-001 check) and watched
  afterwards (weekly health check).
- If Free Edition has no service principals, dev and prod are separated by code, not by permissions.
  That is weaker, and is stated in SECURITY.md and the model card.

## Decision

*To be written by Martins.*

<!-- Suggested points to cover: A (where prod runs), B (isolation, incl. the docker-group caveat),
C (catalogs or fallback, after checking catalog creation), D (credentials, after Martins checks
whether Free Edition has service principals), E (manual deploys). -->
