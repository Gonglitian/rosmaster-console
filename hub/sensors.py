"""Start/stop and health of the car's sensors: chassis ('base'), lidar, camera.

Nothing starts at boot; the panel switches each one on. States:
off -> starting -> on, and error if a start fails or a running sensor goes
quiet. The lidar start retries with a USB replug (see usbreset.py).
"""
import asyncio
import logging
import os
import time

from . import config, usbreset
from .devices import LidarMotor, allow_depth_sensor_suspend
from .procs import ChildProcess

log = logging.getLogger('hub.sensors')

OFF, STARTING, ON, ERROR, STOPPING = 'off', 'starting', 'on', 'error', 'stopping'
OUR_DRIVER_NODE = 'hf_driver'
LIDAR_OK = 'current scan mode'
LIDAR_FAIL = ('Can not start scan', 'Error, operation time out', 'Error, cannot bind')
ODOM_MISSING = '可以开车，但没收到 EKF 的 /odom，面板上看不到实测速度'


class Sensor(object):
    def __init__(self, name):
        self.name = name
        self.state = OFF
        self.message = ''
        self.attempt = 0
        self.since = time.time()
        self.task = None

    def snapshot(self):
        return {'state': self.state, 'message': self.message, 'attempt': self.attempt,
                'since': round(self.since, 1)}


