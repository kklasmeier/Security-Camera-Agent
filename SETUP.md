# Security Camera Agent — Raspberry Pi Setup Guide

## 🧩 Overview

This document provides **step-by-step installation and configuration instructions** for setting up a Raspberry Pi as a Security Camera Agent node in the distributed camera system.

These instructions apply to:

* **Raspberry Pi Zero 2 W**, Raspberry Pi 3, 4, and 5
* **Raspberry Pi OS Lite (64-bit)** — *Bookworm* or *Trixie*
* **Security-Camera-Agent** repository:
  `https://github.com/kklasmeier/Security-Camera-Agent`

---

## 1. 🔧 Base OS Setup

1. Flash **Raspberry Pi OS Lite (64-bit)** using Raspberry Pi Imager.
2. Enable **SSH** and set hostname (e.g., `piCameraFront2`) before writing the image.
3. Boot the Pi and log in:

   ```bash
   ssh pi@<ip_address>
   ```

4. Update the system:

   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo reboot
   ```

---

## 2. 📷 Camera and Firmware

1. Verify camera detection:

   ```bash
   rpicam-hello
   ```

   You should see the camera preview in a local window or logs like:

   ```
   Registered camera imx708
   ```

2. If needed, update firmware:

   ```bash
   sudo apt install --reinstall -y raspberrypi-kernel raspberrypi-bootloader
   ```

---

## 3. 📂 Clone the Project

```bash
cd /home/pi
git clone https://github.com/kklasmeier/Security-Camera-Agent.git
cd Security-Camera-Agent
```

---

## 4. 🧠 Install Dependencies (system-wide, not venv)

> ⚠️ Do *not* use a Python virtual environment.
> The system Python integrates directly with Raspberry Pi's libcamera stack and uses less memory.

```bash
sudo apt install -y \
  python3-flask \
  python3-requests \
  python3-opencv \
  python3-pil \
  python3-numpy \
  python3-psutil \
  python3-libcamera \
  python3-picamera2
```

Verify:

```bash
python3 - <<'PY'
import cv2, flask, requests, numpy, psutil
from picamera2 import Picamera2
import libcamera
print("✅ All core dependencies loaded successfully.")
PY
```

---

## 5. 🎯 Configure Camera Identity (CRITICAL STEP)

> **🚨 CRITICAL: The system will NOT start without this step!**

Each camera MUST have a unique identity to prevent conflicts in the multi-camera system. This is configured in a local override file that is NOT committed to git.

### Create `config_local.py`:

```bash
cd /home/pi/Security-Camera-Agent
cat > config_local.py << 'EOF'
"""
Security Camera Agent - Local Configuration Override
DO NOT commit this file to git - it contains camera-specific settings
"""

# REQUIRED: Camera Identity (customize for each camera)
CAMERA_ID = "camera_2"              # Unique: camera_1, camera_2, camera_3, camera_4
CAMERA_NAME = "Back Yard"           # Descriptive name
CAMERA_LOCATION = "Rear Entrance"   # Physical location

# OPTIONAL: Override central server if different from default (192.168.1.26:8000)
# CENTRAL_SERVER_HOST = "192.168.1.100"
# CENTRAL_SERVER_PORT = 8000
EOF
```

**Customize the values:**

| Setting           | Example Value       | Description                                    |
| ----------------- | ------------------- | ---------------------------------------------- |
| `CAMERA_ID`       | `"camera_2"`        | Unique identifier (camera_1, camera_2, etc.)   |
| `CAMERA_NAME`     | `"Back Yard"`       | Human-readable name for this camera            |
| `CAMERA_LOCATION` | `"Rear Entrance"`   | Physical location where camera is mounted      |

**Why is this required?**
- Prevents accidental camera ID collisions
- Each camera must have a unique identity in the multi-camera system
- The system will refuse to start without this file
- This file is git-ignored so it won't be overwritten during updates

**Example configurations for different cameras:**

```python
# Front camera
CAMERA_ID = "camera_1"
CAMERA_NAME = "Front Walkway"
CAMERA_LOCATION = "Front Entrance"

