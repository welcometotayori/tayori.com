/* 部屋の席（球面配置・2026-07-28）のテスト。
   このファイルは単体では動かない——scripts/test_room_seat.py が
   templates/mood.html の pure:room-seat 区画を頭に貼ってから jsc に渡す。
   「テスト用の写し」を持たないための作りで、テストは必ず**出荷されるコード**を見る。 */
'use strict';

var fails = 0;
function ok(cond, name, extra) {
  if (!cond) { fails++; print('NG  ' + name + (extra ? '  ' + extra : '')); }
}
function approx(a, b, eps, name) { ok(Math.abs(a - b) <= eps, name, '(' + a + ' vs ' + b + ')'); }
var DEG = Math.PI / 180;
var TAU = Math.PI * 2;

/* 球面上の角距離。cos d = cosθa·cosθb + sinθa·sinθb·cos(φa−φb) */
function angDist(a, b) {
  var c = Math.cos(a.theta) * Math.cos(b.theta)
        + Math.sin(a.theta) * Math.sin(b.theta) * Math.cos(a.phi - b.phi);
  return Math.acos(Math.max(-1, Math.min(1, c)));
}
function seatsOf(N) {
  var out = [];
  for (var i = 0; i < N; i++) out.push(roomSeat(i, N));
  return out;
}

/* ── ⓪ 格子の諸元：K・Δθ・N₀・容量が仕様の表と一致する ──────────
   g=0: K=12 Δθ=15° N₀=24 容量184 ／ g=1: 732 ／ g=2: 2938 */
var L0 = skyLattice(0);
ok(L0.length === 12, 'g=0 のリング数は 12', '→ ' + L0.length);
var N_EXPECT = [3, 9, 15, 19, 22, 24, 24, 22, 19, 15, 9, 3];
for (var k = 0; k < 12; k++) {
  ok(L0[k].n === N_EXPECT[k], 'g=0 リング' + k + ' の席数は ' + N_EXPECT[k], '→ ' + L0[k].n);
  approx(L0[k].theta, (k + 0.5) * 15 * DEG, 1e-12, 'g=0 リング' + k + ' の θ は (k+0.5)·15°');
}
ok(skyCapacity(0) === 184, 'g=0 の容量は 184', '→ ' + skyCapacity(0));
ok(skyCapacity(1) === 732, 'g=1 の容量は 732', '→ ' + skyCapacity(1));
ok(skyCapacity(2) === 2938, 'g=2 の容量は 2938', '→ ' + skyCapacity(2));

/* ── ① 決定性：同じ index・同じ総数なら、いつ呼んでも同じ席 ─────── */
var d1 = roomSeat(7, 14), d2 = roomSeat(7, 14);
ok(d1.theta === d2.theta && d1.phi === d2.phi, '同じ index・同じ総数なら同じ (θ,φ)');

/* ── ② 輪が途切れない（本題・2026-07-28 夜）─────────────────────
   どの総数 N でも、席を置いたリングは **まるごと均等割り** になっていること。
   ＝リングの席を φ で並べたとき、隣り合う間隔が（一周を跨ぐ所も含めて）全部同じ。
   ここが崩れると「弧が途中でキレる」が再発する。 */
function ringGapReport(N) {
  var seats = seatsOf(N), byRing = {};
  seats.forEach(function (s) { (byRing[s.ring] = byRing[s.ring] || []).push(s.phi); });
  var worst = 0, detail = '', count = 0;
  Object.keys(byRing).forEach(function (r) {
    var ph = byRing[r].slice().sort(function (a, b) { return a - b; });
    count += ph.length;
    var want = TAU / ph.length, lo = Infinity, hi = -Infinity;
    for (var i = 0; i < ph.length; i++) {
      var g = (i + 1 < ph.length) ? ph[i + 1] - ph[i] : TAU - (ph[ph.length - 1] - ph[0]);
      if (g < lo) lo = g;
      if (g > hi) hi = g;
    }
    var err = Math.max(Math.abs(hi - want), Math.abs(lo - want));
    if (err > worst) { worst = err; detail = 'N=' + N + ' ring' + r + ' n=' + ph.length; }
  });
  return { worst: worst, detail: detail, count: count };
}
var gapWorst = 0, gapWhere = '', sumBad = '';
[1, 2, 3, 5, 8, 13, 14, 15, 20, 23, 24, 25, 30, 40, 60, 90, 120, 184].forEach(function (N) {
  var r = ringGapReport(N);
  if (r.worst > gapWorst) { gapWorst = r.worst; gapWhere = r.detail; }
  if (r.count !== N) sumBad += ' N=' + N + '→' + r.count;
});
ok(gapWorst < 1e-12, 'どの総数でも、置いたリングは一周まるごと均等割り（＝輪が途切れない）',
   'worst=' + gapWorst.toExponential(2) + ' @' + gapWhere);
