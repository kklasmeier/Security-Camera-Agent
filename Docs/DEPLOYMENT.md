# Fleet deployment workflow

How code changes reach the 5-camera fleet. Follow this for every release.

## Fleet layout

| Node | Hostname / alias | IP | Role |
|------|------------------|-----|------|
| **Study** | PiCam-Study | 192.168.1.21 | **Development node** — code changes land here first |
| Back | PiCam-Back | 192.168.1.54 | Production (Ansible) |
| MBR | PiCam-MBR | 192.168.1.55 | Production (Ansible) |
| MBR2 | PiCam-MBR2 | 192.168.1.53 | Production (Ansible) |
| Outside | PiCam-Outside | 192.168.1.57 | Production (Ansible) |

Production path on every Pi: `/home/pi/Security-Camera-Agent`

Monitoring / Ansible host: **192.168.1.16** (`ubuntu@192.168.1.16`)

---

## Development node (Study / `.21`)

The **working copy in this repository is mounted on the dev machine via NFS** and is the same directory as `/home/pi/Security-Camera-Agent` on Study.

**Implication:** Edits made in the working folder are **immediately on disk** at Study. You do **not** `git pull` or run Ansible on `.21` for routine code releases.

Study is **intentionally excluded** from Ansible `cameras.ini` — it is the dev target, not an Ansible-managed deploy.

Per-camera settings still live in `config_local.py` on each node (git-ignored). Study’s file is edited on the node (or via the same NFS mount).

---

## Release checklist

### 1. Develop and test on Study

- Make code changes in the working folder (NFS → Study).
- Test on Study (`.21`) as needed.
- If the running agent must load new Python code, restart on Study only:

```bash
ssh pi@192.168.1.21 'sudo systemctl restart security-camera-agent camera-reboot-watchdog'
```

No `git pull` on Study — the mount already has your changes.

### 2. Bump version

Update `SYSTEM_VERSION` in `config.py` for every release (semver, e.g. `1.1.29`).

### 3. Push to GitHub

From the repo root:

```bash
./gitsync.sh
```

Interactive script: stage changes, commit, push to `origin/main`. Use a clear commit message.

### 4. Deploy production nodes via Ansible

From the Ansible host (`.16`):

```bash
ssh ubuntu@192.168.1.16
cd ~/ansible/pi-fleet
./camera_upgrade.sh -a
```

This upgrades **Back, MBR, MBR2, Outside** (`.54`, `.55`, `.53`, `.57`): `git pull` in `/home/pi/Security-Camera-Agent`, syncs systemd units, restarts `security-camera-agent` and `camera-reboot-watchdog`.

**Does not touch Study (`.21`).**

Playbook: `~/ansible/pi-fleet/upgrade_security_cameras.yml`  
Inventory: `~/ansible/pi-fleet/cameras.ini`

### 5. Verify

- Grafana **Camera Fleet Health** on `.16` — use `from=now-24h&to=now`
- Central server logs — watchdog lines, version if logged
- Confirm `SYSTEM_VERSION` on production nodes if needed:

```bash
ssh pi@192.168.1.54 'python3 -c "import sys; sys.path.insert(0,\"/home/pi/Security-Camera-Agent\"); from config import config; print(config.SYSTEM_VERSION)"'
```

---

## What not to do

| Don't | Why |
|-------|-----|
| Manual `git pull` on Study for releases | Working folder **is** Study’s tree via NFS |
| Add Study to Ansible `cameras.ini` | Study is the dev node; NFS + local test is the workflow |
| Skip `SYSTEM_VERSION` bump | Makes it hard to know what is running on each node |
| Deploy only some production nodes without reason | Use `./camera_upgrade.sh -a` unless intentionally testing one host |

---

## Grafana / monitoring updates

Dashboard or Prometheus changes on `.16` are **separate** from camera Ansible:

- Dashboard JSON: `/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json`
- Helper scripts in repo: `scripts/update_grafana_*.py` — copy to `.16`, run, restart Grafana

Camera metric export (`scripts/pi_health_export.sh`) is deployed with the camera agent via Ansible (cron on each node).

---

## Quick reference

```
[Dev machine / NFS mount = Study .21 code]
        │
        ├─► Test on Study (restart service if needed)
        │
        ├─► Bump config.py SYSTEM_VERSION
        │
        ├─► ./gitsync.sh  →  GitHub
        │
        └─► Ansible on .16: ./camera_upgrade.sh -a  →  .54 .55 .53 .57
```