# Back camera
CAMERA_ID = "camera_2"
CAMERA_NAME = "Back Yard"
CAMERA_LOCATION = "Rear Entrance"

# Side camera
CAMERA_ID = "camera_3"
CAMERA_NAME = "Driveway"
CAMERA_LOCATION = "Side Gate"

# Garage camera
CAMERA_ID = "camera_4"
CAMERA_NAME = "Garage Interior"
CAMERA_LOCATION = "Garage"
```

You can also refer to `config_local.py.example` in the repository for a template.

---

## 6. 🖧 Mount Central Storage (NFS)

By default, Raspberry Pi OS Lite does not include the NFS client utilities required to mount NFS shares.
You need one small package to handle that.

### Install NFS Client

```bash
sudo apt install -y nfs-common
```

This package provides:
- `mount.nfs` → the actual helper used by the mount command
- `rpc.statd` and other RPC daemons used for NFSv3 and NFSv4
- automatic support for `_netdev`, `soft`, `timeo`, and other NFS options in `/etc/fstab`

### Verify Installation

```bash
dpkg -l | grep nfs-common
```

You should see something like:

```
ii  nfs-common    1:2.6.4-2+deb13u1    arm64    NFS support files common to client and server
```

### Create Mount Point

```bash
sudo mkdir -p /home/pi/Security-Camera-Agent/security_footage
```

### Test Manual Mount

**Important:** Replace `camera_2` with your camera's ID from `config_local.py`

```bash
sudo mount -t nfs 192.168.1.26:/mnt/sdcard/security_camera/security_footage/camera_2 \
  /home/pi/Security-Camera-Agent/security_footage
```

Verify the mount:

```bash
mount | grep security_footage
ls -la /home/pi/Security-Camera-Agent/security_footage
```

You should see subdirectories: `pictures/`, `videos/`, `thumbs/`

### Add to `/etc/fstab` for Automatic Mounting

**Important:** Replace `camera_2` with your actual camera ID

```bash
sudo tee -a /etc/fstab > /dev/null << 'EOF'
192.168.1.26:/mnt/sdcard/security_camera/security_footage/camera_2 /home/pi/Security-Camera-Agent/security_footage nfs defaults,_netdev,nofail,soft,timeo=30,retrans=3 0 0
EOF
```

Test the fstab entry:

```bash
sudo umount /home/pi/Security-Camera-Agent/security_footage
sudo mount -a
mount | grep security_footage
```

---

## 7. 🚀 Run the Agent

```bash
cd /home/pi/Security-Camera-Agent
./run.sh
```

Expected log output:

```
===================================================
Starting Security Camera: <timestamp>
Logging to: /home/pi/Security-Camera-Agent/logs/runtime_<date>.log
===================================================
✓ Loaded camera identity from config_local.py
  Camera ID:   camera_2
  Camera Name: Back Yard
  Location:    Rear Entrance
