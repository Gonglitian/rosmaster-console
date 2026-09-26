'use strict';
// RosMaster Console. Talks to the hub over /ws (protocol in docs/API.md).
// Coordinates: ROS base_link, x forward, y left, wz > 0 turns left.

const $ = (id) => document.getElementById(id);
const S = {
  ws: null, connected: false, cfg: null, state: null, scan: null, odom: null, debug: null,
  range: 4, rtts: [], seq: 0, dirty: true,
  input: { move: [0, 0], rot: 0, padMove: false, padRot: false, keys: new Set() },
  wasDriving: false,
};
const MODE_TEXT = { idle: 'idle', manual: 'manual', policy: 'policy' };
const SENSOR_TEXT = { off: 'off', starting: 'starting', on: 'on', error: 'error', stopping: 'stopping' };
const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function store(key, val) { try { localStorage.setItem(key, val); } catch (e) {} }
function load(key, dflt) { try { const v = localStorage.getItem(key); return v === null ? dflt : v; } catch (e) { return dflt; } }

// ---------- connection ----------
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  S.ws = ws;
  ws.onopen = () => { S.connected = true; renderConn(); };
  ws.onclose = () => {
    S.connected = false; S.ws = null; renderConn();
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.t === 'scan') { S.scan = m; S.scan.rx = performance.now(); S.dirty = true; }
    else if (m.t === 'odom') { S.odom = m; S.dirty = true; }
    else if (m.t === 'state') { S.state = m; renderState(); S.dirty = true; }
    else if (m.t === 'policy_debug') { S.debug = m; S.debug.rx = performance.now(); S.dirty = true; }
    else if (m.t === 'hello') { S.cfg = m.config; }
    else if (m.t === 'error') { $('sensor-msg').textContent = 'Hub: ' + m.msg; }
    else if (m.t === 'pong') {
      S.rtts.push(performance.now() - m.c); if (S.rtts.length > 5) S.rtts.shift();
      const sorted = S.rtts.slice().sort((a, b) => a - b);
      $('rtt').textContent = Math.round(sorted[Math.floor(sorted.length / 2)]) + ' ms';
    }
  };
}
function send(m) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(m)); }
setInterval(() => send({ t: 'ping', c: performance.now() }), 1000);

function renderConn() {
  $('conn').classList.toggle('on', S.connected);
  $('offline').classList.toggle('hidden', S.connected);
  if (!S.connected) $('rtt').textContent = '— ms';
}

