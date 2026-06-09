# Capture hang investigation

**Status:** Active — Phase B fleet rollout (v1.1.28)  
**Started:** 2026-06-07  
**Current version:** `1.1.28` — in-process recovery + lores capture @ 1.0s  
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

### 2026-06-07 — Lores capture + encode-only soak experiment

**Lores capture (Option B) — deployed fleet-wide, did not fix hangs**

- Main stream encode-only for H264; `capture_array("lores")` + YUV→RGB for picture buffer (`USE_LORES_CAPTURE = True`).
- Deployed after OS upgrade (libcamera 0.7.1, Picamera2 0.3.36) on all 5 nodes.
- **Result:** MBR2 still hung ~15 min after deploy (`Camera frontend has timed out` during event); Outside hit `NoFrames` ~2h on lores. Lores isolates main from `capture_array` but **still blocks in libcamera** on the lores path.

**Encode-only soak — hypothesis test**

- **Question:** Is the hang in continuous H264 encode, or in `capture_array()` / motion / events?
- **Method:** `ENCODE_ONLY_SOAK = True` — main H264 circular buffer only; no `capture_array()`, no motion, no event processor.
- **Monitoring:** `NoEncode` / encode metrics in watchdog, `pi_health_export.sh`, Grafana fleet table (Encode soak, Enc chunks, Enc stale, NoEncode).

| Phase | Scope | Commit | Deploy |
|-------|--------|--------|--------|
| Soak v1 | Study (.21) only | `1db8013` | Manual + Study `config_local.py` |
| Soak v2 | All 5 nodes | `d0ce170` v1.1.27 | `gitsync.sh` → Ansible `-a` (.54/.55/.53/.57); Study `.21` git pull (dev node, not in `cameras.ini`) |

**Fleet encode soak checkpoint (~2 hours, 2026-06-07 ~10:42 PM local)**

All 5 nodes simultaneously healthy — first time on the same day lores had already failed on MBR2/Outside:

| Node | Agent OK | Encode soak | Enc chunks | Enc stale | NoEncode | NoFrames |
|------|----------|-------------|------------|-----------|----------|----------|
| Study (.21) | YES | 1 | 2500 | 0–6s | 0 | 0 |
| Back (.54) | YES | 1 | 2500 | 0–6s | 0 | 0 |
| MBR (.55) | YES | 1 | 2500 | 0–6s | 0 | 0 |
| MBR2 (.53) | YES | 1 | 2500 | 0–6s | 0 | 0 |
| Outside (.57) | YES | 1 | 2500 | 0–6s | 0 | 0 |

Logs: `ENCODE_ACTIVE`, evictions climbing (~60k–74k), `Watchdog: ✓ All healthy | Encode:2500chunks/0-5s_stale`. No `CALLING_CAPTURE_ARRAY`, no libcamera frontend timeout.

**Early conclusion (2h, not final):** Continuous H264 encode appears stable across the fleet; hangs on the same hardware/OS within 1–2h on lores strongly implicate the **picture capture path**, not encode-only.

---

## Where we came from → where we are → success criteria

### Where we came from

1. **Symptom:** `camera_agent_healthy=0` (NoFrames) while nodes stay on WiFi; capture thread stuck in `capture_array()` with no Python exception.
2. **Fixes already shipped:** local-first health, reboot watchdog `/sbin/reboot` (`e6b2a70`), buffered event stills (`5001b50`), fleet WiFi hardening, OS/libcamera upgrade.
3. **Lores experiment:** Reduced main-stream contention — **hangs persisted** on MBR2/Outside.
4. **Encode-only soak:** Remove capture/motion/events entirely to test whether H264 encode alone is the failure mode.

### Where we are now (2026-06-07 evening)

- **Code:** `ENCODE_ONLY_SOAK = True` fleet-wide in `config.py` v**1.1.27** (`d0ce170`).
- **Runtime:** All 5 nodes in encode-only mode; motion detection and event pipeline **disabled** (no security events, no NFS transfers from new motion).
- **Health:** ~2h stable on all nodes; Grafana encode columns populated on every camera.
- **Workflow:** Develop on Study `.21` → `./gitsync.sh` → Ansible `camera_upgrade.sh -a` on `.54/.55/.53/.57`.
- **Not done yet:** Soak duration (target 48–72h); restore full pipeline; prove stability under motion + capture + encode.

### Success criteria — encode-only soak (experiment complete)

Call the soak **successful** when **all** of the following hold for **48–72 hours** on **all 5 nodes**:

| Criterion | Target |
|-----------|--------|
| `camera_agent_healthy` | 1 continuously |
| `encode_only_soak` | 1 |
| Enc chunks | ~2500 (buffer full), evictions increasing |
| Enc stale (s) | < 60s always; never `NoEncode` > 2m |
| NoFrames | 0 (N/A in soak — no capture) |
| Agent restarts | None required for hang recovery |
| Pi reboots | None triggered by hang watchdog |