[INFO] System initializing...
Camera detected: imx708
...
```

**If you see an error about config_local.py:**

```
❌ CRITICAL ERROR: config_local.py NOT FOUND
```

Go back to **Step 5** and create the `config_local.py` file.

Stop anytime with `Ctrl+C`.

---

## 8. ⚙️ Optional — Autostart on Boot

Create a systemd service:

```bash
sudo tee /etc/systemd/system/security-camera-agent.service > /dev/null <<'EOF'
[Unit]
Description=Security Camera Agent
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/Security-Camera-Agent
ExecStart=/usr/bin/python3 /home/pi/Security-Camera-Agent/sec_cam_main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now security-camera-agent.service
```

Check status:

```bash
sudo systemctl status security-camera-agent
```

Check logs:

```bash
journalctl -u security-camera-agent -f
```

---

## 9. 🔄 Updating Camera Software

When you make improvements to the camera software, deployment is simple:

```bash
cd /home/pi/Security-Camera-Agent
git pull
sudo systemctl restart security-camera-agent
```

**Your `config_local.py` file is git-ignored and will NOT be overwritten during updates!**

This means:
- ✅ Camera identity stays intact
- ✅ No need to reconfigure after updates
- ✅ Same process works for all cameras
- ✅ Quick, safe, and repeatable

---

## 10. 🧩 Troubleshooting Quick Reference

| Symptom                          | Cause                                  | Fix                                                  |
| -------------------------------- | -------------------------------------- | ---------------------------------------------------- |
| `config_local.py NOT FOUND`      | Missing camera identity configuration  | Create `config_local.py` (see Step 5)                |
| `config_local.py INCOMPLETE`     | Missing required fields                | Add CAMERA_ID, CAMERA_NAME, CAMERA_LOCATION          |
| `ModuleNotFoundError: picamera2` | Using venv or missing apt package      | `sudo apt install python3-picamera2`                 |
| `ModuleNotFoundError: cv2`       | OpenCV missing                         | `sudo apt install python3-opencv`                    |
| `Camera not detected`            | Bad ribbon / wrong interface           | Check cable and run `rpicam-hello`                   |
| Mount fails                      | NFS address/permissions                | Check `/etc/exports` on server                       |
| Mount fails                      | NFS client not installed               | `sudo apt install nfs-common`                        |
| Wrong footage location           | CAMERA_ID mismatch in fstab            | Update fstab with correct camera ID                  |
| Logs missing                     | No `/logs` dir                         | `mkdir -p ~/Security-Camera-Agent/logs`              |

---

## 11. 🌐 Verifying Central Connection

Once the agent starts, it should log:

```
[INFO] API Client initialized: http://192.168.1.26:8000/api/v1
[INFO] ✓ Camera registered successfully
[INFO] Motion detector active...
```

If not, check network connectivity and central API availability:

```bash
curl http://192.168.1.26:8000/api/v1/health
```

---

## 12. 🧱 Design Decisions (for future maintainers)

| Decision                   | Rationale                                                           |
| -------------------------- | ------------------------------------------------------------------- |
| **No venv**                | Simplifies deployment, avoids libcamera linking issues              |
| **System Python**          | Integrates with Pi OS camera stack                                  |
| **APT-based dependencies** | Ensures ABI compatibility and smaller footprint                     |
| **NFS mount per camera**   | Enables centralized footage without local SD wear                   |
| **Systemd service**        | Provides automatic recovery on crash/reboot                         |
| **config_local.py**        | Git-ignored per-camera config prevents ID collisions                |
| **Required identity**      | System refuses to start without unique camera identity              |

---

## ✅ Done!

Your camera node should now:
- ✅ Have a unique identity that won't conflict with other cameras
- ✅ Capture and transfer events automatically to the central server
- ✅ Survive git updates without losing its configuration
- ✅ Be ready for easy redeployment to additional cameras

---

## 📋 Quick Setup Checklist

Use this checklist when setting up a new camera:

- [ ] Flash Raspberry Pi OS Lite (64-bit)
- [ ] Enable SSH and set hostname
- [ ] Update system packages
- [ ] Verify camera with `rpicam-hello`
- [ ] Clone repository
- [ ] Install dependencies (APT packages)
- [ ] **CREATE `config_local.py` with unique camera identity**
- [ ] Install `nfs-common`
- [ ] Create NFS mount point
- [ ] Test manual NFS mount
- [ ] Add NFS to `/etc/fstab`
- [ ] Test run with `./run.sh`
- [ ] Create systemd service
- [ ] Enable and start service
- [ ] Verify logs and registration
- [ ] Test motion detection
- [ ] Document camera location and ID

---

## 📚 Additional Resources

- Main Repository: `https://github.com/kklasmeier/Security-Camera-Agent`
- Example Config: `config_local.py.example`
- Central Server Setup: See main repository documentation