ok(sumBad === '', '配ったぶんの合計は必ず総数と一致する（欠けも溢れもない）', sumBad);

/* 14室（いま本番にある数）は赤道の一本の輪に、360/14=25.714° 等間隔で並ぶ */
var s14 = seatsOf(14);
var rings14 = {};
s14.forEach(function (s) { rings14[s.ring] = 1; });
ok(Object.keys(rings14).length === 1, '14室は一本のリングに収まる', '→ ' + Object.keys(rings14));
approx(s14[1].phi - s14[0].phi, TAU / 14, 1e-12, '14室の間隔は 360/14（＝25.714°）');

/* ── ③ 一意性：どの総数でも席が重ならない ─────────────────────── */
var dupBad = '';
[7, 14, 25, 40, 90, 184].forEach(function (N) {
  var seats = seatsOf(N), seen = {};
  seats.forEach(function (s) {
    var key = s.theta.toFixed(9) + '/' + (s.phi % TAU).toFixed(9);
    if (seen[key]) dupBad += ' N=' + N;
    seen[key] = 1;
  });
});
ok(dupBad === '', 'どの総数でも (θ,φ) が重複しない', dupBad);

/* ── ④ 均一性：満席（184）では最小角距離 ≥ 0.80·Δθ ─────────────
   仕様の実測値は 12.98°（= 0.865·Δθ）。 */
var full = seatsOf(184), minD = Infinity, minPair = '';
for (var a = 0; a < full.length; a++) {
  for (var b = a + 1; b < full.length; b++) {
    var d = angDist(full[a], full[b]);
    if (d < minD) { minD = d; minPair = a + '/' + b; }
  }
}
var dth0 = Math.PI / 12;
ok(minD >= 0.80 * dth0, '満席の最小角距離 ≥ 0.80·Δθ',
   'min=' + (minD / DEG).toFixed(2) + '° (' + (minD / dth0).toFixed(3) + '·Δθ, ' + minPair + ')');

/* ── ⑤ 充填順：赤道から。最初の部屋は正面（φ=0）に立つ ─────────── */
ok(roomSeat(0, 14).ring === 6, '席0 は赤道リング（k=6）', '→ ' + roomSeat(0, 14).ring);
approx(roomSeat(0, 14).theta, 97.5 * DEG, 1e-12, '席0 の θ は 97.5°（赤道のすぐ南）');
approx(roomSeat(0, 14).phi, 0, 1e-12, '席0 は正面中央（φ=0）');
ok(seatsOf(30).some(function (s) { return s.ring !== 6; }),
   '30室では赤道の外側のリングも使う（球へ育つ）');

/* ── ⑥ 半コマずらし：奇数リングは φ が π/n だけずれて始まる ───────
   放射状に揃うと格子に見えて、球である意味が消える。 */
var s30 = seatsOf(30);
var odd = s30.filter(function (s) { return s.ring % 2 === 1; });
ok(odd.length > 0, '30室では奇数リングが使われている');
var oddN = odd.length ? odd.filter(function (s) { return s.ring === odd[0].ring; }).length : 0;
if (oddN) {
  var oddMin = Math.min.apply(null, odd.filter(function (s) { return s.ring === odd[0].ring; })
                                       .map(function (s) { return s.phi; }));
  approx(oddMin, Math.PI / oddN, 1e-12, '奇数リングは π/n ずれて始まる');
}
var evenMin = Math.min.apply(null, s30.filter(function (s) { return s.ring % 2 === 0; })
                                      .map(function (s) { return s.phi; }));
approx(evenMin, 0, 1e-12, '偶数リングはずらさない');

/* ── ⑦ 総数が変わると席は動く（2026-07-28 夜に意図して降ろした線）──
   これは仕様である。「席は動かない」を守ったままでは、満席に満たない輪が
   必ずどこかで途切れる——両立しないので、途切れないほうを採った。 */
var before = roomSeat(3, 14), after = roomSeat(3, 15);
ok(before.phi !== after.phi, '部屋が増えると全室の φ が割り直される（輪を保つための代償）');
approx(roomSeat(0, 14).phi, roomSeat(0, 15).phi, 1e-12, 'ただし先頭は正面に居続ける（基準は動かない）');

/* ── ⑧ 世代：入りきらなくなったら、より細かい格子へ ──────────── */
ok(roomSeat(0, 184).gen === 0, '184室までは g=0', '→ ' + roomSeat(0, 184).gen);
ok(roomSeat(0, 185).gen === 1, '185室からは g=1', '→ ' + roomSeat(0, 185).gen);

