/* 部屋の席（円配置）のテスト。
   このファイルは単体では動かない——scripts/test_room_seat.py が
   templates/mood.html の pure:room-seat 区画を頭に貼ってから jsc に渡す。
   「テスト用の写し」を持たないための作りで、テストは必ず**出荷されるコード**を見る。 */
'use strict';

var fails = 0;
function ok(cond, name, extra) {
  if (!cond) { fails++; print('NG  ' + name + (extra ? '  ' + extra : '')); }
}
function approx(a, b, eps, name) { ok(Math.abs(a - b) <= eps, name, '(' + a + ' vs ' + b + ')'); }

/* ── ① 向き：12時から時計回り ─────────────────────────────
   ここを間違えると反時計回りになる。目で見て気づけないので、必ず数で押さえる。 */
var s0 = roomSeat(0, 'x');
approx(s0.x, 0, 1e-9, '席0 は真上（x=0）');
ok(s0.y < 0, '席0 は真上（y が負＝画面の上）', 'y=' + s0.y);

var s1 = roomSeat(1, 'x');
ok(s1.x > 0 && s1.y < 0, '席1 は右上（時計回り）', 'x=' + s1.x + ' y=' + s1.y);

var s3 = roomSeat(3, 'x');   // 6席リングの半周＝真下
approx(s3.x, 0, 1e-9, '席3 は真下（x=0）');
ok(s3.y > 0, '席3 は真下（y が正）', 'y=' + s3.y);

/* 角度が席番号とともに単調に増える（＝一方向にだけ回る） */
var prev = -1, monotone = true;
for (var i = 0; i < 6; i++) { var a = roomSeat(i, 'x').angle; if (a <= prev) monotone = false; prev = a; }
ok(monotone, 'リング0 の角度は席順に増える（逆回りしない）');

/* ── ② リングの割り当て：6,12,18,24… と無限に伸びる ────────── */
var edges = [[0, 0], [5, 0], [6, 1], [17, 1], [18, 2], [35, 2], [36, 3], [59, 3], [60, 4]];
edges.forEach(function (e) {
  ok(roomSeat(e[0], 'x').ring === e[1],
     '席' + e[0] + ' はリング' + e[1], '→ ' + roomSeat(e[0], 'x').ring);
});
ok(seatSlots(0) === 6 && seatSlots(1) === 12 && seatSlots(2) === 18 && seatSlots(3) === 24,
   'リングの席数は 6,12,18,24…');
ok(seatRadius(0) === 105 && seatRadius(1) === 175 && seatRadius(2) === 245 && seatRadius(3) === 315,
   '半径は 105,175,245,315…');

/* ── ③ 重ならない（旧 RING=[6,12,18] は37席目から重なっていた）────
   200席ぶん置いて、どの二つも一定以上離れていることを見る。 */
var pts = [], minD = Infinity, minPair = '';
for (var k = 0; k < 200; k++) { var p = roomSeat(k, 'r' + k); pts.push(p); }
for (var a1 = 0; a1 < pts.length; a1++) {
  for (var b1 = a1 + 1; b1 < pts.length; b1++) {
    var dx = pts[a1].x - pts[b1].x, dy = pts[a1].y - pts[b1].y;
    var d = Math.sqrt(dx * dx + dy * dy);
    if (d < minD) { minD = d; minPair = a1 + '/' + b1; }
  }
}
ok(minD > 40, '200席のどの二つも 40px 以上離れている', 'min=' + minD.toFixed(1) + ' (' + minPair + ')');

/* ── ④ 奇数リングは半コマずれている（放射状に揃わない）──────── */
var r0a = roomSeat(0, 'x').angle;                 // リング0 の先頭＝0
var r1a = roomSeat(6, 'x').angle;                 // リング1 の先頭
approx(r1a, Math.PI / 12, 1e-9, 'リング1 は半コマ（π/12）ずれて始まる');
ok(Math.abs(r0a - r1a) > 1e-6, 'リング0 とリング1 の先頭は同じ角度に並ばない');

/* ── ⑤ 決定論：同じ入力なら、いつ呼んでも同じ場所 ───────────── */
var q1 = roomSeat(7, 'room-42'), q2 = roomSeat(7, 'room-42');
ok(q1.x === q2.x && q1.y === q2.y, '同じ席・同じ種なら同じ座標');
ok(roomSeat(7, 'room-42').x !== roomSeat(7, 'room-99').x, '種が違えば揺らぎも違う');

/* ── ⑥ 揺らぎは ±8px に収まる ──────────────────────────── */
var lo = Infinity, hi = -Infinity;
for (var m = 0; m < 500; m++) { var j = seatJitter('room-' + m); if (j < lo) lo = j; if (j > hi) hi = j; }
ok(lo >= -8 && hi <= 8, '揺らぎは ±8px の中', 'lo=' + lo.toFixed(2) + ' hi=' + hi.toFixed(2));
ok(hi - lo > 12, '揺らぎは実際にばらけている（固定値になっていない）', 'range=' + (hi - lo).toFixed(2));

/* ── ⑦ 穴が空いても、他の席は動かない ─────────────────────
   席は番号で決まるので、隣が消えても座標は不変（繰り上げない設計の裏づけ）。 */
var before = roomSeat(9, 'room-9');
var after = roomSeat(9, 'room-9');               // 席8 の部屋が消えても 9 の引数は変わらない
ok(before.x === after.x && before.y === after.y, '席8 が空いても席9 は動かない');

/* ── ⑧ 変な入力で落ちない ──────────────────────────────── */
[[-5, 'x'], [0.7, 'x'], [NaN, 'x'], [null, 'x']].forEach(function (arg) {
  var p = roomSeat(arg[0], arg[1]);
  ok(isFinite(p.x) && isFinite(p.y), '壊れた席番号 ' + String(arg[0]) + ' でも有限の座標を返す');
});

if (fails) { print(fails + ' 件 失敗'); throw new Error('test_room_seat failed'); }
print('ok: 席の向き（12時・時計回り）／リング 6,12,18,24…／200席で重なりなし（min '
      + minD.toFixed(1) + 'px）／半コマずれ／決定論／±8px／穴で動かない／変な入力');
