"""Chassis bringup for the X3, started and stopped by the hub.

Same nodes as Yahboom's yahboomcar_bringup_X3_launch.py, except:
- the stock driver (Mcnamu_driver_X3) is replaced by car/nodes/hf_driver_x3.py,
  which only listens to /hub/cmd_vel and stops on its own after 0.5 s of silence;
- the joystick node (yahboom_joy_X3) is dropped, because it publishes /cmd_vel;
- the static base_link -> laser transform from laser_bringup_launch.py lives here,
  so the TF tree is complete whether or not the lidar is running.
"""
import os

from ament_index_python.packages import get_package_share_directory, get_package_share_path
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, '..', 'nodes', 'hf_driver_x3.py')


def generate_launch_description():
    urdf = get_package_share_path('yahboomcar_description') / 'urdf/yahboomcar_X3.urdf'
    robot_description = ParameterValue(Command(['xacro ', str(urdf)]), value_type=str)
    imu_filter_config = os.path.join(
        get_package_share_directory('yahboomcar_bringup'), 'param', 'imu_filter_param.yaml')

    return LaunchDescription([
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             parameters=[{'robot_description': robot_description}]),
        Node(package='joint_state_publisher', executable='joint_state_publisher'),
        ExecuteProcess(
            cmd=['python3', DRIVER, '--ros-args',
                 '-p', 'cmd_timeout:=' + os.environ.get('HF_CMD_TIMEOUT', '0.5'),
                 '-p', 'max_linear:=' + os.environ.get('HF_DRIVER_MAX_LINEAR', '1.0'),
                 '-p', 'max_angular:=' + os.environ.get('HF_DRIVER_MAX_ANGULAR', '3.0')],
            output='screen', sigterm_timeout='3', sigkill_timeout='5'),
        Node(package='yahboomcar_base_node', executable='base_node_X3',
             parameters=[{'pub_odom_tf': False}]),
        Node(package='imu_filter_madgwick', executable='imu_filter_madgwick_node',
             parameters=[imu_filter_config]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('robot_localization'), 'launch', 'ekf_x1_x3_launch.py'))),
        Node(package='tf2_ros', executable='static_transform_publisher',
             arguments=['0.0435', '5.258E-05', '0.11', '3.14', '0', '0', 'base_link', 'laser']),
    ])
