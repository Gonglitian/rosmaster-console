"""Pack voltage while the chassis driver is off.

Only the chassis driver reads the STM32 serial port, so with the chassis off
there used to be no battery data at all. This reader opens /dev/myserial with
Rosmaster_Lib (read-only use: it never sends a motion command) and polls the
voltage the board reports on its own. The SensorManager stops it before the
chassis driver starts (only one process may own the port) and restarts it when
the chassis is switched off.
"""
import logging
import threading

log = logging.getLogger('hub.battery')

# Rosmaster_Lib's receive thread has no exit path: when we close the port it dies
# with a traceback on its next read. Expected; log one line instead.
_default_excepthook = threading.excepthook


def _quiet_receive_thread(args):
    if args.thread is not None and args.thread.name == 'task_serial_receive':
        log.debug('Rosmaster_Lib receive thread ended: %r', args.exc_value)
        return
    _default_excepthook(args)


threading.excepthook = _quiet_receive_thread


class BatteryReader(object):
    def __init__(self, loop, on_voltage, dev='/dev/myserial', period=0.1):
        self.loop = loop
        self.on_voltage = on_voltage
        self.dev = dev
        self.period = period
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='battery-reader', daemon=True)
        self._thread.start()

    def stop(self, timeout=3.0):
        """Blocking: returns once the serial port is closed."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def _run(self):
        car = None
        try:
            from Rosmaster_Lib import Rosmaster
            car = Rosmaster(com=self.dev)
            car.create_receive_threading()
            log.info('battery monitor reading %s (chassis driver off)', self.dev)
            while not self._stop.wait(self.period):
                self.loop.call_soon_threadsafe(self.on_voltage, car.get_battery_voltage())
        except Exception as e:
            log.warning('battery monitor stopped: %s', e)
        finally:
            if car is not None:
                try:
                    car.ser.close()   # Rosmaster_Lib's receive thread exits on the next read
                except Exception:
                    pass
            log.info('battery monitor released %s', self.dev)
