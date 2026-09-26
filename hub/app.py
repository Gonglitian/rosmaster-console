"""The hub: web panel, WebSocket protocol, control loop.

Panel protocol (JSON text frames on /ws; full spec in docs/protocol.md):
  client -> hub: manual{vx,vy,wz,seq} | manual_release | hand_back | estop |
                 estop_release | sensor{name,on} | beep | ping{c}
  hub -> client: hello | state (5 Hz) | scan (every scan) | odom (every odom) |
                 pong{c,s} | ack{seq,s} | error{msg}
Times from the hub ('stamp', 's', 'server_time') are the car's wall clock.
"""
import asyncio
import json
import logging
import time

import tornado.iostream
import tornado.web
import tornado.websocket

from . import config, sysinfo
from .arbiter import Arbiter
from .battery import BatteryEstimator
from .camera import Camera
from .sensors import SensorManager

log = logging.getLogger('hub')
VERSION = '0.1.0'


class Hub(object):
    def __init__(self, loop, bridge_factory):
        self.loop = loop
        self.started = time.time()
        self.clients = {}
        self._next_client = 1
        self.arbiter = Arbiter(config.MAX_LINEAR, config.MAX_ANGULAR, config.MAX_LINEAR_ACCEL,
                               config.MAX_ANGULAR_ACCEL, config.SOURCE_TIMEOUT)
        self.camera = Camera(loop, config.CAMERA_DEVICE, config.CAMERA_SIZE)
        self.battery = BatteryEstimator()
        self.bridge = bridge_factory(loop, config.CMD_TOPIC, self._on_scan, self._on_odom,
                                     self._on_voltage)
        self.sensors = SensorManager(loop, self.bridge, self.camera, self.push_state)
        self.last_odom = None
        self._sys = sysinfo.snapshot()
        self._idle_since = None

    # ---- clients ------------------------------------------------------
    def add_client(self, sock):
        cid = self._next_client
        self._next_client += 1
        self.clients[cid] = sock
        return cid

    def remove_client(self, cid):
        self.clients.pop(cid, None)

    def send(self, sock, msg):
        try:
            sock.write_message(json.dumps(msg, separators=(',', ':')))
        except tornado.websocket.WebSocketClosedError:
            pass

    def broadcast(self, msg):
        if not self.clients:
            return
        data = json.dumps(msg, separators=(',', ':'))
        for sock in list(self.clients.values()):
            try:
                sock.write_message(data)
            except tornado.websocket.WebSocketClosedError:
                pass

    def hello(self, cid):
        return {'t': 'hello', 'version': VERSION, 'client_id': cid, 'server_time': time.time(),
                'config': {'laser_x': config.LASER_X, 'laser_yaw': config.LASER_YAW,
                           'max_linear': config.MAX_LINEAR, 'max_angular': config.MAX_ANGULAR,
                           'control_hz': config.CONTROL_HZ,
                           'source_timeout': config.SOURCE_TIMEOUT,
                           'camera_max_fps': config.CAMERA_MAX_FPS}}

    # ---- incoming panel messages -----------------------------------------
    def handle(self, cid, sock, msg):
        kind = msg.get('t')
        a = self.arbiter
        if kind == 'manual':
            before = a.mode
            if a.set_manual(msg.get('vx', 0), msg.get('vy', 0), msg.get('wz', 0), origin=cid):
                # Act now instead of waiting up to 50 ms for the next control tick.
                self._publish(*a.tick())
            if a.mode != before:
                self.push_state()
            self.send(sock, {'t': 'ack', 'seq': msg.get('seq'), 's': time.time()})
        elif kind == 'manual_release':
            a.release_manual()
            self._publish(*a.tick())
        elif kind == 'hand_back':
            a.hand_back()
            self._publish(*a.tick())
            self.push_state()
        elif kind == 'estop':
            a.trigger_estop('client %s' % cid)
            self._publish(0.0, 0.0, 0.0)  # do not wait for the next control tick
            log.warning('E-STOP from client %s', cid)
            self.push_state()
        elif kind == 'estop_release':
            a.release_estop()
            log.warning('e-stop released by client %s', cid)
            self.push_state()
        elif kind == 'sensor':
            self.sensors.request(msg.get('name'), bool(msg.get('on')))
        elif kind == 'beep':
            self._beep()
        elif kind == 'ping':
            self.send(sock, {'t': 'pong', 'c': msg.get('c'), 's': time.time()})
        else:
            self.send(sock, {'t': 'error', 'msg': 'unknown message type %r' % kind})

    def _beep(self):
        if self.sensors.sensors['base'].state != 'on':
            return
        self.bridge.buzzer(True)
        self.loop.call_later(0.15, self.bridge.buzzer, False)

    # ---- sensor data from ROS ------------------------------------------
    def _on_scan(self, payload):
        self.broadcast(payload)

    def _on_odom(self, payload):
        self.last_odom = payload
        self.broadcast(payload)

    def _on_voltage(self, volts):
        moving = any(abs(v) > 1e-3 for v in self.arbiter.output)
        self.battery.update(volts, time.time(), moving)

    # ---- periodic ---------------------------------------------------------
    def _publish(self, vx, vy, wz):
        try:
            self.bridge.publish_cmd(vx, vy, wz)
        except Exception:
            log.exception('publish failed')

    def push_state(self):
        """Broadcast state now instead of waiting for the next 5 Hz tick."""
        self.loop.call_soon(lambda: self.broadcast(self.state()))

    def state(self):
        snap = self.arbiter.snapshot()
        return {
            't': 'state', 'stamp': round(time.time(), 3),
            'control': snap,
            'sensors': self.sensors.snapshot(),
            'topics': self.bridge.topic_status(),
            'battery_v': self.bridge.voltage,
            'battery': self.battery.snapshot(time.time()),
            'clients': len(self.clients),
            'uptime': round(time.time() - self.started),
            'sys': self._sys,
        }

    async def control_loop(self):
        period = 1.0 / config.CONTROL_HZ
        next_t = time.monotonic()
        while True:
            self._publish(*self.arbiter.tick())
            next_t += period
            delay = next_t - time.monotonic()
            if delay < -period:  # fell behind (e.g. suspended); resync
                next_t = time.monotonic()
                delay = 0
            await asyncio.sleep(max(0.0, delay))

    async def state_loop(self):
        n = 0
        while True:
            await asyncio.sleep(1.0 / config.STATE_HZ)
            n += 1
            if n % int(config.STATE_HZ) == 0:
                self.sensors.check_health()
                self._check_idle()
                self._check_battery_floor()
            if n % int(5 * config.STATE_HZ) == 0:
                self._sys = await self.loop.run_in_executor(None, sysinfo.snapshot)
            self.broadcast(self.state())

    def _check_battery_floor(self):
        if self.battery.below_floor(time.time()) and not self.arbiter.estop:
            log.warning('battery at or below 9.0 V: e-stop')
            self.arbiter.trigger_estop('电池电压 ≤ 9.0 V，请立即充电')
            self._publish(0.0, 0.0, 0.0)
            self.push_state()

    def _check_idle(self):
        busy = self.clients or self.arbiter.active_policy is not None or not self.sensors.any_on()
        if busy:
            self._idle_since = None
            return
        if self._idle_since is None:
            self._idle_since = time.time()
        elif time.time() - self._idle_since > config.IDLE_OFF_AFTER:
            log.info('no panel and no policy for %.0f s: switching sensors off', config.IDLE_OFF_AFTER)
            self._idle_since = None
            self.loop.create_task(self.sensors.stop_all())

    async def shutdown(self):
        self.arbiter.trigger_estop('hub shutdown')
        self._publish(0.0, 0.0, 0.0)
        await self.sensors.stop_all()
        self.bridge.shutdown()


class PanelSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, hub):
        self.hub = hub
        self.cid = None

    def check_origin(self, origin):
        return True  # LAN-only, no access control (decision 11)

    def open(self):
        self.set_nodelay(True)
        self.cid = self.hub.add_client(self)
        log.info('panel %s connected from %s', self.cid, self.request.remote_ip)
        self.hub.send(self, self.hub.hello(self.cid))
        self.hub.send(self, self.hub.state())

    def on_message(self, message):
        try:
            msg = json.loads(message)
        except ValueError:
            return
        if isinstance(msg, dict):
            self.hub.handle(self.cid, self, msg)

    def on_close(self):
        self.hub.remove_client(self.cid)
        log.info('panel %s disconnected', self.cid)


class MjpegHandler(tornado.web.RequestHandler):
    def initialize(self, hub):
        self.hub = hub

    async def get(self):
        cam = self.hub.camera
        self.set_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.set_header('Cache-Control', 'no-cache, no-store')
        min_gap = 1.0 / config.CAMERA_MAX_FPS
        last_id, last_sent = -1, 0.0
        while not self.request.connection.stream.closed():
            frame = await cam.wait_frame(timeout=2.0)
            if frame is None:
                if not cam.running:
                    break
                continue
            if cam.frame_id == last_id:
                continue
            wait = min_gap - (time.monotonic() - last_sent)
            if wait > 0:
                await asyncio.sleep(wait)
                frame, last_id = cam.latest, cam.frame_id
                if frame is None:
                    continue
            else:
                last_id = cam.frame_id
            self.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n'
                       % len(frame))
            self.write(frame)
            self.write(b'\r\n')
            try:
                await self.flush()
            except tornado.iostream.StreamClosedError:
                break
            last_sent = time.monotonic()


class StateHandler(tornado.web.RequestHandler):
    def initialize(self, hub):
        self.hub = hub

    def get(self):
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps(self.hub.state()))


class NoCacheStatic(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header('Cache-Control', 'no-cache')


def make_app(hub):
    return tornado.web.Application([
        (r'/ws', PanelSocket, {'hub': hub}),
        (r'/camera.mjpg', MjpegHandler, {'hub': hub}),
        (r'/api/state', StateHandler, {'hub': hub}),
        (r'/(.*)', NoCacheStatic, {'path': config.WEB_DIR, 'default_filename': 'index.html'}),
    ], websocket_ping_interval=5, websocket_ping_timeout=15)
