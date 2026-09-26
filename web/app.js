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
  renderBatteryPage(st);

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
const STATE_TEXT = { resting: '静止', settling: '估算中（需要静止约 2 秒）', driving: '行驶中（读数保持）', nodata: '没有电压数据' };
const LEVEL_TEXT = { ok: '正常', warn: '电量低，尽快充电', critical: '电量很低，结束测试准备充电', stop: '已到 9.6 V 报警线，立即停车并关机充电', unknown: '—' };
const SOURCE_TEXT = { driver: '底盘驱动（/voltage）', monitor: '电量监测（底盘关闭时直接读底盘板）' };
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
  if (m < 1) return '不到 1 分钟';
  if (m < 90) return Math.round(m) + ' 分钟';
  const h = Math.floor(m / 60), r = Math.round(m % 60);
  return h + ' 小时' + (r ? ' ' + r + ' 分' : '');
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
    [T.alarm, '9.6 V 报警', 'alarm'], [T.critical, '10.0 V 收车', 'warn'],
    [T.warn, '10.5 V 提醒', 'warn'], [T.full, '12.6 V 满', '']];
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
  sp.textContent = '存放 11.1–11.7 V'; sp.style.left = ((b0 + b1) / 2) + '%';
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
  $('bp-sub').textContent = b.v == null ? '底盘关着时由电量监测读电压；如果一直没有数据，检查 hub 日志'
    : b.v.toFixed(2) + ' V · 每节电芯 ' + b.cell_v.toFixed(2) + ' V · 来源：' + (SOURCE_TEXT[b.source] || '—');
  const fill = $('bp-fill');
  fill.style.width = (pctExact == null ? 0 : Math.max(0, Math.min(100, pctExact))) + '%';
  fill.className = 'bat-fill' + (b.level === 'critical' || b.level === 'stop' ? ' low' : b.level === 'warn' ? ' warn' : '');
  $('bp-now').style.display = pctExact == null ? 'none' : '';
  $('bp-now').style.left = (pctExact || 0) + '%';

  // Discharge: measured drain rate
  const e = b.eta, tr = b.trend, dis = $('bp-discharge');
  if (b.state === 'nodata') dis.innerHTML = '<span class="muted">没有电压数据。</span>';
  else if (!e) dis.innerHTML = '<span class="muted">正在积累数据：需要连续静止约 5 分钟，才能算出耗电速度。</span>';
  else if (e.status === 'steady') dis.innerHTML = '最近 ' + tr.span_min + ' 分钟电量几乎没变（' + tr.pct_per_h + '%/小时）。';
  else if (e.status === 'rising') dis.innerHTML = '最近 ' + tr.span_min + ' 分钟电压在上升（刚停车后的回升，或正在开机充电；Yahboom 不建议边充边用）。';
  else {
    const line = (name, label) => e[name] === 0 ? label + '：<b>已经低于</b>' : label + '：约 <b>' + fmtMin(e[name]) + '</b>';
    const on = Object.entries(st.sensors).filter(([, v]) => v.state === 'on').map(([k]) => ({ base: '底盘', lidar: '雷达', camera: '相机' }[k]));
    dis.innerHTML = line('warn', '到提醒线 10.5 V') + '<br>' + line('critical', '到收车线 10.0 V') + '<br>' +
      line('alarm', '到蜂鸣报警 9.6 V') +
      '<div class="note">按最近 ' + tr.span_min + ' 分钟的耗电速度（' + (-tr.pct_per_h).toFixed(1) + '%/小时）推算；当前开着：' +
      (on.length ? on.join('、') : '无') + '。开车或多开传感器会更快。</div>';
  }

  // Charge: estimate from capacity and charger current
  const cap = +$('bp-cap').value, cur = +$('bp-cur').value, chg = $('bp-charge');
  if (pctExact == null || !cap || !cur) chg.innerHTML = '<span class="muted">没有电量数据时无法估算。</span>';
  else {
    const hours = (p) => Math.max(0, (p - pctExact) / 100) * cap / (cur * 1000) * 1.15;   // +15 % for the constant-voltage tail
    const storeLo = pctFor(BAT.report ? BAT.report.thresholds.storage[0] : 11.1, tbl);
    chg.innerHTML = '从 ≈' + (b.pct != null ? b.pct : Math.round(pctExact)) + '% 充满：约 <b>' + fmtMin(hours(100) * 60) + '</b>' +
      (pctExact < storeLo ? '<br>只充到长期存放区间（11.1 V）：约 <b>' + fmtMin(hours(storeLo) * 60) + '</b>' : '') +
      '<div class="note">按 ' + cap + ' mAh、' + cur + ' A 估算，另加 15% 给末段恒压充电。</div>';
  }

  const d = [
    ['当前读数', b.v != null ? b.v.toFixed(2) + ' V' : '—'],
    ['静止电压（平滑后）', b.v_rest != null ? b.v_rest.toFixed(2) + ' V' : '—'],
    ['最近 2 秒平均', b.v_fast != null ? b.v_fast.toFixed(2) + ' V' : '—'],
    ['每节电芯（3 节串联）', b.cell_v != null ? b.cell_v.toFixed(3) + ' V' : '—'],
    ['估算电量', pctExact != null ? pctExact.toFixed(1) + '%' : '—'],
    ['电压变化', tr ? tr.v_per_h.toFixed(2) + ' V/小时（最近 ' + tr.span_min + ' 分钟）' : '数据不足'],
    ['电量变化', tr ? tr.pct_per_h.toFixed(1) + ' %/小时' : '数据不足'],
    ['状态', (STATE_TEXT[b.state] || '—') + ' · ' + (LEVEL_TEXT[b.level] || '—')],
    ['数据来源', SOURCE_TEXT[b.source] || '—'],
    ['数据更新', b.age != null ? b.age + ' 秒前' : '—'],
    ['0% 的定义', '9.6 V（Yahboom 蜂鸣报警点）'],
    ['自动急停', '9.0 V'],
    ['电池类型', '3S 锂电，按三元锂曲线估算（类型未确认）'],
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
  g.fillStyle = '#8b95a3'; g.fillText(mins + ' 分钟前', padL, h - 5 * dpr);
  const lbl = '现在'; g.fillText(lbl, w - padR - g.measureText(lbl).width, h - 5 * dpr);
  if (!pts.length) { $('bp-chart-note').textContent = '还没有数据。'; return; }
  for (const resting of [false, true]) {
    g.strokeStyle = resting ? '#5aa9ff' : '#8b95a3'; g.lineWidth = (resting ? 2 : 1) * dpr;
    g.beginPath(); let pen = false, lastT = null;
    for (const [t, v, r] of pts) {
      if (r !== resting || (lastT != null && t - lastT > 30)) { pen = false; }
      if (r === resting) { if (!pen) { g.moveTo(X(t), Y(v)); pen = true; } else g.lineTo(X(t), Y(v)); lastT = t; }
    }
    g.stroke();
  }
  $('bp-chart-note').textContent = '蓝线：静止电压（平滑后）；灰线：行驶中的读数（被负载拉低，不代表电量）。每 5 秒一个点，最多保留 3 小时（hub 重启会清空）。';
}
window.addEventListener('resize', () => { if (batteryVisible()) drawBatteryChart(); });

