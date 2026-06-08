# Capture hang investigation

**Status:** Draft — ongoing discussion  
**Started:** 2026-06-07  
**Fleet:** 5× Pi Zero 2 W WiFi camera nodes (PiCam-Study, Back, MBR, MBR2, Outside)  
**Monitoring:** Grafana `camera-fleet-health` on 192.168.1.16  

---

## Summary

Fleet nodes intermittently show **unhealthy** in Grafana (`camera_agent_healthy=0`) while remaining network-reachable. Root symptom: the capture thread blocks indefinitely inside **Picamera2 `capture_array()`** — no new frames, no Python exception.

Recovery previously failed because the reboot watchdog detected hangs correctly but could not reboot (`systemctl reboot` + polkit). **Fixed 2026-06-07** in commit `e6b2a70` (`/sbin/reboot` with `CAP_SYS_BOOT`). Fleet recovered after deploy + agent restart.

**Concurrent picam2 access during events fixed 2026-06-07** in commit `5001b50`: Picture A/B now saved from the two-frame picture buffer (`save_buffered_still()`) instead of `capture_file()` / `capture_array()` during motion events. EventProcessor no longer calls into Picamera2. Deployed to all production nodes via Ansible.

This document tracks **possible software bugs and design gaps** that may trigger or worsen hangs. Underlying Picamera2/libcamera behavior on Pi Zero 2 W may still cause hangs even with perfect application code.

---

## Observed failure pattern

1. Normal capture for hours (`[CAPTURE DEBUG] Frame #N` every ~0.5s)
2. `⚠️ SLOW CAPTURE: capture_array() took 2–5s` warnings
3. Thread state stuck at `CALLING_CAPTURE_ARRAY` (minutes to hours)
4. `Watchdog: ⚠ ISSUES DETECTED: NoFrames:…`
5. Motion detector keeps running on **stale frames** (all motion scores 0)
6. Threads report 5/5 alive — misleading partial health

**Example (PiCam-Back, 2026-06-06):** last frame ~23:26, slow captures at 23:26:48 / 23:26:57, hung by 23:33.

---

## Thread & process architecture

Everything below runs inside **one process**: `security-camera-agent.service` → `python3 sec_cam_main.py`.  
**Separate process:** `camera-reboot-watchdog.service` (monitors health, may reboot Pi — does not touch `picam2`).

### Inside the agent process

| # | Name (systemd log) | Component | Touches `picam2`? | What it does |
|---|-------------------|-----------|-------------------|--------------|
| — | Main | `sec_cam_main.py` | Init only | Startup/shutdown orchestration; sleeps until SIGTERM |
| 1 | `PictureCapture` | `circular_buffer._capture_pictures` | **YES — continuous** | Loop: `capture_array()` every ~0.5s → update two-frame buffer → hash frame. Also owns H264 encoder started at init. |
| 2 | `MotionDetector` | `motion_detector._detection_loop` | No (reads buffer) | Copies downscaled prev/curr frames from buffer; pixel-diff motion; creates event on central API; signals Thread 3 |
| 3 | `EventProcessor` | `event_processor._process_event` | **No** (reads buffer) | On signal: `save_event_still()` from picture buffer (A) → thumbnail → wait 4s → buffer still (B) → dump H264 from circular buffer |
| 4 | `CameraControlAPI` | Flask in `camera_control_api.py` | Indirect | HTTP control (port 5000): start/stop MJPEG stream, config. Flask `threaded=True` — request handlers run in worker threads |
| 4b | `MJPEGServerHTTP` | `mjpeg_server` (on demand) | No (reads buffer) | Serves `/stream.mjpg` from latest frame in buffer when streaming enabled |
| 5 | `TransferManager` | `transfer_manager._transfer_loop` | No | Scans `pending/` for `.READY` files → copies to NFS → API status updates |
| 6 | `SystemWatchdog` | `system_watchdog._watchdog_loop` | No (reads health) | Every 60s: check threads, NoFrames, disk, memory; writes `local_health.json` |
| 7 | `LogWriter` | `logger._batch_writer` | No | Batches logs to disk + central API |

**Watchdog “5/5 threads”** = CircularBuffer capture + MotionDetector + EventProcessor + TransferManager + LogWriter (not API/MJPEG unless streaming).

### Data flow (happy path)

```
picam2 ──capture_array()──► two-frame buffer ──► MotionDetector (diff)
                              │                      │
                              │                      └── motion? ──► API create event
                              │                                    └── signal EventProcessor
                              │
                              ├── EventProcessor: save_event_still() (A/B from buffer)
                              └── circular_output (H264) for video clip (no capture call)
```

### Picamera2 access (post-`5001b50`)

Only **Thread 1** (`PictureCapture`) calls into Picamera2 for frames:

- Thread 1: `capture_array()` in a tight loop + H264 encoder (started at init)

All other consumers read from the buffer or H264 circular output:

- Thread 2 (MotionDetector): downscaled copies via `get_frames_for_detection()`
- Thread 3 (EventProcessor): `save_buffered_still()` → copies `current_frame` under `frame_lock`, Pillow JPEG
- MJPEG: `get_latest_frame_for_livestream()`

**`frame_lock`** protects the two-frame numpy buffer. No `picam2_lock` is required as long as EventProcessor stays off the camera object.

Legacy path: `capture_color_still()` remains in code; set `USE_BUFFERED_EVENT_STILLS = False` in config to revert to ISP JPEG.

### Discussion notes (2026-06-07)

- Motion detector keeps comparing **stale** prev/curr when Thread 1 hangs — checks continue, scores stay 0.
- A/B from buffer trades ISP JPEG for software JPEG (same 1280×720 pixels); config flag allows rollback.
- MJPEG streaming only changes capture **interval** (`start_streaming()` / `stop_streaming()`), not separate picam2 calls from the HTTP thread.

---

## Related work (context)

| Change | Date | Notes |
|--------|------|--------|
| Local-first health (`local_health.json`) | 2026-06-06 | Reboot watchdog no longer depends on central API (.26). See `watchdog-local-health-fix.md` in General Work. |
| Watchdog runs as `pi` + `CAP_SYS_BOOT` | 2026-06-06 | Fixes root-owned log files at midnight rotation. |
| `systemctl reboot` regression | 2026-06-06 | Polkit blocked reboot for `pi`; only Study (still `User=root`) could reboot. |
| `/sbin/reboot` fix | 2026-06-07 | Commit `e6b2a70`, deployed via Ansible to production cameras. |
| Buffered event stills (A/B) | 2026-06-07 | Commit `5001b50`. EventProcessor uses `save_buffered_still()`; no concurrent `capture_file()` during events. Deployed to .54, .55, .53, .57 via Ansible; Study (.21) on same commit. |

---

## Possible software bugs / design gaps

### 1. Concurrent Picamera2 access — ~~no lock~~ **addressed** (`5001b50`)

