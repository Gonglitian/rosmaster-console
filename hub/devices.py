"""Really switching devices off, not just stopping the software that reads them.

RPLIDAR-A1 motor: the A1 has no motor command; its motor runs whenever the USB
adapter's DTR line is deasserted. The Slamtec SDK deasserts DTR when it opens
the port (net_serial.cpp: clearDTR() in open) and never asserts it again, and
Linux deasserts it when the port is closed. So after sllidar_node exits the motor
keeps spinning. The only way to stop it is to hold the port open with DTR
asserted. Verified 2026-09-26: with DTR asserted a SCAN request returns only the
7-byte descriptor (motor stopped); with DTR deasserted, ~3.8 KB/s of samples.
When the hub process exits the port closes and the motor spins again.

Orbbec depth sensor: we never use it, but its USB power control is "on" (never
suspend). Setting it to "auto" lets the kernel suspend it. The colour camera is
already "auto" and suspends by itself once ffmpeg closes /dev/video0.
"""
import glob
import logging
import os

log = logging.getLogger('hub.devices')


class LidarMotor(object):
    """Stops the A1 motor by holding /dev/rplidar open with DTR asserted.

    - stop(): open the port (as the only holder) with DTR asserted -> motor off.
    - release(): close it. Linux deasserts DTR at the last close -> the motor
      starts, so call it ~1.5 s before launching sllidar_node, which sends its
      scan request right after opening the port; with a motor that has only just
      started, the A1 answers with a timeout (80008002) or invalid data (80008000).
    Tried and rejected (2026-09-26): keeping the port open while sllidar_node also
    has it. Every start after a successful run then failed, three attempts in a row.
    """

    def __init__(self, dev='/dev/rplidar'):
        self.dev = dev
        self.ser = None

    def stop(self):
        """Motor off. Blocking; safe to repeat. A handle left over from before the
        USB device re-enumerated fails with EBADF/EIO; then retry with a new one."""
        for attempt in (1, 2):
            try:
                if self.ser is None or not self.ser.is_open:
                    import serial
                    s = serial.Serial()
                    s.port, s.baudrate, s.timeout = self.dev, 115200, 0
                    s.dtr = True
                    s.open()
                    self.ser = s
                self.ser.dtr = True
                log.info('lidar motor stopped (holding %s with DTR asserted)', self.dev)
                return True
            except Exception as e:  # stale handle, device missing, CP2102 refusing DTR
                log.warning('could not stop lidar motor (try %d): %s', attempt, e)
                self.release()
        return False

    def release(self):
        """Close the port: the motor starts and sllidar_node can have the port."""
        if self.ser is None:
            return
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None

    forget = release


def allow_depth_sensor_suspend():
    """Let the unused Orbbec depth sensor (2bc5:060f) autosuspend."""
    done = []
    for dev in glob.glob('/sys/bus/usb/devices/*'):
        try:
            with open(os.path.join(dev, 'idVendor')) as f:
                vendor = f.read().strip()
            with open(os.path.join(dev, 'idProduct')) as f:
                product = f.read().strip()
            if (vendor, product) == ('2bc5', '060f'):
                with open(os.path.join(dev, 'power', 'control'), 'w') as f:
                    f.write('auto')
                done.append(os.path.basename(dev))
        except (OSError, IOError):
            continue
    if done:
        log.info('depth sensor autosuspend enabled: %s', ', '.join(done))
    return done
