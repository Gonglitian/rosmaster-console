#!/usr/bin/env python3
"""Command-line probe for the hub's /ws protocol (no browser needed).

  python3 scripts/ws_probe.py --on base lidar camera   # switch sensors on, watch states
  python3 scripts/ws_probe.py --off base lidar camera
  python3 scripts/ws_probe.py --watch 10               # just print state/scan/odom rates

Never sends velocity commands.
"""
import argparse
import json
import time

from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect


async def main(args):
    ws = await websocket_connect('ws://%s/ws' % args.host)
    for name in args.on or []:
        ws.write_message(json.dumps({'t': 'sensor', 'name': name, 'on': True}))
    for name in args.off or []:
        ws.write_message(json.dumps({'t': 'sensor', 'name': name, 'on': False}))
    wanted = {n: 'on' for n in (args.on or [])}
    wanted.update({n: 'off' for n in (args.off or [])})

    counts = {'scan': 0, 'odom': 0, 'state': 0}
    last = {}
    t0 = time.time()
    deadline = t0 + args.timeout
    while time.time() < deadline:
        raw = await ws.read_message()
        if raw is None:
            print('connection closed')
            return 1
        m = json.loads(raw)
        kind = m.get('t')
        if kind in counts:
            counts[kind] += 1
        if kind == 'hello':
            print('hello: client %s, config %s' % (m['client_id'], m['config']))
        if kind != 'state':
            continue
        for name, s in m['sensors'].items():
            key = (s['state'], s['message'], s['attempt'])
            if last.get(name) != key:
                last[name] = key
                print('%6.1fs  %-6s %-8s %s' % (time.time() - t0, name, s['state'], s['message']))
        if wanted and all(m['sensors'][n]['state'] in (w, 'error') for n, w in wanted.items()):
            if not args.watch:
                break
    dt = time.time() - t0
    print('received in %.1fs: %s' % (dt, ', '.join('%s %d (%.1f Hz)' % (k, v, v / dt)
                                                  for k, v in counts.items())))
    if last:
        print('battery_v:', m.get('battery_v'), ' topics:', m.get('topics'))
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='rosmaster.local:8080')
    p.add_argument('--on', nargs='*')
    p.add_argument('--off', nargs='*')
    p.add_argument('--watch', type=float, default=0, help='keep watching for N seconds')
    p.add_argument('--timeout', type=float, default=60)
    a = p.parse_args()
    if a.watch:
        a.timeout = a.watch
    raise SystemExit(IOLoop.current().run_sync(lambda: main(a)))