If any node hits `NoEncode` or needs restart during the window, note time-to-failure and node identity — still useful data, but soak is not “clean success.”

### Success criteria — return to full functionality (production ready)

Call **stability restored** when the **full pipeline** runs on all 5 nodes for **7+ days** with:

| Criterion | Target |
|-----------|--------|
| Motion detection | Active; events created on central server |
| Event stills + H264 clips | A/B from buffer + buffer dump (keep `5001b50`) |
| `camera_agent_healthy` | 1; NoFrames = 0 |
| Hang recovery | In-process agent/camera restart < 5 min (not 60 min Pi reboot) |
| No manual intervention | No ad-hoc SSH restarts for capture hangs |

---

## Suggested path forward (as of 2026-06-07)

Assuming encode-only soak completes successfully (encode path is not the primary failure):

### Phase A — Finish soak (now → 48–72h)

- [ ] Leave `ENCODE_ONLY_SOAK = True` on all nodes; monitor Grafana (`from=now-24h&to=now`).
- [ ] Record first failure (if any): node, uptime, Enc stale, central log excerpt.
- [ ] Document soak end time and outcome in session log below.

### Phase B — Re-introduce capture without losing encode stability

Priority order (incremental, one change at a time; Study `.21` first, then fleet):

1. **In-process hang recovery (do before re-enabling capture on fleet)**  
   - Watchdog: if `CALLING_CAPTURE_ARRAY` > 5 min → `systemctl restart security-camera-agent` (or circular_buffer stop/start).  
   - Keep Pi reboot at 60 min as last resort.  
   - *Why first:* Full pipeline will hang again without this; soak proved encode is fine but capture will still block in libcamera.

2. **Re-enable picture capture on one node (Study)**  
   - Set `ENCODE_ONLY_SOAK = False` in Study `config_local.py` only; keep `USE_LORES_CAPTURE = True`.  
   - Watch 48h: NoFrames vs encode metrics (if we add buffer metrics on non-soak path).  
   - If hang returns → capture path confirmed; tune recovery + consider capture changes below.

3. **Capture load reduction (if hangs return on lores)**  
   - Slower picture interval (e.g. 1.0s vs 0.5s).  
   - Keep lores for `capture_array` (already separates from main encode).  
   - Optional: lighter frame fingerprint than SHA256 every frame.  
   - Do **not** re-add EventProcessor `capture_file()` — keep buffered stills.

4. **Re-enable motion + events (after capture stable 48h+ on Study)**  
   - Motion detector + EventProcessor on Study; verify events and NFS transfers.  
   - Ansible deploy to fleet; same monitoring window.

5. **Fleet rollout**  
   - `ENCODE_ONLY_SOAK = False` in `config.py` default once Study + one production node prove stable.  
   - Bump `SYSTEM_VERSION`; `gitsync.sh` + Ansible.

### Phase C — Monitoring / ops (ongoing)

- Grafana: encode columns remain useful even in full mode if we export H264 buffer health on all nodes (not only soak).
- Reboot watchdog pause warnings are expected until pause timers expire — unrelated to soak health.
- Study stays out of `cameras.ini` by design; document manual `git pull` + restart after Ansible fleet deploy.

### What we are **not** pursuing (unless soak fails)

- If encode-only **also** hangs → investigate encoder/libcamera/OS (bitrate, keyframe interval, thermal, PSU). Soak would falsify the “capture-only” hypothesis.

### 2026-06-09 — Phase B fleet rollout (v1.1.28)

**Soak outcome:** ~40h stable on 4/5 nodes (encode stale max 4s); MBR2 offline (WiFi). Soak successful — encode path not primary failure mode.

**Shipped fleet-wide:**

1. **In-process recovery** — `system_watchdog`: NoFrames/hung `capture_array()` ≥ 5 min → `os._exit(1)` for systemd `Restart=always`; rate limit 3/hour, 10 min cooldown; skips during livestream. Pi reboot watchdog remains at 60 min.
2. **Full pipeline restored** — `ENCODE_ONLY_SOAK = False` (motion + events re-enabled).
3. **Slower capture** — `PICTURE_CAPTURE_INTERVAL = 1.0s` (was 0.5s); lores capture unchanged.

**Monitor:** `camera_agent_healthy`, NoFrames, agent restart count in logs, motion events on central server.

---

## References

- Repo: `circular_buffer.py`, `system_watchdog.py`, `camera_reboot_watchdog.py`, `event_processor.py`
- General Work: `watchdog-local-health-fix.md`, `camera-nodes-diagnosis.md`
- Grafana dashboard: `/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json`
- Ansible deploy: `/home/ubuntu/ansible/pi-fleet/upgrade_security_cameras.yml`