**Original hypothesis:** Unsynchronized multi-threaded use of the same `picam2` object provokes libcamera deadlocks/hangs — especially `capture_file()` while H264 encoder is active (picamera2 #1305).

**What was wrong (before fix):**

| Thread | Calls |
|--------|--------|
| `_capture_pictures` (background) | `picam2.capture_array()` every ~0.5s |
| `event_processor` (on motion) | `capture_color_still()` → `capture_file()` or `capture_array("main")` |

**Fix applied (2026-06-07, commit `5001b50`):**

- Added `save_buffered_still()` and `save_event_still()` in `circular_buffer.py`
- `event_processor.py` saves Picture A/B from `current_frame` in the two-frame buffer (Pillow JPEG)
- Config: `USE_BUFFERED_EVENT_STILLS = True` (default); `False` reverts to `capture_color_still()`
- **Result:** single-threaded picam2 access — only `_capture_pictures` touches the camera

**Not implemented (deemed unnecessary after buffer approach):**

- ~~Single `picam2_lock` around all capture/still operations~~
- ~~Pause background capture loop during event stills~~

**Remaining risk:** Idle hangs with no motion events are unaffected — continuous `capture_array()` + encoder alone can still hang on Pi Zero 2 W. Monitor fleet over coming days; confirm event logs show `Saved buffered still:` not `capture_color_still`.

**Files:** `circular_buffer.py`, `event_processor.py`, `config.py`

---

### 2. No in-process recovery when `capture_array()` blocks (**high priority — top open item**)

**Hypothesis:** Hangs are invisible to `except Exception`; the thread blocks forever until external reboot.

- Code comments acknowledge `capture_array()` can hang (`circular_buffer.py` ~516).
- `system_watchdog.py` detects hung state and logs diagnostics but **does not restart** camera or agent.
- Reboot watchdog waits **60 minutes** before full Pi reboot (now working after `e6b2a70`).

**Fix directions (to discuss):**

- [ ] Watchdog triggers `circular_buffer.stop()` + `start()` after N minutes hung
- [ ] Or `systemctl restart security-camera-agent` after shorter threshold (e.g. 5 min)
- [ ] Keep full reboot as last resort after agent restart fails

**Files:** `system_watchdog.py`, possibly `sec_cam_main.py`

---

### 3. H264 encoder + full-res capture load on Pi Zero 2 W (medium priority)

**Hypothesis:** Simultaneous H264 circular buffer encoding and full RGB888 `capture_array()` stress Pi Zero–specific encoder paths.

Concurrent load:

- `H264Encoder` → circular buffer (continuous)
- `capture_array()` at 1280×720 RGB888 (~2.7 MB/frame; config `VIDEO_RESOLUTION`)
- SHA256 hash of every frame (`frame.tobytes()`)
- ~~Occasional `capture_file()` for event stills~~ removed in `5001b50`

Upstream references:

- picamera2 #858 — Pi Zero encoder stop/hang with threads
- picamera2 #1228 — encoder can freeze silently under certain scene/bitrate conditions

**Fix directions (to discuss):**

- [ ] Lower still-capture resolution or use lores stream for motion
- [ ] Review `VIDEO_BITRATE` / encoder settings per node
- [ ] Reduce hash frequency or use lighter fingerprint (not every frame)

**Files:** `circular_buffer.py`, `config.py`

---

### 4. Misleading health signals when capture is dead (medium priority)

**Hypothesis:** Dashboard and watchdog over-report liveness.

When capture hangs:

- Motion thread alive, checks increment, all scores 0
- `Threads: 5/5 alive` in health report
- NFS and API may still be OK → looks like partial failure only

**Fix directions (to discuss):**

- [ ] Treat stale frames + zero motion variance as degraded/hung sooner
- [ ] Grafana: separate “threads alive” from “capture active”
- [ ] `camera_agent_healthy` already keys off NoFrames — confirm threshold/alerts

**Files:** `system_watchdog.py`, `motion_detector.py`, Grafana dashboard

---

### 5. Reboot / recovery architecture gaps (partially addressed)

| Issue | Status |
|-------|--------|
| Central API failure treated as healthy | Fixed — local-first health |
| `systemctl reboot` auth failure on `pi` | Fixed — `/sbin/reboot` |
| No in-process camera restart | Open |
| 60 min threshold before any recovery | Open — may be too long |
| PiCam-Study watchdog still `User=root` | Open — fleet inconsistency |
| Ansible deploy: Outside watchdog restart sudo timeout | Observed 2026-06-07 — manual fix |

---

## Probably not application bugs

| Factor | Role |
|--------|------|
| WiFi drops / no route to .26 | Broke **old** reboot detection; not cause of capture hang |
| NFS unmounted | Separate failure mode; was not the Jun 6 hang pattern |
| Node exporter down | Infrastructure visibility; unrelated to capture |
| Hardware (cable, PSU, heat) | Possible contributor; temp was ~54–75°C during investigation — not clearly throttling |

---

## Open questions (discussion)

1. ~~**Lock vs pause:** Is a global `picam2_lock` enough, or should event stills pause the capture loop entirely?~~ **Resolved:** buffer stills eliminate EventProcessor picam2 calls; lock not needed for now.
2. ~~**Still quality:** Can A/B images come from the two-frame buffer instead of `capture_file()`?~~ **Resolved:** yes, deployed `5001b50`; watch image quality on next events.
3. **Recovery tiering:** Agent restart at 5 min, Pi reboot at 60 min — acceptable?
4. **Repro:** Can we trigger hang on Study with deliberate concurrent capture under load? (Less relevant post-`5001b50`; focus on idle/encoder-only hangs.)
5. **Study node:** Align watchdog unit to `User=pi` like the rest of the fleet?
6. **Motion on stale frames:** Should motion detector pause when `last_frame_time` is stale?
7. **Fleet validation:** Does hang rate drop after `5001b50` on motion-heavy nodes (Outside, Back)?

---

## Session log

### 2026-06-07 — Initial investigation + reboot fix

- Grafana showed 4/5 unhealthy; metric was `camera_agent_healthy=0` (NoFrames), not network down.
- Three nodes hung in `capture_array()` 40–70+ minutes; Outside healthy; Study had rebooted successfully.
- Reboot watchdog logged `REBOOT IS NEEDED` but failed: `Interactive authentication required` on `systemctl reboot`.
- Study still had `User=root` in watchdog unit — only node that could reboot.
- Fix: `e6b2a70` — `/sbin/reboot`; deployed to .54, .55, .53, .57 via Ansible.
- Post-deploy: all 5 nodes `camera_agent_healthy=1`.
- Identified concurrent `picam2` access and lack of in-process recovery as top software follow-ups.

### 2026-06-07 — Buffered event stills (concurrent picam2 fix)

- Discussed A/B resolution (same 1280×720 as video; ISP JPEG was for color processing, not extra pixels).
- Implemented `save_buffered_still()` / `save_event_still()`; EventProcessor no longer calls picam2 on motion.
- Commit `5001b50` pushed via `gitsync.sh`; deployed `./camera_upgrade.sh -a` from `.16`.
- All 4 production nodes + Study on `5001b50`; agent and reboot watchdog active on all.
- Top remaining software gap: in-process recovery when `capture_array()` blocks with no motion events.

---

## References

- Repo: `circular_buffer.py`, `system_watchdog.py`, `camera_reboot_watchdog.py`, `event_processor.py`
- General Work: `watchdog-local-health-fix.md`, `camera-nodes-diagnosis.md`
- Grafana dashboard: `/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json`
- Ansible deploy: `/home/ubuntu/ansible/pi-fleet/upgrade_security_cameras.yml`
