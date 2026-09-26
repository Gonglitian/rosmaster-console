"""Client library for writing a policy that drives the RosMaster car through the hub.

A *worker* is any program that connects to the hub's WebSocket, receives the
car's sensor data and sends velocity commands. It needs no ROS. The hub only
executes a worker's commands after someone presses "Give control" for it in the
dashboard, and it still enforces the e-stop, speed/acceleration limits, the
0.5 s command timeout and manual override. Protocol: docs/API.md.

Minimal use:

    from rc_worker import Worker

    class MyPolicy(Worker):
        def on_scan(self, scan):
            pts = self.scan_points(scan)            # [(x, y), ...] metres, base_link
            self.send_cmd(0.1, 0.0, 0.0, obs=scan)  # vx, vy (m/s), wz (rad/s)

    MyPolicy(name='my-policy').run()

Callbacks run on the WebSocket thread: keep them short (well under the ~150 ms
between scans). Put slow work in a thread and call send_cmd from there; it is
thread-safe.

Requires Python >= 3.8 and tornado >= 6.1 (pip install tornado).
"""
import json
import logging
import math
import socket
import threading
import time

from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

log = logging.getLogger('rc_worker')


class Worker(object):
    def __init__(self, url='ws://rosmaster.local:8080/ws', name='policy', host=None):
        self.url = url
        self.name = name
        self.host = host or socket.gethostname()
        self.config = {}          # laser_x, laser_yaw, max_linear, max_angular, ... from the hub
        self.worker_id = None
        self.active = False       # True while the dashboard has given this worker control
        self.driving = False      # True if the last command was actually executed
        self.state = None         # latest hub state (5 Hz), see docs/API.md
        self._ws = None
        self._loop = None
        self._seq = 0

    # ---- callbacks: override the ones you need --------------------------
    def on_connect(self):
        """Connected and registered with the hub."""

    def on_scan(self, scan):
        """Every lidar scan (~6.7 Hz). Keys: seq, stamp, amin, ainc, cm (see scan_points)."""

    def on_odom(self, odom):
        """Every odometry message (~10 Hz). Keys: x, y, yaw, vx, vy, wz, stamp, seq."""

    def on_state(self, state):
        """Hub state (5 Hz): control mode, sensors, battery, ..."""

    def on_active(self, active):
        """The dashboard gave (True) or took back (False) control."""

    # ---- actions ----------------------------------------------------------
    def send_cmd(self, vx, vy, wz, obs=None):
        """Velocity in the car frame: vx forward, vy left (m/s), wz counter-clockwise
        (rad/s). Pass the scan/odom message the command was computed from as `obs`
        so the hub can measure observation-to-command latency. Commands are
        ignored unless this worker has control; send at least every 0.5 s while
        driving, or the hub stops the car."""
        self._seq += 1
        msg = {'t': 'policy_cmd', 'seq': self._seq, 'vx': float(vx), 'vy': float(vy), 'wz': float(wz)}
        if obs is not None:
            msg['obs_seq'] = obs.get('seq')
            msg['obs_stamp'] = obs.get('stamp')
        self._send(msg)

    def send_debug(self, markers=(), text=''):
        """Show things on the dashboard's top-down view. markers: list of
        {'x': m, 'y': m, 'kind': 'human'|'target'|'goal'|'point', 'label': str}
        in the car frame (x forward, y left). text: one short status line."""
        self._send({'t': 'policy_debug', 'markers': list(markers), 'text': str(text)})

    def stop(self):
        """Command zero velocity (still subject to having control)."""
        self.send_cmd(0.0, 0.0, 0.0)

    # ---- helpers ------------------------------------------------------------
    def scan_points(self, scan, max_range=None):
        """Scan -> list of (x, y) points in metres in the car frame (base_link).
        The lidar is mounted 0.0435 m ahead of the base, rotated by pi."""
        lx = self.config.get('laser_x', 0.0435)
        yaw = self.config.get('laser_yaw', 3.14)
        pts = []
        a0, da = scan['amin'] + yaw, scan['ainc']
        for i, cm in enumerate(scan['cm']):
            if not cm:
                continue
            r = cm / 100.0
            if max_range is not None and r > max_range:
                continue
            a = a0 + i * da
            pts.append((lx + r * math.cos(a), r * math.sin(a)))
        return pts

    # ---- plumbing -------------------------------------------------------------
    def _send(self, msg):
        data = json.dumps(msg)
        loop, ws = self._loop, self._ws
        if loop is None or ws is None:
            return
        loop.add_callback(self._write, ws, data)   # thread-safe hand-off to the IOLoop

    @staticmethod
    def _write(ws, data):
        try:
            ws.write_message(data)
        except Exception:
            pass

    async def _session(self):
        ws = await websocket_connect(self.url, ping_interval=5, ping_timeout=15)
        self._ws = ws
        ws.write_message(json.dumps({'t': 'worker_hello', 'name': self.name, 'host': self.host,
                                     'protocol': 1}))
        while True:
            raw = await ws.read_message()
            if raw is None:
                break
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            t = m.get('t')
            try:
                if t == 'scan':
                    self.on_scan(m)
                elif t == 'odom':
                    self.on_odom(m)
                elif t == 'state':
                    self.state = m
                    self.on_state(m)
                elif t == 'worker_welcome':
                    self.worker_id, self.config = m['worker_id'], m.get('config', {})
                    self.active = bool(m.get('active'))
                    log.info('registered as %s with the hub at %s', self.worker_id, self.url)
                    self.on_connect()
                elif t == 'policy_active':
                    self.active = bool(m.get('active'))
                    log.info('control %s by the dashboard', 'GIVEN' if self.active else 'TAKEN BACK')
                    self.on_active(self.active)
                elif t == 'policy_ack':
                    self.driving = bool(m.get('driving'))
                elif t == 'error':
                    log.warning('hub: %s', m.get('msg'))
            except Exception:
                log.exception('error in %s callback', t)
        self._ws = None

    def run(self, reconnect=True):
        """Blocking. Reconnects every 2 s if the hub goes away (Ctrl-C to quit)."""
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
        self._loop = IOLoop.current()

        async def forever():
            while True:
                try:
                    await self._session()
                    log.warning('hub closed the connection')
                except Exception as e:
                    log.warning('cannot reach the hub at %s: %s', self.url, e)
                self.active = self.driving = False
                if not reconnect:
                    return
                await _sleep(2.0)
        try:
            self._loop.run_sync(forever)
        except KeyboardInterrupt:
            pass


async def _sleep(seconds):
    import tornado.gen
    await tornado.gen.sleep(seconds)


def run_in_thread(worker):
    """Start `worker.run()` in a background thread; returns the thread."""
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    time.sleep(0.1)
    return t
