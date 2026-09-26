"""Entry point: `python3 -m hub` (inside the car's hub container)."""
import asyncio
import logging
import signal

from . import config
from .app import Hub, make_app


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(name)s %(levelname)s %(message)s')
    log = logging.getLogger('hub')
    loop = asyncio.get_event_loop()

    from .ros_bridge import RosBridge
    hub = Hub(loop, RosBridge)
    hub.bridge.start()
    make_app(hub).listen(config.PORT)
    loop.create_task(hub.sensors.startup())
    loop.create_task(hub.control_loop())
    loop.create_task(hub.state_loop())
    log.info('hub listening on :%d (web dir %s)', config.PORT, config.WEB_DIR)

    stop = asyncio.Event()
    # Installed after rclpy.init(), so these take over SIGINT from rclpy.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(stop.wait())
    log.info('shutting down: stopping car and sensors')
    loop.run_until_complete(hub.shutdown())


if __name__ == '__main__':
    main()
