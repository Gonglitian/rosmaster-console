"""Battery percentage from voltage alone.

The chassis board only reports pack voltage in 0.1 V steps
(Rosmaster_Lib.get_battery_voltage); there is no current, capacity or percent.
Research notes (2026-09-26):
- Pack is 3S (12.6 V full). Chemistry assumed 18650 NMC Li-ion (Yahboom's
  related models ship "12.6V 6000mAh lithium-ion"); NOT verified on this car.
- Yahboom's firmware beeps at 9.6 V and says to power off and charge, so 9.6 V
  is shown as 0%.
- The table is averaged pseudo-OCV data of four NMC cells
  (github.com/soorajsunil/Piecewise-Battery-OCV), rescaled so 9.6 V = 0%.
- Voltage sags under load, so only readings taken at rest update the estimate;
  while driving the last resting estimate is held.
"""
import collections

# (pack volts, percent), 0% = Yahboom's 9.6 V low-voltage alarm.
NMC_TABLE = [(9.60, 0), (9.90, 3), (10.20, 8), (10.50, 17), (10.80, 28), (11.10, 43),
             (11.40, 54), (11.70, 65), (12.00, 76), (12.30, 92), (12.45, 97), (12.60, 100)]

WARN_V = 10.5      # resting: charge soon
CRITICAL_V = 10.0  # resting (or 9.9 V fast mean): finish and head back
STOP_V = 9.6       # fast mean: Yahboom's buzzer point, stop and charge
FLOOR_V = 9.0      # fast mean: hub e-stops the car

VALID = (5.0, 13.5)        # outside this the reading is "no data" (0.0 at driver start)
REST_AFTER = 5.0           # s without commanded motion before readings count as resting
TAU = 20.0                 # s, EMA time constant at rest
RESET_RISE = 0.3           # V above the shown estimate ...
RESET_HOLD = 30.0          # ... sustained this long = battery charged or swapped
FAST_WINDOW = 2.0          # s, mean used for the safety thresholds
STALE_AFTER = 3.0          # s without a valid sample = no data (chassis off)
JUMP_SAMPLES = 30          # consecutive 'spikes' (~3 s) = a real jump, start over
STORAGE_V = (11.1, 11.7)   # Yahboom: keep the pack here for long-term storage

HISTORY_EVERY = 5.0        # s between points kept for the battery page's chart
HISTORY_SPAN = 3 * 3600.0  # s of history kept
TREND_WINDOW = 900.0       # s of resting points used for the drain rate
TREND_MIN_SPAN = 300.0     # s of resting data needed before a rate is reported


def percent_for(volts, table=NMC_TABLE):
    if volts <= table[0][0]:
        return 0.0
    for (v0, p0), (v1, p1) in zip(table, table[1:]):
        if volts <= v1:
            return p0 + (p1 - p0) * (volts - v0) / (v1 - v0)
    return 100.0