// ---------- state ----------
function renderState() {
  const st = S.state, c = st.control;
  const mode = $('mode');
  mode.className = 'chip ' + (c.estop ? 'estop' : c.mode);
  mode.textContent = c.estop ? 'E-STOP' : (MODE_TEXT[c.mode] || c.mode);
  $('estop-banner').classList.toggle('hidden', !c.estop);
  $('estop-reason').textContent = c.estop_reason ? ' (' + c.estop_reason + ')' : '';

  renderBattery(st.battery);
  renderBatteryPage(st);
  renderPolicy(st);

  let msg = '', bad = false;
  for (const btn of document.querySelectorAll('[data-sensor]')) {
    const s = st.sensors[btn.dataset.sensor];
    btn.className = 'sensor ' + s.state;
    let text = SENSOR_TEXT[s.state] || s.state;
    if (s.state === 'starting' && s.attempt > 1) text += ' #' + s.attempt;
    btn.querySelector('span').textContent = text;
    if (s.message && s.state !== 'stopping') {
      msg += btn.querySelector('b').textContent + ': ' + s.message + '  ';
      bad = bad || s.state === 'error';
    }
  }
  $('sensor-msg').textContent = msg;
  $('sensor-msg').classList.toggle('bad', bad);
  $('beep').disabled = st.sensors.base.state !== 'on';

  const hb = $('hand-back');
  const canResume = c.active_policy && c.mode === 'idle' && !c.estop;
  hb.disabled = c.estop || !(c.mode === 'manual' || canResume);
  hb.textContent = c.active_policy ? 'Hand back to policy' : 'Exit manual';

  const o = c.output;
  $('cmd').textContent = 'Output vx ' + o[0].toFixed(2) + ' · vy ' + o[1].toFixed(2) + ' · ω ' + o[2].toFixed(2) +
    (c.mode !== 'idle' && c.source_age != null ? ' · last command ' + Math.round(c.source_age * 1000) + ' ms ago' : '');
  for (const p of document.querySelectorAll('.pad')) p.classList.toggle('disabled', c.estop);

  const cam = st.sensors.camera.state === 'on', img = $('cam');
  $('camera-card').classList.toggle('hidden', !cam);
  if (cam && !img.getAttribute('src')) img.src = '/camera.mjpg?' + Date.now();
  if (!cam && img.getAttribute('src')) img.removeAttribute('src');

  const t = st.topics, sys = st.sys || {}, net = sys.net || {};
  $('info').textContent = [
    'lidar ' + t.scan.hz.toFixed(1) + ' Hz', 'odom ' + t.odom.hz.toFixed(1) + ' Hz',
    'dashboards ' + (st.panels != null ? st.panels : st.clients),
    net.hotspot ? 'hotspot ' + net.ssid : net.ssid ? 'Wi-Fi ' + net.ssid : null,
    (net.ip || sys.ip) ? 'IP ' + (net.ip || sys.ip) : null,
    sys.disk_free_gb != null ? 'disk free ' + sys.disk_free_gb.toFixed(1) + ' GB' : null,
    sys.cpu_temp_c != null ? 'CPU ' + sys.cpu_temp_c + '°C' : null,
    'hub up ' + fmtDur(st.uptime),
  ].filter(Boolean).join(' · ');
}
// Battery chip in the header: the hub estimates percent from resting voltage (hub/battery.py).
const BAT_STATE = { resting: '', settling: 'estimating', driving: 'driving, held' };
function renderBattery(b) {
  const el = $('battery');
  el.classList.remove('low', 'warn');
  if (!b || b.state === 'nodata') { el.textContent = '🔋 —'; el.title = 'No voltage reading'; return; }
  const pct = b.pct == null ? '—' : '≈' + b.pct + '%';
  const note = BAT_STATE[b.state] ? ' · ' + BAT_STATE[b.state] : '';
  el.textContent = '🔋 ' + pct + ' · ' + b.v.toFixed(1) + ' V' + note;
  const LEVEL = { warn: 'Battery low: charge soon', critical: 'Battery very low: finish and charge', stop: 'At the 9.6 V alarm: stop and charge now' };
  el.title = (LEVEL[b.level] ? LEVEL[b.level] + '\n' : '') + 'Resting voltage ' + (b.v_rest == null ? '—' : b.v_rest.toFixed(2) + ' V') +
    '. Estimated from a 3S NMC curve, 0% = 9.6 V (Yahboom alarm). Pack chemistry not confirmed. Click for details.';
  if (b.level === 'critical' || b.level === 'stop') el.classList.add('low');
  else if (b.level === 'warn') el.classList.add('warn');
}
function fmtDur(s) { return s < 90 ? s + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h'; }

// ---------- policy workers ----------
function renderPolicy(st) {
  const c = st.control, workers = st.workers || [];
  $('pol-mode').textContent = c.active_policy
    ? (c.mode === 'policy' && !c.estop ? 'policy is driving' : 'policy has control, not driving (' + (c.estop ? 'E-STOP' : c.mode) + ')')
    : 'no policy has control';
  if (!workers.length) {
    $('pol-list').innerHTML = '<li class="note">No policy worker connected. Start one on a laptop on the same network, e.g. <code>python3 worker/examples/keep_distance.py</code> (see docs/POLICY.md).</li>';
    return;
  }
  $('pol-list').innerHTML = workers.map((w) => {
    const driving = w.active && c.mode === 'policy' && !c.estop;
    const meta = w.cmd_hz + ' Hz' + (w.latency_ms != null ? ' · ' + w.latency_ms + ' ms obs→cmd' : '') +
      (w.last_cmd_age != null && w.last_cmd_age > 1 ? ' · silent ' + w.last_cmd_age.toFixed(0) + ' s' : '');
    const btn = w.active
      ? '<button type="button" data-deactivate="1">Stop policy</button>'
      : '<button type="button" data-activate="' + esc(w.id) + '"' + (c.estop ? ' disabled' : '') + '>Give control</button>';
    return '<li class="nw-item"><div class="top"><span class="name">' + esc(w.name) + ' <span class="meta">@' + esc(w.host) + '</span></span>' +
      (driving ? '<span class="nw-tag use">driving</span>' : w.active ? '<span class="nw-tag">has control</span>' : '') +
      '<span class="meta">' + meta + '</span>' + btn + '</div></li>';
  }).join('');
}
$('pol-list').addEventListener('click', (e) => {
  const a = e.target.closest('[data-activate]'), d = e.target.closest('[data-deactivate]');
  if (a) send({ t: 'activate_policy', worker_id: a.dataset.activate });
  if (d) send({ t: 'deactivate_policy' });
});

// ---------- buttons ----------
$('estop').addEventListener('click', () => send({ t: 'estop' }));
$('estop-release').addEventListener('click', () => send({ t: 'estop_release' }));
$('hand-back').addEventListener('click', () => send({ t: 'hand_back' }));
$('beep').addEventListener('click', () => send({ t: 'beep' }));
for (const btn of document.querySelectorAll('[data-sensor]')) {
  btn.addEventListener('click', () => {
    const s = S.state && S.state.sensors[btn.dataset.sensor];
    if (!s) return;
    send({ t: 'sensor', name: btn.dataset.sensor, on: s.state === 'off' || s.state === 'error' });
  });
}
for (const btn of document.querySelectorAll('[data-range]')) {
  btn.addEventListener('click', () => {
    S.range = +btn.dataset.range; store('range', S.range); S.dirty = true;
    document.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('on', b === btn));
  });
}
for (const id of ['vmax', 'wmax']) {
  const el = $(id), out = $(id + '-out');
  el.value = load(id, el.value);
  const sync = () => { out.textContent = (+el.value).toFixed(2); store(id, el.value); };
  el.addEventListener('input', sync); sync();
}
S.range = +load('range', 4);
document.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('on', +b.dataset.range === S.range));

