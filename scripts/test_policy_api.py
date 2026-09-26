#!/usr/bin/env python3
"""End-to-end test of the policy-worker protocol WITHOUT moving the car.

Plays both a dashboard and a policy worker over /ws and records what the hub
publishes on /hub/cmd_vel. Refuses to run unless Base is off and nothing else
subscribes to /hub/cmd_vel (so no chassis driver can turn it into motion).

On a ROS 2 Foxy machine on the car's network (e.g. tasl-l1):
  source /opt/ros/foxy/setup.bash
  ROS_DOMAIN_ID=32 python3 scripts/test_policy_api.py [host:port]
Against a test hub on the same machine (HF_NO_HARDWARE=1, see docs/OPERATIONS.md):
  ROS_DOMAIN_ID=77 python3 scripts/test_policy_api.py localhost:8091
"""
import json
import sys
import threading
import time

import rclpy
import tornado.gen
from geometry_msgs.msg import Twist
from rclpy.node import Node
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

HOST = sys.argv[1] if len(sys.argv) > 1 else 'rosmaster.local:8080'
samples = []   # (monotonic, vx, vy, wz)


class Conn(object):
    """A WebSocket that keeps the latest message of each type."""

    def __init__(self, ws):
        self.ws, self.last, self.inbox = ws, {}, []

    @classmethod
    async def open(cls):
        c = cls(await websocket_connect('ws://%s/ws' % HOST))
        IOLoop.current().spawn_callback(c._pump)
        return c

    async def _pump(self):
        while True:
            raw = await self.ws.read_message()
            if raw is None:
                return
            m = json.loads(raw)
            self.last[m.get('t')] = m
            self.inbox.append(m)

    def send(self, **m):
        self.ws.write_message(json.dumps(m))

    async def wait_for(self, pred, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in self.inbox:
                if pred(m):
                    return m
            await tornado.gen.sleep(0.02)
        return None


def window(t0, t1):
    return [s for s in samples if t0 <= s[0] < t1]


async def main():
    rclpy.init()
    node = Node('hf_policy_api_test')
    node.create_subscription(Twist, '/hub/cmd_vel',
                             lambda m: samples.append((time.monotonic(), m.linear.x, m.linear.y, m.angular.z)), 10)
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()

    panel = await Conn.open()
    st = await panel.wait_for(lambda m: m.get('t') == 'state')
    if st is None or st['sensors']['base']['state'] != 'off':
        print('ABORT: Base must be off (this test must not move the car)')
        return 2
    if st['control']['estop']:
        panel.send(t='estop_release')
    await tornado.gen.sleep(2.0)
    if node.count_subscribers('/hub/cmd_vel') != 1:
        print('ABORT: something else subscribes to /hub/cmd_vel')
        return 2

    ok = True

    def check(name, cond, detail=''):
        nonlocal ok
        ok = ok and bool(cond)
        print('%s  %-46s %s' % ('PASS' if cond else 'FAIL', name, detail))

    worker = await Conn.open()
    worker.send(t='worker_hello', name='api-test', host='test', protocol=1)
    wel = await worker.wait_for(lambda m: m.get('t') == 'worker_welcome')
    check('worker_hello -> worker_welcome', wel is not None, wel and wel['worker_id'])
    wid = wel['worker_id'] if wel else None
    await tornado.gen.sleep(0.4)
    st = panel.last.get('state')
    check('worker listed in state.workers', st and any(w['id'] == wid for w in st.get('workers', [])))

    async def drive(conn, seconds, vx=0.0, vy=0.0, wz=0.0, t='policy_cmd'):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            stamp = (panel.last.get('state') or {}).get('stamp')
            if t == 'policy_cmd':
                conn.send(t=t, vx=vx, vy=vy, wz=wz, obs_stamp=stamp, obs_seq=0)
            else:
                conn.send(t=t, vx=vx, vy=vy, wz=wz, seq=0)
            await tornado.gen.sleep(0.1)

    # Not active yet: ignored.
    t0 = time.monotonic()
    await drive(worker, 1.0, vx=0.2)
    ack = worker.last.get('policy_ack') or {}
    check('commands ignored before activation', ack.get('accepted') is False and
          all(s[1] == 0.0 for s in window(t0, time.monotonic())), 'ack %s' % ack)

    # Dashboard gives control.
    panel.send(t='activate_policy', worker_id=wid)
    act = await worker.wait_for(lambda m: m.get('t') == 'policy_active' and m.get('active'))
    check('worker told it has control', act is not None)
    t0 = time.monotonic()
    await drive(worker, 1.2, vx=0.2)
    out = window(t0 + 0.8, time.monotonic())
    check('policy drives /hub/cmd_vel after activation', out and abs(out[-1][1] - 0.2) < 1e-3,
          'vx %.3f' % (out[-1][1] if out else -1))
    check('ack says driving', (worker.last.get('policy_ack') or {}).get('driving') is True)
    await tornado.gen.sleep(0.4)
    w = [x for x in (panel.last.get('state') or {}).get('workers', []) if x['id'] == wid]
    check('latency and rate reported', w and w[0]['latency_ms'] is not None and w[0]['cmd_hz'] > 0,
          w and 'latency %s ms, %s Hz' % (w[0]['latency_ms'], w[0]['cmd_hz']))

    # Manual override from the dashboard.
    t0 = time.monotonic()
    both = [drive(worker, 1.0, vx=0.2), drive(panel, 1.0, vy=0.1, t='manual')]
    await tornado.gen.multi(both)
    out = window(t0 + 0.7, time.monotonic())
    check('manual overrides the policy', out and out[-1][1] == 0.0 and abs(out[-1][2] - 0.1) < 1e-3,
          'vx %.3f vy %.3f' % ((out[-1][1], out[-1][2]) if out else (-1, -1)))
    check('policy ack says not driving during manual', (worker.last.get('policy_ack') or {}).get('driving') is False)

    # Hand back.
    panel.send(t='manual_release')
    panel.send(t='hand_back')
    t0 = time.monotonic()
    await drive(worker, 1.0, vx=0.2)
    out = window(t0 + 0.7, time.monotonic())
    check('hand_back returns control to the policy', out and abs(out[-1][1] - 0.2) < 1e-3)

    # E-stop.
    panel.send(t='estop')
    t0 = time.monotonic()
    await drive(worker, 0.8, vx=0.2)
    out = window(t0 + 0.1, time.monotonic())
    check('e-stop zeroes the policy', out and all(s[1:] == (0.0, 0.0, 0.0) for s in out), '%d samples' % len(out))
    panel.send(t='estop_release')
    t0 = time.monotonic()
    await drive(worker, 0.8, vx=0.2)
    out = window(t0 + 0.1, time.monotonic())
    check('policy does not resume by itself after e-stop', out and all(s[1] == 0.0 for s in out))

    # Activate again, then the worker disappears.
    panel.send(t='activate_policy', worker_id=wid)
    await drive(worker, 1.0, vx=0.2)
    worker.ws.close()
    t_close = time.monotonic()
    await tornado.gen.sleep(0.6)
    zero = next((s[0] for s in samples if s[0] > t_close and s[1] == 0.0), None)
    check('worker disconnect stops the car', zero is not None and zero - t_close < 0.3,
          '%.0f ms' % (((zero or t_close) - t_close) * 1000))
    st = panel.last.get('state') or {}
    check('no active policy after disconnect', st.get('control', {}).get('active_policy') is None
          and not st.get('workers'), 'mode %s' % st.get('control', {}).get('mode'))

    print('\nRESULT:', 'ALL PASS' if ok else 'FAILURES ABOVE')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(IOLoop.current().run_sync(main, timeout=120))
