// ── 捨てられない屑籠：投げるアニメーション ──
// 依存ライブラリは足さない（tayoriは素のJSで通す）。Web Animations APIで投げる。
// 掴んで持ち歩いた紙は transform で手に追従しているので、投げ始めは常に「今の位置」から。
// reduced-motion の時は飛ばさず、静かに消えるだけにする。

const EASE_OUT = "cubic-bezier(.23,1,.32,1)";   // --ease-out と同値
const EASE_SINK = "cubic-bezier(.32,.06,.4,1)"; // --ease-sink と同値

function reduced() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// 現在の transform の平行移動成分（手に追従した分）を読む。ここを起点に投げる。
function baseOffset(el) {
  try {
    const m = new DOMMatrix(getComputedStyle(el).transform);
    return { x: m.e || 0, y: m.f || 0 };
  } catch (e) {
    return { x: 0, y: 0 };
  }
}

// カメラ経路：リリース時の速度ベクトル(px/ms)の向きへ紙玉を飛ばし、縮めて消す。
export function throwPaper(el, { vx, vy }, onDone) {
  if (reduced()) {
    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: "forwards" })
      .onfinish = () => onDone && onDone();
    return;
  }
  const b = baseOffset(el);
  const K = 460; // 感度係数（要実機調整）
  let dx = vx * K, dy = vy * K;
  // どんな弱い投げでも画面外までは飛ばす（中途半端に浮いて残らないように）
  const L = Math.hypot(dx, dy) || 1;
  const MIN = Math.hypot(window.innerWidth, window.innerHeight) * 0.85;
  if (L < MIN) { dx *= MIN / L; dy *= MIN / L; }
  const rot = (vx + vy) * 140 + (Math.random() * 40 - 20);
  el.animate(
    [
      { transform: `translate(${b.x}px,${b.y}px) rotate(0deg) scale(1)`, opacity: 1 },
      { transform: `translate(${b.x + dx * 0.6}px,${b.y + dy * 0.6}px) rotate(${rot * 0.7}deg) scale(0.5)`, opacity: 0.9, offset: 0.55 },
      { transform: `translate(${b.x + dx}px,${b.y + dy}px) rotate(${rot}deg) scale(0.08)`, opacity: 0 },
    ],
    { duration: 620, easing: EASE_OUT, fill: "forwards" }
  ).onfinish = () => onDone && onDone();
}

// 長押し経路：屑籠の位置（画面座標）へ、弧を描いて落とす。
export function throwInto(el, target, onDone) {
  if (reduced()) {
    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: "forwards" })
      .onfinish = () => onDone && onDone();
    return;
  }
  const b = baseOffset(el);
  const r = el.getBoundingClientRect();
  const fx = r.left + r.width / 2, fy = r.top + r.height / 2;
  const dx = target.x - fx, dy = target.y - fy;
  const lift = Math.min(-70, dy - 110); // 一度ふわりと持ち上がってから落ちる
  el.animate(
    [
      { transform: `translate(${b.x}px,${b.y}px) rotate(0deg) scale(1)`, opacity: 1 },
      { transform: `translate(${b.x + dx * 0.45}px,${b.y + lift}px) rotate(70deg) scale(0.45)`, opacity: 1, offset: 0.5 },
      { transform: `translate(${b.x + dx}px,${b.y + dy}px) rotate(150deg) scale(0.08)`, opacity: 0.1 },
    ],
    { duration: 640, easing: EASE_SINK, fill: "forwards" }
  ).onfinish = () => onDone && onDone();
}