// ---------- manual driving ----------
function makePad(el, horizontalOnly, onMove) {
  const knob = el.querySelector('.knob');
  let id = null;
  const update = (e) => {
    const r = el.getBoundingClientRect();
    const rad = (horizontalOnly ? r.width : Math.min(r.width, r.height)) / 2;
    let dx = (e.clientX - (r.left + r.width / 2)) / rad;
    let dy = horizontalOnly ? 0 : (e.clientY - (r.top + r.height / 2)) / rad;
    const len = Math.hypot(dx, dy);
    if (len > 1) { dx /= len; dy /= len; }
    const kx = dx * (horizontalOnly ? r.width / 2 - r.height / 2 : rad * 0.64);
    knob.style.transform = 'translate(calc(-50% + ' + kx + 'px), calc(-50% + ' + dy * rad * 0.64 + 'px))';
    const dead = (v) => (Math.abs(v) < 0.08 ? 0 : v);
    onMove(dead(dx), dead(dy), true);
  };
  el.addEventListener('pointerdown', (e) => {
    if (S.state && S.state.control.estop) return;
    id = e.pointerId; el.setPointerCapture(id); el.classList.add('active'); update(e);
  });
  el.addEventListener('pointermove', (e) => { if (e.pointerId === id) update(e); });
  const end = (e) => {
    if (e.pointerId !== id) return;
    id = null; el.classList.remove('active'); knob.style.transform = ''; onMove(0, 0, false);
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}
makePad($('pad-move'), false, (dx, dy, on) => { S.input.move = [dx, dy]; S.input.padMove = on; pushManual(); });
makePad($('pad-rot'), true, (dx, dy, on) => { S.input.rot = dx; S.input.padRot = on; pushManual(); });

const KEYS = ['w', 'a', 's', 'd', 'q', 'e'];
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  const k = e.key.toLowerCase();
  if (k === 'escape') { send({ t: 'estop' }); return; }
  if (KEYS.includes(k)) {
    e.preventDefault();
    if (!S.input.keys.has(k)) { S.input.keys.add(k); pushManual(); }
  }
});
document.addEventListener('keyup', (e) => { if (S.input.keys.delete(e.key.toLowerCase())) pushManual(); });
window.addEventListener('blur', () => { S.input.keys.clear(); pushManual(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { S.input.keys.clear(); pushManual(); } });

function manualCommand() {
  const I = S.input, vmax = +$('vmax').value, wmax = +$('wmax').value;
  const k = (a, b) => (I.keys.has(a) ? 1 : 0) - (I.keys.has(b) ? 1 : 0);
  let fx = -I.move[1] + k('w', 's'), fy = -I.move[0] + k('a', 'd');
  const n = Math.hypot(fx, fy); if (n > 1) { fx /= n; fy /= n; }
  const fw = Math.max(-1, Math.min(1, -I.rot + k('q', 'e')));
  return { vx: fx * vmax, vy: fy * vmax, wz: fw * wmax };
}
// Send as soon as the input changes (at most one command per 40 ms while dragging),
// plus a keep-alive every 100 ms while held: the hub stops after 0.5 s of silence.
// Letting go is sent at once, never throttled.
const SEND_MIN_GAP = 40, KEEPALIVE = 100;
let lastSend = 0, pendingSend = null;
function isDriving() {
  const I = S.input;
  return !document.hidden && (I.padMove || I.padRot || I.keys.size > 0);
}
function flushManual() {
  const driving = isDriving();
  if (driving) send(Object.assign({ t: 'manual', seq: ++S.seq }, manualCommand()));
  else if (S.wasDriving) send({ t: 'manual_release' });
  if (driving || S.wasDriving) lastSend = performance.now();
  S.wasDriving = driving;
}
function pushManual() {
  if (!isDriving()) {
    if (pendingSend) { clearTimeout(pendingSend); pendingSend = null; }
    flushManual();
    return;
  }
  if (pendingSend) return;
  const wait = SEND_MIN_GAP - (performance.now() - lastSend);
  if (wait <= 0) { flushManual(); return; }
  pendingSend = setTimeout(() => { pendingSend = null; flushManual(); }, wait);
}
setInterval(() => {
  if ((isDriving() || S.wasDriving) && performance.now() - lastSend >= KEEPALIVE) flushManual();
}, 20);

// ---------- top-down view ----------
const canvas = $('topdown'), ctx = canvas.getContext('2d');
const MARKER_COLOR = { human: '#f5a142', target: '#e25cf0', goal: '#3ecf8e', point: '#5ad1ff' };
function draw() {
  requestAnimationFrame(draw);
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; S.dirty = true; }
  const scanAge = S.scan ? (performance.now() - S.scan.rx) / 1000 : null;
  if (!S.dirty && !(scanAge != null && scanAge > 1)) return;
  S.dirty = false;

  const cx = w / 2, cy = h * 0.6, s = Math.min(w / 2, cy) / S.range;
  const P = (x, y) => [cx - y * s, cy - x * s];   // base_link -> screen
  ctx.clearRect(0, 0, w, h);

  ctx.lineWidth = dpr; ctx.font = 11 * dpr + 'px sans-serif'; ctx.fillStyle = '#56606e';
  for (let r = 1; r <= S.range * 1.6; r++) {
    ctx.strokeStyle = r % 5 === 0 ? '#303845' : '#232a33';
    ctx.beginPath(); ctx.arc(cx, cy, r * s, 0, Math.PI * 2); ctx.stroke();
    if (r <= S.range) ctx.fillText(r + ' m', cx + 3 * dpr, cy - r * s - 3 * dpr);
  }
  ctx.strokeStyle = '#232a33';
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, h); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke();

  const cfg = S.cfg || { laser_x: 0.0435, laser_yaw: 3.14 };
  const note = [];
  if (S.scan) {
    const stale = scanAge > 1;
    ctx.fillStyle = stale ? '#555c66' : getComputedStyle(document.documentElement).getPropertyValue('--scan');
    const cm = S.scan.cm, a0 = S.scan.amin + cfg.laser_yaw, da = S.scan.ainc, rad = 1.6 * dpr;
    for (let i = 0; i < cm.length; i++) {
      if (!cm[i]) continue;
      const r = cm[i] / 100, a = a0 + i * da;
      const [px, py] = P(cfg.laser_x + r * Math.cos(a), r * Math.sin(a));
      ctx.fillRect(px - rad / 2, py - rad / 2, rad, rad);
    }
    if (stale) note.push('lidar data is ' + scanAge.toFixed(0) + ' s old');
  } else note.push('No lidar data: turn on Base and Lidar');

  // Markers sent by the policy worker (policy_debug), shown for 2 s.
  if (S.debug && performance.now() - S.debug.rx < 2000) {
    for (const m of S.debug.markers || []) {
      if (typeof m.x !== 'number' || typeof m.y !== 'number') continue;
      const [px, py] = P(m.x, m.y);
      ctx.fillStyle = ctx.strokeStyle = MARKER_COLOR[m.kind] || MARKER_COLOR.point;
      ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.arc(px, py, 7 * dpr, 0, Math.PI * 2); ctx.stroke();
      if (m.label) ctx.fillText(String(m.label).slice(0, 24), px + 9 * dpr, py - 6 * dpr);
    }
  }

  // Robot footprint (~0.30 m long, 0.26 m wide) and heading.
  const L = 0.15, W = 0.13, corners = [[L, W], [L, -W], [-L, -W], [-L, W]].map(([x, y]) => P(x, y));
  ctx.fillStyle = '#2c3440'; ctx.strokeStyle = '#8b95a3'; ctx.lineWidth = 1.5 * dpr;
  ctx.beginPath(); corners.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath(); ctx.fill(); ctx.stroke();
  const [hx, hy] = P(L + 0.08, 0), [bx, by] = P(L - 0.02, 0);
  ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(hx, hy); ctx.stroke();

  // Velocity arrows: commanded (hub output) and measured (odometry). 1 m/s = 1 m.
  const arrow = (vx, vy, color) => {
    if (Math.hypot(vx, vy) < 0.01) return;
    const [x1, y1] = P(vx, vy), ang = Math.atan2(y1 - cy, x1 - cx), hl = 8 * dpr;
    ctx.strokeStyle = ctx.fillStyle = color; ctx.lineWidth = 2.5 * dpr;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - hl * Math.cos(ang - 0.4), y1 - hl * Math.sin(ang - 0.4));
    ctx.lineTo(x1 - hl * Math.cos(ang + 0.4), y1 - hl * Math.sin(ang + 0.4)); ctx.fill();
  };
  if (S.odom) arrow(S.odom.vx, S.odom.vy, '#5aa9ff');
  if (S.state) { const o = S.state.control.output; arrow(o[0], o[1], '#3ecf8e'); }
  const dbg = S.debug && performance.now() - S.debug.rx < 2000 && S.debug.text ? 'policy: ' + S.debug.text : null;
  $('pol-debug').textContent = dbg || '';
  $('view-note').textContent = note.join(' · ') || 'green arrow: commanded velocity · blue arrow: measured velocity';
}

