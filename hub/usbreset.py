"""Software replug of the USB device behind a serial port.

The RPLIDAR-A1's USB adapter (CP2102) sometimes stops accepting the DTR
request that spins the lidar motor: dmesg shows
`cp210x ttyUSB0: failed set request 0x12 status: -110` and sllidar_node fails
with `Can not start scan: 80008002` (start-scan timeout). Restarting the node
does not help; unbinding and rebinding the USB device does. Needs write access
to /sys, i.e. a privileged container.
"""
import logging
import os
import time

log = logging.getLogger('hub.usb')


def usb_device_for(dev):
    """'/dev/rplidar' -> USB device name such as '1-2'."""
    tty = os.path.basename(os.path.realpath(dev))
    path = os.path.realpath('/sys/class/tty/%s/device' % tty)
    while path not in ('/', '') and not os.path.exists(os.path.join(path, 'idVendor')):
        path = os.path.dirname(path)
    if path in ('/', ''):
        raise RuntimeError('no USB device found behind %s' % dev)
    return os.path.basename(path)


def replug(dev, settle=1.5, wait=6.0, while_unbound=None):
    name = usb_device_for(dev)
    log.warning('USB replug of %s (%s)', dev, name)
    with open('/sys/bus/usb/drivers/usb/unbind', 'w') as f:
        f.write(name)
    if while_unbound is not None:
        while_unbound()   # e.g. drop open handles while the device is gone (closing is instant)
    time.sleep(settle)
    with open('/sys/bus/usb/drivers/usb/bind', 'w') as f:
        f.write(name)
    deadline = time.time() + wait
    while time.time() < deadline:
        if os.path.exists(dev):
            time.sleep(0.5)
            return name
        time.sleep(0.2)
    raise RuntimeError('%s did not come back after replug' % dev)
