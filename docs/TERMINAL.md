# Terminal fallback

Use the dashboard ([DASHBOARD.md](DASHBOARD.md)) whenever you can. This page is for when it does not load, or when you need something it does not offer. Everything below works from **tasl-l1** or from **your own laptop** (macOS, Linux, or Windows with PowerShell's `ssh`/`curl`). Commands marked *(tasl-l1)* need ROS 2 Foxy, which only tasl-l1 has.

Placeholders are in angle brackets: `<car IP>`, `<SSID>` (a Wi-Fi network name), `<password>`.

## 1. Find the car

The car and your laptop must be on the **same network**. Either both are on one Wi-Fi network, or your laptop is on the car's hotspot `ROSMASTER`.

| Try | Command |
|---|---|
| By name (mDNS) | `ping rosmaster.local` |
| On the car's hotspot | `ping 192.168.1.11` |
| Read the IP from the car | The small OLED screen on the car shows its current IP. |
| Scan the subnet (Linux/macOS, needs `nmap`) | `nmap -sn 192.168.68.0/22`, using your own subnet, then look for `raspberrypi`/`rosmaster` or a Raspberry Pi MAC address |

If `ping rosmaster.local` fails but the IP works, the network blocks mDNS: use the IP everywhere below instead of `rosmaster.local`. If neither works, see [Can't reach the car at all](#cant-reach-the-car-at-all).

## 2. Get your laptop onto the car's hotspot

The car opens the hotspot by itself when it finds no known Wi-Fi network after power-on (details in [DASHBOARD.md](DASHBOARD.md#which-network-the-car-picks-after-power-on)). Network `ROSMASTER`, password `12345678`, car at `192.168.1.11`. The hotspot is 2.4 GHz and has no internet.

| Laptop | Command |
|---|---|
| macOS | `networksetup -setairportnetwork en0 ROSMASTER 12345678` (or use the Wi-Fi menu) |
| Ubuntu / tasl-l1 | `nmcli dev wifi connect ROSMASTER password 12345678` (tasl-l1 already has it saved: `nmcli con up id ROSMASTER`) |
| Windows | Wi-Fi menu in the taskbar, or `netsh wlan connect name=ROSMASTER` once the profile exists |

To go back afterwards, rejoin your normal network the same way (on Ubuntu: `nmcli con up id "<SSID>"`).

### tasl-l1 on two networks

tasl-l1 has a single Wi-Fi card (`wlo1`), so joining `ROSMASTER` drops its internet. Options, best first:
1. **Put the car on the same Wi-Fi as tasl-l1** (dashboard Network tab, or [section 5](#5-wi-fi-setup-from-the-terminal)). Then there's no conflict.
2. **Ethernet for internet, Wi-Fi for the car.** Plug a cable into tasl-l1's Ethernet port (`eno2`) and join `ROSMASTER` over Wi-Fi. Keep the internet route on the cable with:
   ```bash
   nmcli con mod ROSMASTER ipv4.never-default yes   # once; the car's hotspot never becomes the default route
   ```
3. **A USB Wi-Fi dongle** as a second card. One card on the lab network, the other on `ROSMASTER` (with `never-default` as above).

## 3. Log in to the car

```bash
ssh pi@rosmaster.local        # or pi@<car IP>; password 1234 (tasl-l1 logs in with a key)
```

What runs where on the car ([ARCHITECTURE.md](ARCHITECTURE.md) has the full picture):
- The hub runs in the Docker container **`rc-hub`**. It starts at boot and restarts itself if it crashes.
- The code is in `~/rosmaster-console` on the car, mounted into the container at `/opt/rc`.
- Yahboom's original container **`x3`** is kept, but stopped. Never run both at once: they fight over the chassis serial port.

| Task | Command (on the car) |
|---|---|
| Is the hub running? | `docker ps` (expect `rc-hub` "Up"; `x3` must not be listed) |
| Hub log, live | `docker logs -f --tail 100 rc-hub` |
| Restart the hub (sensors go off) | `docker restart rc-hub` |
| Stop / start the hub | `docker stop rc-hub` / `docker start rc-hub`. A stopped hub stays stopped across reboots until started. |
| Rebuild the image and recreate the container | `bash ~/rosmaster-console/car/install_hub.sh` (idempotent, about 1 minute) |
| Is the dashboard served? | `curl -s localhost:8080/api/state \| head -c 300` |
| Disk space | `df -h /` and `docker system df` |
| Shut the car down cleanly | `sudo poweroff` (password `1234`), wait about 20 s, then switch off |

## 4. Control the car with curl

The hub has a small HTTP API next to the dashboard. The full reference is in [API.md](API.md). These calls work from any laptop on the car's network, and from the car itself with `localhost`.

```bash
CAR=rosmaster.local:8080          # or <car IP>:8080, or 192.168.1.11:8080 on the hotspot

curl -s http://$CAR/api/state | python3 -m json.tool | head -40      # mode, sensors, battery, workers
curl -s -X POST http://$CAR/api/estop                                # E-STOP (latches)
curl -s -X POST http://$CAR/api/estop -d '{"release": true}'         # release it
curl -s -X POST http://$CAR/api/sensor -d '{"name": "base",  "on": true}'
curl -s -X POST http://$CAR/api/sensor -d '{"name": "lidar", "on": false}'
curl -s http://$CAR/api/battery | python3 -m json.tool | head -30
```

There is deliberately no HTTP call that drives the car. Driving needs a WebSocket client, which keeps sending commands: the hub stops the car 0.5 s after the last command. Such a client is the dashboard or a policy worker ([POLICY.md](POLICY.md)).

A command-line WebSocket probe that never drives, useful to test the WebSocket connection:

```bash
cd ~/rosmaster-console          # on tasl-l1 (use /usr/bin/python3 there; see POLICY.md)
python3 scripts/ws_probe.py --watch 10                 # print state/scan/odom rates for 10 s
python3 scripts/ws_probe.py --on base lidar            # switch sensors on and wait until they are
python3 scripts/ws_probe.py --host 192.168.1.11:8080 --off base lidar camera
```

## 5. Wi-Fi setup from the terminal

The dashboard's Network tab does all of this with undo and fallback ([DASHBOARD.md](DASHBOARD.md#network-tab)). By hand you get no safety net, so read each step before running it.

### 5a. Through the hub's API (no SSH needed)

This works from any laptop that can reach the car, for example over the hotspot. It uses the same code as the dashboard, including the automatic undo, and falls back to the hotspot on failure.

```bash
CAR=192.168.1.11:8080                                            # on the hotspot
curl -s http://$CAR/api/wifi | python3 -m json.tool | head -30  # current network + saved profiles
curl -s -X POST http://$CAR/api/wifi/scan | python3 -c 'import json,sys; [print(n["signal"], n["band"], n["ssid"]) for n in json.load(sys.stdin)["scan"]]'
curl -s -X POST http://$CAR/api/wifi/connect -d '{"ssid": "<SSID>", "password": "<password>"}'
```

The connect call answers at once and the car switches about 1.5 s later. Then move your laptop to `<SSID>` and find the car ([section 1](#1-find-the-car)). If the switch failed, the car is back on its previous network or on the hotspot, and `curl http://$CAR/api/wifi` shows the reason under `attempt`.

Other calls:
- `POST /api/wifi/hotspot`: switch to the car's own hotspot.
- `POST /api/wifi/forget` with `{"name": "<profile name>"}`: delete a saved network.

### 5b. With nmcli over SSH

The car uses NetworkManager, interface `wlan0`. Run these on the car after `ssh pi@...`. They need `sudo`, password `1234`.

```bash
# What is the car on, and what does it know?
nmcli -f DEVICE,STATE,CONNECTION dev
nmcli -f NAME,TYPE,AUTOCONNECT-PRIORITY con show
sudo nmcli dev wifi list --rescan yes            # nearby networks (5-10 s)

# Add a WPA/WPA2-Personal network with priority 60 (above the hotspot's 50)
sudo nmcli con add type wifi ifname wlan0 con-name "<SSID>" ssid "<SSID>" \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<password>" \
     connection.autoconnect yes connection.autoconnect-priority 60
# Open network: leave out the two wifi-sec.* settings.
# Hidden network: add  802-11-wireless.hidden yes

# Switch to it. If you are logged in over the hotspot, your SSH session drops here,
# so detach the command from the session:
sudo nohup nmcli --wait 45 con up id "<SSID>" > /tmp/wifi-switch.log 2>&1 &
```

Then move your laptop to `<SSID>` and find the car. **If the car does not show up within a minute**, the switch probably failed. The hotspot comes back only after a **power cycle**, because unlike the dashboard, nmcli does not fall back by itself. After power-on the car joins the highest-priority network in range, otherwise its hotspot. So a wrong password on a priority-60 profile costs one reboot, never the car.

Other recipes:

```bash
sudo nmcli con mod "<name>" connection.autoconnect-priority 60   # make a saved network win over the hotspot
sudo nmcli con mod "<name>" wifi-sec.psk "<new password>"        # fix a password
sudo nmcli con delete id "<name>"                                # forget a network (never delete ROSMASTER)
sudo nohup nmcli con up id ROSMASTER > /tmp/wifi-switch.log 2>&1 &   # back to the hotspot
```

The priorities are `TASL` 100, `TP-Link-1700` 60, `ROSMASTER` hotspot 50, and 0 for anything older. Wi-Fi power saving is disabled in `/etc/NetworkManager/conf.d/99-wifi-powersave-off.conf`, because it caused lag. Enterprise networks that need a username (802.1X, e.g. `UCR-SECURE`) are not supported by the dashboard and have not been tried by hand.

### Can't reach the car at all

1. **Power-cycle the car and wait 2 minutes.** It then joins a known network in range, or opens `ROSMASTER`. Look for `ROSMASTER` in your laptop's Wi-Fi list.
2. **Read the OLED screen.** It shows the IP the car has now.
3. Still nothing: plug an HDMI screen and a USB keyboard into the Raspberry Pi. Log in as `pi` / `1234`, open a terminal, and use the nmcli recipes above.

## 6. Watch ROS topics *(tasl-l1)*

The car's ROS 2 graph uses ROS domain **32**. tasl-l1's `~/.bashrc` already sources ROS 2 Foxy and sets `ROS_DOMAIN_ID=32`. From tasl-l1, on the same network as the car:

```bash
ros2 topic list                     # /scan /odom /voltage /imu/... appear once Base / Lidar are on
ros2 topic hz /scan                 # expect ~6.7 Hz with Lidar on
ros2 topic echo /hub/cmd_vel        # what the hub commands, 20 Hz
```

Reaching the car's topics from tasl-l1 over Wi-Fi uses DDS multicast discovery. Some networks block it; the car's hotspot does not. This was not re-checked after the car switched to its UDP-only DDS profile, so if `ros2 topic list` stays empty, use the car itself:

```bash
ssh pi@rosmaster.local
docker exec -it rc-hub bash -c 'source /opt/ros/foxy/setup.bash; export ROS_DOMAIN_ID=32 FASTRTPS_DEFAULT_PROFILES_FILE=/opt/rc/car/fastdds_udp.xml; ros2 topic hz /scan'
```

> **Never publish on `/hub/cmd_vel` yourself.** The chassis driver obeys that topic directly. Anything published there bypasses the hub's E-STOP, priorities and limits, and fights the hub's own 20 Hz stream. Drive through the dashboard or a policy worker only.

`/cmd_vel` (without `/hub/`) is ignored by the car. The old tasl-l1 pipeline publishes there, which is why it no longer drives the car: see [POLICY.md](POLICY.md#6-running-the-old-human-following-pipeline).

## 7. Fixing common problems

| Problem | What to do |
|---|---|
| **Lidar** start fails 3 times | The hub already replugs the lidar's USB in software on each attempt. Try once more. Then physically unplug and replug the lidar's USB cable, and press **Lidar** again. Look for `failed set request 0x12` in `sudo dmesg \| tail`: that is the lidar's USB serial chip (CP2102) hanging. |
| **Base**: "Another chassis driver is running" | Something else owns the chassis. `docker ps`: if `x3` is running, `docker stop x3`. Otherwise run `sudo fuser -v /dev/myserial` and stop that process. |
| Hub keeps restarting | `docker logs --tail 200 rc-hub` shows the Python traceback. The last known-good code is the previous git commit on tasl-l1: redeploy it (see below). |
| Disk full | `df -h /`. Old logs: `sudo journalctl --vacuum-size=200M`. **Never run `docker system prune -a` or `docker image prune -a`.** They delete `rc-base` and Yahboom's `x3` image, which cannot be downloaded again. Both images show as "reclaimable" because they are not in use at that moment. |
| The car drives but the dashboard shows no measured speed | Switch **Base** off and on. |

### Deploy or roll back the hub code *(tasl-l1)*

```bash
cd ~/rosmaster-console
git log --oneline | head -5              # the code history (local repository only)
bash scripts/deploy_car.sh               # copy this checkout to the car and restart the hub
git checkout <older commit> -- . && bash scripts/deploy_car.sh   # roll back (then `git checkout HEAD -- .` to return)
curl -s http://rosmaster.local:8080/ | grep -o '<title>.*</title>'   # expect <title>RosMaster Console</title>
```

Details in [OPERATIONS.md](OPERATIONS.md).

## 8. Going back to Yahboom's original setup

For demos of the old pipeline, or Yahboom's own tools. Never run both at once.

```bash
# on the car
docker stop rc-hub && docker start x3     # old setup: x3 listens on /cmd_vel, no dashboard
# ... use the old workflow (tasl-l1: ~/human-following/scripts/start_real_robot.sh) ...
docker stop x3 && docker start rc-hub     # back to the dashboard
```

The old `x3` container has none of the new protections. Anything on the network can publish `/cmd_vel`, and the car keeps the last speed if commands stop. Keep the wheels off the ground, or stay within reach of the power switch.
