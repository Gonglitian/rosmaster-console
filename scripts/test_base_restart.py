#!/usr/bin/env python3
"""Regression test for the 2026-09-26 "chassis start fails" bug. Never sends velocity.

Symptom then: after the chassis was stopped and restarted within a few seconds,
the hub never again received /odom from the new EKF, declared the start failed
after 15 s and stopped a driver that was working (keyboard driving worked while
the button said "starting", then stopped). Fixes: UDP-only Fast DDS in the
container (car/fastdds_udp.xml), graceful process-group stop, and readiness based
on the driver's /voltage with missing /odom only a warning.

This script: six fast on/off cycles, then SIGKILL of the EKF while running
(expect: still on, warning shown), then a restart 0.5 s after stopping
(expect: /odom back). Run from a machine on the car's LAN with ssh access:
  python3 scripts/test_base_restart.py
"""
import json
import subprocess
import time

from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

HOST, CAR = 'rosmaster.local:8080', 'pi@rosmaster.local'


async def main():
    ws = await websocket_connect('ws://%s/ws' % HOST)
    st = {}

    async def pump(secs, pred=lambda m: False):
        nonlocal st
        until = time.time() + secs
        while time.time() < until:
            m = json.loads(await ws.read_message())
            if m.get('t') == 'state':
                st = m
                if pred(m):
                    return True
        return False

    base = lambda m: m['sensors']['base']
    odom_hz = lambda: st['topics']['odom']['hz']

    def switch(on):
        ws.write_message(json.dumps({'t': 'sensor', 'name': 'base', 'on': on}))

    failures = []
    for i in range(1, 7):
        switch(True)
        await pump(20, lambda m: base(m)['state'] in ('on', 'error'))
        await pump(2.5)
        ok = base(st)['state'] == 'on' and odom_hz() > 5
        print('cycle %d: %s, odom %.1f Hz %s' % (i, base(st)['state'], odom_hz(), '' if ok else '<-- FAIL'))
        if not ok:
            failures.append('cycle %d' % i)
        switch(False)
        await pump(20, lambda m: base(m)['state'] == 'off')
        await pump(1.0 if i % 2 else 0.2)

    switch(True)
    await pump(20, lambda m: base(m)['state'] == 'on')
    subprocess.run(['ssh', CAR, 'docker exec rc-hub pkill -9 -f ekf_node'], check=False)
    await pump(8)
    ok = base(st)['state'] == 'on' and base(st)['message'] != ''
    print('EKF killed: base %s, warning %r %s' % (base(st)['state'], base(st)['message'], '' if ok else '<-- FAIL'))
    if not ok:
        failures.append('ekf kill')
    switch(False)
    await pump(20, lambda m: base(m)['state'] == 'off')
    await pump(0.5)
    switch(True)
    await pump(20, lambda m: base(m)['state'] == 'on')
    await pump(2.5)
    ok = odom_hz() > 5
    print('restart after kill: odom %.1f Hz %s' % (odom_hz(), '' if ok else '<-- FAIL'))
    if not ok:
        failures.append('restart after kill')
    switch(False)
    await pump(20, lambda m: base(m)['state'] == 'off')
    print('\nRESULT:', 'ALL PASS' if not failures else 'FAIL: ' + ', '.join(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(IOLoop.current().run_sync(main))