connect();
requestAnimationFrame(draw);
// ---------- network page (Wi-Fi setup; the car has no keyboard) ----------
const NW = { report: null, openForm: null, watcher: null, hotspotArmed: 0 };
const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(path, body) {
  const opt = body === undefined ? { cache: 'no-store' } : { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
async function loadWifi() {
  try { NW.report = await api('/api/wifi'); renderWifi(); } catch (e) { $('nw-now').textContent = '读取网络状态失败：' + e.message; }
}
function bars(sig) {
  const n = sig >= 75 ? 4 : sig >= 55 ? 3 : sig >= 35 ? 2 : 1;
  return '<span class="nw-bars" title="信号 ' + sig + '%">' + [4, 7, 10, 12].map((h, i) => '<i class="' + (i < n ? 'on' : '') + '" style="height:' + h + 'px"></i>').join('') + '</span>';
}
function renderWifi() {
  const r = NW.report; if (!r) return;
  const st = r.status || {};
  const host = location.hostname;
  const addr = st.ip ? 'http://' + st.ip + ':8080' : '—';
  $('nw-now').innerHTML = '<dt>模式</dt><dd>' + (st.hotspot ? '小车热点（' + esc(st.ssid) + '，密码 12345678）' : st.state === 'connected' ? '已连接 Wi-Fi' : esc(st.state || '—')) + '</dd>' +
    (st.hotspot ? '' : '<dt>网络</dt><dd>' + esc(st.ssid || '—') + '</dd>') +
    '<dt>小车 IP</dt><dd>' + esc(st.ip || '—') + '</dd>' +
    '<dt>面板地址</dt><dd>http://rosmaster.local:8080　或　' + esc(addr) + '</dd>';
  $('nw-hotspot').disabled = !!st.hotspot;
  // scan list
  const note = r.scan_error ? '扫描失败：' + esc(r.scan_error) + '（小车开热点时可能扫不了，可以手动输入网络名）'
    : r.scan_time ? '上次扫描：' + Math.round(Date.now() / 1000 - r.scan_time) + ' 秒前（扫描时 Wi-Fi 会有零点几秒的延迟抖动，开车时别频繁点）' : '还没扫描，点右上角「扫描」。';
  $('nw-scan-note').innerHTML = note;
  $('nw-list').innerHTML = (r.scan || []).map((n) => {
    const open = NW.openForm === n.ssid;
    const tags = (n.in_use ? '<span class="nw-tag use">正在使用</span>' : '') + (n.known ? '<span class="nw-tag">已保存</span>' : '');
    const lock = n.security ? '🔒 ' + esc(n.security) : '开放';
    return '<li class="nw-item"><div class="top"><span class="name">' + esc(n.ssid) + '</span>' + tags +
      '<span class="meta">' + bars(n.signal) + ' ' + n.band + ' · ' + lock + '</span>' +
      (n.in_use ? '' : '<button type="button" data-connect="' + esc(n.ssid) + '">' + (open ? '取消' : '连接') + '</button>') + '</div>' +
      (open ? '<div class="nw-form">' + (n.security ? '<input type="password" id="nw-inline-pass" placeholder="' + (n.known ? '已保存密码，可留空' : 'Wi-Fi 密码') + '" autocomplete="off">' : '') +
        '<button type="button" data-go="' + esc(n.ssid) + '" data-secured="' + (n.security ? 1 : 0) + '" data-known="' + (n.known ? 1 : 0) + '">连接到这个网络</button></div>' : '') + '</li>';
  }).join('');
  // saved list
  $('nw-saved').innerHTML = (r.saved || []).map((p) => {
    const using = st.connection === p.name;
    const tag = p.mode === 'ap' ? '<span class="nw-tag">小车热点</span>' : using ? '<span class="nw-tag use">正在使用</span>' : '';
    const del = p.mode === 'ap' || using ? '' : '<button type="button" data-forget="' + esc(p.name) + '">删除</button>';
    return '<li class="nw-item"><div class="top"><span class="name">' + esc(p.name) + (p.ssid && p.ssid !== p.name ? '（' + esc(p.ssid) + '）' : '') + '</span>' + tag +
      '<span class="meta">优先级 ' + p.priority + '</span>' + del + '</div></li>';
  }).join('');
}
$('nw-scan').addEventListener('click', async () => {
  const b = $('nw-scan'); b.disabled = true; b.textContent = '扫描中…';
  try { NW.report = await api('/api/wifi/scan', {}); renderWifi(); } catch (e) { $('nw-scan-note').textContent = '扫描失败：' + e.message; }
  b.disabled = false; b.textContent = '扫描';
});
$('nw-list').addEventListener('click', (e) => {
  const c = e.target.closest('[data-connect]'), g = e.target.closest('[data-go]');
  if (c) { NW.openForm = NW.openForm === c.dataset.connect ? null : c.dataset.connect; renderWifi(); const i = $('nw-inline-pass'); if (i) i.focus(); }
  if (g) {
    const pw = $('nw-inline-pass') ? $('nw-inline-pass').value : '';
    if (g.dataset.secured === '1' && g.dataset.known !== '1' && pw.length < 8) { $('nw-scan-note').textContent = '请输入密码（至少 8 位）'; return; }
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
  if (f.dataset.armed !== '1') { f.dataset.armed = '1'; f.textContent = '再点一次确认删除'; return; }
  try { NW.report = await api('/api/wifi/forget', { name: f.dataset.forget }); renderWifi(); } catch (err) { alertLine(err.message); }
});
$('nw-hotspot').addEventListener('click', async () => {
  const b = $('nw-hotspot');
  if (Date.now() - NW.hotspotArmed > 4000) { NW.hotspotArmed = Date.now(); b.textContent = '再点一次确认（当前网络上的面板会断开）'; setTimeout(() => { b.textContent = '切换到小车热点'; }, 4000); return; }
  b.textContent = '切换到小车热点';
  try { await api('/api/wifi/hotspot', {}); } catch (e) { alertLine(e.message); return; }
  showOverlay('小车正在切换到自己的热点', '<ol><li>小车会离开当前网络，开热点 <b>ROSMASTER</b>（密码 12345678）。</li><li>把这台电脑或手机连到 ROSMASTER。</li><li>这个页面会自动跳到 http://192.168.1.11:8080；没跳的话手动打开它。</li></ol>');
  watch(['http://192.168.1.11:8080', 'http://rosmaster.local:8080'], 'ROSMASTER');
});
function alertLine(msg) { $('nw-scan-note').textContent = msg; }
async function startConnect(ssid, pw) {
  try { await api('/api/wifi/connect', { ssid: ssid, password: pw || '' }); } catch (e) { alertLine('没能开始连接：' + e.message); return; }
  NW.openForm = null;
  showOverlay('小车正在连接「' + esc(ssid) + '」',
    '<ol><li>小车切换网络大约要 10–45 秒。</li><li>如果你现在是通过 ROSMASTER 热点连着小车，热点会消失、连接会断开，这是正常的。</li>' +
    '<li>把这台电脑或手机也连到「' + esc(ssid) + '」。</li><li>这个页面会自动跳到 http://rosmaster.local:8080。一直没跳的话，看小车 OLED 小屏上的 IP，打开 http://那个IP:8080。</li>' +
    '<li>如果密码错了，小车会回到原来的网络；如果原来的网络也不在附近，它会自己开热点 ROSMASTER，重新连上热点再试。</li></ol>');
  watch(targetsFor(ssid), ssid);
}
function showOverlay(title, body) {
  $('nw-ov-title').textContent = title; $('nw-ov-body').innerHTML = body; $('nw-ov-status').textContent = '';
  $('nw-overlay').classList.remove('hidden');
}
$('nw-ov-close').addEventListener('click', () => { $('nw-overlay').classList.add('hidden'); if (NW.watcher) clearInterval(NW.watcher); NW.watcher = null; loadWifi(); });
// Ask the car at `base` which network it is on. /api/net allows cross-origin
// reads (and is fast: no listing of saved networks), so this works from the page's old address. Short timeout: a dead
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
          done('在「' + ssid + '」上找到小车了（' + (st.ip || u) + '），正在跳转…');
          location.href = (u === here ? here : u) + '/';
          return;
        }
      }
      // 2) Still answering at this page's address? Then the switch failed and the car came back.
      if (secs > 6 && !targets.includes(here)) {
        const r = await probeCar(here, 3000);
        const a = r && r.attempt;
        if (a && a.ssid === ssid && a.state === 'failed') { done('连接失败：' + (a.reason || '') + '。可以关闭这个窗口重试。'); return; }
      }
      $('nw-ov-status').textContent = secs > 180
        ? '3 分钟还没找到小车：看 OLED 小屏上的 IP，或者重新连 ROSMASTER 热点检查。'
        : '正在寻找小车… ' + secs + ' 秒（在：' + targets.map((u) => u.replace('http://', '')).join('、') + '）';
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
