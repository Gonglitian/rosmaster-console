"""Hub settings. Every value can be overridden by an HF_* environment variable."""
import os


def _env(name, default, cast=float):
    raw = os.environ.get('HF_' + name)
    return default if raw is None else cast(raw)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PORT = _env('PORT', 8080, int)
WEB_DIR = os.path.join(REPO_ROOT, 'web')
LAUNCH_DIR = os.path.join(REPO_ROOT, 'car', 'launch')

# Control output. The hub publishes /hub/cmd_vel at CONTROL_HZ, always, so the
# driver's own 0.5 s watchdog only fires when the hub is gone.
CONTROL_HZ = _env('CONTROL_HZ', 20.0)
CMD_TOPIC = '/hub/cmd_vel'
# A source (manual or policy) that has been silent this long is treated as zero.
SOURCE_TIMEOUT = _env('SOURCE_TIMEOUT', 0.5)
MAX_LINEAR = _env('MAX_LINEAR', 0.7)          # m/s, |(vx, vy)|
MAX_ANGULAR = _env('MAX_ANGULAR', 1.5)        # rad/s
# 0.25 m/s per decider cycle (~0.17 s) in the paper setup ~= 1.5 m/s^2.
MAX_LINEAR_ACCEL = _env('MAX_LINEAR_ACCEL', 1.5)   # m/s^2
MAX_ANGULAR_ACCEL = _env('MAX_ANGULAR_ACCEL', 4.0)  # rad/s^2

# Sensors
LIDAR_START_TIMEOUT = _env('LIDAR_START_TIMEOUT', 12.0)
LIDAR_MAX_ATTEMPTS = _env('LIDAR_MAX_ATTEMPTS', 3, int)
LIDAR_SPINUP = _env('LIDAR_SPINUP', 1.5)   # s of motor spin-up before sllidar_node starts
BASE_START_TIMEOUT = _env('BASE_START_TIMEOUT', 15.0)
STALE_TOPIC_TIMEOUT = _env('STALE_TOPIC_TIMEOUT', 5.0)
# Stop all sensors after this long with no panel connected and no active policy.
IDLE_OFF_AFTER = _env('IDLE_OFF_AFTER', 600.0)

# Camera: the UVC colour camera emits MJPEG natively, so frames are forwarded
# as-is (no encoding on the Pi).
CAMERA_DEVICE = os.environ.get('HF_CAMERA_DEVICE', '/dev/video0')
CAMERA_SIZE = os.environ.get('HF_CAMERA_SIZE', '640x480')
CAMERA_MAX_FPS = _env('CAMERA_MAX_FPS', 15.0)

# Geometry of the laser in base_link (from Yahboom's laser_bringup_launch.py).
LASER_X = 0.0435
LASER_YAW = 3.14

STATE_HZ = 5.0
