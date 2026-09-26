# Writing and running a policy

A **policy** is any program that decides how the car moves: a learned RL model, a person-following pipeline, or a ten-line rule. In this system a policy runs as a **worker**, an ordinary Python program on a laptop. It connects to the hub over the network, receives lidar scans and odometry, and sends velocity commands back. The worker needs no ROS, and the car needs no knowledge of the policy.

```
worker (tasl-l1 GPU, or any laptop)          hub on the car
  on_scan(scan) ──► your model ──► send_cmd(vx, vy, wz) ──► checks: given control? E-STOP? limits? ──► wheels
```

The protocol itself is in [API.md](API.md#policy-worker-messages). This page uses the Python client `worker/rc_worker.py`, a single file whose only dependency is tornado.

## 1. Set up the environment

### On tasl-l1 (recommended: GPU, ROS 2 and the code are already there)

The code is in `~/rosmaster-console`. **Use the system Python, `/usr/bin/python3` (3.8).** The interactive shell on tasl-l1 starts conda's `base` environment, whose `python3` is 3.13 and has no tornado. `rc_worker` stops with an explanation if you use it by mistake.

```bash
cd ~/rosmaster-console
/usr/bin/python3 -c "import tornado, numpy, torch; print(tornado.version, numpy.__version__, torch.__version__, torch.cuda.is_available())"
# expected: 6.4 1.22.4 2.3.0+cu121 True
```

The system Python already has tornado 6.4, numpy 1.22.4, PyTorch 2.3.0 with CUDA 12.1 (RTX 3070), OpenCV 4.10 and ROS 2 Foxy's `rclpy`. Alternatively, run `conda deactivate` first and then `python3` is the system one. If you create your own conda or venv environment, `pip install tornado` in it.

### On your own laptop (macOS, Linux or Windows)

Python ≥ 3.8 and tornado:

```bash
python3 -m venv ~/rc-venv && source ~/rc-venv/bin/activate
pip install tornado numpy        # plus whatever your model needs (torch, ...)
```

Copy the `worker/` folder from the code snapshot in the handoff package (or copy it from tasl-l1: `scp -r lee@<tasl-l1 IP>:rosmaster-console/worker .`). Nothing else from the repository is needed. The laptop must be on the car's network, and your worker then connects to `ws://rosmaster.local:8080/ws` (or `--url ws://<car IP>:8080/ws`).

## 2. Try the examples

Turn on **Base** and **Lidar** in the dashboard first.

```bash
cd ~/rosmaster-console/worker
/usr/bin/python3 examples/print_nearest.py        # read-only: prints the nearest obstacle, never drives
```

The worker appears in the dashboard's **Policy** card as `print-nearest`, and a cyan marker follows the nearest point on the top-down view.

Now one that drives. Put the car in open space, stand next to it with the dashboard open, and keep the E-STOP within reach:

```bash
/usr/bin/python3 examples/keep_distance.py --distance 1.0 --max-speed 0.15
```

Nothing moves until you press **Give control** next to `keep-distance` in the Policy card. Then put a box or your hand in front of the car: it keeps 1 m to whatever is straight ahead (within ±15°). Touch the joystick to take over; press **Stop policy** to end it. Ctrl-C in the terminal also stops the car, because the hub stops it the moment the worker disconnects.

| Example | What it shows |
|---|---|
| `examples/print_nearest.py` | Reading scans, converting to points, debug markers. Never drives. |
| `examples/keep_distance.py` | A complete driving policy: control from each scan, `obs=` for latency, debug text |
| `examples/ros_cmd_vel_bridge.py` | Forwarding an existing ROS pipeline's `/cmd_vel` to the hub (see [section 6](#6-running-the-old-human-following-pipeline)) |

## 3. Write your own

```python
import math, sys
sys.path.insert(0, '/home/lee/rosmaster-console/worker')   # wherever rc_worker.py is
from rc_worker import Worker

class MyPolicy(Worker):
    def on_scan(self, scan):
        pts = self.scan_points(scan, max_range=5.0)     # [(x, y), ...] m, car frame: x forward, y left
        vx, vy, wz = 0.1, 0.0, 0.0                       # your decision
        self.send_cmd(vx, vy, wz, obs=scan)              # obs= lets the hub measure latency
        self.send_debug([{'x': 1.0, 'y': 0.0, 'kind': 'goal', 'label': 'goal'}], 'going forward')

    def on_active(self, active):
        print('control', 'given' if active else 'taken back')

MyPolicy(url='ws://rosmaster.local:8080/ws', name='my-policy').run()   # blocks; reconnects by itself
```

**Callbacks** run on the WebSocket thread. Override the ones you need:

| Callback | Called with | Rate |
|---|---|---|
| `on_connect()` | Registered with the hub; `self.worker_id` and `self.config` are set | once per connection |
| `on_scan(scan)` | `{seq, stamp, amin, ainc, cm}`: see [API.md](API.md#scan) | ~6.7 Hz |
| `on_odom(odom)` | `{seq, stamp, x, y, yaw, vx, vy, wz}` | ~10 Hz (Base on) |
| `on_state(state)` | Full hub state: mode, sensors, battery, workers | 5 Hz |
| `on_active(active)` | A dashboard gave (`True`) or took back (`False`) control | on change |

**Attributes:**
- `self.active`: this worker has control.
- `self.driving`: the last command actually reached the wheels.
- `self.state`: the latest hub state.
- `self.config`: `laser_x`, `laser_yaw`, `max_linear`, `max_angular`, …

**Methods** (all thread-safe):
- `send_cmd(vx, vy, wz, obs=None)`
- `send_debug(markers, text)`
- `stop()`
- `scan_points(scan, max_range=None)`

**Slow models.** Keep callbacks well under the ~150 ms between scans. For a model that takes longer, or to decouple inference, store the latest observation and run the model in your own thread:

```python
import threading, time, torch

class GpuPolicy(Worker):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.latest = None
        self.model = torch.jit.load('policy.pt').cuda().eval()
        threading.Thread(target=self.loop, daemon=True).start()

    def on_scan(self, scan):
        self.latest = scan                          # just store it

    def loop(self):
        while True:
            scan = self.latest
            if scan is not None:
                self.latest = None
                obs = torch.tensor([c / 100.0 for c in scan['cm']], device='cuda')
                with torch.no_grad():
                    vx, vy, wz = self.model(obs[None])[0].tolist()
                self.send_cmd(vx, vy, wz, obs=scan)
            time.sleep(0.01)
```

**Camera.** Images are not sent over the WebSocket. Read the MJPEG stream directly, for example with OpenCV: `cv2.VideoCapture('http://rosmaster.local:8080/camera.mjpg')`. It needs Camera on and gives 640×480 at up to 15 fps.

**Debug markers** are drawn on every open dashboard's top-down view for 2 s after each `send_debug`. Positions are in the car frame. `kind` sets the colour: `human` orange, `target` magenta, `goal` green, `point` cyan. `text` appears under the Policy card. This is the quickest way to see what your policy "thinks".

## 4. Rules the hub enforces (you can't break them)

- **Nothing moves until a person presses Give control.** Before that, and after **Stop policy**, commands are acknowledged with `accepted: false`.
- **Manual beats policy.** Any joystick or keyboard input takes over at once. The worker stays active but not driving (`self.active == True`, `self.driving == False`) until someone presses **Hand back to policy**. The same applies after an E-STOP is released. Watch `self.driving`, not only `on_active`.
- **Keep sending.** If no command arrives for 0.5 s, the hub commands zero. Send on every scan even when the answer is "stand still" (`send_cmd(0, 0, 0)`).
- **Limits:** 0.7 m/s (combined vx, vy), 1.5 rad/s, acceleration 1.5 m/s² and 4 rad/s². Braking is immediate. If your policy was trained with a higher speed or acceleration, the car will be slower than it expects.
- **Disconnect = stop.** If the active worker's connection drops, the car stops at once and control must be given again after reconnecting.
- Only one worker is active at a time. Giving control to another one takes it away from the first.

## 5. Latency and testing

**Latency.** The Policy card, and `state.workers[].latency_ms`, show the median time from an observation reaching the hub to the command computed from it coming back. That includes the Wi-Fi round trip and your compute. It appears only if you pass `obs=scan` (or `obs=odom`) to `send_cmd`. For reference, the hub adds under 1 ms, and a WebSocket-to-`/hub/cmd_vel` trip over home Wi-Fi measured 12 ms median.

**Test without the car.** On tasl-l1 you can run a hub in test mode, which touches no hardware and refuses Wi-Fi changes:

```bash
cd ~/rosmaster-console
bash scripts/test_hub.sh start          # http://localhost:8091, ROS domain 77, no sensors
/usr/bin/python3 worker/examples/keep_distance.py --url ws://localhost:8091/ws
# open http://localhost:8091 (tasl-l1's browser), press Give control, watch Output
ROS_DOMAIN_ID=77 /usr/bin/python3 scripts/test_policy_api.py localhost:8091   # 14 automated checks
bash scripts/test_hub.sh stop
```

The test hub has no scans, so `on_scan` never fires there. Use it to test connection, control hand-over and command flow. To test logic that reacts to scans, call `on_scan` yourself with recorded or synthetic scans.

**First real run checklist:**
1. Battery above 11 V.
2. Wheels off the ground (the car on a box) for the first run of a new policy.
3. `--max-speed` or your own clamp set low (0.1–0.2 m/s).
4. Dashboard open on a phone in your hand. The E-STOP works from any open dashboard.
5. Give control, watch, and take over by touching the joystick.

## 6. Running the old human-following pipeline

The research pipeline from the paper lives on tasl-l1 in `~/human-following` (ROS 2 package workspace `ros2_following/`, launcher `scripts/start_real_robot.sh`). It is a chain of ROS nodes on tasl-l1: DR-SPAAM person detection from lidar, SORT tracking, trajectory prediction, occupancy grid, and the RL decider. The decider publishes `/cmd_vel`, **which the car no longer obeys**. Two ways to use it now:

### A. Unchanged, through the bridge (works today; not yet tried on the real car)

1. In the dashboard: **Base** on, **Lidar** on. The car now publishes `/scan` and `/odom` on ROS domain 32. From tasl-l1, `ros2 topic hz /scan` should show ~6.7 Hz (same network as the car).
2. On tasl-l1, start the pipeline as before, but with the car's current IP. The script pings that IP first:
   ```bash
   ROBOT_IP=<car IP> METHOD=ours SPEED=slow PREF=0 bash ~/human-following/scripts/start_real_robot.sh
   ```
   **Ignore the script's own advice about the car.** If it reports `/scan` missing and tells you to `docker exec ... x3 ... laser_bringup_launch.py`, don't: `x3` must stay stopped under the new system. Its "LiDAR recovery" step, which does `docker exec x3 ...` over SSH, simply fails now. Instead, fix the lidar from the dashboard. If `/scan` is on in the dashboard but not visible on tasl-l1, the network is blocking DDS discovery ([TERMINAL.md](TERMINAL.md#6-watch-ros-topics-tasl-l1)): use the car's hotspot.
3. Start the bridge, which forwards `/cmd_vel` to the hub:
   ```bash
   /usr/bin/python3 ~/rosmaster-console/worker/examples/ros_cmd_vel_bridge.py
   ```
   Every 5 s it prints how many `/cmd_vel` messages arrived and from which ROS nodes. If a node other than the decider shows up, find out why before giving control.
4. In the dashboard: **Give control** to `ros-bridge`.
5. Stop: **Stop policy** or the joystick, then `bash ~/human-following/scripts/stop_real_robot.sh` and Ctrl-C the bridge.

The bridge was tested against the test hub only: 10 Hz forwarding, manual override, hand back, and a zero command within 0.5 s of the publisher stopping. Two things are not re-verified since the car moved to the new setup: that `/scan` and `/odom` reach tasl-l1 over Wi-Fi, and the whole chain on the real car. `SPEED=fast` (1 m/s) is capped at 0.7 m/s by the hub.

### B. Port it to a worker (the planned next step, not started)

Milestone 2 intended to replace the ROS chain with a single worker. The worker would rebuild the `LaserScan` ranges from `cm` (`r = cm/100`, same `amin`/`ainc`), run DR-SPAAM, the tracker, the predictor and the RL model directly in PyTorch on tasl-l1's GPU, and send debug markers for the detected people (`human`), the chosen target (`target`) and the goal (`goal`). This removes DDS from the loop: only the WebSocket crosses the network. It also gives exact observation-to-command latency. The model weights are in `~/human-following/ros2_following/decider/model_weight/` (RL policies, e.g. `meta_4.pt` for "Ours") and `~/human-following/ros2_following/dr_spaam_ros2/model_weight/dr_spaam_e40.pth` (person detector). Tap-to-select-target in the dashboard was planned to go with this.
