"""The hub's only contact with ROS: one rclpy node spun in a background thread.

Sensor callbacks run in the ROS thread, convert the message to a small dict
there, and hand it to the asyncio loop with call_soon_threadsafe. Every message
gets a hub sequence number and the car's receive time ('stamp', wall clock).
"""
import collections
import logging
import math
import threading
import time

import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, Float32

log = logging.getLogger('hub.ros')


class RateMeter(object):
    """Receive rate over the last few seconds, plus the time of the last message."""

    def __init__(self, window=3.0):
        self.window = window
        self.times = collections.deque()
        self.last = 0.0

    def hit(self, t):
        self.last = t
        self.times.append(t)
        while self.times and t - self.times[0] > self.window:
            self.times.popleft()

    def hz(self, now):
        while self.times and now - self.times[0] > self.window:
            self.times.popleft()
        if len(self.times) < 2:
            return 0.0
        return (len(self.times) - 1) / max(1e-6, self.times[-1] - self.times[0])

    def age(self, now):
        return None if self.last == 0.0 else now - self.last


def _yaw(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class RosBridge(object):
    def __init__(self, loop, cmd_topic, on_scan, on_odom):
        self.loop = loop
        self.cmd_topic = cmd_topic
        self.on_scan = on_scan
        self.on_odom = on_odom
        self.rates = {'scan': RateMeter(), 'odom': RateMeter(), 'voltage': RateMeter()}
        self.voltage = None
        self._seq = {'scan': 0, 'odom': 0}
        self.node = None
        self._executor = None
        self._thread = None

    def start(self):
        rclpy.init()
        self.node = Node('hf_hub')
        self._cmd_pub = self.node.create_publisher(Twist, self.cmd_topic, 1)
        self._buzzer_pub = self.node.create_publisher(Bool, 'Buzzer', 1)
        self.node.create_subscription(LaserScan, '/scan', self._scan_cb, qos_profile_sensor_data)
        self.node.create_subscription(Odometry, '/odom', self._odom_cb, qos_profile_sensor_data)
        self.node.create_subscription(Float32, '/voltage', self._voltage_cb, 10)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._spin, name='ros-spin', daemon=True)
        self._thread.start()
        log.info('ROS bridge up, publishing %s', self.cmd_topic)

    def _spin(self):
        try:
            self._executor.spin()
        except Exception:  # rclpy raises on shutdown
            log.debug('executor stopped', exc_info=True)

    def shutdown(self):
        if self.node is None:
            return
        try:
            self.publish_cmd(0.0, 0.0, 0.0)
            self._executor.shutdown(timeout_sec=1.0)
            self.node.destroy_node()
            rclpy.shutdown()
        except Exception:
            log.debug('shutdown', exc_info=True)

    # ---- outputs (called from the asyncio thread) ---------------------
    def publish_cmd(self, vx, vy, wz):
        msg = Twist()
        msg.linear.x, msg.linear.y, msg.angular.z = float(vx), float(vy), float(wz)
        self._cmd_pub.publish(msg)

    def buzzer(self, on):
        self._buzzer_pub.publish(Bool(data=bool(on)))

    def count_publishers(self, topic):
        return self.node.count_publishers(topic)

    def publisher_nodes(self, topic):
        """Names of nodes publishing `topic` (used to spot a foreign chassis driver)."""
        names = []
        for name, ns in self.node.get_node_names_and_namespaces():
            try:
                for t, _ in self.node.get_publisher_names_and_types_by_node(name, ns):
                    if t == topic:
                        names.append(name)
            except Exception:
                pass
        return names

    # ---- sensor callbacks (ROS thread) --------------------------------
    def _scan_cb(self, msg):
        now = time.time()
        self.rates['scan'].hit(now)
        self._seq['scan'] += 1
        r = np.asarray(msg.ranges, dtype=np.float32)
        bad = ~np.isfinite(r) | (r < msg.range_min) | (r > msg.range_max)
        cm = np.where(bad, 0, np.round(r * 100.0)).astype(np.int32)
        payload = {
            't': 'scan', 'seq': self._seq['scan'], 'stamp': round(now, 4),
            'hstamp': msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            'amin': msg.angle_min, 'ainc': msg.angle_increment, 'cm': cm.tolist(),
        }
        self.loop.call_soon_threadsafe(self.on_scan, payload)

    def _odom_cb(self, msg):
        now = time.time()
        self.rates['odom'].hit(now)
        self._seq['odom'] += 1
        p, tw = msg.pose.pose, msg.twist.twist
        payload = {
            't': 'odom', 'seq': self._seq['odom'], 'stamp': round(now, 4),
            'x': round(p.position.x, 4), 'y': round(p.position.y, 4),
            'yaw': round(_yaw(p.orientation), 4),
            'vx': round(tw.linear.x, 3), 'vy': round(tw.linear.y, 3), 'wz': round(tw.angular.z, 3),
        }
        self.loop.call_soon_threadsafe(self.on_odom, payload)

    def _voltage_cb(self, msg):
        self.rates['voltage'].hit(time.time())
        self.voltage = round(msg.data, 2)

    def topic_status(self):
        now = time.time()
        out = {}
        for name, meter in self.rates.items():
            age = meter.age(now)
            out[name] = {'hz': round(meter.hz(now), 2), 'age': None if age is None else round(age, 2)}
        return out
