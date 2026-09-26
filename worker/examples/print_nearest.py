#!/usr/bin/env python3
"""Read-only example: print the nearest obstacle on every scan. Never sends a command.

    python3 worker/examples/print_nearest.py [--url ws://rosmaster.local:8080/ws]

Turn on Base and Lidar in the dashboard first.
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from rc_worker import Worker  # noqa: E402


class PrintNearest(Worker):
    def on_connect(self):
        print('connected as %s; waiting for scans (is Lidar on in the dashboard?)' % self.worker_id, flush=True)

    def on_scan(self, scan):
        pts = self.scan_points(scan)
        if not pts:
            return
        x, y = min(pts, key=lambda p: math.hypot(*p))
        print('scan %5d: %4d points, nearest %.2f m at %+4.0f deg (x %+.2f, y %+.2f)'
              % (scan['seq'], len(pts), math.hypot(x, y), math.degrees(math.atan2(y, x)), x, y), flush=True)
        self.send_debug([{'x': x, 'y': y, 'kind': 'point', 'label': 'nearest'}],
                        'nearest %.2f m' % math.hypot(x, y))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='ws://rosmaster.local:8080/ws')
    a = p.parse_args()
    PrintNearest(url=a.url, name='print-nearest').run()