// ---------- tabs ----------
const TABS = ['drive', 'battery', 'net'];
function showTab(name) {
  if (!TABS.includes(name)) name = 'drive';
  for (const b of document.querySelectorAll('[data-tab]')) b.classList.toggle('on', b.dataset.tab === name);
  for (const t of TABS) $('tab-' + t).classList.toggle('hidden', t !== name);
  store('tab', name);
  S.dirty = true;
  if (name === 'battery') { fetchBattery(); if (S.state) renderBatteryPage(S.state); }
  if (name === 'net') loadWifi();
}
for (const b of document.querySelectorAll('[data-tab]')) b.addEventListener('click', () => showTab(b.dataset.tab));
$('battery').addEventListener('click', () => showTab('battery'));
const batteryVisible = () => !$('tab-battery').classList.contains('hidden');

// ---------- battery page ----------
const BAT = { report: null };
const STATE_TEXT = { resting: 'resting', settling: 'estimating (needs ~2 s at rest)', driving: 'driving (reading held)', nodata: 'no voltage reading' };
const LEVEL_TEXT = { ok: 'OK', warn: 'Battery low: charge soon', critical: 'Battery very low: finish and charge', stop: 'At the 9.6 V alarm: stop, switch off and charge', unknown: '—' };
const SOURCE_TEXT = { driver: 'chassis driver (/voltage)', monitor: 'battery monitor (reads the chassis board while Base is off)' };
for (const id of ['bp-cap', 'bp-cur']) {
  const el = $(id);
  el.value = load(id, el.value);
  el.addEventListener('input', () => { store(id, el.value); if (S.state) renderBatteryPage(S.state); });
}
function pctFor(v, table) {
  if (!table || v == null) return null;
  if (v <= table[0][0]) return 0;
  for (let i = 1; i < table.length; i++) {
    const [v0, p0] = table[i - 1], [v1, p1] = table[i];
    if (v <= v1) return p0 + (p1 - p0) * (v - v0) / (v1 - v0);
  }
  return 100;
}
function fmtMin(m) {
  if (m == null) return '—';
  if (m < 1) return 'under 1 min';
  if (m < 90) return Math.round(m) + ' min';
  const h = Math.floor(m / 60), r = Math.round(m % 60);
  return h + ' h' + (r ? ' ' + r + ' min' : '');
}
async function fetchBattery() {
  try {
    const r = await fetch('/api/battery', { cache: 'no-store' });
    BAT.report = await r.json();
    buildScale();
    drawBatteryChart();
    if (S.state) renderBatteryPage(S.state);
  } catch (e) {}
}
setInterval(() => { if (batteryVisible()) fetchBattery(); }, 10000);

