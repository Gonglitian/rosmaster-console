#!/usr/bin/env python3
"""Send a zero-velocity command straight to the chassis board over serial.

Used by car/entrypoint.sh at container start and after the hub exits, as a
belt-and-braces stop that does not depend on ROS or on any driver process.
"""
import sys
import time

try:
    from Rosmaster_Lib import Rosmaster
    car = Rosmaster()          # opens /dev/myserial
    car.set_car_type(1)
    for _ in range(3):
        car.set_car_motion(0.0, 0.0, 0.0)
        time.sleep(0.03)
    print('stop_wheels: zero velocity sent')
except Exception as e:  # serial missing or busy: report, never crash the supervisor
    print('stop_wheels: failed: %s' % e, file=sys.stderr)
    sys.exit(1)
