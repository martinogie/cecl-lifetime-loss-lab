# ADR-001: Tools and data sizing

- **Status:** draft, waiting for the VM check below and Martins' decision (Phase 0, November 2026)
- **Related:** ADR-004 (environments and shared VM)

## Context

The full Freddie Mac history is too large to load casually on free tools. Before building the
pipeline, this ADR records what we have to work with: the VM, Databricks Free Edition and the data.
It then sets how much data each phase uses.

### The VM (shared with Bank Health)

| Item | Value |
|---|---|
| Provider, region | Oracle Cloud, US Midwest |
| Shape | VM.Standard.A1.Flex: **4 OCPU, 24 GB RAM** |
| CPU architecture | **Arm (aarch64)** |
| OS | Ubuntu 24.04 |
| Disk | 100 GB boot volume |
| Account | Pay As You Go (going over the free allowance costs money) |
| Already running | Bank Health: Airflow in Docker, weekly prod run Monday 06:00 to noon Chicago, self-hosted GitHub runner, unattended upgrades with an automatic 03:00 reboot |

The address, hostname, OCIDs and user names are never written here (placeholder: `<vm-host>`).

**Planned CECL share** (ADR-004): at most about 6 GB RAM and 2 CPUs, through Docker caps. Disk use
is limited to code, Docker images, logs and small outputs. Freddie files never stay on the VM.

#### Step for Martins: confirm current free capacity

Run on the VM yourself, **not on a Monday morning** (Bank Health's run) and not between 02:30 and
04:00 (reboot window). Then paste the output into the table below. Remove anything that looks like an
address, hostname or user name before pasting.

```bash
df -h /                      # disk: size, used, free on the boot volume
free -h                      # memory: total, used, available
docker stats --no-stream     # what Bank Health's containers use right now (CPU %, memory)
```

| Check | Date / time checked (Chicago) | Result |
|---|---|---|
| Disk free (`df -h /`) | *to fill* | *to fill* |
| Memory available (`free -h`) | *to fill* | *to fill* |
| Bank Health containers, total memory and CPU (`docker stats`) | *to fill* | *to fill* |

**Pass if:** at least 6 GB of memory stays available beyond Bank Health's use, and enough free disk
for the CECL image, logs and a temporary download buffer. Sizes will be measured when the image is
first built (Phase 1).

### Databricks Free Edition (from Bank Health's experience)

- Serverless compute only, with a **daily compute quota**. When it is used up, compute stops for the
  rest of the day.
- At most 5 concurrent job tasks; one small SQL warehouse.
- Restricted outbound internet: notebooks can't download data, so Airflow downloads the public
  economic data (FRED, FHFA, BLS, Fed scenarios). Freddie files are downloaded by hand (terms below).
- The quota is **shared with Bank Health's jobs** in the same workspace.

### The data (Freddie Mac General User Guide, Release 48, October 2026)

- **Standard dataset:** about 56 million fixed-rate loans originated 1999 to 2026. One origination
  file and one monthly performance file per quarter, pipe-delimited, zipped by quarter. The monthly
  history runs to billions of rows.
- **Sample dataset:** a simple random sample of **50,000 loans per full vintage year** (and a
  proportionate number for partial years), with the same fields. One origination and one performance
  file per year.
- Download requires registration on Freddie Mac's Clarity site (Martins registers and downloads).

**Terms of use (read by Martins, November 2026):**

- Research results may be published for non-commercial purposes, **as long as the underlying data
  can't be rebuilt from them**. So the project publishes aggregates only (counts, rates, reserve
  totals by group), never loan-level rows or tables fine-grained enough to reconstruct loans.
- The data itself **may not be redistributed**: no raw or row-level files in git, on the website, in
  R2 or anywhere public (enforced by `.gitignore`, the CI `no-data` step and control C16).
- The terms don't address scripted (automated) downloads. So **downloads stay manual, through the
  browser, by Martins**, unless Freddie grants API access. Airflow doesn't download Freddie files;
  Martins places them under `data/raw/freddie/` (dev) or uploads them to Databricks storage (prod),
  and the pipeline starts from there.

**The sample files we have** (downloaded by Martins, November 2026): 28 zips, `sample_1999.zip` to
`sample_2026.zip`, in `data/raw/freddie/sample/` (git-ignored). Release 48: originations and
performance to 30 June 2026, 50,000 loans per full vintage year. Each zip holds
`sample_orig_YYYY.txt` and `sample_perf_YYYY.txt`.

| Measure | Value |
|---|---|
| Zips, compressed | about 1.2 GB in total |
| Text inside, uncompressed | about 8.8 GB in total (for example 1999: 6 MB origination, 288 MB performance) |
| Free disk on the development PC | about 12 to 13 GB |

### Rule: read from the zip, never unzip

The uncompressed files would use most of the PC's free disk. So **every tool reads the sample files
straight out of their zips, streaming one file at a time; nothing is ever extracted to disk.**

- The contract checker does this today (`ingestion.data_contracts.check_zip`; run with
  `python -m scripts.check_freddie_samples`). Checking all 28 years takes a few minutes and writes only a
  small JSON summary.
- Phase 1 loaders must do the same: stream from the zip into DuckDB or Parquet, and only write
  outputs that are much smaller than the input (typed, compressed, only the needed columns), after
  checking free disk first.
- Any temporary file needs a size estimate and a check of free disk first; a job that would leave
  less than about 3 GB free stops instead of filling the drive.

## Options

| Option | For | Against |
|---|---|---|
| **1. Sample files for all development and testing; full data only for chosen vintages** (for example the 2005 to 2008 crisis years and one recent year) once the pipeline is proven | Fits Free Edition's quota; fast local work in DuckDB; the crisis backtest still uses full data where it matters most | Results on the sample have wider error bars; the chosen vintages must be justified |
| 2. Full dataset from the start | Most complete | Billions of rows against a daily quota shared with Bank Health; slow iteration; high risk of quota stops |
| 3. Sample files only | Cheapest, simplest | The crisis backtest and vintage calibration rest on 50,000 loans per year; weaker evidence |
| 4. Pay for Databricks compute | Removes the quota | Costs money; not needed to show the method |

## Consequences

- Phases 1 to 3 run on the sample files locally (DuckDB) and in `cecl_lab_dev`. Full-data runs are
  separate, scheduled jobs that respect the quota and the VM's blocked windows.
- Every Databricks run records its duration, so we can see when we are near the quota.
- Freddie data never touches the VM: it goes from Martins' PC to Databricks storage. The VM only
  downloads the small public economic files and keeps code, logs and small outputs.
- **Manual downloads (settled by the terms above):** Martins downloads Freddie's files in the browser
  on his PC and uploads them to Databricks storage; the VM never touches them. If Freddie later
  grants API access, an Airflow download task can be added then.

## Decision

*To be written by Martins, after the VM check above.*