class BatteryEstimator(object):
    def __init__(self):
        self.rest_v = None        # EMA of resting voltage
        self.shown_v = None       # monotonically non-increasing display voltage
        self.last_motion = None   # time of last nonzero command
        self.last_sample = None
        self.fast = collections.deque()
        self.latest = None
        self._rise_since = None
        self._init_samples = []
        self._spikes = 0
        self.last_valid = None
        self.history = collections.deque(maxlen=int(HISTORY_SPAN / HISTORY_EVERY))
        self._last_hist = None

    def update(self, volts, now, moving):
        """Feed one /voltage sample. `moving` = the hub is commanding nonzero velocity."""
        if moving:
            self.last_motion = now
        if volts is None or not (VALID[0] <= volts <= VALID[1]):
            return
        if self.rest_v is not None and abs(volts - self.rest_v) > 0.5 and not self._driving(now):
            self._spikes += 1
            if self._spikes < JUMP_SAMPLES:
                return  # isolated spike
            self._restart()  # the level really changed (charged or swapped)
        self._spikes = 0
        self.latest = volts
        self.last_valid = now
        self.fast.append((now, volts))
        while self.fast and now - self.fast[0][0] > FAST_WINDOW:
            self.fast.popleft()

        if self._driving(now):
            self.last_sample = now
            self._record(now, volts, resting=False)
            return
        if self.rest_v is None:
            self._init_samples.append(volts)
            if len(self._init_samples) >= 20:   # ~2 s at 10 Hz
                self.rest_v = sum(self._init_samples) / len(self._init_samples)
                self.shown_v = self.rest_v
        else:
            dt = 0.1 if self.last_sample is None else max(0.0, min(1.0, now - self.last_sample))
            alpha = dt / (TAU + dt)
            self.rest_v += alpha * (volts - self.rest_v)
            self._update_shown(now)
        self.last_sample = now
        if self.rest_v is not None:
            self._record(now, self.rest_v, resting=True)

    def _record(self, now, volts, resting):
        if self._last_hist is None or now - self._last_hist >= HISTORY_EVERY:
            self.history.append((now, volts, resting))
            self._last_hist = now

    def trend(self, now):
        """Least-squares slope of resting voltage and percent over the last
        TREND_WINDOW seconds, or None until TREND_MIN_SPAN of resting data exists."""
        pts = [(t, v) for t, v, rest in self.history if rest and now - t <= TREND_WINDOW]
        if len(pts) < 10 or pts[-1][0] - pts[0][0] < TREND_MIN_SPAN:
            return None
        n = float(len(pts))
        mt = sum(t for t, _ in pts) / n
        dt2 = sum((t - mt) ** 2 for t, _ in pts)
        if dt2 <= 0:
            return None
        mv = sum(v for _, v in pts) / n
        pcts = [percent_for(v) for _, v in pts]
        mp = sum(pcts) / n
        sv = sum((t - mt) * (v - mv) for t, v in pts) / dt2
        sp = sum((t - mt) * (p - mp) for (t, _), p in zip(pts, pcts)) / dt2
        return {'v_per_h': round(sv * 3600, 3), 'pct_per_h': round(sp * 3600, 2),
                'span_min': round((pts[-1][0] - pts[0][0]) / 60.0, 1)}

    def eta(self, now):
        """Minutes until each alarm line at the measured drain rate."""
        tr = self.trend(now)
        if tr is None or self.shown_v is None:
            return None
        pct_now = percent_for(self.shown_v)
        if tr['pct_per_h'] > 2.0:
            return {'status': 'rising'}
        if tr['pct_per_h'] > -0.5:
            return {'status': 'steady'}
        per_min = -tr['pct_per_h'] / 60.0
        out = {'status': 'discharging'}
        for name, volts in (('warn', WARN_V), ('critical', CRITICAL_V), ('alarm', STOP_V)):
            target = percent_for(volts)
            out[name] = 0 if pct_now <= target else int(round((pct_now - target) / per_min))
        return out

    def history_points(self):
        return [[round(t, 1), round(v, 3), bool(rest)] for t, v, rest in self.history]

    def _restart(self):
        self.rest_v = self.shown_v = None
        self._init_samples = []
        self._rise_since = None
        self._spikes = 0

    def _driving(self, now):
        return self.last_motion is not None and now - self.last_motion < REST_AFTER

    def _update_shown(self, now):
        if self.rest_v < self.shown_v:
            self.shown_v = self.rest_v
            self._rise_since = None
        elif self.rest_v > self.shown_v + RESET_RISE:
            if self._rise_since is None:
                self._rise_since = now
            elif now - self._rise_since > RESET_HOLD:
                self.shown_v = self.rest_v
                self._rise_since = None
        else:
            self._rise_since = None

    def fast_mean(self):
        if not self.fast:
            return None
        return sum(v for _, v in self.fast) / len(self.fast)

    def snapshot(self, now):
        fast = self.fast_mean()
        if self.latest is None or now - self.last_valid > STALE_AFTER:
            return {'state': 'nodata', 'v': None, 'pct': None, 'level': 'unknown'}
        state = 'driving' if self._driving(now) else ('settling' if self.shown_v is None else 'resting')
        pct = None if self.shown_v is None else int(round(percent_for(self.shown_v) / 5.0) * 5)
        rest = self.shown_v
        if fast is not None and fast <= STOP_V:
            level = 'stop'
        elif (rest is not None and rest <= CRITICAL_V) or (fast is not None and fast <= 9.9):
            level = 'critical'
        elif (rest if rest is not None else fast) is not None and (rest if rest is not None else fast) <= WARN_V:
            level = 'warn'
        else:
            level = 'ok'
        basis = rest if rest is not None else self.latest
        return {'state': state, 'v': round(self.latest, 2),
                'v_rest': None if rest is None else round(rest, 2),
                'v_fast': None if fast is None else round(fast, 2),
                'cell_v': round(basis / 3.0, 3),
                'pct': pct, 'pct_exact': None if rest is None else round(percent_for(rest), 1),
                'level': level, 'trend': self.trend(now), 'eta': self.eta(now),
                'age': round(now - self.last_valid, 1)}

    def below_floor(self, now=None):
        if now is not None and (self.last_valid is None or now - self.last_valid > STALE_AFTER):
            return False
        fast = self.fast_mean()
        return fast is not None and len(self.fast) >= 10 and fast <= FLOOR_V
