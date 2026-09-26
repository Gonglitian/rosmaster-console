# Quick start

Read this first. It gets you from a switched-off car to driving it from a browser, and back to a safely charged car. Everything here is done in the **dashboard**: a web page that the car serves itself. No software needs to be installed on your laptop or phone.

## What you have

| Item | Notes |
|---|---|
| **The car**: Yahboom ROSMASTER X3 | Mecanum wheels, so it can drive sideways and turn on the spot. A Raspberry Pi 5 inside runs everything. On top: an RPLIDAR-A1 laser scanner (the "lidar"). At the front: an Orbbec depth camera (only its colour stream is used). A small OLED screen shows the car's IP address. |
| **Battery and charger** | 3-cell lithium pack, 12.6 V when full. The main power switch is on the expansion board. |
| **tasl-l1**: the lab laptop | Ubuntu 20.04, RTX 3070 GPU, ROS 2 Foxy installed. User `lee`; ask Litian for the password. Code lives in `~/rosmaster-console`. It is the development machine: this is where you write and run your own policy (see [POLICY.md](POLICY.md)). |

## Addresses and passwords

These are lab defaults. Do not post them publicly.

| What | Value |
|---|---|
| Dashboard | `http://rosmaster.local:8080`, or `http://<car IP>:8080` |
| Car's own Wi-Fi hotspot | network `ROSMASTER`, password `12345678`; the car is then at `192.168.1.11` |
| Log in to the car over SSH | `ssh pi@rosmaster.local`, password `1234` (from tasl-l1 no password is needed: it has a key) |

`rosmaster.local` is the car's name on the local network. Your laptop finds it by asking the network (a protocol called mDNS), so it works even when the car's IP changes, but only when your laptop and the car are on the same network.

## From off to driving

1. **Check the battery.** A charged pack reads 12.0–12.6 V. The dashboard shows the charge once the car is on; at 10.5 V or below, charge first.
2. **Switch the car on** with the main power switch. The Raspberry Pi takes about a minute to boot, and the dashboard comes up about a minute after that.
3. **Get your laptop or phone onto the same network as the car.** One of two things happens by itself:
   - **The car knows a Wi-Fi network here and joins it.** It joins `TASL` (the lab network; highest priority) or `TP-Link-1700` (Litian's home) by itself. Put your laptop on the same Wi-Fi and open `http://rosmaster.local:8080`. If your lab uses another network (the older setup used `TASL_5G-1`), connect the car to it once from the Network tab; after that it joins by itself.
   - **It knows none, so it opens its own hotspot `ROSMASTER`.** Join `ROSMASTER` (password `12345678`) and open `http://192.168.1.11:8080`. To put the car on the local Wi-Fi, use the **Network** tab: see [DASHBOARD.md](DASHBOARD.md#network-tab).

   If neither address opens, read the IP on the car's OLED screen and open `http://<that IP>:8080`.
4. **Turn the sensors on.** In the **Drive** tab, press **Base** (the wheel driver; ready in about 2 s), then **Lidar** (about 4–10 s; it sometimes retries on its own). **Camera** is optional.
5. **Drive.** Use the on-screen joysticks (left: move, right: rotate) or the keyboard (W/S forward and back, A/D sideways, Q/E rotate). The **Max speed** slider starts at 0.3 m/s. The first time, drive slowly in open space.
6. **Stop.**
   - Letting go of the joystick or keys stops the car straight away.
   - The big red **E-STOP** button (or the Esc key) stops it and keeps it stopped until someone presses **Release E-STOP**.
   - If the dashboard is closed or loses the connection, the car stops within half a second.

## Finishing

1. Turn **Lidar**, **Camera** and **Base** off. The lidar motor really stops, which saves battery.
2. Switch the car off with the main power switch.
3. **Charge only with the car switched off.** This is Yahboom's rule. The charger LED is red while charging and turns green when the pack is full; unplug it then.

## Safety rules

- Only the dashboard can make the car move; see [ARCHITECTURE.md](ARCHITECTURE.md#safety-model). But anyone on the same network can open the dashboard, because there is no login. Keep an eye on who has it open.
- **Battery:**
  - Stop at 10.0 V.
  - At 9.6 V the car's board beeps: switch the car off and charge it.
  - At 9.0 V the software engages E-STOP by itself.
- **Not yet verified:** that the software watchdog stops wheels that are already turning, and what the chassis board does if the software on the Pi dies completely. Before long or fast runs, do the wheels-off-the-ground test in [OPERATIONS.md](OPERATIONS.md#not-yet-verified).

## Where to go next

| Want to… | Read |
|---|---|
| Learn every button in the dashboard, including Wi-Fi setup | [DASHBOARD.md](DASHBOARD.md) |
| Do things from a terminal, e.g. when the dashboard does not load | [TERMINAL.md](TERMINAL.md) |
| Understand how the system is built | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Write a policy that drives the car | [POLICY.md](POLICY.md) and [API.md](API.md) |
| Update the software, run the tests, or look up known problems | [OPERATIONS.md](OPERATIONS.md) |