function buildScale() {
  const rep = BAT.report; if (!rep) return;
  const T = rep.thresholds, tbl = rep.table, bar = $('bp-bar'), scale = $('bp-scale');
  bar.querySelectorAll('.bat-tick').forEach((e) => e.remove());
  scale.innerHTML = '';
  const marks = [
    [T.alarm, '9.6 V alarm', 'alarm'], [T.critical, '10.0 V finish', 'warn'],
    [T.warn, '10.5 V warning', 'warn'], [T.full, '12.6 V full', '']];
  for (const [v, label, cls] of marks) {
    const x = pctFor(v, tbl);
    const tick = document.createElement('div');
    tick.className = 'bat-tick ' + cls; tick.style.left = x + '%';
    bar.appendChild(tick);
    const sp = document.createElement('span');
    sp.textContent = label; sp.style.left = x + '%';
    if (x < 3) sp.className = 'edge-l'; else if (x > 97) sp.className = 'edge-r';
    // The alarm lines sit close together at the left end: one label per line.
    if (v === T.warn) sp.style.top = '15px';
    if (v === T.critical) { sp.style.top = '30px'; sp.className = 'edge-l'; }
    scale.appendChild(sp);
  }
  const b0 = pctFor(T.storage[0], tbl), b1 = pctFor(T.storage[1], tbl);
  const band = $('bp-band'); band.style.left = b0 + '%'; band.style.width = (b1 - b0) + '%';
  const sp = document.createElement('span');
  sp.textContent = 'storage 11.1–11.7 V'; sp.style.left = ((b0 + b1) / 2) + '%';
  scale.appendChild(sp);
}

function renderBatteryPage(st) {
  if (!batteryVisible()) return;
  const b = st.battery || {}, tbl = BAT.report && BAT.report.table;
  const pctExact = b.pct_exact != null ? b.pct_exact : (b.v != null ? pctFor(b.v, tbl) : null);
  $('bp-pct').textContent = b.pct != null ? '≈' + b.pct + '%' : '—';
  $('bp-state').textContent = b.state === 'nodata' ? STATE_TEXT.nodata
    : (LEVEL_TEXT[b.level] || '—') + ' · ' + (STATE_TEXT[b.state] || b.state);
  $('bp-state').style.color = b.level === 'critical' || b.level === 'stop' ? 'var(--bad)' : b.level === 'warn' ? 'var(--warn)' : '';
  $('bp-sub').textContent = b.v == null ? 'With Base off the battery monitor reads the voltage; if nothing shows up, check the hub log'
    : b.v.toFixed(2) + ' V · per cell ' + b.cell_v.toFixed(2) + ' V · source: ' + (SOURCE_TEXT[b.source] || '—');
  const fill = $('bp-fill');
  fill.style.width = (pctExact == null ? 0 : Math.max(0, Math.min(100, pctExact))) + '%';
  fill.className = 'bat-fill' + (b.level === 'critical' || b.level === 'stop' ? ' low' : b.level === 'warn' ? ' warn' : '');
  $('bp-now').style.display = pctExact == null ? 'none' : '';
  $('bp-now').style.left = (pctExact || 0) + '%';

  // Discharge: measured drain rate
  const e = b.eta, tr = b.trend, dis = $('bp-discharge');
  if (b.state === 'nodata') dis.innerHTML = '<span class="muted">No voltage reading.</span>';
  else if (!e) dis.innerHTML = '<span class="muted">Collecting data: the drain rate needs about 5 minutes at rest.</span>';
  else if (e.status === 'steady') dis.innerHTML = 'Almost no change in the last ' + tr.span_min + ' min (' + tr.pct_per_h + ' %/h).';
  else if (e.status === 'rising') dis.innerHTML = 'Voltage rising over the last ' + tr.span_min + ' min (recovering after driving, or charging while switched on, which Yahboom advises against).';
  else {
    const line = (name, label) => e[name] === 0 ? label + ': <b>already below</b>' : label + ': about <b>' + fmtMin(e[name]) + '</b>';
    const on = Object.entries(st.sensors).filter(([, v]) => v.state === 'on').map(([k]) => ({ base: 'Base', lidar: 'Lidar', camera: 'Camera' }[k]));
    dis.innerHTML = line('warn', 'to the 10.5 V warning') + '<br>' + line('critical', 'to the 10.0 V finish line') + '<br>' +
      line('alarm', 'to the 9.6 V alarm') +
      '<div class="note">From the drain rate over the last ' + tr.span_min + ' min (' + (-tr.pct_per_h).toFixed(1) + ' %/h). On now: ' +
      (on.length ? on.join(', ') : 'nothing') + '. Driving or more sensors drain faster.</div>';
  }

  // Charge: estimate from capacity and charger current
  const cap = +$('bp-cap').value, cur = +$('bp-cur').value, chg = $('bp-charge');
  if (pctExact == null || !cap || !cur) chg.innerHTML = '<span class="muted">No battery reading to estimate from.</span>';
  else {
    const hours = (p) => Math.max(0, (p - pctExact) / 100) * cap / (cur * 1000) * 1.15;   // +15 % for the constant-voltage tail
    const storeLo = pctFor(BAT.report ? BAT.report.thresholds.storage[0] : 11.1, tbl);
    chg.innerHTML = 'From ≈' + (b.pct != null ? b.pct : Math.round(pctExact)) + '% to full: about <b>' + fmtMin(hours(100) * 60) + '</b>' +
      (pctExact < storeLo ? '<br>Only to the storage range (11.1 V): about <b>' + fmtMin(hours(storeLo) * 60) + '</b>' : '') +
      '<div class="note">Assumes ' + cap + ' mAh and ' + cur + ' A, plus 15% for the final constant-voltage phase.</div>';
  }

  const d = [
    ['Current reading', b.v != null ? b.v.toFixed(2) + ' V' : '—'],
    ['Resting voltage (smoothed)', b.v_rest != null ? b.v_rest.toFixed(2) + ' V' : '—'],
    ['Last 2 s average', b.v_fast != null ? b.v_fast.toFixed(2) + ' V' : '—'],
    ['Per cell (3 in series)', b.cell_v != null ? b.cell_v.toFixed(3) + ' V' : '—'],
    ['Estimated charge', pctExact != null ? pctExact.toFixed(1) + '%' : '—'],
    ['Voltage trend', tr ? tr.v_per_h.toFixed(2) + ' V/h (last ' + tr.span_min + ' min)' : 'not enough data'],
    ['Charge trend', tr ? tr.pct_per_h.toFixed(1) + ' %/h' : 'not enough data'],
    ['State', (STATE_TEXT[b.state] || '—') + ' · ' + (LEVEL_TEXT[b.level] || '—')],
    ['Source', SOURCE_TEXT[b.source] || '—'],
    ['Updated', b.age != null ? b.age + ' s ago' : '—'],
    ['0% means', '9.6 V (Yahboom alarm beep)'],
    ['Automatic E-STOP', '9.0 V'],
    ['Pack', '3S lithium, estimated with an NMC curve (chemistry not confirmed)'],
  ];
  $('bp-details').innerHTML = d.map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('');
}

