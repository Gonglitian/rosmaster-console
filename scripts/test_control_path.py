#!/usr/bin/env python3
"""End-to-end test of the hub's control path WITHOUT moving the car.

Requires the chassis to be OFF in the panel: then /hub/cmd_vel is still
published by the hub, but no driver turns it into wheel motion. The script
refuses to run if anything else subscribes to /hub/cmd_vel.

Run on a machine with ROS 2 Foxy on the car's LAN (tasl-l1):
  source /opt/ros/foxy/setup.bash
  ROS_DOMAIN_ID=32 python3 scripts/test_control_path.py

It sends manual commands over /ws and records what the hub publishes, checking
the speed clamp, the acceleration limit, the 0.5 s source timeout and e-stop.
"""
import json
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect
import tornado.gen

HOST = sys.argv[1] if len(sys.argv) > 1 else 'rosmaster.local:8080'
samples = []  # (monotonic time, vx, vy, wz)


def start_recorder():
    rclpy.init()
    node = Node('hf_control_path_test')
    node.create_subscription(
        Twist, '/hub/cmd_vel',
        lambda m: samples.append((time.monotonic(), m.linear.x, m.linear.y, m.angular.z)), 10)
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    return node


def window(t0, a, b):
    return [s for s in samples if t0 + a <= s[0] < t0 + b]


async def main():
    ws = await websocket_connect('ws://%s/ws' % HOST)
    state = None
    while state is None:
        m = json.loads(await ws.read_message())
        if m.get('t') == 'state':
            state = m
    if state['sensors']['base']['state'] != 'off':
        print('ABORT: chassis is %s. Switch 底盘 off first; this test must not move the car.'
              % state['sensors']['base']['state'])
        return 2
    if state['control']['estop']:
        ws.write_message(json.dumps({'t': 'estop_release'}))

    node = start_recorder()
    await tornado.gen.sleep(2.0)
    subs = node.count_subscribers('/hub/cmd_vel')
    if subs != 1:
        print('ABORT: /hub/cmd_vel has %d subscribers (expected only this test).' % subs)
        return 2

    def send(**m):
        ws.write_message(json.dumps(m))

    async def hold(seconds, **cmd):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            send(t='manual', **cmd)
            await tornado.gen.sleep(0.1)

    t0 = time.monotonic()
    await hold(2.0, vx=1.0, vy=0.0, wz=0.0)          # 0-2 s: ask for 1.0 m/s
    await tornado.gen.sleep(1.0)                       # 2-3 s: silence
    await hold(1.0, vx=0.0, vy=0.3, wz=3.0)           # 3-4 s: strafe + turn beyond limit
    t_estop = time.monotonic()
    send(t='estop')                                    # 4 s: e-stop
    await hold(1.0, vx=0.5, vy=0.0, wz=0.0)           # 4-5 s: commands must be ignored
    send(t='estop_release')
    send(t='hand_back')
    await tornado.gen.sleep(0.5)
    ws.close()

    ok = True

    def check(name, cond, detail):
        nonlocal ok
        ok = ok and cond
        print('%s  %-34s %s' % ('PASS' if cond else 'FAIL', name, detail))

    rate = len(window(t0, 0.0, 5.0)) / 5.0
    check('publish rate ~20 Hz', 17 <= rate <= 23, '%.1f Hz' % rate)
    ramp = window(t0, 0.0, 0.3)
    first = next((s for s in ramp if s[1] > 0), None)
    steps = [b[1] - a[1] for a, b in zip(ramp, ramp[1:])]
    check('acceleration limited', steps and max(steps) <= 1.5 * 0.05 * 1.6 + 1e-6,
          'largest step %.3f m/s per tick (limit 0.075 at 20 Hz)' % (max(steps) if steps else -1))
    top = max(s[1] for s in window(t0, 1.0, 2.0))
    check('speed clamped to 0.7 m/s', abs(top - 0.7) < 1e-3, 'max vx %.3f' % top)
    last_cmd = t0 + 2.0
    zero_at = next((s[0] for s in samples if s[0] > last_cmd and s[1] == 0.0), None)
    gap = None if zero_at is None else zero_at - last_cmd
    check('stops after 0.5 s of silence', gap is not None and 0.35 <= gap <= 0.75,
          'zero %.2f s after last command (hub sent the last one ~0.1 s before)' % (gap or -1))
    turn = window(t0, 3.6, 4.0)
    check('angular clamped to 1.5 rad/s', turn and abs(max(s[3] for s in turn) - 1.5) < 1e-3,
          'max wz %.3f' % (max(s[3] for s in turn) if turn else -1))
    check('vy follows 0.3 m/s', turn and abs(turn[-1][2] - 0.3) < 1e-3,
          'vy %.3f' % (turn[-1][2] if turn else -1))
    after = [s for s in samples if s[0] > t_estop + 0.1 and s[0] < t_estop + 1.0]
    first_zero = next((s[0] for s in samples if s[0] > t_estop and s[1:] == (0.0, 0.0, 0.0)), None)
    check('e-stop zeroes output at once', first_zero is not None and first_zero - t_estop < 0.1,
          '%.0f ms' % (((first_zero or t_estop) - t_estop) * 1000))
    check('commands ignored during e-stop', after and all(s[1:] == (0.0, 0.0, 0.0) for s in after),
          '%d samples, all zero' % len(after))
    print('\nRESULT:', 'ALL PASS' if ok else 'FAILURES ABOVE')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(IOLoop.current().run_sync(main))
