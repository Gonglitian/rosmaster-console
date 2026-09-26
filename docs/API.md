# API reference

Everything the hub offers on port 8080: HTTP endpoints, the WebSocket protocol used by both the dashboard and policy workers, and the message formats. Protocol version **1**, hub version 0.2.0. If you only want to write a policy, start with [POLICY.md](POLICY.md): the Python client `worker/rc_worker.py` wraps all of this.

## Conventions

- **Frames.** Velocities and positions are in the car's frame `base_link`: **x forward, y left, z up**, angles counter-clockwise, SI units (m, m/s, rad, rad/s). `odom` poses are in the EKF's `odom` frame, which starts at 0 when Base starts.
- **Time.** Every `stamp` / `s` / `server_time` is the **car's wall clock** in Unix seconds (float), taken when the hub received or sent the message. Your laptop's clock may differ by seconds. To compare, use the hub's stamps or measure the offset with `ping`/`pong`.
- **JSON.** Every WebSocket frame is one JSON object (text frame) with a type field `t`. Unknown fields should be ignored, since newer hubs may add some.
- **No authentication.** Anything on the network can connect (design decision; see [ARCHITECTURE.md](ARCHITECTURE.md#design-decisions)).

## HTTP endpoints

| Method and path | Body | Returns |
|---|---|---|
| `GET /` | | The dashboard (static files from `web/`) |
| `GET /api/state` | | The same object as the WebSocket `state` message (below) |
| `GET /api/battery` | | Battery report: `snapshot` (see state), `history` `[[t, volts, resting], …]` (3 h), `thresholds` `{warn, critical, alarm, floor, full, storage}`, `table` (voltage→% curve) |
| `GET /camera.mjpg` | | `multipart/x-mixed-replace` MJPEG stream (640×480, ≤ 15 fps). Ends when Camera is switched off. Open it in a browser `<img>` or with OpenCV: `cv2.VideoCapture('http://rosmaster.local:8080/camera.mjpg')` |
| `POST /api/estop` | `{}` or `{"release": true}` | Engage (latches) or release the E-STOP. Returns `{"ok", "control", "sensors"}`. |
| `POST /api/sensor` | `{"name": "base"\|"lidar"\|"camera", "on": bool}` | Starts or stops that sensor (asynchronous: poll `/api/state`). 400 for an unknown name. |
| `GET /api/net` | | `{"status": <wifi status>, "attempt": <last switch>}` (fast). CORS-enabled. |
| `GET /api/wifi` | | `status`, `saved` (profiles), `scan` (last scan), `scan_time`, `scan_error`, `attempt`, `hotspot_profile`, `new_priority`, `ip_by_ssid`. CORS-enabled. |
| `POST /api/wifi/scan` | | Rescans (5–10 s), then returns the `/api/wifi` object |
| `POST /api/wifi/connect` | `{"ssid": str, "password": str}` | Validates and answers at once. The switch starts 1.5 s later and takes 10–45 s, with undo and hotspot fallback on failure. Password may be empty for open or already-saved networks. |
| `POST /api/wifi/hotspot` | | Switch to the car's own hotspot `ROSMASTER` (1.5 s later) |
| `POST /api/wifi/forget` | `{"name": profile}` | Delete a saved profile. The hotspot and the one in use are refused. |

Errors are HTTP 400 with `{"error": "…"}`. Wi-Fi changes return 400 on a test hub (`HF_NO_HARDWARE=1`).

Wi-Fi objects:
- `status`: `{"state": "connected"|…, "connection": profile, "mode": "ap"|"infrastructure", "ssid", "ip", "hotspot": bool}`
- `saved[]`: `{"name", "ssid", "mode", "priority", "secured"}`
- `scan[]`: `{"ssid", "signal" (0–100), "security", "band": "2.4G"|"5G", "in_use", "known"}`
- `attempt`: `{"ssid", "state": "connecting"|"connected"|"failed", "reason", "ip"?, "time"}`

There is no HTTP call that moves the car, on purpose. Motion needs a live WebSocket client, so a crashed client stops the car.

## WebSocket `/ws`

Connect to `ws://<car>:8080/ws`. The hub sends WebSocket pings every 5 s and drops a client that does not answer within 15 s. Any number of clients may connect. A client counts as a **panel** (a dashboard) by default, and becomes a **worker** once it sends `worker_hello`.

### Sent by the hub to every client

| `t` | When | Fields |
|---|---|---|
| `hello` | on connect | `version`, `protocol`, `client_id`, `server_time`, `config` (below) |
| `state` | on connect, then 5 Hz, plus at once on changes | see [state](#state) |
| `scan` | every lidar scan (~6.7 Hz) | `seq`, `stamp`, `hstamp`, `amin`, `ainc`, `cm` |
| `odom` | every odometry message (~10 Hz) | `seq`, `stamp`, `x`, `y`, `yaw`, `vx`, `vy`, `wz` |
| `policy_debug` | relayed from a worker | `worker_id`, `stamp`, `markers`, `text` |
| `pong` | reply to `ping` | `c` (echoed), `s` (car time) |
| `error` | bad request | `msg` |

`config`: `{laser_x: 0.0435, laser_yaw: 3.14, max_linear: 0.7, max_angular: 1.5, control_hz: 20, source_timeout: 0.5, camera_max_fps: 15}`

#### `scan`

```json
{"t":"scan","seq":812,"stamp":1790000000.1234,"hstamp":1790000000.08,
 "amin":-3.1416,"ainc":0.0043,"cm":[0,0,153,152,151, ...]}
```

- `cm[i]` is the range of beam `i` in **centimetres**, integer. `0` means no valid return (too near, too far, or no echo).
- The beam angle in the **laser frame** is `amin + i*ainc`. The laser is mounted `laser_x` = 0.0435 m ahead of the base centre and rotated by `laser_yaw` = π. So the point in `base_link` is:
  ```python
  a = amin + i * ainc + laser_yaw
  x = laser_x + r * cos(a);  y = r * sin(a)       # r = cm[i] / 100
  ```
  `Worker.scan_points()` does exactly this.
- `seq` counts scans since the hub started. `stamp` is when the hub received it (car clock). `hstamp` is the ROS header stamp set by the lidar driver.

#### `odom`

`x`, `y`, `yaw` are the EKF pose in the `odom` frame. `vx`, `vy`, `wz` are the measured velocity in `base_link`. Present only while Base is on.

#### `state`

```jsonc
{"t": "state", "stamp": 1790000000.2,
 "control": {"mode": "idle"|"manual"|"policy", "estop": false, "estop_reason": null,
             "active_policy": "w3"|null, "output": [vx, vy, wz], "source_age": 0.04,
             "limits": {"linear": 0.7, "angular": 1.5}},
 "sensors": {"base":   {"state": "off"|"starting"|"on"|"error"|"stopping", "message": "", "attempt": 0, "since": 1790000000.0},
             "lidar":  {...}, "camera": {...}},
 "topics":  {"scan": {"hz": 6.7, "age": 0.1}, "odom": {...}, "voltage": {...}},
 "battery_v": 11.8,
 "battery": {"state": "resting"|"driving"|"settling"|"nodata", "v": 11.8, "v_rest": 11.8, "v_fast": 11.75,
             "cell_v": 3.933, "pct": 60, "pct_exact": 58.3, "level": "ok"|"warn"|"critical"|"stop"|"unknown",
             "trend": {...}|null, "eta": {...}|null, "age": 0.4, "source": "driver"|"monitor"},
 "clients": 3, "panels": 2,
 "workers": [{"id": "w3", "name": "keep-distance", "host": "tasl-l1", "connected_s": 42,
              "cmd_hz": 6.5, "last_cmd_age": 0.12, "latency_ms": 18, "active": true}],
 "uptime": 3600,
 "sys": {"ip": "192.168.68.80", "hostname": "rosmaster", "disk_free_gb": 2.8, "cpu_temp_c": 55.0,
         "net": {"state": "connected", "ssid": "TP-Link-1700", "mode": "infrastructure", "ip": "…", "hotspot": false}}}
```

- `control.output` is what the hub is commanding right now, after limits.
- `source_age` is the seconds since the current source's last command.
- `workers[].latency_ms` is the median, over the last 50 commands, of (command arrival − `obs_stamp`) on the car clock: the time from the observation reaching the hub to the command computed from it reaching the hub. It is null if the worker does not send `obs_stamp`.
- `pct` is rounded to 5 %.

### Sent by a dashboard

| `t` | Fields | Effect |
|---|---|---|
| `manual` | `vx`, `vy`, `wz`, `seq` | Switches to **manual** mode (overriding a policy) and sets the manual command. The hub answers `ack {seq, s}`. The dashboard sends every 40–100 ms while a joystick or key is held. After 0.5 s without one, the command counts as zero. |
| `manual_release` | | Joystick let go: command zero, but stay in manual mode |
| `hand_back` | | Leave manual mode: back to the active policy, or idle |
| `estop` / `estop_release` | | Engage / release the E-STOP. Releasing sets mode idle. |
| `sensor` | `name`, `on` | Same as `POST /api/sensor` |
| `beep` | | 0.15 s beep (Base must be on) |
| `activate_policy` | `worker_id` | Give that worker control (mode → policy). Refused with `error` while E-STOP is engaged. |
| `deactivate_policy` | | Take control back from the active worker (mode → idle) |
| `ping` | `c` (anything) | Hub answers `pong {c, s}`. The dashboard sends one per second to measure round-trip time. |

Nothing technically prevents a worker from sending these too. By convention, workers never send `manual`, `estop_release`, `hand_back` or `activate_policy`: giving control is a human decision.

### Policy worker messages

```
worker                                  hub                                   dashboard
  │── worker_hello {name, host, protocol} ─►│                                      │
  │◄─ worker_welcome {worker_id, active, config}                                   │
  │◄─ scan / odom / state (like any client) │── state.workers[] lists it ─────────►│
  │── policy_cmd {...} ────────────────────►│  (ignored: not active)               │
  │◄─ policy_ack {accepted:false, driving:false}                                   │
  │                                         │◄──── activate_policy {worker_id} ────│  "Give control"
  │◄─ policy_active {active:true} ──────────│                                      │
  │── policy_cmd {vx,vy,wz,seq,obs_seq,obs_stamp} ──►│── /hub/cmd_vel ──► wheels   │
  │◄─ policy_ack {accepted:true, driving:true}                                     │
  │── policy_debug {markers, text} ────────►│── policy_debug (relayed) ───────────►│
```

| `t` (direction) | Fields | Notes |
|---|---|---|
| `worker_hello` (→ hub) | `name` (≤ 40 chars), `host` (≤ 60), `protocol`: 1 | Registers this connection as worker `w<client_id>`. Send it first. |
| `worker_welcome` (← hub) | `worker_id`, `protocol`, `active`, `config` | |
| `policy_cmd` (→ hub) | `vx`, `vy`, `wz` (floats), `seq` (your counter), `obs_seq`, `obs_stamp` (optional: the `seq`/`stamp` of the `scan` or `odom` the command was computed from) | Always answered by a `policy_ack`. It moves the car only if this worker is active, the mode is policy and there is no E-STOP. Non-finite values count as 0. |
| `policy_ack` (← hub) | `seq`, `accepted`, `driving`, `mode`, `estop` | `accepted`: this worker has control. `driving`: the command was actually passed to the wheels. |
| `policy_active` (← hub) | `active` | Sent when a dashboard gives or takes back control, or when another worker is given control |
| `policy_debug` (→ hub) | `markers`: up to 200 `{x, y, kind, label}`; `text`: ≤ 300 chars | Relayed to all clients. Markers are in `base_link`; `kind` is `human` (orange), `target` (magenta), `goal` (green) or `point` (cyan). The dashboard shows them for 2 s after each message. |

**`active` is not the same as `driving`.** After an E-STOP is released, or while a person is driving manually, the worker is still the active policy (`active_policy` = its id) but mode is idle or manual. Its commands are then `accepted: true, driving: false`, and no `policy_active` message is sent. Use `policy_ack.driving` (in `rc_worker`: `self.driving`) to know whether your commands are moving the car. Control returns when someone presses **Give control** or **Hand back to policy**.

**Timing rules for workers.**
- Send at least every 0.5 s while you want the car to move, or the hub treats your command as zero. Sending once per scan (~6.7 Hz) is typical. There is no benefit above the 20 Hz control rate.
- If your connection drops while you are active, the hub stops the car at once and takes control back. After you reconnect, someone must press **Give control** again.

## Settings

All values are in `hub/config.py`. Each can be overridden with an environment variable `HF_<NAME>` on the hub (for the car, add `-e HF_...` to the `docker run` line in `car/install_hub.sh`).

| Setting | Default | Meaning |
|---|---|---|
| `PORT` | 8080 | HTTP / WebSocket port |
| `CONTROL_HZ` | 20 | `/hub/cmd_vel` publish rate |
| `SOURCE_TIMEOUT` | 0.5 s | Silent source → zero |
| `MAX_LINEAR` / `MAX_ANGULAR` | 0.7 m/s / 1.5 rad/s | Speed limits |
| `MAX_LINEAR_ACCEL` / `MAX_ANGULAR_ACCEL` | 1.5 m/s² / 4 rad/s² | Acceleration limits (deceleration is unlimited) |
| `LIDAR_MAX_ATTEMPTS` / `LIDAR_START_TIMEOUT` / `LIDAR_SPINUP` | 3 / 12 s / 1.5 s | Lidar start retries |
| `BASE_START_TIMEOUT` | 15 s | Base start deadline |
| `IDLE_OFF_AFTER` | 600 s | Sensors off with no panel and no active policy |
| `CAMERA_MAX_FPS` | 15 | Per viewer |
| `NO_HARDWARE` | off | `HF_NO_HARDWARE=1`: test hub without the car |

Driver-side limits (`car/launch/base.launch.py`, env `HF_CMD_TIMEOUT`, `HF_DRIVER_MAX_LINEAR`, `HF_DRIVER_MAX_ANGULAR`): 0.5 s, 1.0 m/s, 3.0 rad/s.