function drawBatteryChart() {
  const rep = BAT.report, c = $('bp-chart');
  if (!rep || !batteryVisible()) return;
  const dpr = window.devicePixelRatio || 1, w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
  c.width = w; c.height = h;
  const g = c.getContext('2d'); g.clearRect(0, 0, w, h);
  const pts = rep.history, T = rep.thresholds;
  const padL = 44 * dpr, padR = 8 * dpr, padT = 8 * dpr, padB = 20 * dpr;
  const vMin = 9.4, vMax = 12.8;
  const now = rep.stamp, t0 = pts.length ? Math.min(pts[0][0], now - 600) : now - 600;
  const X = (t) => padL + (w - padL - padR) * (t - t0) / Math.max(1, now - t0);
  const Y = (v) => padT + (h - padT - padB) * (vMax - v) / (vMax - vMin);
  g.font = 11 * dpr + 'px sans-serif'; g.lineWidth = dpr;
  const lines = [[T.full, '#3ecf8e', '12.6'], [T.storage[1], '#5a6472', '11.7'], [T.storage[0], '#5a6472', '11.1'],
    [T.warn, '#f5b942', '10.5'], [T.critical, '#f5b942', '10.0'], [T.alarm, '#ef4c4c', '9.6']];
  for (const [v, col, lab] of lines) {
    g.strokeStyle = col; g.globalAlpha = 0.5; g.setLineDash([4 * dpr, 4 * dpr]);
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1; g.fillStyle = '#8b95a3'; g.fillText(lab + ' V', 4 * dpr, Y(v) + 4 * dpr);
  }
  const mins = Math.round((now - t0) / 60);
  g.fillStyle = '#8b95a3'; g.fillText(mins + ' min ago', padL, h - 5 * dpr);
  const lbl = 'now'; g.fillText(lbl, w - padR - g.measureText(lbl).width, h - 5 * dpr);
  if (!pts.length) { $('bp-chart-note').textContent = 'No data yet.'; return; }
  for (const resting of [false, true]) {
    g.strokeStyle = resting ? '#5aa9ff' : '#8b95a3'; g.lineWidth = (resting ? 2 : 1) * dpr;
    g.beginPath(); let pen = false, lastT = null;
    for (const [t, v, r] of pts) {
      if (r !== resting || (lastT != null && t - lastT > 30)) { pen = false; }
      if (r === resting) { if (!pen) { g.moveTo(X(t), Y(v)); pen = true; } else g.lineTo(X(t), Y(v)); lastT = t; }
    }
    g.stroke();
  }
  $('bp-chart-note').textContent = 'Blue: resting voltage (smoothed). Grey: readings while driving (pulled down by the load, not a charge level). One point every 5 s, up to 3 h (cleared when the hub restarts).';
}
window.addEventListener('resize', () => { if (batteryVisible()) drawBatteryChart(); });

