// ── 捨てられない屑籠：ジェスチャー判定 ──
// 手の開閉は「手首(0)→中指先(12)の距離」を「掌の幅(5→17)」で割って正規化して測る。
// 掌幅で割ることでカメラとの距離に依存しなくなる（近くても遠くても同じ握り具合が出る）。

export const OPEN_R = 2.15; // パー：中指先までの距離が掌幅の約2.1倍以上（要実機微調整）
export const FIST_R = 1.40; // グー：約1.4倍以下（要実機微調整）

function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

// 掌の幅（人差し指の付け根5 → 小指の付け根17）。手の大きさ・距離の基準にする。
export function handScale(lm) {
  return dist(lm[5], lm[17]) || 1e-6;
}

export function fistRatio(lm) {
  return dist(lm[0], lm[12]) / handScale(lm);
}

// 握り具合 0.0(パー)〜1.0(グー)
export function graspRatio(lm) {
  const r = (OPEN_R - fistRatio(lm)) / (OPEN_R - FIST_R);
  return Math.min(1, Math.max(0, r));
}

export function isFist(lm) {
  return fistRatio(lm) < FIST_R;
}

// 掌の中心（手首と四指の付け根の平均）。掴む位置・追従の基準点はここにする。
export function palmCenter(lm) {
  const idx = [0, 5, 9, 13, 17];
  let x = 0, y = 0;
  for (const i of idx) { x += lm[i].x; y += lm[i].y; }
  return { x: x / idx.length, y: y / idx.length };
}

// ランドマーク(0〜1・カメラ座標)を画面座標へ。インカメは鏡像なので x を反転する。
export function toScreen(pt, mirror = true) {
  return {
    x: (mirror ? 1 - pt.x : pt.x) * window.innerWidth,
    y: pt.y * window.innerHeight,
  };
}

// 手（画面座標）が矩形に重なっているか。pad で当たり判定を甘くする。
export function isOverRect(p, rect, pad = 60) {
  return (
    p.x >= rect.left - pad && p.x <= rect.right + pad &&
    p.y >= rect.top - pad && p.y <= rect.bottom + pad
  );
}

// 直近フレームの掌座標から平均速度（px/ms）を出す。リリース時の投げる向き・強さに使う。
export class VelocityTracker {
  constructor(len = 6) { this.len = len; this.h = []; }
  push(x, y) {
    this.h.push({ x, y, t: performance.now() });
    if (this.h.length > this.len) this.h.shift();
  }
  velocity() {
    if (this.h.length < 2) return { vx: 0, vy: 0 };
    const a = this.h[0], b = this.h[this.h.length - 1];
    const dt = (b.t - a.t) || 16;
    return { vx: (b.x - a.x) / dt, vy: (b.y - a.y) / dt };
  }
  // 区間中の瞬間速度の最大値。手は「開く直前」が一番速いので、平均でなくピークで投げを判定する。
  peakSpeed() {
    let m = 0;
    for (let i = 1; i < this.h.length; i++) {
      const dt = (this.h[i].t - this.h[i - 1].t) || 16;
      m = Math.max(m, Math.hypot(this.h[i].x - this.h[i - 1].x, this.h[i].y - this.h[i - 1].y) / dt);
    }
    return m;
  }
  reset() { this.h.length = 0; }
}
