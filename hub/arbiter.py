"""Who drives the car, and how fast it may change speed.

Pure logic with an injectable clock so it can be unit-tested without ROS.

Priority: e-stop > manual > policy.
- E-stop latches until someone releases it; releasing returns to IDLE, so a
  policy never resumes on its own after an e-stop.
- Any manual command switches to MANUAL at once, even while a policy drives.
  Only an explicit hand_back() returns control to the policy.
- A source that has been silent for SOURCE_TIMEOUT counts as zero velocity.
- The output is clamped to the speed limits, and speeding up is rate-limited.
  Slowing down (towards zero on an axis) is never rate-limited.
"""
import math
import time

IDLE, MANUAL, POLICY = 'idle', 'manual', 'policy'


class Command(object):
    __slots__ = ('vx', 'vy', 'wz', 'time', 'origin')

    def __init__(self, vx=0.0, vy=0.0, wz=0.0, t=0.0, origin=None):
        self.vx, self.vy, self.wz, self.time, self.origin = vx, vy, wz, t, origin

    def as_tuple(self):
        return (self.vx, self.vy, self.wz)


def _finite(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _ramp(last, target, max_step):
    """Move one axis from last towards target by at most max_step, but let it
    drop towards zero immediately and never ramp through a sign change."""
    if target == 0.0 or (target * last > 0 and abs(target) <= abs(last)):
        return target
    if target * last < 0:
        return 0.0
    step = max(-max_step, min(max_step, target - last))
    return last + step


class Arbiter(object):
    def __init__(self, max_linear, max_angular, max_linear_accel, max_angular_accel,
                 source_timeout, clock=time.monotonic):
        self.max_linear = max_linear
        self.max_angular = max_angular
        self.max_linear_accel = max_linear_accel
        self.max_angular_accel = max_angular_accel
        self.source_timeout = source_timeout
        self.clock = clock

        self.estop = False
        self.estop_reason = None
        self.mode = IDLE
        self.manual = Command()
        self.policy = Command()
        self.active_policy = None      # worker id, milestone 2
        self.output = (0.0, 0.0, 0.0)
        self._last_tick = None

    # ---- inputs -------------------------------------------------------
    def set_manual(self, vx, vy, wz, origin=None):
        if self.estop:
            return False
        self.manual = Command(_finite(vx), _finite(vy), _finite(wz), self.clock(), origin)
        self.mode = MANUAL
        return True

    def set_policy(self, worker_id, vx, vy, wz):
        """Accepted only from the active policy; ignored otherwise."""
        if worker_id != self.active_policy:
            return False
        self.policy = Command(_finite(vx), _finite(vy), _finite(wz), self.clock(), worker_id)
        return True

    def hand_back(self):
        """Return control from MANUAL to the active policy (or IDLE if none)."""
        if self.estop:
            return False
        self.manual = Command()
        self.mode = POLICY if self.active_policy is not None else IDLE
        return True

    def activate_policy(self, worker_id):
        self.active_policy = worker_id
        self.policy = Command()
        if self.mode == POLICY and worker_id is None:
            self.mode = IDLE

    def release_manual(self):
        """Manual control ended without handing back: stay put, stay in charge."""
        self.manual = Command(t=self.clock())

    def trigger_estop(self, reason='button'):
        self.estop = True
        self.estop_reason = reason
        self.output = (0.0, 0.0, 0.0)

    def release_estop(self):
        self.estop = False
        self.estop_reason = None
        self.mode = IDLE
        self.manual = Command()
        self.policy = Command()

    # ---- output -------------------------------------------------------
    def _target(self, now):
        if self.estop:
            return (0.0, 0.0, 0.0)
        if self.mode == MANUAL:
            src = self.manual
        elif self.mode == POLICY:
            src = self.policy
        else:
            return (0.0, 0.0, 0.0)
        if now - src.time > self.source_timeout:
            return (0.0, 0.0, 0.0)
        return src.as_tuple()

    def source_age(self):
        src = {MANUAL: self.manual, POLICY: self.policy}.get(self.mode)
        return None if src is None or src.time == 0.0 else self.clock() - src.time

    def tick(self):
        now = self.clock()
        dt = 0.05 if self._last_tick is None else max(0.0, min(0.2, now - self._last_tick))
        self._last_tick = now

        vx, vy, wz = self._target(now)
        speed = math.hypot(vx, vy)
        if speed > self.max_linear:
            vx, vy = vx * self.max_linear / speed, vy * self.max_linear / speed
        wz = max(-self.max_angular, min(self.max_angular, wz))

        if self.estop:
            self.output = (0.0, 0.0, 0.0)
        else:
            lx, ly, lw = self.output
            self.output = (_ramp(lx, vx, self.max_linear_accel * dt),
                           _ramp(ly, vy, self.max_linear_accel * dt),
                           _ramp(lw, wz, self.max_angular_accel * dt))
        return self.output

    def snapshot(self):
        age = self.source_age()
        return {
            'mode': self.mode,
            'estop': self.estop,
            'estop_reason': self.estop_reason,
            'active_policy': self.active_policy,
            'output': [round(v, 3) for v in self.output],
            'source_age': None if age is None else round(age, 3),
            'limits': {'linear': self.max_linear, 'angular': self.max_angular},
        }