/* ── ⑨ 変な入力で落ちない ──────────────────────────────────── */
[[-5, 14], [0.7, 14], [NaN, 14], [null, 14], [3, 0], [0, null]].forEach(function (arg) {
  var p = roomSeat(arg[0], arg[1]);
  ok(isFinite(p.theta) && isFinite(p.phi),
     '壊れた入力 (' + String(arg[0]) + ',' + String(arg[1]) + ') でも有限の (θ,φ) を返す');
});

/* ── ⑩ 正射影：向きと不変量 ─────────────────────────────────
   θ=90°,φ=0 が正面中央（depth=+1）。傾けても depth（z）は変わらない。
   R は格子に入っていないので、席（θ,φ）はスケールと無関係——ここで押さえる。 */
var front = seatProject({theta: Math.PI / 2, phi: 0}, 0, 0);
approx(front.x, 0, 1e-12, '正面中央は x=0');
approx(front.y, 0, 1e-12, '正面中央は y=0');
approx(front.depth, 1, 1e-12, '正面中央は depth=+1');
var north = seatProject({theta: 0, phi: 0}, 0, 0);
approx(north.y, 1, 1e-12, '北極は真上（y=+1・数学の向き）');
var tilted = seatProject({theta: 60 * DEG, phi: 40 * DEG}, 0, 18 * DEG);
var flat = seatProject({theta: 60 * DEG, phi: 40 * DEG}, 0, 0);
approx(tilted.depth, flat.depth, 1e-12, '傾き（視線 z 軸まわり）は depth を変えない');
approx(Math.hypot(tilted.x, tilted.y), Math.hypot(flat.x, flat.y), 1e-12,
       '傾きは中心からの距離を変えない（回すだけ）');
var rot = seatProject({theta: Math.PI / 2, phi: -30 * DEG}, 30 * DEG, 0);
approx(rot.depth, 1, 1e-12, '自転の位相 rot は φ に足される（φ+rot=0 で正面）');

/* ── ⑩' ピッチ：赤道を線から楕円へ開く ─────────────────────────
   ピッチ 0 では赤道リング（θ=90°）は y が一定＝画面上で一本の線に潰れる。
   ピッチを入れると赤道の各点が y 方向にばらけ、手前（depth>0）と奥（depth<0）が
   上下に分かれる（＝楕円が開く）＝回しても輪に繋がる根拠。 */
function equatorY(phi, beta) { return seatProject({theta: Math.PI / 2, phi: phi * DEG}, 0, 0, beta).y; }
var flatYs = [0, 45, 90, 135].map(function (p) { return equatorY(p, 0); });
ok(Math.max.apply(null, flatYs) - Math.min.apply(null, flatYs) < 1e-9,
   'ピッチ0：赤道は y 一定（一本の線に潰れる）');
var pitchYs = [0, 45, 90, 135].map(function (p) { return equatorY(p, 25 * DEG); });
ok(Math.max.apply(null, pitchYs) - Math.min.apply(null, pitchYs) > 0.3,
   'ピッチ有り：赤道が y 方向に開く（楕円になる）');
var near = seatProject({theta: Math.PI / 2, phi: 20 * DEG}, 0, 0, 25 * DEG);   // 手前
var far = seatProject({theta: Math.PI / 2, phi: 160 * DEG}, 0, 0, 25 * DEG);   // 奥
ok(near.depth > 0 && far.depth < 0, 'ピッチ後も手前/奥（depth の符号）は保たれる');
ok((near.y < 0) !== (far.y < 0), '手前と奥は上下の別の弧に分かれる（線でなく輪）');
var frontPitch = seatProject({theta: Math.PI / 2, phi: 0}, 0, 0, 25 * DEG);
approx(frontPitch.depth, Math.cos(25 * DEG), 1e-12, 'ピッチは正面の depth を cosβ に寝かせる');

/* ── ⑪ 旧・同心円版（roomSeatPlanar）はロールバック用に生きている ── */
var p0 = roomSeatPlanar(0, 'x');
approx(p0.x, 0, 1e-9, '平面版：席0 は真上（x=0）');
ok(p0.y < 0, '平面版：席0 は真上（y が負）');
ok(roomSeatPlanar(6, 'x').ring === 1, '平面版：リング割りも当時のまま');

if (fails) { print(fails + ' 件 失敗'); throw new Error('test_room_seat failed'); }
print('ok: 格子(184/732/2938)／決定性／**どの総数でもリングは均等割り＝輪が途切れない**／'
      + '合計一致／14室は一本の輪(25.714°)／重複なし／満席の最小角距離 '
      + (minD / DEG).toFixed(2) + '°／赤道から充填(席0=正面)／半コマずれ／'
      + '増えたら割り直す／世代／変な入力／正射影／ピッチ／平面版ロールバック可');
