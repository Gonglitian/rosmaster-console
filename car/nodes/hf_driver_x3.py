#!/usr/bin/env python3
"""Safe chassis driver for the Yahboom ROSMASTER X3.

A copy of Yahboom's Mcnamu_driver_X3.py (yahboomcar_bringup) with three
changes, because the stock driver lets anything on the LAN drive the car and
keeps the last velocity forever when commands stop:

1. It listens on /hub/cmd_vel instead of /cmd_vel. By convention only the hub
   publishes there, so stray /cmd_vel publishers (joystick node, old laptop
   scripts) are ignored.
2. Watchdog: if no command arrives for `cmd_timeout` seconds, it stops the car.
   The hub publishes at 20 Hz, so this only fires when the hub is gone.
3. Hard clamp on every command (`max_linear`, `max_angular`). The hub enforces
   the configured limits; this is the last line of defence.

Everything else (IMU, voltage, joint states, vel_raw at 10 Hz; RGB light and
buzzer subscriptions) matches the stock driver so base_node_X3 and the EKF
keep working unchanged.
"""
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.clock import Clock
from std_msgs.msg import Float32, Int32, Bool
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu, MagneticField, JointState
from Rosmaster_Lib import Rosmaster


def clamp(v, lim):
    if not math.isfinite(v):
        return 0.0
    return max(-lim, min(lim, v))


class SafeDriver(Node):
    def __init__(self):
        super().__init__('hf_driver')
        self.declare_parameter('imu_link', 'imu_link')
        self.declare_parameter('cmd_topic', '/hub/cmd_vel')
        self.declare_parameter('cmd_timeout', 0.5)
        self.declare_parameter('max_linear', 1.0)
        self.declare_parameter('max_angular', 3.0)
        p = lambda n: self.get_parameter(n).get_parameter_value()
        self.imu_link = p('imu_link').string_value
        cmd_topic = p('cmd_topic').string_value
        self.cmd_timeout = p('cmd_timeout').double_value
        self.max_linear = p('max_linear').double_value
        self.max_angular = p('max_angular').double_value

        self.car = Rosmaster()
        self.car.set_car_type(1)
        self.car.create_receive_threading()

        self.last_cmd_time = 0.0
        self.moving = False
        self.watchdog_trips = 0

        self.create_subscription(Twist, cmd_topic, self.on_cmd, 1)
        self.create_subscription(Int32, 'RGBLight', self.on_rgb, 10)
        self.create_subscription(Bool, 'Buzzer', self.on_buzzer, 10)

        self.edition_pub = self.create_publisher(Float32, 'edition', 10)
        self.voltage_pub = self.create_publisher(Float32, 'voltage', 10)
        self.joint_pub = self.create_publisher(JointState, 'joint_states', 10)
        self.vel_pub = self.create_publisher(Twist, 'vel_raw', 50)
        self.imu_pub = self.create_publisher(Imu, 'imu/data_raw', 100)
        self.mag_pub = self.create_publisher(MagneticField, 'imu/mag', 100)

        self.create_timer(0.1, self.publish_state)
        self.create_timer(0.05, self.check_watchdog)
        self.get_logger().info(
            'listening on %s, timeout %.2fs, clamp %.2f m/s %.2f rad/s'
            % (cmd_topic, self.cmd_timeout, self.max_linear, self.max_angular))

    def on_cmd(self, msg):
        vx = clamp(msg.linear.x, self.max_linear)
        vy = clamp(msg.linear.y, self.max_linear)
        wz = clamp(msg.angular.z, self.max_angular)
        speed = math.hypot(vx, vy)
        if speed > self.max_linear:
            vx, vy = vx * self.max_linear / speed, vy * self.max_linear / speed
        self.car.set_car_motion(vx, vy, wz)
        self.last_cmd_time = time.monotonic()
        self.moving = (vx, vy, wz) != (0.0, 0.0, 0.0)

    def check_watchdog(self):
        if self.moving and time.monotonic() - self.last_cmd_time > self.cmd_timeout:
            self.stop()
            self.watchdog_trips += 1
            self.get_logger().warn('no command for %.2fs, stopped (trip #%d)'
                                   % (self.cmd_timeout, self.watchdog_trips))

    def stop(self):
        self.car.set_car_motion(0.0, 0.0, 0.0)
        self.moving = False

    def on_rgb(self, msg):
        for _ in range(3):
            self.car.set_colorful_effect(msg.data, 6, parm=1)

    def on_buzzer(self, msg):
        for _ in range(3):
            self.car.set_beep(1 if msg.data else 0)

    def publish_state(self):
        stamp = Clock().now().to_msg()
        state = JointState()
        state.header.stamp = stamp
        state.header.frame_id = 'joint_states'
        state.name = ['back_right_joint', 'back_left_joint', 'front_left_steer_joint',
                      'front_left_wheel_joint', 'front_right_steer_joint',
                      'front_right_wheel_joint']

        ax, ay, az = self.car.get_accelerometer_data()
        gx, gy, gz = self.car.get_gyroscope_data()
        mx, my, mz = self.car.get_magnetometer_data()
        vx, vy, wz = self.car.get_motion_data()

        imu = Imu()
        imu.header.stamp = stamp
        imu.header.frame_id = self.imu_link
        imu.linear_acceleration.x = ax * 1.0
        imu.linear_acceleration.y = ay * 1.0
        imu.linear_acceleration.z = az * 1.0
        imu.angular_velocity.x = gx * 1.0
        imu.angular_velocity.y = gy * 1.0
        imu.angular_velocity.z = gz * 1.0

        mag = MagneticField()
        mag.header.stamp = stamp
        mag.header.frame_id = self.imu_link
        mag.magnetic_field.x = mx * 1.0
        mag.magnetic_field.y = my * 1.0
        mag.magnetic_field.z = mz * 1.0

        twist = Twist()
        twist.linear.x = vx * 1.0
        twist.linear.y = vy * 1.0
        twist.angular.z = wz * 1.0

        self.vel_pub.publish(twist)
        self.imu_pub.publish(imu)
        self.mag_pub.publish(mag)
        self.voltage_pub.publish(Float32(data=self.car.get_battery_voltage() * 1.0))
        self.edition_pub.publish(Float32(data=self.car.get_version() * 1.0))
        self.joint_pub.publish(state)


def main():
    rclpy.init()
    node = SafeDriver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the wheels whatever happens to this process.
        node.stop()
        time.sleep(0.05)
        node.stop()
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:  # already shut down by the SIGINT handler
            pass


if __name__ == '__main__':
    main()
