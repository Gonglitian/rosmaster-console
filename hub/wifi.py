"""Wi-Fi setup from the browser: the car has no keyboard.

The hub container talks to the host's NetworkManager with nmcli over the host's
D-Bus socket (/run/dbus is mounted in; the container's nmcli 1.22 works against
the host's NetworkManager 1.42).

How the car picks a network (NetworkManager autoconnect priority):
- TASL 100 (lab) > networks added here 60 > ROSMASTER hotspot 50 > the rest 0.
- The hotspot is an access-point profile, so it is always "available": at a place
  with no known network the car falls back to its own hotspot (192.168.1.11).
  A saved network with priority below 50 therefore never wins over the hotspot;
  networks added or re-keyed here get 60.
Only open and WPA/WPA2-Personal networks are supported (no 802.1X/enterprise).
Passwords are passed to nmcli and never logged or returned.
"""
import json
import logging
import os
import subprocess
import threading
import time

log = logging.getLogger('hub.wifi')

IFACE = 'wlan0'
HOTSPOT = 'ROSMASTER'          # NetworkManager profile name of the car's own hotspot
NEW_PRIORITY = 60
# IP the car got on each network, so a page that switches the car can look for it
# at that address when the new network blocks mDNS (rosmaster.local). Kept outside
# the deployed files (deploy_car.sh rsyncs with --delete; .state is excluded).
IP_BOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.state', 'wifi_ips.json')


