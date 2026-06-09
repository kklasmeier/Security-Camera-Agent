# Capture hang investigation

**Status:** Active — Phase B deployed; **monitoring period** (v1.1.31)  
**Started:** 2026-06-07  
**Current version:** `1.1.31` — full pipeline + 5 min recovery + 48 MB buffer + lores A/B @ 640×480  
**Fleet:** 5× Pi Zero 2 W WiFi camera nodes (PiCam-Study, Back, MBR, MBR2, Outside)  
**Monitoring:** Grafana `camera-fleet-health` on 192.168.1.16 (`from=now-24h&to=now`)  
**Deploy workflow:** See `Docs/DEPLOYMENT.md` (Study = NFS dev mount; production = Ansible)

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
| 1 | `PictureCapture` | `circular_buffer._capture_pictures` | **YES — continuous** | Loop: `capture_array("lores")` every **1.0s** → YUV→RGB → two-frame buffer → lightweight hash. Main stream H264 encode only. |
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
- A/B from buffer trades ISP JPEG for software JPEG from **lores stream** (currently **640×480, 4:3** — see [A/B still quality](#ab-still-quality-deferred) below). Config flag `USE_BUFFERED_EVENT_STILLS` allows rollback to `capture_file()`.
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

### 2. No in-process recovery when `capture_array()` blocks — **addressed** (v1.1.28)

**Hypothesis:** Hangs are invisible to `except Exception`; the thread blocks forever until external reboot.

**Fix shipped (2026-06-09, v1.1.28):**

- `system_watchdog`: NoFrames or hung `CALLING_CAPTURE_ARRAY` ≥ **5 min** → log + `os._exit(1)` → systemd `Restart=always`
- Rate limit: 3 recoveries/hour, 10 min cooldown; skips recovery during livestream
- Pi reboot watchdog unchanged at **60 min** (last resort)
- Event history: `var/agent-event-history.json`; exported to Prometheus/Grafana (v1.1.29)

**Validated:** PiCam-Back hung ~5 min post–Phase B deploy (2026-06-09 ~1:26–1:31 PM); agent self-recovered without Pi reboot.

**Files:** `system_watchdog.py`, `config.py`, `local_health.py`, `scripts/pi_health_export.sh`

---

### 3. H264 encoder + capture load on Pi Zero 2 W — **partially addressed**

**Hypothesis:** Simultaneous H264 encode and `capture_array()` stress Pi Zero–specific paths.

**Mitigations shipped:**

| Change | Version | Notes |
|--------|---------|--------|
| Lores capture (main encode-only) | pre-soak | `USE_LORES_CAPTURE = True`; hangs can still occur on lores path |
| Capture interval 1.0s | v1.1.28 | Was 0.5s |
| Lightweight frame fingerprint | v1.1.30 | 32×24 subsample hash; removed full-frame SHA256 |
| H264 buffer 48 MB / 2000 chunks | v1.1.30 | Was 60 MB / 2500 |
| Removed periodic `gc.collect()` | v1.1.30 | Capture + motion loops |

**Still open:**

- [ ] Raise lores to **1024×576 (16:9)** for A/B quality — deferred; see below
- [ ] Review hang rate over 7+ days before further load changes
- [ ] Tier 3 architecture (no periodic `capture_array`) if recovery frequency unacceptable

**Files:** `circular_buffer.py`, `config.py`, `motion_detector.py`

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
| No in-process camera restart | **Fixed** — 5 min agent restart (v1.1.28) |
| 60 min threshold before any recovery | **Tiered** — 5 min agent, 60 min Pi reboot |
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
3. ~~**Recovery tiering:** Agent restart at 5 min, Pi reboot at 60 min — acceptable?~~ **Shipped** v1.1.28; monitoring hang/recovery rate.
4. **Repro:** Can we trigger hang on Study with deliberate concurrent capture under load? (Less relevant post-`5001b50`; focus on idle/encoder-only hangs.)
5. **Study node:** Align watchdog unit to `User=pi` like the rest of the fleet?
6. **Motion on stale frames:** Should motion detector pause when `last_frame_time` is stale?
7. ~~**Fleet validation:** Does hang rate drop after `5001b50`?~~ Ongoing — lores + Phase B; Back hung once, recovery worked.
8. **A/B still resolution:** Raise lores to 1024×576 after monitoring window — see deferred plan below.

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

### Where we are now (2026-06-09)

- **Code:** v**1.1.31** fleet-wide (Study via NFS; .54/.55/.57 via Ansible; MBR2 offline intermittently).
- **Runtime:** Full pipeline — lores capture @ 1.0s, motion, events, H264 buffer (48 MB / 2000 chunks), 5 min agent recovery.
- **Encode-only soak:** Complete (~40h clean on 4/5 nodes); encode path ruled out as primary failure mode.
- **Hang behavior:** Capture hangs still occur on lores path; **in-process recovery validated** on Back (~5 min outage, no Pi reboot).
- **Grafana:** Hang/recovery event panels + `Agent up/down timeline`; fleet table shows Hangs/Recoveries (24h).
- **A/B stills:** 640×480 (4:3) from lores buffer; **color fix** BGR→RGB shipped v1.1.31; **resolution/aspect upgrade deferred** — see below.
- **Video:** Unchanged at 1280×720 H264 (16:9) — quality fine.

### Current config snapshot (v1.1.31)

| Setting | Value |
|---------|--------|
| `ENCODE_ONLY_SOAK` | `False` |
| `USE_LORES_CAPTURE` | `True` |
| `LORES_RESOLUTION` | `(640, 480)` — **4:3** |
| `VIDEO_RESOLUTION` | `(1280, 720)` — 16:9 encode |
| `PICTURE_CAPTURE_INTERVAL` | `1.0` s |
| `CIRCULAR_BUFFER_MAX_BYTES` | 48 MB |
| `CIRCULAR_BUFFER_MAX_CHUNKS` | 2000 |
| `AGENT_RECOVERY_HANG_THRESHOLD_MINUTES` | 5 |
| `THUMBNAIL_SIZE` | `(240, 180)` — **4:3** (should match still aspect when upgraded) |
| `USE_BUFFERED_EVENT_STILLS` | `True` |

### Success criteria — encode-only soak (**complete**)

Soak passed on reachable nodes (~40h, encode stale max 4s). MBR2 had WiFi gaps, not encode failures.

### Success criteria — return to full functionality (**in progress**)

Target: **7+ days** full pipeline on all nodes with:

| Criterion | Target | Status |
|-----------|--------|--------|
| Motion detection + events | Active on central server | ✅ Events flowing |
| A/B + H264 from buffer | Buffered stills + clip dump | ✅ Working; A/B quality TBD |
| `camera_agent_healthy` | 1; NoFrames = 0 | ⏳ Monitoring; recoveries expected |
| Hang recovery | < 5 min, no manual SSH | ✅ Validated once (Back) |
| Recovery frequency | Rare (not daily per node) | ⏳ **Watch 7+ days** before more changes |
| No Pi reboots for hangs | Agent recovery sufficient | ⏳ Monitoring |

---

## A/B still quality (deferred)

**Purpose:** Picture A at motion trigger; Picture B **4 seconds later**. Quality should be **decent** — useful for identifying people/objects at the event. Video clip (H264) remains the primary forensic record; A/B are quick reference stills.

### What happened to A/B size over time

| Era | Source | Typical size | Aspect | Notes |
|-----|--------|--------------|--------|--------|
| Pre-lores / ISP path | `capture_file()` or main stream | **1024×576** or 1280×720 | **16:9** | Sunday 2026-06-08 samples; good width match to video |
| Current (lores buffer) | `LORES_RESOLUTION` → `save_buffered_still()` | **640×480** | **4:3** | ~2026-06-09; smaller; looks distorted if UI stretches to 16:9 |

Measured from event JPEGs: Sunday **1024×576**; today **640×480**.

### Color issue (fixed v1.1.31)

OpenCV lores path stores frames in **BGR** order; PIL saved as RGB → red/blue swap (red playground → blue, etc.). Fixed via `_frame_rgb_for_jpeg()` (same as MJPEG stream). Events after v1.1.31 deploy should have correct colors.

### Planned fix — **Option B: match Sunday** (not implemented yet)

**Decision (2026-06-09):** After monitoring period, raise lores to match Sunday quality without reverting to event-time `capture_file()` (concurrent picam2 risk).

| Config change | From | To | Rationale |
|---------------|------|-----|-----------|
| `LORES_RESOLUTION` | `(640, 480)` | **`(1024, 576)`** | 16:9; matches Sunday samples; ~2.7× more pixels than 640×480 |
| `THUMBNAIL_SIZE` | `(240, 180)` 4:3 | **`(320, 180)`** 16:9 | Thumbnail stays small but matches still aspect |

**Why not implement now:** Fleet just entered Phase B + recovery + buffer/hash + color fixes. Want **7+ days** of Grafana/log data (hang rate, recovery count, stability) before adding lores pixel load.

**When to implement:** After monitoring window; bump `SYSTEM_VERSION`; `./gitsync.sh` → Ansible. Watch `Agent up/down timeline` and recovery counts for regression.

**Alternatives rejected for now:**

- **640×360** — fixes aspect only, still smaller than Sunday
- **1280×720 lores** — best quality, highest hang risk
- **Upscale on save** — fake resolution, no real detail
- **Event-only `capture_file()`** — best ISP color/size but reintroduces concurrent picam2 access (`5001b50` regression)

---

## Suggested path forward (updated 2026-06-09)

### Phase A — Encode soak — **complete**

- [x] 48–72h encode-only soak on reachable nodes
- [x] Conclusion: encode-only stable; capture path is the failure mode

### Phase B — Full pipeline + recovery — **deployed**

- [x] In-process recovery (~5 min) fleet-wide (v1.1.28)
- [x] `ENCODE_ONLY_SOAK = False` fleet-wide
- [x] Capture interval 1.0s; lores capture
- [x] Buffer 48 MB; lightweight hash (v1.1.30)
- [x] A/B color fix BGR→RGB (v1.1.31)
- [x] Hang/recovery Grafana metrics (v1.1.29)
- [x] Deploy workflow documented (`Docs/DEPLOYMENT.md`)

### Phase C — Monitoring period (**now → ~7 days**)

- [ ] Grafana: `Agent up/down timeline`, Hang/Recovery event charts, fleet table counts
- [ ] Track recovery frequency per node (target: not daily)
- [ ] Confirm A/B colors correct on post–v1.1.31 events
- [ ] Note any hangs correlated with motion events (Back hung ~1 min after event 27286)
- [ ] MBR2: deploy when WiFi returns; include in stats

### Phase D — A/B still resolution (**deferred**)

- [ ] `LORES_RESOLUTION = (1024, 576)`
- [ ] `THUMBNAIL_SIZE = (320, 180)` (16:9)
- [ ] Verify next events match Sunday size/aspect; compare hang rate before/after

### Phase E — Long-term (only if needed)

- [ ] If recovery frequency too high → Tier 3 (architecture without periodic `capture_array`)
- [ ] Study watchdog `User=pi` alignment
- [ ] Motion detector pause on stale frames

### 2026-06-09 — Phase B fleet rollout (v1.1.28)

**Soak outcome:** ~40h stable on 4/5 nodes (encode stale max 4s); MBR2 offline (WiFi). Soak successful — encode path not primary failure mode.

**Shipped fleet-wide:**

1. **In-process recovery** — `system_watchdog`: NoFrames/hung `capture_array()` ≥ 5 min → `os._exit(1)` for systemd `Restart=always`; rate limit 3/hour, 10 min cooldown; skips during livestream. Pi reboot watchdog remains at 60 min.
2. **Full pipeline restored** — `ENCODE_ONLY_SOAK = False` (motion + events re-enabled).
3. **Slower capture** — `PICTURE_CAPTURE_INTERVAL = 1.0s` (was 0.5s); lores capture unchanged.

**Monitor:** `camera_agent_healthy`, NoFrames, agent restart count in logs, motion events on central server.

### 2026-06-09 — Monitoring + optimization + A/B quality (v1.1.29–v1.1.31)

**Hang/recovery visibility (v1.1.29):**

- `agent-event-history.json` records down/up transitions
- Prometheus: `camera_agent_hang_events_total`, `camera_agent_recovery_events_total`, last-event timestamps
- Grafana: Hang & recovery event row, `Agent up/down timeline`, fleet table columns

**Load reduction (v1.1.30):**

- H264 buffer 60 MB → **48 MB**; chunks 2500 → **2000**
- SHA256 every frame → **32×24 lightweight fingerprint**
- Removed forced `gc.collect()` in capture/motion loops
- Motion wait/cooldown poll aligned to 1.0s
- Streaming health check: `/api/health` (was 404 on `/streaming/status`)

**A/B color fix (v1.1.31):**

- `_frame_rgb_for_jpeg()`: BGR→RGB before PIL JPEG (red/blue swap on lores stills)
- Deployed fleet-wide commit `1dd143f`

**Back hang + recovery (2026-06-09 ~1:26–1:31 PM):**

- NoFrames 1m→4m; `HUNG in capture_array() for 204.7s`; motion scores 0 on stale frames
- Agent recovery at ~5 min; systemd restart; **no Pi reboot**
- Correlated with motion event ~1:25 PM (video transfer) — timing noted, not proven causal

**A/B resolution regression identified (2026-06-09):**

- Sunday events: **1024×576** (16:9); today: **640×480** (4:3) from `LORES_RESOLUTION`
- 4:3 stills look distorted when UI expects 16:9 (same as video)
- **Planned (deferred):** `LORES_RESOLUTION = (1024, 576)`, `THUMBNAIL_SIZE = (320, 180)` after 7-day monitoring window

---

## References

- Repo: `circular_buffer.py`, `system_watchdog.py`, `camera_reboot_watchdog.py`, `event_processor.py`
- General Work: `watchdog-local-health-fix.md`, `camera-nodes-diagnosis.md`
- Grafana dashboard: `/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json`
- Ansible deploy: `/home/ubuntu/ansible/pi-fleet/upgrade_security_cameras.yml`
