'use strict';
// 小车中控台 panel. Talks to the hub over /ws (protocol in docs/protocol.md).
// Coordinates: ROS base_link, x forward, y left, wz > 0 turns left.

const $ = (id) => document.getElementById(id);
const S = {
  ws: null, connected: false, cfg: null, state: null, scan: null, odom: null,
  range: 4, rtts: [], seq: 0, dirty: true,
  input: { move: [0, 0], rot: 0, padMove: false, padRot: false, keys: new Set() },
  wasDriving: false,
};
const MODE_TEXT = { idle: '空闲', manual: '手动', policy: 'policy' };
const SENSOR_TEXT = { off: '关', starting: '启动中', on: '运行', error: '故障', stopping: '关闭中' };

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
    else if (m.t === 'hello') { S.cfg = m.config; }
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
  mode.textContent = c.estop ? '急停' : (MODE_TEXT[c.mode] || c.mode);
  $('estop-banner').classList.toggle('hidden', !c.estop);
  $('estop-reason').textContent = c.estop_reason ? '（' + c.estop_reason + '）' : '';

  renderBattery(st.battery);

  let msg = '', bad = false;
  for (const btn of document.querySelectorAll('[data-sensor]')) {
    const s = st.sensors[btn.dataset.sensor];
    btn.className = 'sensor ' + s.state;
    let text = SENSOR_TEXT[s.state] || s.state;
    if (s.state === 'starting' && s.attempt > 1) text += '·' + s.attempt;
    btn.querySelector('span').textContent = text;
    if (s.message && s.state !== 'stopping') {
      msg += btn.querySelector('b').textContent + '：' + s.message + '  ';
      bad = bad || s.state === 'error';
    }
  }
  $('sensor-msg').textContent = msg;
  $('sensor-msg').classList.toggle('bad', bad);
  $('beep').disabled = st.sensors.base.state !== 'on';

  const hb = $('hand-back');
  hb.disabled = c.estop || c.mode !== 'manual';
  hb.textContent = c.active_policy ? '交还 policy' : '退出手动';

  const o = c.output;
  $('cmd').textContent = '输出 vx ' + o[0].toFixed(2) + ' · vy ' + o[1].toFixed(2) + ' · ω ' + o[2].toFixed(2) +
    (c.mode !== 'idle' && c.source_age != null ? ' · 指令距今 ' + Math.round(c.source_age * 1000) + ' ms' : '');
  for (const p of document.querySelectorAll('.pad')) p.classList.toggle('disabled', c.estop);

  const cam = st.sensors.camera.state === 'on', img = $('cam');
  $('camera-card').classList.toggle('hidden', !cam);
  if (cam && !img.getAttribute('src')) img.src = '/camera.mjpg?' + Date.now();
  if (!cam && img.getAttribute('src')) img.removeAttribute('src');

  const t = st.topics, sys = st.sys || {};
  $('info').textContent = [
    '雷达 ' + t.scan.hz.toFixed(1) + ' Hz', '里程计 ' + t.odom.hz.toFixed(1) + ' Hz',
    '面板 ' + st.clients, sys.ip ? 'IP ' + sys.ip : null,
    sys.disk_free_gb != null ? '磁盘剩 ' + sys.disk_free_gb.toFixed(1) + ' GB' : null,
    sys.cpu_temp_c != null ? 'CPU ' + sys.cpu_temp_c + '°C' : null,
    'hub 运行 ' + fmtDur(st.uptime),
  ].filter(Boolean).join(' · ');
}
// Battery: the hub estimates percent from resting voltage (see hub/battery.py).
const BAT_STATE = { resting: '', settling: '估算中', driving: '行驶中，保持读数' };
function renderBattery(b) {
  const el = $('battery');
  el.classList.remove('low', 'warn');
  if (!b || b.state === 'nodata') { el.textContent = '🔋 —'; el.title = '底盘关闭时没有电压数据'; return; }
  const pct = b.pct == null ? '—' : '≈' + b.pct + '%';
  const note = BAT_STATE[b.state] ? ' · ' + BAT_STATE[b.state] : '';
  el.textContent = '🔋 ' + pct + ' · ' + b.v.toFixed(1) + ' V' + note;
  const LEVEL = { warn: '电量低，尽快充电', critical: '电量很低，结束测试准备充电', stop: '已到 9.6 V 报警线，立即停车充电' };
  el.title = (LEVEL[b.level] ? LEVEL[b.level] + '\n' : '') + '静止电压 ' + (b.v_rest == null ? '—' : b.v_rest.toFixed(2) + ' V') +
    '，按 3S 三元锂曲线估算，0% = 9.6 V（Yahboom 报警点）。电池类型未确认。';
  if (b.level === 'critical' || b.level === 'stop') el.classList.add('low');
  else if (b.level === 'warn') el.classList.add('warn');
}
function fmtDur(s) { return s < 90 ? s + ' 秒' : s < 5400 ? Math.round(s / 60) + ' 分' : (s / 3600).toFixed(1) + ' 时'; }

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
    if (stale) note.push('雷达数据已 ' + scanAge.toFixed(0) + ' 秒未更新');
  } else note.push('没有雷达数据：在右侧打开「底盘」和「雷达」');

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
  $('view-note').textContent = note.join(' · ') || '绿箭头：下发速度　蓝箭头：实测速度';
}

connect();
requestAnimationFrame(draw);