def _split(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' and '\\\\' escaped)."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == '\\':
            esc = True
        elif ch == ':':
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out


def _nmcli(args, timeout=20):
    p = subprocess.run(['nmcli'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr.strip()


def _rows(args, timeout=20):
    rc, out, err = _nmcli(['-t'] + args, timeout)
    if rc != 0:
        raise RuntimeError(err or 'nmcli failed')
    return [_split(l) for l in out.splitlines() if l]


def saved():
    """Wi-Fi profiles: name, ssid, mode (ap/infrastructure), priority."""
    out = []
    for name, ctype in (r[:2] for r in _rows(['-f', 'NAME,TYPE', 'con', 'show'])):
        if ctype != '802-11-wireless':
            continue
        rc, val, _ = _nmcli(['-g', '802-11-wireless.ssid,802-11-wireless.mode,'
                             'connection.autoconnect-priority,802-11-wireless-security.key-mgmt',
                             'con', 'show', 'id', name])
        if rc != 0:
            continue
        parts = (val.splitlines() + ['', '', '', ''])[:4]
        try:
            prio = int(parts[2] or 0)
        except ValueError:
            prio = 0
        out.append({'name': name, 'ssid': parts[0], 'mode': parts[1] or 'infrastructure',
                    'priority': prio, 'secured': bool(parts[3])})
    out.sort(key=lambda p: -p['priority'])
    return out


def status():
    state, conn = 'unknown', ''
    for dev, dtype, st, c in (r[:4] for r in _rows(['-f', 'DEVICE,TYPE,STATE,CONNECTION', 'dev'])):
        if dev == IFACE:
            state, conn = st, c
    mode = ssid = ip = None
    if conn:
        rc, val, _ = _nmcli(['-g', '802-11-wireless.mode,802-11-wireless.ssid', 'con', 'show', 'id', conn])
        if rc == 0:
            parts = (val.splitlines() + ['', ''])[:2]
            mode, ssid = parts[0] or 'infrastructure', parts[1]
    rc, val, _ = _nmcli(['-g', 'IP4.ADDRESS', 'dev', 'show', IFACE])
    if rc == 0 and val.strip():
        ip = val.strip().splitlines()[0].split('/')[0]
    return {'state': state, 'connection': conn or None, 'mode': mode, 'ssid': ssid, 'ip': ip,
            'hotspot': mode == 'ap'}


def scan(rescan=True):
    """Nearby networks, one entry per SSID (strongest access point), hidden ones dropped."""
    rows = _rows(['-f', 'IN-USE,SSID,SIGNAL,SECURITY,CHAN,FREQ', 'dev', 'wifi', 'list',
                  '--rescan', 'yes' if rescan else 'no'], timeout=40)
    known = {p['ssid'] for p in saved() if p['mode'] != 'ap'}
    best = {}
    for inuse, ssid, sig, sec, chan, freq in (r[:6] for r in rows):
        if not ssid:
            continue
        try:
            sig = int(sig)
        except ValueError:
            sig = 0
        band = '5G' if freq.startswith('5') else '2.4G'
        cur = best.get(ssid)
        if cur is None or sig > cur['signal']:
            best[ssid] = {'ssid': ssid, 'signal': sig, 'security': sec or '', 'band': band,
                          'in_use': inuse == '*' or (cur or {}).get('in_use', False),
                          'known': ssid in known}
        elif inuse == '*':
            cur['in_use'] = True
    return sorted(best.values(), key=lambda n: (-n['in_use'], -n['signal']))


class WifiManager(object):
    """Runs the slow parts (scan, connect) in threads and remembers the outcome."""

    def __init__(self):
        self.last_scan = []
        self.last_scan_time = None
        self.scan_error = None
        self.attempt = None       # {'ssid', 'state': connecting/connected/failed, 'reason', 'time'}
        self._lock = threading.Lock()
        self.ip_by_ssid = self._load_ips()

    def _load_ips(self):
        try:
            with open(IP_BOOK) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _note_ip(self, st):
        if st.get('state') == 'connected' and st.get('mode') != 'ap' and st.get('ssid') and st.get('ip'):
            if self.ip_by_ssid.get(st['ssid']) != st['ip']:
                self.ip_by_ssid[st['ssid']] = st['ip']
                try:
                    os.makedirs(os.path.dirname(IP_BOOK), exist_ok=True)
                    with open(IP_BOOK, 'w') as f:
                        json.dump(self.ip_by_ssid, f)
                except OSError:
                    log.warning('could not save %s', IP_BOOK)

    def refresh_scan(self, rescan=True):
        try:
            self.last_scan = scan(rescan)
            self.last_scan_time = time.time()
            self.scan_error = None
        except Exception as e:  # e.g. scanning refused while the radio is a hotspot
            self.scan_error = str(e)
            log.warning('wifi scan failed: %s', e)
        return self.last_scan

    def where(self):
        """Cheap answer to 'which network is the car on?' for pages probing for it."""
        try:
            st = status()
            self._note_ip(st)
        except Exception as e:
            st = {'state': 'unknown', 'error': str(e)}
        return {'status': st, 'attempt': self.attempt}

    def report(self):
        try:
            st = status()
            self._note_ip(st)
        except Exception as e:
            st = {'state': 'unknown', 'error': str(e)}
        try:
            prof = saved()
        except Exception:
            prof = []
        return {'status': st, 'saved': prof, 'scan': self.last_scan,
                'scan_time': self.last_scan_time, 'scan_error': self.scan_error,
                'attempt': self.attempt, 'hotspot_profile': HOTSPOT, 'new_priority': NEW_PRIORITY,
                'ip_by_ssid': self.ip_by_ssid}

    def _guard(self):
        from . import config
        if config.NO_HARDWARE:
            raise RuntimeError('Wi-Fi changes are disabled in no-hardware (test) mode')

    def connect(self, ssid, password=None):
        """Validate, then switch in the background so the HTTP reply gets out first
        (switching drops a browser that came in over the hotspot)."""
        self._guard()
        ssid = (ssid or '').strip()
        if not ssid or len(ssid) > 32:
            raise ValueError('SSID is empty or longer than 32 bytes')
        if password is not None and password != '' and not (8 <= len(password) <= 63):
            raise ValueError('A WPA password has 8 to 63 characters')
        if not self._lock.acquire(False):
            raise RuntimeError('The previous switch is still in progress')
        self.attempt = {'ssid': ssid, 'state': 'connecting', 'reason': None, 'time': time.time()}
        threading.Thread(target=self._connect, args=(ssid, password), daemon=True).start()

    def _profile_for(self, ssid, password):
        """Returns (profile name, undo) where undo() reverts what we changed."""
        existing = [p for p in saved() if p['ssid'] == ssid and p['mode'] != 'ap']
        if existing:
            name = existing[0]['name']
            old_psk = None
            if password:
                rc, val, _ = _nmcli(['-s', '-g', '802-11-wireless-security.psk', 'con', 'show', 'id', name])
                old_psk = val.strip() if rc == 0 else None
            args = ['con', 'mod', 'id', name, 'connection.autoconnect', 'yes',
                    'connection.autoconnect-priority', str(max(NEW_PRIORITY, existing[0]['priority']))]
            if password:
                args += ['wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
            rc, _, err = _nmcli(args)
            if rc != 0:
                raise RuntimeError(err)

            def undo():
                if old_psk:
                    _nmcli(['con', 'mod', 'id', name, 'wifi-sec.psk', old_psk])
            return name, undo
        if not password and any(n['ssid'] == ssid and n['security'] for n in self.last_scan):
            raise RuntimeError('This network needs a password')
        visible = any(n['ssid'] == ssid for n in self.last_scan)
        args = ['con', 'add', 'type', 'wifi', 'ifname', IFACE, 'con-name', ssid, 'ssid', ssid,
                'connection.autoconnect', 'yes', 'connection.autoconnect-priority', str(NEW_PRIORITY)]
        if not visible:
            args += ['802-11-wireless.hidden', 'yes']
        if password:
            args += ['wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
        rc, _, err = _nmcli(args)
        if rc != 0:
            raise RuntimeError(err)
        return ssid, lambda: _nmcli(['con', 'delete', 'id', ssid])   # don't keep a profile that failed

    def _connect(self, ssid, password):
        prev = None
        undo = None
        try:
            prev = status().get('connection')
            name, undo = self._profile_for(ssid, password)
            time.sleep(1.5)
            log.info('switching Wi-Fi from %r to %r', prev, ssid)
            rc, _, err = _nmcli(['--wait', '45', 'con', 'up', 'id', name], timeout=60)
            if rc == 0:
                st = status()
                self._note_ip(st)
                self.attempt = {'ssid': ssid, 'state': 'connected', 'reason': None,
                                'ip': st.get('ip'), 'time': time.time()}
                log.info('Wi-Fi now %r, ip %s', ssid, st.get('ip'))
            else:
                self._failed(ssid, err, prev, undo)
        except Exception as e:
            self._failed(ssid, str(e), prev, undo)
        finally:
            self._lock.release()

    def _failed(self, ssid, reason, prev, undo):
        log.warning('Wi-Fi switch to %r failed: %s', ssid, reason)
        self.attempt = {'ssid': ssid, 'state': 'failed', 'reason': _explain(reason), 'time': time.time()}
        if undo is not None:
            try:
                undo()
            except Exception:
                log.exception('undo failed')
        self._restore(prev)

    def _restore(self, prev):
        """Go back to the network we came from; if that does not work, open the
        hotspot so the car can always be reached and set up again. Waiting for
        NetworkManager to reconnect on its own is not enough: once anything else
        (e.g. the hotspot) is up, it never switches back by itself."""
        try:
            if status()['state'] == 'connected':
                return
        except Exception:
            pass
        if prev and prev != HOTSPOT:
            log.info('restoring previous Wi-Fi %r', prev)
            rc, _, err = _nmcli(['--wait', '30', 'con', 'up', 'id', prev], timeout=45)
            if rc == 0:
                return
            log.warning('could not restore %r: %s', prev, err)
        log.warning('starting the %s hotspot so the car stays reachable', HOTSPOT)
        _nmcli(['--wait', '30', 'con', 'up', 'id', HOTSPOT], timeout=45)

    def hotspot(self):
        self._guard()
        if not self._lock.acquire(False):
            raise RuntimeError('The previous switch is still in progress')
        self.attempt = {'ssid': HOTSPOT, 'state': 'connecting', 'reason': None, 'time': time.time()}

        def run():
            try:
                time.sleep(1.5)
                rc, _, err = _nmcli(['--wait', '30', 'con', 'up', 'id', HOTSPOT], timeout=45)
                self.attempt = {'ssid': HOTSPOT, 'state': 'connected' if rc == 0 else 'failed',
                                'reason': None if rc == 0 else _explain(err), 'time': time.time()}
            finally:
                self._lock.release()
        threading.Thread(target=run, daemon=True).start()

    def forget(self, name):
        self._guard()
        st = status()
        if name == HOTSPOT:
            raise ValueError("The car's own hotspot cannot be deleted")
        if name == st.get('connection'):
            raise ValueError('This network is in use; switch to another one first')
        rc, _, err = _nmcli(['con', 'delete', 'id', name])
        if rc != 0:
            raise RuntimeError(err)


def _explain(err):
    first = (err or '').strip().splitlines()[0] if (err or '').strip() else ''   # drop nmcli's "Hint:" line
    e = first.lower()
    if 'secrets were required' in e or 'no secrets' in e or 'psk' in e:
        return 'wrong or missing password'
    if 'no network with ssid' in e or 'not found' in e or 'could not be found' in e:
        return 'network not found nearby'
    if 'timeout' in e or 'timed out' in e:
        return 'timed out (weak signal or wrong password)'
    return first or 'unknown error'
