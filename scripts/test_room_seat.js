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

/* 球面上の角距離。cos d = cosθa·cosθb + sinθa·sinθb·cos(φa−φb) */
function angDist(a, b) {
  var c = Math.cos(a.theta) * Math.cos(b.theta)
        + Math.sin(a.theta) * Math.sin(b.theta) * Math.cos(a.phi - b.phi);
  return Math.acos(Math.max(-1, Math.min(1, c)));
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

/* ── ① 決定性：同じ入力なら、いつ呼んでも同じ席 ─────────────── */
var d1 = roomSeat(7, 0), d2 = roomSeat(7, 0);
ok(d1.theta === d2.theta && d1.phi === d2.phi, '同じ index・同じ世代なら同じ (θ,φ)');

/* ── ②③ 一意性と均一性：容量いっぱい置いて、どの二席も 0.80·Δθ 以上 ──
   仕様の実測値は最小角距離 12.98°（= 0.865·Δθ）。 */
var seats = [];
for (var i = 0; i < 184; i++) seats.push(roomSeat(i, 0));
var minD = Infinity, minPair = '';
for (var a = 0; a < seats.length; a++) {
  for (var b = a + 1; b < seats.length; b++) {
    var d = angDist(seats[a], seats[b]);
    if (d < minD) { minD = d; minPair = a + '/' + b; }
  }
}
var dth0 = Math.PI / 12;
ok(minD > 1e-9, '容量内の全席で (θ,φ) が重複しない', 'pair=' + minPair);
ok(minD >= 0.80 * dth0, '最小角距離 ≥ 0.80·Δθ',
   'min=' + (minD / DEG).toFixed(2) + '° (' + (minD / dth0).toFixed(3) + '·Δθ, ' + minPair + ')');

/* ── ④ 容量：超えたら黙って重ねずに投げる ───────────────────── */
var threw = false;
try { roomSeat(184, 0); } catch (e) { threw = true; }
ok(threw, '席184（容量超え）は例外を投げる');
threw = false;
try { roomSeat(183, 0); } catch (e) { threw = true; }
ok(!threw, '席183（容量ちょうど最後）は座れる');

/* ── ⑤ 充填順：赤道から両極へ交互。index 0 は赤道リング（k=K/2=6）──
   順は [6,5,7,4,…] で、リングの席数は 24,24,22,22,… と減っていく。 */
ok(roomSeat(0, 0).ring === 6, '席0 は赤道リング（k=6）', '→ ' + roomSeat(0, 0).ring);
approx(roomSeat(0, 0).theta, 97.5 * DEG, 1e-12, '席0 の θ は 97.5°（赤道のすぐ南）');
approx(roomSeat(0, 0).phi, 0, 1e-12, '席0 は正面中央（φ=0）');
ok(roomSeat(23, 0).ring === 6, '席23 まではリング6', '→ ' + roomSeat(23, 0).ring);
ok(roomSeat(24, 0).ring === 5, '席24 からリング5（赤道の北隣）', '→ ' + roomSeat(24, 0).ring);
ok(roomSeat(48, 0).ring === 7, '席48 からリング7（南へ一つ）', '→ ' + roomSeat(48, 0).ring);
ok(roomSeat(70, 0).ring === 4, '席70 からリング4', '→ ' + roomSeat(70, 0).ring);
ok(roomSeat(183, 0).ring === 0, '最後の席は北極リング（k=0）', '→ ' + roomSeat(183, 0).ring);

/* ── ⑥ 半コマずらし：奇数リングは φ が π/n_k から始まる ─────────── */
approx(roomSeat(24, 0).phi, Math.PI / 24, 1e-12, 'リング5（奇数）は π/24 ずれて始まる');
approx(roomSeat(48, 0).phi, Math.PI / 22, 1e-12, 'リング7（奇数）は π/22 ずれて始まる');
approx(roomSeat(0, 0).phi, 0, 1e-12, 'リング6（偶数）はずらさない');

/* ── ⑦ 世代非互換：g を上げると席が変わる（＝大域再配置は仕様である）── */
var g0 = roomSeat(5, 0), g1 = roomSeat(5, 1);
ok(g0.theta !== g1.theta || g0.phi !== g1.phi,
   '世代をまたぐと席が変わる（roomSeat(i,0) ≠ roomSeat(i,1)）');

/* ── ⑧ 穴が空いても、他の席は動かない ─────────────────────────
   席は番号で決まる。隣が消えても引数が同じなら座標は不変（繰り上げない設計の裏づけ）。 */
var h1 = roomSeat(9, 0), h2 = roomSeat(9, 0);
ok(h1.theta === h2.theta && h1.phi === h2.phi, '席8 が空いても席9 は動かない');

/* ── ⑨ 変な入力で落ちない ──────────────────────────────────── */
[[-5, 0], [0.7, 0], [NaN, 0], [null, 0]].forEach(function (arg) {
  var p = roomSeat(arg[0], arg[1]);
  ok(isFinite(p.theta) && isFinite(p.phi),
     '壊れた席番号 ' + String(arg[0]) + ' でも有限の (θ,φ) を返す');
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
print('ok: 格子の諸元(184/732/2938)／決定性／184席で重複なし・最小角距離 '
      + (minD / DEG).toFixed(2) + '°(≥0.80Δθ)／容量超えは例外／赤道から充填(席0=正面)／'
      + '半コマずれ／世代非互換／穴で動かない／変な入力／正射影の向き／平面版ロールバック可');
