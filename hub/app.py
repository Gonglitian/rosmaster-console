"""The hub: web panel, WebSocket protocol, control loop.

Protocol (JSON text frames on /ws; full spec in docs/API.md):
  client -> hub: manual{vx,vy,wz,seq} | manual_release | hand_back | estop |
                 estop_release | sensor{name,on} | beep | ping{c}
  hub -> client: hello | state (5 Hz) | scan (every scan) | odom (every odom) |
                 pong{c,s} | ack{seq,s} | error{msg}
Times from the hub ('stamp', 's', 'server_time') are the car's wall clock.
"""
import asyncio
import collections
import json
import logging
import statistics
import time

import tornado.iostream
import tornado.web
import tornado.websocket

from . import config, sysinfo
from .arbiter import Arbiter, POLICY
from .battery import BatteryEstimator, NMC_TABLE, WARN_V, CRITICAL_V, STOP_V, FLOOR_V, STORAGE_V
from .battery_reader import BatteryReader
from .camera import Camera
from .sensors import SensorManager
from .wifi import WifiManager

log = logging.getLogger('hub')
VERSION = '0.2.0'
PROTOCOL = 1


class Hub(object):
    def __init__(self, loop, bridge_factory):
        self.loop = loop
        self.started = time.time()
        self.clients = {}
        self.workers = {}          # client id -> policy worker record (see _worker_hello)
        self._next_client = 1
        self.arbiter = Arbiter(config.MAX_LINEAR, config.MAX_ANGULAR, config.MAX_LINEAR_ACCEL,
                               config.MAX_ANGULAR_ACCEL, config.SOURCE_TIMEOUT)
        self.camera = Camera(loop, config.CAMERA_DEVICE, config.CAMERA_SIZE)
        self.battery = BatteryEstimator()
        self.bridge = bridge_factory(loop, config.CMD_TOPIC, self._on_scan, self._on_odom,
                                     self._on_voltage)
        self.sensors = SensorManager(loop, self.bridge, self.camera, self.push_state)
        self.voltage_source = None
        self.wifi = WifiManager()
        self.sensors.reader = BatteryReader(loop, self._on_monitor_voltage)
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
        w = self.workers.pop(cid, None)
        if w is not None:
            if self.arbiter.active_policy == w['id']:
                self.arbiter.activate_policy(None)
                self._publish(*self.arbiter.tick())
                log.warning('active policy %s (%s) disconnected: car stopped', w['id'], w['name'])
            else:
                log.info('policy worker %s (%s) disconnected', w['id'], w['name'])
            self.push_state()

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
        return {'t': 'hello', 'version': VERSION, 'protocol': PROTOCOL, 'client_id': cid,
                'server_time': time.time(),
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
        elif kind == 'worker_hello':
            self._worker_hello(cid, sock, msg)
        elif kind == 'policy_cmd':
            self._policy_cmd(cid, sock, msg)
        elif kind == 'policy_debug':
            w = self.workers.get(cid)
            if w is not None:
                self.broadcast({'t': 'policy_debug', 'worker_id': w['id'], 'stamp': round(time.time(), 3),
                                'markers': list(msg.get('markers') or [])[:200],
                                'text': str(msg.get('text') or '')[:300]})
        elif kind == 'activate_policy':
            self._activate(msg.get('worker_id'), cid, sock)
        elif kind == 'deactivate_policy':
            self._deactivate('client %s' % cid)
        else:
            self.send(sock, {'t': 'error', 'msg': 'unknown message type %r' % kind})

    # ---- policy workers (protocol in docs/API.md) ------------------------
    def _worker_hello(self, cid, sock, msg):
        wid = 'w%d' % cid
        self.workers[cid] = {
            'id': wid, 'sock': sock, 'since': time.time(), 'last_cmd': None,
            'name': str(msg.get('name') or 'policy')[:40],
            'host': str(msg.get('host') or sock.request.remote_ip)[:60],
            'cmd_times': collections.deque(maxlen=50), 'latency': collections.deque(maxlen=50)}
        log.info('policy worker %s (%s on %s) connected', wid, self.workers[cid]['name'],
                 self.workers[cid]['host'])
        self.send(sock, {'t': 'worker_welcome', 'worker_id': wid, 'protocol': PROTOCOL,
                         'active': self.arbiter.active_policy == wid, 'config': self.hello(cid)['config']})
        self.push_state()

    def _policy_cmd(self, cid, sock, msg):
        w = self.workers.get(cid)
        if w is None:
            self.send(sock, {'t': 'error', 'msg': 'send worker_hello before policy_cmd'})
            return
        a, now = self.arbiter, time.time()
        w['cmd_times'].append(now)
        w['last_cmd'] = now
        obs = msg.get('obs_stamp')
        if isinstance(obs, (int, float)) and 0 <= now - obs < 10:
            w['latency'].append((now - obs) * 1000.0)   # observation -> command, car clock
        accepted = a.set_policy(w['id'], msg.get('vx', 0), msg.get('vy', 0), msg.get('wz', 0))
        driving = accepted and a.mode == POLICY and not a.estop
        if driving:
            self._publish(*a.tick())
        self.send(sock, {'t': 'policy_ack', 'seq': msg.get('seq'), 'accepted': accepted,
                         'driving': driving, 'mode': a.mode, 'estop': a.estop})

    def _worker_by_id(self, wid):
        for w in self.workers.values():
            if w['id'] == wid:
                return w
        return None

    def _activate(self, wid, by, sock):
        a = self.arbiter
        w = self._worker_by_id(wid)
        if w is None:
            self.send(sock, {'t': 'error', 'msg': 'no connected policy worker %r' % wid})
            return
        if a.estop:
            self.send(sock, {'t': 'error', 'msg': 'release the e-stop first'})
            return
        old = self._worker_by_id(a.active_policy)
        a.activate_policy(wid)
        a.hand_back()                  # mode -> POLICY
        self._publish(*a.tick())
        log.warning('policy %s (%s) given control by client %s', wid, w['name'], by)
        if old is not None and old is not w:
            self.send(old['sock'], {'t': 'policy_active', 'active': False})
        self.send(w['sock'], {'t': 'policy_active', 'active': True})
        self.push_state()

    def _deactivate(self, by):
        a = self.arbiter
        old = self._worker_by_id(a.active_policy)
        a.activate_policy(None)        # mode POLICY -> IDLE
        self._publish(*a.tick())
        if old is not None:
            log.warning('policy %s (%s) stopped by %s', old['id'], old['name'], by)
            self.send(old['sock'], {'t': 'policy_active', 'active': False})
        self.push_state()

    def workers_snapshot(self):
        now = time.time()
        out = []
        for w in self.workers.values():
            recent = [t for t in w['cmd_times'] if now - t < 2.0]
            out.append({'id': w['id'], 'name': w['name'], 'host': w['host'],
                        'connected_s': round(now - w['since']),
                        'cmd_hz': round(len(recent) / 2.0, 1),
                        'last_cmd_age': None if w['last_cmd'] is None else round(now - w['last_cmd'], 2),
                        'latency_ms': round(statistics.median(w['latency'])) if w['latency'] else None,
                        'active': self.arbiter.active_policy == w['id']})
        return out

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
        """From the chassis driver's /voltage (chassis on)."""
        self.voltage_source = 'driver'
        self._feed_battery(volts)

    def _on_monitor_voltage(self, volts):
        """From the battery monitor reading the STM32 directly (chassis off)."""
        if self.sensors.sensors['base'].state in ('off', 'error'):
            self.voltage_source = 'monitor'
            self._feed_battery(volts)

    def _feed_battery(self, volts):
        moving = any(abs(v) > 1e-3 for v in self.arbiter.output)
        self.battery.update(volts, time.time(), moving)

    def battery_report(self):
        now = time.time()
        snap = self.battery.snapshot(now)
        snap['source'] = self.voltage_source if snap['state'] != 'nodata' else None
        return {'t': 'battery', 'stamp': round(now, 1), 'snapshot': snap,
                'history': self.battery.history_points(),
                'thresholds': {'warn': WARN_V, 'critical': CRITICAL_V, 'alarm': STOP_V,
                               'floor': FLOOR_V, 'full': NMC_TABLE[-1][0],
                               'storage': list(STORAGE_V)},
                'table': NMC_TABLE}

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
            'battery': dict(self.battery.snapshot(time.time()), source=self.voltage_source),
            'clients': len(self.clients),
            'panels': len(self.clients) - len(self.workers),
            'workers': self.workers_snapshot(),
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
            self.arbiter.trigger_estop('battery at or below 9.0 V: charge now')
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
        if self.sensors.reader is not None:
            await self.loop.run_in_executor(None, self.sensors.reader.stop)
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


class BatteryHandler(tornado.web.RequestHandler):
    def initialize(self, hub):
        self.hub = hub

    def get(self):
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')
        self.write(json.dumps(self.hub.battery_report()))


class WifiHandler(tornado.web.RequestHandler):
    """GET /api/wifi, POST /api/wifi/{scan,connect,hotspot,forget}. Wi-Fi setup for a
    car with no keyboard; usable over the car's own hotspot (192.168.1.11:8080)."""

    def initialize(self, hub):
        self.hub = hub

    def _json(self, obj, code=200):
        self.set_status(code)
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')
        self.write(json.dumps(obj))

    async def get(self, action=None):
        # Readable cross-origin, so a page loaded from the car's old address can
        # confirm the car is on the new network before jumping there.
        self.set_header('Access-Control-Allow-Origin', '*')
        loop = self.hub.loop
        self._json(await loop.run_in_executor(None, self.hub.wifi.report))

    async def post(self, action):
        wifi, loop = self.hub.wifi, self.hub.loop
        try:
            body = json.loads(self.request.body or b'{}')
        except ValueError:
            body = {}
        try:
            if action == 'scan':
                await loop.run_in_executor(None, wifi.refresh_scan, True)
            elif action == 'connect':
                if not wifi.last_scan:
                    await loop.run_in_executor(None, wifi.refresh_scan, False)
                wifi.connect(body.get('ssid'), body.get('password') or None)
                log.warning('Wi-Fi switch to %r requested from %s', body.get('ssid'), self.request.remote_ip)
            elif action == 'hotspot':
                wifi.hotspot()
                log.warning('hotspot requested from %s', self.request.remote_ip)
            elif action == 'forget':
                await loop.run_in_executor(None, wifi.forget, body.get('name'))
            else:
                return self._json({'error': 'unknown action'}, 404)
        except (ValueError, RuntimeError) as e:
            return self._json({'error': str(e)}, 400)
        self._json(await loop.run_in_executor(None, wifi.report))


class NetHandler(tornado.web.RequestHandler):
    """GET /api/net: which network the car is on (fast; readable cross-origin)."""

    def initialize(self, hub):
        self.hub = hub

    async def get(self):
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Content-Type', 'application/json')
        self.set_header('Cache-Control', 'no-cache')
        self.write(json.dumps(await self.hub.loop.run_in_executor(None, self.hub.wifi.where)))


class ControlHandler(tornado.web.RequestHandler):
    """Terminal fallback without a WebSocket client:
      POST /api/estop            {"release": false}   engage (default) or release the e-stop
      POST /api/sensor           {"name": "base"|"lidar"|"camera", "on": true|false}
    """

    def initialize(self, hub):
        self.hub = hub

    def post(self, action):
        try:
            body = json.loads(self.request.body or b'{}')
        except ValueError:
            body = {}
        hub, a = self.hub, self.hub.arbiter
        who = 'http %s' % self.request.remote_ip
        if action == 'estop':
            if body.get('release'):
                a.release_estop()
                log.warning('e-stop released by %s', who)
            else:
                a.trigger_estop(who)
                hub._publish(0.0, 0.0, 0.0)
                log.warning('E-STOP from %s', who)
            hub.push_state()
        elif action == 'sensor':
            name = body.get('name')
            if name not in hub.sensors.sensors:
                self.set_status(400)
                self.write({'error': 'name must be base, lidar or camera'})
                return
            hub.sensors.request(name, bool(body.get('on')))
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps({'ok': True, 'control': a.snapshot(), 'sensors': hub.sensors.snapshot()}))


class NoCacheStatic(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header('Cache-Control', 'no-cache')


def make_app(hub):
    return tornado.web.Application([
        (r'/ws', PanelSocket, {'hub': hub}),
        (r'/camera.mjpg', MjpegHandler, {'hub': hub}),
        (r'/api/state', StateHandler, {'hub': hub}),
        (r'/api/battery', BatteryHandler, {'hub': hub}),
        (r'/api/wifi', WifiHandler, {'hub': hub}),
        (r'/api/net', NetHandler, {'hub': hub}),
        (r'/api/(estop|sensor)', ControlHandler, {'hub': hub}),
        (r'/api/wifi/(scan|connect|hotspot|forget)', WifiHandler, {'hub': hub}),
        (r'/(.*)', NoCacheStatic, {'path': config.WEB_DIR, 'default_filename': 'index.html'}),
    ], websocket_ping_interval=5, websocket_ping_timeout=15)
