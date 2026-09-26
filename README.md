# rosmaster-console

Drive and debug the Yahboom ROSMASTER X3 car from a browser, and let a policy running on any laptop drive it.

The car runs a small service, the **hub**. Open `http://rosmaster.local:8080` on a phone or laptop on the same network to get the **dashboard**:
- a live lidar top-down view and the camera
- joysticks and an E-STOP
- sensor switches
- a battery estimate
- Wi-Fi setup

Policies run as **workers**, plain Python programs on tasl-l1 (GPU) or any laptop. A worker connects to the hub, receives scans and odometry, and sends velocity commands. It drives only after someone presses **Give control** in the dashboard. Laptops and phones need no ROS; ROS 2 runs only on the car.

## Documentation

| Read | For |
|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | **Start here.** From a switched-off car to driving, and back. |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | Every part of the dashboard, including Wi-Fi setup in a new place |
| [docs/TERMINAL.md](docs/TERMINAL.md) | When the dashboard is not enough: SSH, Docker, curl, nmcli Wi-Fi recipes, recovery |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is built: processes, topics, control path, safety model, design decisions |
| [docs/POLICY.md](docs/POLICY.md) | Writing and running a policy; running the old human-following pipeline |
| [docs/API.md](docs/API.md) | HTTP endpoints, WebSocket messages, formats, settings |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Develop, deploy, test; known issues; what is not verified yet |

## Layout

```
car/        on-car: Docker image, install script, supervisor, launch files, safe chassis driver
hub/        the hub (Python 3.8, tornado)
web/        the dashboard (plain HTML/JS, no build step)
worker/     rc_worker.py (policy client) and examples/
scripts/    deploy, test hub, probes and end-to-end tests (run on tasl-l1)
tests/      unit tests (no ROS needed)
docs/       documentation
```

## Common commands (on tasl-l1)

```bash
bash scripts/deploy_car.sh                          # push this checkout to the car, restart the hub
bash scripts/deploy_car.sh --install                # first install, or after changing car/Dockerfile
/usr/bin/python3 -m unittest discover tests         # unit tests
bash scripts/test_hub.sh start                      # hub without the car at http://localhost:8091
/usr/bin/python3 worker/examples/keep_distance.py   # example policy (press Give control in the dashboard)
```
