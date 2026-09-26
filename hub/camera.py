"""Colour camera as MJPEG, with no encoding on the Pi.

The Orbbec's UVC colour stream (/dev/video0) outputs MJPEG natively
(640x480 @ 30 fps, ~70 KB per frame, standard JPEG with Huffman tables), so
ffmpeg only copies frames out (`-c:v copy`) and we split them on the JPEG
start/end markers. Viewers are served the latest frame, capped at
CAMERA_MAX_FPS each.
"""
import asyncio
import collections
import logging
import os
import signal
import subprocess
import threading
import time

log = logging.getLogger('hub.camera')

SOI, EOI = b'\xff\xd8', b'\xff\xd9'


class Camera(object):
    def __init__(self, loop, device, size):
        self.loop = loop
        self.device = device
        self.size = size
        self.proc = None
        self.latest = None
        self.frame_id = 0
        self.frame_time = 0.0
        self.errors = collections.deque(maxlen=20)
        self._event = asyncio.Event()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'v4l2',
                '-input_format', 'mjpeg', '-video_size', self.size, '-framerate', '30',
                '-i', self.device, '-c:v', 'copy', '-f', 'mjpeg', 'pipe:1']
        log.info('start: %s', ' '.join(argv))
        self.errors.clear()
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL, preexec_fn=os.setsid)
        threading.Thread(target=self._read_frames, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._read_errors, args=(self.proc,), daemon=True).start()

    def stop(self):
        if not self.running:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
            self.proc.wait(3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.latest = None
        self.loop.call_soon_threadsafe(self._wake)

    def _read_frames(self, proc):
        buf = bytearray()
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, 'read1') else proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(SOI)
                if start < 0:
                    buf.clear()
                    break
                end = buf.find(EOI, start + 2)
                if end < 0:
                    if start > 0:
                        del buf[:start]
                    break
                frame = bytes(buf[start:end + 2])
                del buf[:end + 2]
                self.loop.call_soon_threadsafe(self._on_frame, frame)

    def _read_errors(self, proc):
        for raw in iter(proc.stderr.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            self.errors.append(line)
            log.warning('ffmpeg: %s', line)

    def _on_frame(self, frame):
        self.latest = frame
        self.frame_id += 1
        self.frame_time = time.time()
        self._wake()

    def _wake(self):
        self._event.set()
        self._event = asyncio.Event()

    async def wait_frame(self, timeout):
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.latest
