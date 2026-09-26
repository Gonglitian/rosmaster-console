# Architecture

How the system is put together, and why. Interactive diagrams of the same material, as HTML pages, are in the `diagrams/` folder of the handoff package.

## The three roles

```
  phone / laptop browser            policy worker (tasl-l1, a MacBook, ...)
  "dashboard" (web/)                 any program using worker/rc_worker.py
         │  HTTP + WebSocket :8080              │  WebSocket :8080
         └───────────────┬──────────────────────┘
                         ▼
  ┌──────────────── the car: Raspberry Pi 5 ─────────────────────────────┐
  │  Docker container rc-hub                                            │
  │    hub (python3 -m hub) ── ROS 2 Foxy, domain 32 ──► chassis, lidar │
  │  host: NetworkManager (Wi-Fi), avahi (rosmaster.local), OLED        │
  └──────────────────────────────────────────────────────────────────────┘
```

| Role | What it is | Where |
|---|---|---|
| **Hub** | A Python service, and the only program that talks to ROS. It starts and stops the sensors, decides who may drive, enforces the safety rules, and serves the dashboard and the WebSocket. | On the car, in the Docker container `rc-hub` |
| **Dashboard** | The web page the hub serves (`web/`): plain HTML/JS, no build step, mobile-first. Any number can be open. | Any browser on the car's network |
| **Worker** | A program that runs a policy: it receives sensor data over the WebSocket and sends velocity commands back. It needs no ROS. It only drives after someone presses **Give control** in a dashboard. | Any machine on the car's network, typically tasl-l1 (GPU) |

"Policy" here means any program that decides how the car should move, whether a learned (RL) model or a hand-written rule.

## On the car

### Processes

```
host (Debian 12, Raspberry Pi OS)
├─ NetworkManager      Wi-Fi client / hotspot (profiles and priorities: DASHBOARD.md)
├─ avahi-daemon        answers "rosmaster.local" on the local network (mDNS)
├─ yahboom_oled.py     the small OLED screen: CPU, RAM, disk, IP (Yahboom's, autostart)
├─ dockerd
│   └─ container rc-hub  (restart unless-stopped, --privileged, --network host)
│       └─ tini ─ car/entrypoint.sh   supervisor: restarts the hub if it dies
│           └─ python3 -m hub         the hub (tornado web server + asyncio)
│               ├─ ros-spin thread      rclpy node "hf_hub"
│               ├─ ros2 launch car/launch/base.launch.py     (when Base is on)
│               │   ├─ hf_driver_x3.py         chassis driver: /hub/cmd_vel → serial
│               │   ├─ base_node_X3            wheel odometry
│               │   ├─ imu_filter_madgwick     IMU orientation
│               │   ├─ ekf (robot_localization)   fused /odom
│               │   ├─ robot_state_publisher, joint_state_publisher
│               │   └─ static TF base_link → laser
│               ├─ ros2 launch car/launch/lidar.launch.py    (when Lidar is on)
│               │   └─ sllidar_node          RPLIDAR-A1 → /scan
│               ├─ ffmpeg                    camera MJPEG copy (when Camera is on)
│               └─ battery reader thread     reads the voltage over serial while Base is off
└─ container x3 (Yahboom's original; STOPPED, kept for going back)
```

The car's own GUI autostarts were cleaned up:
- Yahboom's phone-app server `rosmaster_main.py` is disabled (`~/.config/autostart/rosmaster.desktop` has `Hidden=true`; the original is backed up as `rosmaster.desktop.orig-2026-09-26`). It held the chassis serial port.
- `emergency_gui.desktop` still starts. It publishes `/cmd_vel`, which nothing listens to any more, so it is harmless.

### ROS 2 topics

All in ROS domain 32, over UDP only: shared-memory transport is disabled by `car/fastdds_udp.xml`, because a killed process could leave stale shared memory that silently broke reception.

| Topic | Type | From → to | Rate |
|---|---|---|---|
| `/hub/cmd_vel` | `geometry_msgs/Twist` | hub → `hf_driver` | 20 Hz, plus one extra message on every input |
| `/scan` | `sensor_msgs/LaserScan` | `sllidar_node` → hub | ~6.7 Hz |
| `/odom` | `nav_msgs/Odometry` | EKF → hub | ~10 Hz |
| `/voltage` | `std_msgs/Float32` | `hf_driver` → hub | ~10 Hz, 0.1 V steps |
| `Buzzer` | `std_msgs/Bool` | hub → `hf_driver` | on the **Beep** button |
| `/imu/data_raw`, `/vel_raw`, `/joint_states`, `/tf`, … | | chassis nodes, internal | |

