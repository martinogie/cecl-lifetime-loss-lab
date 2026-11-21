# VM cutover: CECL's prod stack on the shared Oracle VM (Phase 8, step 1)

- **Prepared:** 21 November 2026, for Martins to run over SSH (deploys are manual by design, rule 6).
- **Never:** on a Monday before 13:00 Chicago (Bank Health's run) or between **02:30 and 04:00** (the VM's
  automatic restart window). Check the clock before each long step (`docker compose build`, about 15-25 min).
- **Bank Health is never touched:** nothing below runs as Bank Health's user, uses its Docker daemon, or reads
  `/srv/bank-health`. CECL gets its own user, its own rootless Docker daemon and its own folders.
- `<vm>` = your SSH alias for the VM. No host names, IPs or account ids go in this repo.

Each phase ends with a **gate**: if a check fails, stop and send me the output (without any secret values).

## Phase A: read-only checks (your admin user, about 5 minutes)

```sh
ssh <vm>
date                                   # Chicago time: not Monday before 13:00, not 02:30-04:00
df -h /                                # free disk
free -h                                # memory
nproc; uptime                          # CPUs and load
sudo docker stats --no-stream          # Bank Health's containers right now (its root Docker daemon)
id cecl; ls -ld /srv/cecl /srv/cecl/* 2>/dev/null
stat -fc %T /sys/fs/cgroup             # must print cgroup2fs
cat /etc/systemd/system/user@.service.d/*.conf 2>/dev/null   # cgroup delegation, if already set
command -v dockerd-rootless-setuptool.sh; docker --version
```

**Gate A:** at least **20 GB free disk**, at least **7 GB "available"** memory with Bank Health idle (CECL caps
total about 5.8 GB), `cgroup2fs`. Note whether the `cecl` user, `/srv/cecl` and rootless Docker already exist:
Phase B skips what exists.

## Phase B: one-time set-up (your admin user with sudo; skip any line already done)

```sh
# 1. The role user and its folders (secrets readable by cecl only)
sudo useradd -m -s /bin/bash cecl                       # skip if `id cecl` worked
sudo mkdir -p /srv/cecl/app /srv/cecl/secrets /srv/cecl/var/data /srv/cecl/var/logs
sudo chown -R cecl:cecl /srv/cecl
sudo chmod 750 /srv/cecl && sudo chmod 700 /srv/cecl/secrets
sudo chmod 600 /srv/cecl/secrets/.env 2>/dev/null       # the secrets file you created

# 2. Let cecl's Docker daemon run without a login session, with memory and CPU caps
sudo loginctl enable-linger cecl
sudo mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf
sudo systemctl daemon-reload

# 3. Rootless Docker prerequisites (Docker CE is already installed for Bank Health)
sudo apt-get update && sudo apt-get install -y uidmap dbus-user-session docker-ce-rootless-extras
grep cecl /etc/subuid /etc/subgid                       # both must list cecl (useradd adds them)
```

Then, **as cecl** (a fresh login so the delegation applies):

```sh
sudo machinectl shell cecl@                             # or: sudo -iu cecl
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker
echo 'export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock' >> ~/.bashrc && source ~/.bashrc
docker info --format '{{.SecurityOptions}} {{.CgroupVersion}}'   # must show rootless and cgroup 2
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers  # memory cpu ...
```

**Gate B:** `docker info` shows **rootless** and cgroup **2**; the controllers line includes **memory** and
**cpu**. `sudo docker ps` (Bank Health's daemon) still lists its containers, untouched.

## Phase C: the CECL Airflow stack (as cecl)

```sh
cd /srv/cecl/app
git clone https://github.com/martinogie/cecl-lifetime-loss-lab.git .     # public repo, no credentials
git log --oneline -1                                                       # note the commit
cat > airflow/.env <<EOF                                                   # machine paths only, no secrets
CECL_ENV_FILE=/srv/cecl/secrets/.env
CECL_DATA_DIR=/srv/cecl/var/data
AIRFLOW_LOGS_DIR=/srv/cecl/var/logs
AIRFLOW_UID=$(id -u)
EOF
```

**The secrets file** `/srv/cecl/secrets/.env` must hold these names (values from you; never in git; prod values,
not dev). Check the names only:

```sh
cut -d= -f1 /srv/cecl/secrets/.env | sort
```

| Name | What |
|---|---|
| `AIRFLOW__CORE__FERNET_KEY` | new key: `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `AIRFLOW__API_AUTH__JWT_SECRET` | new random string: `openssl rand -hex 32` |
| `_AIRFLOW_WWW_USER_PASSWORD` | the Airflow admin password (the UI is reached only through the SSH tunnel) |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH` | prod (Phase D creates the prod catalog) |
| `CECL_DATABRICKS_LAYOUT` | `catalogs` |
| `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL` | the **[CECL] prod** channels (not dev, not Bank Health's) |
| `FRED_API_KEY` | economic data |
| `FFIEC_PWS_USERNAME`, `FFIEC_PWS_TOKEN` | call reports (peer ranges) |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` | site data publish (bucket `cecl-data`) — done |

Build and start (check the clock first: 15-25 minutes, never into 02:30):

```sh
cd /srv/cecl/app/airflow
docker compose build
docker compose up airflow-init          # first time: database and admin user; ends with exit code 0
docker compose up -d
docker compose ps                       # all healthy after 2-3 minutes
docker stats --no-stream                # CECL's memory and CPU stay within the caps
```

**Smoke check** from the PC (a tunnel; nothing is opened to the internet):

```sh
ssh -N -L 8091:localhost:8081 <vm>      # leave running; open http://localhost:8091 and log in as admin
```

- Every DAG is listed and **paused**: `cecl_health_check`, `cecl_macro_data`, `cecl_ffiec_call_reports`,
  `cecl_publish_site`.
- Trigger `cecl_health_check` by hand: it ends with one **[CECL]** alert in Slack and Discord.

**Gate C:** stack healthy, caps respected, the health-check alert received, `sudo docker ps` shows Bank
Health's containers unchanged.

## Phase D onwards (me, with your approval; not on the VM)

| Step | What |
|---|---|
| D | Databricks prod: catalog `cecl_lab`, schemas, the prod MLflow experiment, `databricks bundle deploy -t prod` (ADR-004) |
| E | Prod data: bronze -> gold in `cecl_lab`, tie-outs; models registered in prod (v2 stays challenger) |
| F | Unpause `cecl_macro_data`, `cecl_ffiec_call_reports` and `cecl_health_check` (their schedules avoid the blocked windows) |
| G | Dry-run close: rebuild the 30 June 2026 reserve in prod, match dev to the cent ($297.5m), then trigger `cecl_publish_site` (the first prod publish; the site switches to the bucket within a minute) |
| H | First live close on Freddie's next release (expected late January 2027), with the committee pack |

## Rollback

The CECL stack can be stopped at any time without affecting Bank Health:
`docker compose down` (as cecl, in `/srv/cecl/app/airflow`). Removing it entirely: `docker compose down -v`,
then `dockerd-rootless-setuptool.sh uninstall` and, with sudo, `loginctl disable-linger cecl`.