connect();
requestAnimationFrame(draw);

// ---------- network page (Wi-Fi setup; the car has no keyboard) ----------
const NW = { report: null, openForm: null, watcher: null, hotspotArmed: 0 };
async function api(path, body) {
  const opt = body === undefined ? { cache: 'no-store' } : { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
async function loadWifi() {
  try { NW.report = await api('/api/wifi'); renderWifi(); } catch (e) { $('nw-now').textContent = 'Could not read the network state: ' + e.message; }
}
function bars(sig) {
  const n = sig >= 75 ? 4 : sig >= 55 ? 3 : sig >= 35 ? 2 : 1;
  return '<span class="nw-bars" title="signal ' + sig + '%">' + [4, 7, 10, 12].map((h, i) => '<i class="' + (i < n ? 'on' : '') + '" style="height:' + h + 'px"></i>').join('') + '</span>';
}
function renderWifi() {
  const r = NW.report; if (!r) return;
  const st = r.status || {};
  const addr = st.ip ? 'http://' + st.ip + ':8080' : '—';
  $('nw-now').innerHTML = '<dt>Mode</dt><dd>' + (st.hotspot ? "Car's own hotspot (" + esc(st.ssid) + ', password 12345678)' : st.state === 'connected' ? 'Connected to Wi-Fi' : esc(st.state || '—')) + '</dd>' +
    (st.hotspot ? '' : '<dt>Network</dt><dd>' + esc(st.ssid || '—') + '</dd>') +
    '<dt>Car IP</dt><dd>' + esc(st.ip || '—') + '</dd>' +
    '<dt>Dashboard</dt><dd>http://rosmaster.local:8080 or ' + esc(addr) + '</dd>';
  $('nw-hotspot').disabled = !!st.hotspot;
  const note = r.scan_error ? 'Scan failed: ' + esc(r.scan_error) + ' (you can still type a network name below)'
    : r.scan_time ? 'Last scan ' + Math.round(Date.now() / 1000 - r.scan_time) + ' s ago (a scan adds a short Wi-Fi latency spike; avoid it while driving)' : 'Not scanned yet: press Scan.';
  $('nw-scan-note').innerHTML = note;
  $('nw-list').innerHTML = (r.scan || []).map((n) => {
    const open = NW.openForm === n.ssid;
    const tags = (n.in_use ? '<span class="nw-tag use">in use</span>' : '') + (n.known ? '<span class="nw-tag">saved</span>' : '');
    const lock = n.security ? '🔒 ' + esc(n.security) : 'open';
    return '<li class="nw-item"><div class="top"><span class="name">' + esc(n.ssid) + '</span>' + tags +
      '<span class="meta">' + bars(n.signal) + ' ' + n.band + ' · ' + lock + '</span>' +
      (n.in_use ? '' : '<button type="button" data-connect="' + esc(n.ssid) + '">' + (open ? 'Cancel' : 'Connect') + '</button>') + '</div>' +
      (open ? '<div class="nw-form">' + (n.security ? '<input type="password" id="nw-inline-pass" placeholder="' + (n.known ? 'saved password; may be left empty' : 'Wi-Fi password') + '" autocomplete="off">' : '') +
        '<button type="button" data-go="' + esc(n.ssid) + '" data-secured="' + (n.security ? 1 : 0) + '" data-known="' + (n.known ? 1 : 0) + '">Connect to this network</button></div>' : '') + '</li>';
  }).join('');
  $('nw-saved').innerHTML = (r.saved || []).map((p) => {
    const using = st.connection === p.name;
    const tag = p.mode === 'ap' ? '<span class="nw-tag">car hotspot</span>' : using ? '<span class="nw-tag use">in use</span>' : '';
    const del = p.mode === 'ap' || using ? '' : '<button type="button" data-forget="' + esc(p.name) + '">Delete</button>';
    return '<li class="nw-item"><div class="top"><span class="name">' + esc(p.name) + (p.ssid && p.ssid !== p.name ? ' (' + esc(p.ssid) + ')' : '') + '</span>' + tag +
      '<span class="meta">priority ' + p.priority + '</span>' + del + '</div></li>';
  }).join('');
}
$('nw-scan').addEventListener('click', async () => {
  const b = $('nw-scan'); b.disabled = true; b.textContent = 'Scanning…';
  try { NW.report = await api('/api/wifi/scan', {}); renderWifi(); } catch (e) { $('nw-scan-note').textContent = 'Scan failed: ' + e.message; }
  b.disabled = false; b.textContent = 'Scan';
});
$('nw-list').addEventListener('click', (e) => {
  const c = e.target.closest('[data-connect]'), g = e.target.closest('[data-go]');
  if (c) { NW.openForm = NW.openForm === c.dataset.connect ? null : c.dataset.connect; renderWifi(); const i = $('nw-inline-pass'); if (i) i.focus(); }
  if (g) {
    const pw = $('nw-inline-pass') ? $('nw-inline-pass').value : '';
    if (g.dataset.secured === '1' && g.dataset.known !== '1' && pw.length < 8) { $('nw-scan-note').textContent = 'Enter the password (at least 8 characters)'; return; }
    startConnect(g.dataset.go, pw);
  }
});
$('nw-manual-go').addEventListener('click', () => {
  const ssid = $('nw-ssid').value.trim(), pw = $('nw-pass').value;
  if (!ssid) { $('nw-ssid').focus(); return; }
  startConnect(ssid, pw);
});
$('nw-saved').addEventListener('click', async (e) => {
  const f = e.target.closest('[data-forget]'); if (!f) return;
  if (f.dataset.armed !== '1') { f.dataset.armed = '1'; f.textContent = 'Press again to delete'; return; }
  try { NW.report = await api('/api/wifi/forget', { name: f.dataset.forget }); renderWifi(); } catch (err) { alertLine(err.message); }
});
$('nw-hotspot').addEventListener('click', async () => {
  const b = $('nw-hotspot');
  if (Date.now() - NW.hotspotArmed > 4000) { NW.hotspotArmed = Date.now(); b.textContent = 'Press again to confirm (dashboards on this network will disconnect)'; setTimeout(() => { b.textContent = "Switch to the car's hotspot"; }, 4000); return; }
  b.textContent = "Switch to the car's hotspot";
  try { await api('/api/wifi/hotspot', {}); } catch (e) { alertLine(e.message); return; }
  showOverlay("The car is switching to its own hotspot", '<ol><li>The car leaves this network and opens the hotspot <b>ROSMASTER</b> (password 12345678).</li><li>Join ROSMASTER with this laptop or phone.</li><li>This page then jumps to http://192.168.1.11:8080 by itself; if not, open that address.</li></ol>');
  watch(['http://192.168.1.11:8080', 'http://rosmaster.local:8080'], 'ROSMASTER');
});
function alertLine(msg) { $('nw-scan-note').textContent = msg; }
async function startConnect(ssid, pw) {
  try { await api('/api/wifi/connect', { ssid: ssid, password: pw || '' }); } catch (e) { alertLine('Could not start: ' + e.message); return; }
  NW.openForm = null;
  showOverlay('The car is joining "' + esc(ssid) + '"',
    '<ol><li>Switching takes about 10–45 s.</li><li>If you reached the car through its ROSMASTER hotspot, the hotspot disappears and this page loses the car. That is expected.</li>' +
    '<li>Move this laptop or phone to "' + esc(ssid) + '" too.</li><li>This page then jumps to http://rosmaster.local:8080 by itself. If it never does, read the IP on the car\'s OLED screen and open http://that-IP:8080.</li>' +
    '<li>With a wrong password the car goes back to the network it was on; if that one is gone too, it opens the ROSMASTER hotspot so you can try again.</li></ol>');
  watch(targetsFor(ssid), ssid);
}
function showOverlay(title, body) {
  $('nw-ov-title').textContent = title; $('nw-ov-body').innerHTML = body; $('nw-ov-status').textContent = '';
  $('nw-overlay').classList.remove('hidden');
}
$('nw-ov-close').addEventListener('click', () => { $('nw-overlay').classList.add('hidden'); if (NW.watcher) clearInterval(NW.watcher); NW.watcher = null; loadWifi(); });
// Ask the car at `base` which network it is on. /api/net allows cross-origin reads
// and is fast, so this works from the page's old address. Short timeout: a dead
// address must never block the next probe.
async function probeCar(base, ms) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms || 4000);
  try {
    const r = await fetch(base + '/api/net', { cache: 'no-store', signal: ctl.signal });
    return r.ok ? await r.json() : null;
  } catch (e) { return null; } finally { clearTimeout(t); }
}
function watch(targets, ssid) {
  if (NW.watcher) clearInterval(NW.watcher);
  const t0 = Date.now(), here = location.origin;
  let busy = false;
  const done = (msg) => { clearInterval(NW.watcher); NW.watcher = null; if (msg) $('nw-ov-status').textContent = msg; };
  NW.watcher = setInterval(async () => {
    if (busy) return;
    busy = true;
    try {
      const secs = Math.round((Date.now() - t0) / 1000);
      // 1) Is the car answering at a new-network address, and on the target network?
      for (const u of targets) {
        const r = await probeCar(u, 4000);
        const st = r && r.status;
        if (st && st.state === 'connected' && st.ssid === ssid) {
          done('Found the car on "' + ssid + '" (' + (st.ip || u) + '), opening the dashboard…');
          location.href = (u === here ? here : u) + '/';
          return;
        }
      }
      // 2) Still answering at this page's address? Then the switch failed and the car came back.
      if (secs > 6 && !targets.includes(here)) {
        const r = await probeCar(here, 3000);
        const a = r && r.attempt;
        if (a && a.ssid === ssid && a.state === 'failed') { done('Failed: ' + (a.reason || '') + '. Close this window and try again.'); return; }
      }
      $('nw-ov-status').textContent = secs > 180
        ? 'Still no car after 3 minutes: check the IP on its OLED screen, or join the ROSMASTER hotspot to look.'
        : 'Looking for the car… ' + secs + ' s (at ' + targets.map((u) => u.replace('http://', '')).join(', ') + ')';
    } finally { busy = false; }
  }, 2000);
}
// Where to look for the car once it has joined `ssid`: its mDNS name, plus the IP it
// had last time on that network (for networks that block mDNS).
function targetsFor(ssid) {
  const out = ['http://rosmaster.local:8080'];
  const ip = NW.report && NW.report.ip_by_ssid && NW.report.ip_by_ssid[ssid];
  if (ip) out.push('http://' + ip + ':8080');
  return out;
}

showTab(load('tab', 'drive'));
