#!/usr/bin/env python3
"""Bridge: forward a ROS 2 Twist topic (default /cmd_vel) to the hub as a policy worker.

For running an existing ROS pipeline unchanged, e.g. the old human-following
decider on tasl-l1, which publishes /cmd_vel. The car ignores /cmd_vel; this
bridge turns each message into a policy_cmd, so the pipeline drives the car
under the hub's rules (Give control, manual override, E-STOP, limits, 0.5 s
timeout). Needs ROS 2 (rclpy) and tornado, i.e. tasl-l1's /usr/bin/python3:

    source /opt/ros/foxy/setup.bash          # tasl-l1: already in ~/.bashrc
    ROS_DOMAIN_ID=32 /usr/bin/python3 worker/examples/ros_cmd_vel_bridge.py [--topic /cmd_vel]

Each ROS message is forwarded once; stale commands are never repeated, so if
the pipeline stops publishing, the hub stops the car after 0.5 s.
"""
import argparse
import os
import sys
import threading

import rclpy
from geometry_msgs.msg import Twist

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from rc_worker import Worker  # noqa: E402


class CmdVelBridge(Worker):
    def __init__(self, topic, **kw):
        super(CmdVelBridge, self).__init__(**kw)
        self.topic = topic
        self.count = 0
        rclpy.init()
        self.node = rclpy.create_node('rc_cmd_vel_bridge')
        self.node.create_subscription(Twist, topic, self._on_twist, 10)
        threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True).start()
        # Show who publishes the topic, so a stray publisher is noticed.
        self.node.create_timer(5.0, self._report)

    def _on_twist(self, msg):      # ROS thread; send_cmd is thread-safe
        self.count += 1
        self.send_cmd(msg.linear.x, msg.linear.y, msg.angular.z)

    def _report(self):
        pubs = [i.node_name for i in self.node.get_publishers_info_by_topic(self.topic)]
        print('%s: %d msgs in 5 s from %s | %s' % (
            self.topic, self.count, pubs or 'no publisher',
            'DRIVING' if self.driving else ('active, not driving' if self.active else 'no control')), flush=True)
        self.count = 0

    def on_active(self, active):
        print('>>> control %s' % ('GIVEN: %s now drives the car' % self.topic if active else 'taken back'), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='ws://rosmaster.local:8080/ws')
    p.add_argument('--topic', default='/cmd_vel', help='geometry_msgs/Twist topic to forward')
    p.add_argument('--name', default='ros-bridge')
    a = p.parse_args()
    CmdVelBridge(a.topic, url=a.url, name=a.name).run()