`/cmd_vel` exists by convention in ROS, but the car ignores it on purpose (see [safety model](#safety-model)).

### Devices

| Device | Path | Notes |
|---|---|---|
| Chassis board (STM32) | `/dev/myserial` | Only one process may own it: `hf_driver` when Base is on, the battery reader when Base is off. |
| RPLIDAR-A1 | `/dev/rplidar` | USB serial adapter (CP2102). The motor runs whenever the adapter's DTR line is low. To stop it, the hub holds the port open with DTR high. |
| Colour camera | `/dev/video0` | The Orbbec's UVC colour stream, MJPEG 640×480. It is forwarded as-is, with no encoding on the Pi. |
| Orbbec depth sensor | USB | Unused. The hub lets the kernel suspend it. |

`/dev/myserial` and `/dev/rplidar` are udev aliases. Use them rather than `/dev/ttyUSB0/1`, whose numbers swap between reboots.

### Files on the car

| Path | Contents |
|---|---|
| `~/rosmaster-console/` | This repository, copied by `scripts/deploy_car.sh` (rsync). It is mounted at `/opt/rc` in the container, so a code update only needs a hub restart. |
| `~/rosmaster-console/.state/wifi_ips.json` | The IP the car had on each Wi-Fi network. Used by the dashboard to find the car after a Wi-Fi switch. Not overwritten by deploys. |
| Docker image `rc-base:x3-20260926` | A snapshot (`docker commit`) of Yahboom's `x3` container: ROS 2 Foxy plus Yahboom's workspace `/root/yahboomcar_ros2_ws`, exactly as used for the paper's real-robot runs. |
| Docker image `rc-hub:latest` | `rc-base` plus tornado 6.4.2 (`car/Dockerfile`). |
| `/etc/NetworkManager/conf.d/99-wifi-powersave-off.conf` | Wi-Fi power saving off, because power saving delays packets. |
| `~/Rosmaster/`, `~/software/oled_yahboom/` | Yahboom's own software, unchanged. |

The SD card is nearly full: about 2.8 GB free, most of `/` taken by Docker images. Keep ROS logs off it (the container writes them to a RAM disk, `/tmp`) and record data on the worker's machine, not on the car.

## Inside the hub

`hub/` is Python 3.8 with a single dependency, tornado. Everything runs on one asyncio event loop, except the ROS spin thread and blocking helpers (nmcli, serial), which run in threads.

| Module | Job |
|---|---|
| `app.py` | `Hub`: WebSocket and HTTP handlers, message dispatch, policy workers, the 20 Hz control loop and the 5 Hz state loop |
| `arbiter.py` | Who drives: modes idle / manual / policy, E-STOP, speed limits, acceleration limits, source timeout. Pure logic, unit-tested. |
| `ros_bridge.py` | The only ROS code: publishes `/hub/cmd_vel` and `Buzzer`; subscribes to `/scan`, `/odom`, `/voltage` and turns each message into a small JSON dict stamped with the car's clock |
| `sensors.py` | Start, stop and health for Base, Lidar and Camera. Lidar retries with a USB replug (`usbreset.py`). |
| `devices.py` | Really off: lidar motor stop (DTR), depth-sensor suspend |
| `camera.py` | ffmpeg → latest JPEG frame → `/camera.mjpg` |
| `battery.py`, `battery_reader.py` | Voltage → charge estimate (resting voltage only), trend, time left; serial voltage reader for when Base is off |
| `wifi.py` | nmcli wrapper behind the Network tab: scan, connect with undo, hotspot, forget |
| `sysinfo.py`, `procs.py`, `config.py` | Footer information, child-process groups, settings (every value can be overridden with an `HF_*` environment variable) |

### Control path

```
dashboard joystick ──manual{vx,vy,wz}──┐
                                        ├─► Arbiter ──► /hub/cmd_vel ──► hf_driver ──serial──► STM32 ──► wheels
worker ────────────policy_cmd{...}─────┘      (clamp, ramp, timeout)    (0.5 s watchdog, clamp)
```

1. Each input goes to the Arbiter. It is published at once, and again on every 50 ms control tick.
2. The Arbiter picks the source by mode, treats a source silent for 0.5 s as zero, clamps to 0.7 m/s and 1.5 rad/s, and limits acceleration (1.5 m/s², 4 rad/s²). Slowing down is never limited.
3. `hf_driver` clamps again (1.0 m/s, 3.0 rad/s) and sends the command to the chassis board over serial (about 3 ms).

Measured with Base off (`scripts/latency_probe.py`, tasl-l1 over home Wi-Fi): from a WebSocket command leaving the laptop to the hub's `/hub/cmd_vel` arriving back at the laptop over DDS, the median is **12.4 ms**. The hub itself takes **0.9 ms**. The rest is two Wi-Fi hops.

### Sensor data path

`/scan` and `/odom` go to every WebSocket client (dashboards and workers alike) as JSON, one message per ROS message. Scan ranges are sent as integer centimetres (`cm`, 0 = no return) to keep messages small. The hub's own state goes out at 5 Hz, plus at once on every change. Camera frames go only to viewers of `/camera.mjpg`, at up to 15 fps each. Message formats: [API.md](API.md).

## Network

| What | Where |
|---|---|
| Dashboard, HTTP API, WebSocket | TCP 8080 on the car |
| SSH | TCP 22 on the car, user `pi` |
| Car's name | `rosmaster.local` via mDNS (avahi). Needs a network that passes multicast. |
| ROS 2 (DDS) | UDP, domain 32. Only needed by ROS tools on other machines (for example `ros2 topic echo` on tasl-l1). Workers don't use it. |

The car joins the highest-priority known Wi-Fi network in range, or else opens its own hotspot `ROSMASTER` (192.168.1.11). Changing networks from the browser is described in [DASHBOARD.md](DASHBOARD.md#network-tab). There is no login and no encryption beyond the Wi-Fi's own, by design: the system is meant only for a trusted lab network.

## Safety model

The goal: the car moves only when a person or an authorised policy is actively commanding it, and it stops when anything in between goes away.

**Who may drive.** Priority is **E-STOP > manual > policy**.
- E-STOP latches until someone releases it. Releasing returns to idle, so a policy never resumes on its own.
- Any manual input takes over immediately, even while a policy is driving. Only **Hand back to policy** returns control.
- Only one worker can be active, and only after someone presses **Give control** in a dashboard. Commands from other workers are acknowledged but ignored.

**Only the hub can drive.** The stock Yahboom driver listens on `/cmd_vel`, so anything on the network could drive the car. It was replaced by `car/nodes/hf_driver_x3.py`, which listens only on `/hub/cmd_vel`. Old scripts and joystick nodes publishing `/cmd_vel` are ignored. Nothing technically prevents publishing on `/hub/cmd_vel`, so never do it.

**When something goes away, the car stops:**

| What fails | What stops the car | Within |
|---|---|---|
| The commanding dashboard stops sending (tab closed, backgrounded, network lost) | Arbiter source timeout | 0.5 s |
| The active worker disconnects | Hub stops at once, mode → idle | immediately |
| The active worker stops sending but stays connected | Arbiter source timeout | 0.5 s |
| The hub crashes or hangs | `hf_driver` watchdog (no `/hub/cmd_vel`). The supervisor then sends a serial zero and restarts the hub with all sensors off. | 0.5 s |
| Battery voltage (average of recent readings) reaches 9.0 V | Hub engages E-STOP | about 1 s |
| The whole container is killed, or the Pi loses power | Only the chassis board itself. **Not verified.** On container start a serial zero is sent again. | ? |

**Limits** are enforced on the car, not in the dashboard: 0.7 m/s, 1.5 rad/s, 1.5 m/s² (hub), and 1.0 m/s, 3.0 rad/s (driver). Sliders in the dashboard only scale within them.

**Not yet verified on the real car** (wheels off the ground): that the driver watchdog stops wheels already turning, and what the STM32 does when serial commands stop. See [OPERATIONS.md](OPERATIONS.md#not-yet-verified).

## Repository layout

```
car/        on-car: Dockerfile, install_hub.sh, entrypoint.sh (supervisor), fastdds_udp.xml,
            stop_wheels.py, launch/ (base, lidar), nodes/hf_driver_x3.py (safe chassis driver)
hub/        the hub (Python 3.8, tornado)
web/        the dashboard (index.html, app.js, style.css; no build step)
worker/     rc_worker.py (policy client library) and examples/
scripts/    dev-machine tools: deploy_car.sh, test_hub.sh, ws_probe.py, test_*.py, latency_probe.py
tests/      unit tests without ROS (arbiter, battery, Wi-Fi parsing)
docs/       this documentation
```

The working copy lives on tasl-l1 at `~/rosmaster-console`; the repository is also on GitHub (private): https://github.com/Gonglitian/rosmaster-console. The older research code is at `~/human-following` on tasl-l1 and is untouched by this project.

## Design decisions

These were agreed with Litian before the build (September 2026):

1. **ROS only on the car.** Laptops and phones need no ROS; they talk JSON over a WebSocket.
2. **The whole policy pipeline runs off the car** (the Pi has no GPU). The car only streams sensors and executes commands.
3. **Control priority E-STOP > manual > policy.** Manual takes over instantly; handing back needs a button press.
4. **A custom, mobile-first web dashboard** instead of RViz or Foxglove, so a phone is enough.
5. **Debugging first, but experiment-grade data.** All messages are time-stamped on the car's clock, and every command carries the stamp of the observation it came from, so latency is measurable. Session recording is the next milestone.
6. **Local network only.** No Tailscale or cloud relay on the car.
7. **At boot only the hub starts.** Sensors start on demand and switch off after 10 idle minutes (saves battery and lidar wear).
8. **Disk:** only logs and apt caches were cleaned; the partition was left as is.
9. **A new repository, `rosmaster-console`.** The research repo `human-following` stays untouched.
10. **Target selection by tapping a person** in the dashboard (planned, not built yet).
11. **No access control** (trusted lab network).
12. **Milestones:** (1) dashboard and manual driving, done. (2) Policy worker protocol, done; porting the old following pipeline to it, not done. (3) Recording and latency analysis, not started.
