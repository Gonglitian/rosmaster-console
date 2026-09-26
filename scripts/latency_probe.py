#!/usr/bin/env python3
"""Measure input-to-command latency WITHOUT moving the car (chassis must be OFF).

For each trial: send one manual command over /ws, then time how long until this
machine sees a nonzero /hub/cmd_vel over DDS. That interval = WebSocket uplink +
hub processing (incl. waiting for the control tick) + DDS downlink. A /ws ping
right before each trial gives the network round trip to subtract.

Run on a ROS 2 Foxy machine on the car's LAN (tasl-l1):
  source /opt/ros/foxy/setup.bash; ROS_DOMAIN_ID=32 python3 scripts/latency_probe.py [trials]
"""
import json
import statistics
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect
import tornado.gen

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
first_nonzero = {'t': None}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))]


async def main():
    rclpy.init()
    node = Node('hf_latency_probe')

    def on_cmd(m):
        if first_nonzero['t'] is None and abs(m.linear.x) > 1e-6:
            first_nonzero['t'] = time.monotonic()
    node.create_subscription(Twist, '/hub/cmd_vel', on_cmd, 10)
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()

    ws = await websocket_connect('ws://rosmaster.local:8080/ws')
    while True:
        m = json.loads(await ws.read_message())
        if m.get('t') == 'state':
            if m['sensors']['base']['state'] != 'off':
                print('ABORT: switch 底盘 off first (this test must not move the car)')
                return 2
            break
    await tornado.gen.sleep(2.0)
    if node.count_subscribers('/hub/cmd_vel') != 1:
        print('ABORT: something else subscribes to /hub/cmd_vel')
        return 2

    e2e, rtt = [], []
    for i in range(TRIALS):
        t = time.monotonic()
        ws.write_message(json.dumps({'t': 'ping', 'c': t}))
        while True:
            m = json.loads(await ws.read_message())
            if m.get('t') == 'pong' and m.get('c') == t:
                rtt.append((time.monotonic() - t) * 1000)
                break
        first_nonzero['t'] = None
        t0 = time.monotonic()
        ws.write_message(json.dumps({'t': 'manual', 'vx': 0.05, 'vy': 0, 'wz': 0, 'seq': i}))
        while first_nonzero['t'] is None and time.monotonic() - t0 < 1.0:
            await tornado.gen.sleep(0.001)
        if first_nonzero['t'] is not None:
            e2e.append((first_nonzero['t'] - t0) * 1000)
        ws.write_message(json.dumps({'t': 'manual_release'}))
        ws.write_message(json.dumps({'t': 'hand_back'}))
        await tornado.gen.sleep(0.4 + 0.013 * (i % 7))   # desynchronise from the 20 Hz tick
    print('trials %d' % len(e2e))
    print('ws round trip      p50 %5.1f  p90 %5.1f  max %5.1f ms' % (pct(rtt, 50), pct(rtt, 90), max(rtt)))
    print('input -> cmd_vel   p50 %5.1f  p90 %5.1f  max %5.1f ms  (seen here, includes ~1 round trip)'
          % (pct(e2e, 50), pct(e2e, 90), max(e2e)))
    print('hub share (e2e - rtt, median) %5.1f ms' % (statistics.median(e2e) - statistics.median(rtt)))
    return 0


if __name__ == '__main__':
    raise SystemExit(IOLoop.current().run_sync(main))
