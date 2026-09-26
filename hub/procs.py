"""Child processes (ros2 launch, ffmpeg) in their own process groups, so that
stopping one stops everything it spawned."""
import collections
import logging
import os
import signal
import subprocess
import threading
import time

log = logging.getLogger('hub.procs')


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ChildProcess(object):
    """Runs argv; each stdout line is kept in a ring buffer and passed to
    on_line(line) on the asyncio loop."""

    def __init__(self, name, argv, loop, on_line=None, env=None):
        self.name = name
        self.argv = argv
        self.loop = loop
        self.on_line = on_line
        self.env = env
        self.proc = None
        self.lines = collections.deque(maxlen=200)
        self.started_at = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self):
        return None if self.proc is None else self.proc.poll()

    def start(self):
        log.info('[%s] start: %s', self.name, ' '.join(self.argv))
        self.lines.clear()
        self.proc = subprocess.Popen(
            self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, preexec_fn=os.setsid, env=self.env)
        self.started_at = time.time()
        threading.Thread(target=self._read, name='read-' + self.name, daemon=True).start()

    def _read(self):
        proc = self.proc
        for raw in iter(proc.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            self.lines.append(line)
            if self.on_line is not None:
                self.loop.call_soon_threadsafe(self.on_line, line)
        proc.stdout.close()

    def stop(self, timeout=8.0):
        """SIGINT the whole group (ros2 launch shuts its nodes down cleanly on
        SIGINT, and the driver stops the wheels), then escalate. Blocking; call
        it from a thread executor."""
        if not self.running:
            return
        pgid = os.getpgid(self.proc.pid)
        for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                break
            try:
                self.proc.wait(wait)
                break
            except subprocess.TimeoutExpired:
                log.warning('[%s] still alive after %s', self.name, sig.name)
        # `ros2 launch` can exit before its nodes finish shutting down. Give the
        # rest of the group time to exit on its own: a node killed mid-shutdown
        # leaves stale DDS state behind. Only then SIGKILL what is left.
        deadline = time.time() + 5.0
        while time.time() < deadline and _group_alive(pgid):
            time.sleep(0.1)
        if _group_alive(pgid):
            log.warning('[%s] group members still alive after 5 s, SIGKILL', self.name)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log.info('[%s] stopped (rc=%s)', self.name, self.returncode)

    def tail(self, n=20):
        return list(self.lines)[-n:]
