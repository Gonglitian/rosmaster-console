# Operations: develop, deploy, test, known issues

## Development workflow

All development happens on **tasl-l1** in `~/rosmaster-console`. It is a git repository, also public on GitHub (see [Git](#git)). The car only ever receives copies.

```
edit on tasl-l1 ──► unit tests / test hub ──► git commit ──► scripts/deploy_car.sh ──► car restarts hub
```

| You changed | Deploy with | Why |
|---|---|---|
| `hub/`, `web/`, `car/launch/`, `car/nodes/`, `car/entrypoint.sh` | `bash scripts/deploy_car.sh` | rsyncs the checkout to `pi@rosmaster.local:rosmaster-console/` (mounted at `/opt/rc` in the container) and runs `docker restart rc-hub` |
| `car/Dockerfile`, `car/install_hub.sh` (image or `docker run` flags) | `bash scripts/deploy_car.sh --install` | also rebuilds the `rc-hub` image and recreates the container (about 1 minute) |
| `worker/` only | nothing | workers run on your machine |

- **Car address.** The script uses `pi@rosmaster.local`. Override it with `CAR=pi@<car IP> bash scripts/deploy_car.sh`.
- **Restarts.** A restart switches all sensors off and disconnects every dashboard and worker. They reconnect by themselves in a few seconds.
- **Left alone.** The rsync uses `--delete` but excludes `.git`, `.state` (the car's Wi-Fi IP book) and `__pycache__`.

**Check after a deploy:**

```bash
curl -s http://rosmaster.local:8080/ | grep -o '<title>.*</title>'      # <title>RosMaster Console</title>
curl -s http://rosmaster.local:8080/api/state | head -c 200; echo
ssh pi@rosmaster.local 'docker logs --tail 20 rc-hub'
```

## Tests

| Test | Where | Needs | Moves the car? |
|---|---|---|---|
| `/usr/bin/python3 -m unittest discover tests` (or `python3 -m pytest tests/`) | anywhere | nothing (no ROS) | no |
| `scripts/test_policy_api.py [host:port]` | tasl-l1, `ROS_DOMAIN_ID` of the hub | Base **off** (it refuses otherwise) | no |
| `scripts/test_control_path.py [host:port]` | tasl-l1, domain 32 | Base **off** | no |
| `scripts/latency_probe.py [trials]` | tasl-l1, domain 32 | Base **off** | no |
| `scripts/test_base_restart.py` | tasl-l1 (uses ssh to the car) | Lidar may be off | no (toggles Base, never sends velocity) |
| `scripts/ws_probe.py --watch 10` | anywhere | | no |

The first three send manual commands and read what the hub publishes on `/hub/cmd_vel` over DDS. With Base off, no driver turns those commands into motion, and the scripts refuse to run if anything subscribes to `/hub/cmd_vel`. Run them on the same network as the car. The two tests check:
- **`test_control_path.py`:** the speed clamp, the acceleration limit, the 0.5 s timeout, E-STOP and the 20 Hz rate.
- **`test_policy_api.py`:** 14 checks of the worker protocol.

State at handoff (2026-09-26):
- The 28 unit tests pass.
- `test_policy_api.py` passes on the test hub.
- `test_control_path.py` passed on the car before the English and policy changes.
- The new version has not run on the car yet: see [Not yet verified](#not-yet-verified).

### Test hub without the car

`scripts/test_hub.sh` runs a real hub on tasl-l1 in test mode (`HF_NO_HARDWARE=1`):
- no sensors, serial, USB power control or Wi-Fi changes
- its own port (8091) and ROS domain (77), so it can't interfere with the car

```bash
bash scripts/test_hub.sh start     # then open http://localhost:8091
bash scripts/test_hub.sh log       # last 40 log lines
bash scripts/test_hub.sh stop
```

Use it for dashboard work and for testing workers. Sensor buttons show "test mode" errors; that's expected.

## Logs

| What | Where |
|---|---|
| Hub (Python logging: sensor starts, E-STOPs, who gave control, Wi-Fi switches) | `docker logs rc-hub` on the car |
| Child process output (sllidar, driver, EKF) | the same log, prefixed by the process |
| ROS logs | `/tmp/roslog` inside the container: RAM disk, lost on restart, on purpose (SD card) |
| Test hub | `/tmp/rc_test_hub.log` on tasl-l1 |

## Known issues

- **Lidar sometimes fails to start.** The lidar's USB serial chip (CP2102) intermittently stalls for 5 s on a control request (`dmesg`: `failed set request 0x12 status: -110`). The hub detects the failed start, replugs the USB device in software (sysfs unbind/bind) and retries, up to 3 attempts. Most starts succeed on the first or second attempt. If all three fail, press again or physically replug.
- **`/dev/ttyUSB0` and `ttyUSB1` swap between reboots.** Always use the aliases `/dev/myserial` (chassis) and `/dev/rplidar`.
- **Base on, "no /odom" warning.** Driving works, only measured speed is missing. It happened after very fast Base off/on cycles before the UDP-only DDS fix. If you see it, toggle Base.
- **Wi-Fi scan while on the hotspot** takes about 8 s, because the single radio has to pause the hotspot. The dashboard waits for it.
- **mDNS (`rosmaster.local`)** does not work on networks that block multicast. The car's IP is on the OLED screen and in the dashboard footer.
- **The car's clock.** The Raspberry Pi 5 has no battery-backed clock. It sets its time over the internet (NTP), so on the hotspot its clock may be wrong. All hub timestamps are car time. Compare them only with each other, never with your laptop's clock.
- **tasl-l1 Python.** The interactive shell starts conda `base` (Python 3.13, no tornado, no ROS). Use `/usr/bin/python3` or `conda deactivate`.
- **Speed limit.** The car was set to 0.7 m/s, the value used in the paper. Older launch files on tasl-l1 default to 1.0 or 0.8 m/s; those are not used by this system.
- **SD card nearly full** (about 2.8 GB free). Don't record data or install packages on the car. Never prune Docker images (see [TERMINAL.md](TERMINAL.md#7-fixing-common-problems)).
- **Yahboom's phone app is disabled** because it held the chassis serial port. To restore it, delete the `Hidden=true` line from `~/.config/autostart/rosmaster.desktop` on the car (backup: `rosmaster.desktop.orig-2026-09-26`) and stop `rc-hub`. The two can't run together.

## Not yet verified

Things that are designed and implemented but not yet confirmed on the real car. Please do these, in this order, and write down what happened.

1. **Deploy the current version to the car**, if the handoff note says it hasn't been done ([deploy](#development-workflow)). The dashboard title must read "RosMaster Console" with Drive / Battery / Network tabs in English. Then run `test_control_path.py` and `test_policy_api.py` against the car with Base off.
2. **Wheels-off-the-ground stop tests.** Put the car on a box so the wheels spin freely. Switch Base on. For each test, drive at about 0.2 m/s with the joystick or `keep_distance.py`, then:

   | Test | How | Expected |
   |---|---|---|
   | Dashboard goes away | Close the browser tab while holding the joystick | Wheels stop within 0.5 s |
   | Worker goes away | Ctrl-C the worker while it drives | Wheels stop at once |
   | Hub crash | On the car: `docker exec rc-hub pkill -9 -f "python3 -m hub"` | Wheels stop within 0.5 s (driver watchdog). The hub is back within about 5 s with all sensors off. |
   | Whole container killed | On the car: `docker kill rc-hub`, with a hand on the power switch | **Unknown.** It depends on whether the chassis board (STM32) keeps the last speed when serial commands stop. Switch off if the wheels keep turning. Restart with `docker start rc-hub`. |
3. **Wi-Fi switch round trip.** From the hotspot, connect the car to a Wi-Fi network in the Network tab, move the laptop, and check that the page jumps to the car by itself. The bug where it did not jump was fixed, but the whole flow was not retested afterwards.
4. **Lab network.** Is `rosmaster.local` reachable on the lab Wi-Fi (mDNS allowed)? Do `/scan` and `/odom` reach tasl-l1 there (`ros2 topic hz /scan`)?
5. **The old pipeline through the bridge** ([POLICY.md](POLICY.md#6-running-the-old-human-following-pipeline)), wheels off the ground first.
6. **Battery facts.** Check the pack label and the charger:
   - chemistry: INR (NMC lithium-ion, which the estimate assumes) or ICR/LiPo
   - capacity in mAh
   - charger output current

   Then set the capacity and current in the Battery tab. The 3S NMC voltage curve in `hub/battery.py` is an assumption.

## Git

- `~/rosmaster-console` on tasl-l1 is the working copy. `git log --oneline` gives the story, one commit per feature or fix.
- The repository is public on GitHub: https://github.com/Gonglitian/rosmaster-console (`origin` in `~/rosmaster-console`). Anyone can clone it; to push, ask Litian to add you as a collaborator.
- Pushing from tasl-l1 needs GitHub credentials there. The old `gh` login on tasl-l1 has expired: run `gh auth login` with your own account, or push from your laptop instead.
- For your own work, make a branch (`git switch -c <your-name>/<topic>`) and commit there.

Litian also has the original design notes (in Chinese). These English documents supersede them.