class SensorManager(object):
    def __init__(self, loop, bridge, camera, on_change):
        self.loop = loop
        self.bridge = bridge
        self.camera = camera
        self.on_change = on_change
        self.sensors = {n: Sensor(n) for n in ('base', 'lidar', 'camera')}
        self.procs = {}
        self.last_activity = time.time()
        self.motor = LidarMotor()
        self.reader = None      # BatteryReader, set by the hub

    # ---- public -------------------------------------------------------
    async def startup(self):
        """Everything starts off, and 'off' means off: stop the lidar motor and let
        the unused depth sensor suspend."""
        await self._blocking(allow_depth_sensor_suspend)
        await self._stop_motor(self.sensors['lidar'])
        self._monitor_battery(True)

    def _monitor_battery(self, on):
        """The battery monitor owns /dev/myserial whenever the chassis driver does not."""
        if self.reader is None:
            return
        if on and 'base' not in self.procs:
            self.reader.start()
        elif not on:
            self.reader.stop()

    def snapshot(self):
        return {n: s.snapshot() for n, s in self.sensors.items()}

    def any_on(self):
        return any(s.state in (STARTING, ON, ERROR) for s in self.sensors.values())

    def request(self, name, on):
        s = self.sensors.get(name)
        if s is None:
            return
        if on and s.state in (OFF, ERROR):
            self._spawn(s, getattr(self, '_start_' + name)(s))
        elif not on and s.state in (STARTING, ON, ERROR):
            self._spawn(s, self._stop(s))

    async def stop_all(self):
        await asyncio.gather(*[self._stop(s) for s in self.sensors.values()
                               if s.state != OFF])

    # ---- internals ----------------------------------------------------
    def _set(self, s, state, message='', attempt=None):
        s.state, s.message, s.since = state, message, time.time()
        if attempt is not None:
            s.attempt = attempt
        log.info('%s -> %s %s', s.name, state, message)
        self.on_change()

    def _spawn(self, s, coro):
        if s.task is not None and not s.task.done():
            s.task.cancel()
        s.task = self.loop.create_task(coro)

    async def _blocking(self, fn, *args):
        return await self.loop.run_in_executor(None, fn, *args)

    async def _stop(self, s):
        if s.task is not None and not s.task.done() and s.task is not asyncio.current_task():
            s.task.cancel()
        self._set(s, STOPPING)
        if s.name == 'camera':
            await self._blocking(self.camera.stop)
        else:
            proc = self.procs.pop(s.name, None)
            if proc is not None:
                await self._blocking(proc.stop)
        self._set(s, OFF, attempt=0)
        if s.name == 'lidar':
            await self._stop_motor(s)
        elif s.name == 'base':
            self._monitor_battery(True)

    async def _stop_motor(self, s):
        if not await self._blocking(self.motor.stop):
            s.message = '驱动已关，但没能停住雷达电机（串口 DTR 设置失败）'
            self.on_change()

    async def _clear_leftover(self, name):
        """A sensor restarted from the error state may still have a process."""
        old = self.procs.pop(name, None)
        if old is not None:
            await self._blocking(old.stop)

    def _launch(self, name, on_line=None):
        path = os.path.join(config.LAUNCH_DIR, name + '.launch.py')
        proc = ChildProcess(name, ['ros2', 'launch', path], self.loop, on_line)
        proc.start()
        self.procs[name] = proc
        return proc

    def _fresh(self, topic, since, max_age=1.0):
        meter = self.bridge.rates[topic]
        return meter.last > since and time.time() - meter.last < max_age

    async def _start_base(self, s):
        self._set(s, STARTING, '检查是否已有底盘驱动在运行', attempt=1)
        await self._clear_leftover('base')
        foreign = [n for n in self.bridge.publisher_nodes('/voltage') if n != OUR_DRIVER_NODE]
        if foreign:
            self._set(s, ERROR, '已有别的底盘驱动在运行（节点 %s），可能是旧的 x3 容器。'
                                '先停掉它，两个驱动会争用串口。' % ', '.join(foreign))
            return
        if self.reader is not None:
            await self._blocking(self.reader.stop)   # the driver needs /dev/myserial
        proc = self._launch('base')
        self._set(s, STARTING, '启动驱动、IMU 滤波和 EKF')
        # Ready = the driver is alive (it publishes /voltage from the STM32 at
        # 10 Hz, and it is the node that turns /hub/cmd_vel into wheel motion).
        # /odom comes from the EKF further down the chain; missing /odom is a
        # warning, never a reason to kill a driver that can drive the car.
        deadline = time.time() + config.BASE_START_TIMEOUT
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            if not proc.running:
                self._set(s, ERROR, '进程退出：' + ' | '.join(proc.tail(3)))
                self.procs.pop('base', None)
                self._monitor_battery(True)
                return
            if self._fresh('voltage', proc.started_at):
                break
        else:
            await self._blocking(proc.stop)
            self.procs.pop('base', None)
            self._set(s, ERROR, '%.0f 秒内没收到驱动的 /voltage，底盘驱动没起来'
                      % config.BASE_START_TIMEOUT)
            self._monitor_battery(True)
            return
        odom_deadline = time.time() + 5.0
        while time.time() < odom_deadline and not self._fresh('odom', proc.started_at):
            await asyncio.sleep(0.25)
        self._set(s, ON, '' if self._fresh('odom', proc.started_at) else ODOM_MISSING)

    async def _start_lidar(self, s):
        last_lines = []
        await self._clear_leftover('lidar')
        for attempt in range(1, config.LIDAR_MAX_ATTEMPTS + 1):
            self._set(s, STARTING, '第 %d 次启动' % attempt, attempt=attempt)
            result = {}

            def on_line(line, result=result):
                if LIDAR_OK in line:
                    result.setdefault('ok', line)
                elif any(k in line for k in LIDAR_FAIL):
                    result.setdefault('fail', line)

            # Let go of the port so the motor spins up before the node opens it and
            # immediately asks for a scan.
            await self._blocking(self.motor.release)
            await asyncio.sleep(config.LIDAR_SPINUP)
            proc = self._launch('lidar', on_line)
            deadline = time.time() + config.LIDAR_START_TIMEOUT
            while time.time() < deadline and not result and proc.running:
                await asyncio.sleep(0.2)
            if 'ok' in result:
                wait_until = time.time() + 5.0
                while time.time() < wait_until:
                    if self._fresh('scan', proc.started_at):
                        self._set(s, ON, result['ok'].split(']:')[-1].strip())
                        return
                    await asyncio.sleep(0.2)
                result['fail'] = '已进入扫描模式，但 5 秒内没收到 /scan'
            reason = result.get('fail') or ('进程退出' if not proc.running else '启动超时')
            last_lines = proc.tail(3)
            log.warning('lidar attempt %d failed: %s', attempt, reason)
            await self._blocking(proc.stop)
            self.procs.pop('lidar', None)
            if attempt < config.LIDAR_MAX_ATTEMPTS:
                self._set(s, STARTING, '第 %d 次失败（%s），USB 软拔插后重试' % (attempt, reason))
                try:
                    await self._blocking(lambda: usbreset.replug('/dev/rplidar',
                                                                 while_unbound=self.motor.forget))
                except Exception as e:  # no /sys write access, device missing, ...
                    log.error('USB replug failed: %s', e)
                    self._set(s, STARTING, 'USB 软拔插失败：%s' % e)
                await asyncio.sleep(1.0)
        self._set(s, ERROR, '%d 次都没启动成功：%s' % (config.LIDAR_MAX_ATTEMPTS, ' | '.join(last_lines)))
        await self._stop_motor(s)

    async def _start_camera(self, s):
        if not os.path.exists(config.CAMERA_DEVICE):
            self._set(s, ERROR, '找不到 %s' % config.CAMERA_DEVICE)
            return
        self._set(s, STARTING, '', attempt=1)
        if self.camera.running:
            await self._blocking(self.camera.stop)
        self.camera.start()
        t0 = time.time()
        while time.time() - t0 < 6.0:
            await asyncio.sleep(0.2)
            if self.camera.frame_time > t0:
                self._set(s, ON, '%s MJPEG' % config.CAMERA_SIZE)
                return
            if not self.camera.running:
                break
        err = ' | '.join(list(self.camera.errors)[-2:]) or '6 秒内没有画面'
        await self._blocking(self.camera.stop)
        self._set(s, ERROR, err)

    # ---- health (called every second) ----------------------------------
    def check_health(self):
        now = time.time()
        base = self.sensors['base']
        if base.state == ON:
            # The driver's /voltage decides health; /odom only toggles a warning.
            odom_ok = now - self.bridge.rates['odom'].last < config.STALE_TOPIC_TIMEOUT
            if odom_ok and base.message == ODOM_MISSING:
                self._set(base, ON, '')
            elif not odom_ok and base.message == '':
                self._set(base, ON, ODOM_MISSING)
        for name, topic in (('base', 'voltage'), ('lidar', 'scan')):
            s = self.sensors[name]
            if s.state != ON:
                continue
            proc = self.procs.get(name)
            if proc is None or not proc.running:
                self.procs.pop(name, None)
                self._set(s, ERROR, '进程意外退出：' + ' | '.join(proc.tail(3) if proc else []))
                if name == 'lidar':
                    self.loop.create_task(self._stop_motor(s))
                elif name == 'base':
                    self._monitor_battery(True)
                continue
            age = now - self.bridge.rates[topic].last
            if age > config.STALE_TOPIC_TIMEOUT:
                self._set(s, ERROR, '/%s 已经 %.0f 秒没有数据' % (topic, age))
        cam = self.sensors['camera']
        if cam.state == ON:
            if not self.camera.running:
                self._set(cam, ERROR, 'ffmpeg 退出：' + ' | '.join(list(self.camera.errors)[-2:]))
            elif now - self.camera.frame_time > 3.0:
                self._set(cam, ERROR, '画面已经 %.0f 秒没有更新' % (now - self.camera.frame_time))
