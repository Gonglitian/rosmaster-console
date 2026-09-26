#!/usr/bin/env python3
"""Driving example: keep a fixed distance to whatever is straight ahead.

    python3 worker/examples/keep_distance.py [--distance 1.0] [--max-speed 0.15]

The car only moves after you press "Give control" next to this worker in the
dashboard's Policy card. Any joystick input takes control back immediately;
E-STOP always wins. Try it with a hand or a box in front of the car.

Controller: look at lidar points within +-15 degrees of straight ahead, take the
nearest, and drive forward/backward proportionally to (distance - target),
limited to --max-speed. No points ahead within 3 m -> stand still.
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from rc_worker import Worker  # noqa: E402


class KeepDistance(Worker):
    def __init__(self, target, max_speed, gain=0.8, **kw):
        super(KeepDistance, self).__init__(**kw)
        self.target, self.max_speed, self.gain = target, max_speed, gain

    def on_active(self, active):
        print('>>> control %s' % ('GIVEN: the car may move now' if active else 'taken back'), flush=True)

    def on_scan(self, scan):
        ahead = [(x, y) for x, y in self.scan_points(scan, max_range=3.0)
                 if x > 0 and abs(math.atan2(y, x)) < math.radians(15)]
        if ahead:
            x, y = min(ahead, key=lambda p: math.hypot(*p))
            d = math.hypot(x, y)
            vx = max(-self.max_speed, min(self.max_speed, self.gain * (d - self.target)))
            if abs(d - self.target) < 0.05:
                vx = 0.0
            markers = [{'x': x, 'y': y, 'kind': 'target', 'label': '%.2f m' % d}]
            text = 'object %.2f m ahead, target %.2f m, vx %+.2f' % (d, self.target, vx)
        else:
            vx, markers, text = 0.0, [], 'nothing within 3 m ahead'
        # Send every scan: the hub stops the car if commands stop for 0.5 s.
        self.send_cmd(vx, 0.0, 0.0, obs=scan)
        self.send_debug(markers, text)
        if self.active:
            print(('DRIVING  ' if self.driving else 'waiting  ') + text, flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='ws://rosmaster.local:8080/ws')
    p.add_argument('--distance', type=float, default=1.0, help='metres to keep')
    p.add_argument('--max-speed', type=float, default=0.15, help='m/s (the hub caps at 0.7)')
    a = p.parse_args()
    KeepDistance(a.distance, a.max_speed, url=a.url, name='keep-distance').run()
