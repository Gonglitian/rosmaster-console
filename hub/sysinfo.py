"""Car health shown in the panel footer: IP, disk, CPU temperature."""
import shutil
import socket


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))  # no packet is sent; just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def disk_free_gb(path='/'):
    return round(shutil.disk_usage(path).free / 1e9, 2)


def cpu_temp_c():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def snapshot():
    out = {'ip': lan_ip(), 'hostname': socket.gethostname(),
           'disk_free_gb': disk_free_gb(), 'cpu_temp_c': cpu_temp_c()}
    try:
        from . import wifi
        out['net'] = wifi.status()
    except Exception:
        out['net'] = None
    return out
