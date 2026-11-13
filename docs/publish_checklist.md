# Publish checklist (Phase 0, Step 11)

The order Martins set for creating and publishing the repository. Every step marked **(Martins
approves)** is public, a settings change or hard to undo: Claude Code stops and asks before it.

| # | Step | Done when |
|---|---|---|
| 1 | Local checks pass: ruff, mypy, pytest with both coverage gates, dbt build, pre-commit | output shown |
| 2 | Commit identity is `Martins Ogiekpolor <…@users.noreply.github.com>` (repo-local config); no `Co-Authored-By`, `Claude-Session` or "Generated with" lines | `git log --format='%an <%ae>%n%b'` checked |
| 3 | **(Martins approves)** Create `martinogie/cecl-lifetime-loss-lab` as **private** | repo exists, private |
| 4 | **(Martins approves)** First commit and push to `main` | pushed |
| 5 | CI green on GitHub. While private, `docker (linux/arm64)` shows **skipped**: GitHub's Arm runners are free only for public repos | all other jobs pass |
| 6 | Pre-publish scan of the **full history**: emails other than the noreply address, workspace URLs, hostnames, IPs, OCIDs, SSH aliases, personal logins, data files, files over 1 MB | scan output shown, nothing found |
| 7 | **(Martins approves)** Switch the repo to **public** | repo public |
| 8 | **(Martins approves)** Turn on GitHub secret scanning and push protection | both on in Settings → Code security |
| 9 | Re-run CI on `main`: `docker (linux/arm64)` now runs and must pass (the image builds for the VM's Arm CPU) | arm64 job green |
| 10 | **(Martins approves)** Branch protection on `main`: pull requests only, CI must pass, no required approvals (a solo owner can't approve their own PR). Required checks: `lint`, `types`, `unit`, `dag-import`, `docker (linux/amd64)`, `dbt`, `secrets`, and **`docker (linux/arm64)`, added only now, after the switch to public** (while private it is skipped and couldn't pass) | rule visible in Settings → Branches |

After step 10, every change goes through a feature branch and a pull request that Martins merges.
