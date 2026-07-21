// ── 捨てられない屑籠:クシャクシャ表現(canvas自前描画)──
// いま書かれている本文と気分の色ごと canvas に写し取り、メッシュワープで潰す。
// リアリティの構成:
//   ①二層の変形 — 大きな「折り」(粗い格子)と細かな「皺」(細かい格子)を重ねる
//   ②面の明暗の量子化 — 紙の面は連続トーンではなく、パキッと割れた平面の集まり
//   ③圧縮陰影 — 潰れて面積が縮んだ面ほど暗い
//   ④稜線ネットワーク — セル辺に走る無数の細い折り目と、大きな皺の稜線
//   ⑤球の立体陰影 — 丸まりきったら左上からの光と縁の沈み
// 屑籠ビューの紙玉も同じエンジンで描く(renderPaperBall)。id から決まる乱数なので
// 同じ紙玉はいつ開いても同じ形。差し替える時は update(ratio) のインターフェースだけ保つこと。

const PAPER  = "#F2EBDD"; // --paper
const PAPER2 = "#EDE3D1"; // --paper-2(封筒の地。縮んだ余白を埋める色)
const INK    = "#3A2E25"; // --ink
const LINE   = "#CBBBA0"; // --line

const COLS = 12, ROWS = 14;
const CSTEP = 3; // 粗い格子は 3セルにひとつ

function easeIn(t) { return t * t; }

// 文字列から決まる擬似乱数(mulberry32)。紙玉の形の再現性に使う。
function seededRng(str) {
  let h = 1779033703;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return function () {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

// 頂点ごとの乱れの種。細かい皺(fine)と大きな折り(coarse)の二層。
function makeSeeds(rng) {
  const fine = [];
  for (let j = 0; j <= ROWS; j++) {
    fine[j] = [];
    for (let i = 0; i <= COLS; i++) {
      fine[j][i] = { dx: rng() * 2 - 1, dy: rng() * 2 - 1, f: rng() };
    }
  }
  const CJ = Math.ceil((ROWS + 1) / CSTEP), CI = Math.ceil((COLS + 1) / CSTEP);
  const coarse = [];
  for (let j = 0; j <= CJ; j++) {
    coarse[j] = [];
    for (let i = 0; i <= CI; i++) {
      coarse[j][i] = { dx: rng() * 2 - 1, dy: rng() * 2 - 1, f: rng() };
    }
  }
  return { fine, coarse };
}

// 大きな皺の稜線:頂点をランダムに数歩たどる折れ線。変形に追従して一緒に潰れていく。
function makeCreases(rng) {
  const creases = [];
  for (let k = 0; k < 10; k++) {
    const chain = [];
    let ci = Math.floor(rng() * (COLS + 1));
    let cj = Math.floor(rng() * (ROWS + 1));
    const steps = 4 + Math.floor(rng() * 4);
    chain.push([ci, cj]);
    for (let s = 0; s < steps; s++) {
      ci = Math.min(COLS, Math.max(0, ci + (rng() < 0.5 ? -1 : 1)));
      cj = Math.min(ROWS, Math.max(0, cj + (rng() < 0.5 ? -1 : 1)));
      chain.push([ci, cj]);
    }
    creases.push(chain);
  }
  return creases;
}

function vertexPosFactory(W, H, seeds) {
  const cx = W / 2, cy = H / 2;
  const { fine, coarse } = seeds;
  return function vertexPos(i, j, r) {
    const bx = (i / COLS) * W, by = (j / ROWS) * H;
    const s = fine[j][i];
    const cs = coarse[Math.floor(j / CSTEP)][Math.floor(i / CSTEP)];
    const shrink = 1 - 0.62 * easeIn(r);
    let x = cx + (bx - cx) * shrink;
    let y = cy + (by - cy) * shrink;
    // 大きな折り:近隣セルがまとまって同じ方向へ倒れる(面の群れ=folds)
    const ampC = r * Math.min(W, H) * (0.05 + 0.09 * r);
    x += cs.dx * ampC;
    y += cs.dy * ampC;
    // 細かい皺:頂点ごとの独立した乱れ
    const ampF = r * Math.min(W, H) * (0.03 + 0.07 * r);
    x += s.dx * ampF;
    y += s.dy * ampF;
    // 終盤は紙玉の丸みに寄せる:中心からの距離を凸凹の球半径へ丸め込む
    if (r > 0.72) {
      const t = (r - 0.72) / 0.28;
      const ballR = Math.min(W, H) * 0.30 * (0.85 + 0.3 * s.f);
      const dx0 = x - cx, dy0 = y - cy;
      const L = Math.hypot(dx0, dy0) || 1;
      const target = Math.min(L, ballR);
      x = cx + (dx0 / L) * (L + (target - L) * t);
      y = cy + (dy0 / L) * (L + (target - L) * t);
    }
    return [x, y];
  };
}

// ワープ本体:src(潰す前の便箋)を tctx(透明地)へ、握り具合 rr で潰して描く。
function renderWarp(tctx, src, W, H, seeds, creases, vertexPos, rr, dpr) {
  const cx = W / 2, cy = H / 2;
  const { fine, coarse } = seeds;
  tctx.setTransform(1, 0, 0, 1, 0, 0);
  tctx.clearRect(0, 0, W, H);

  const sw = W / COLS, sh = H / ROWS;
  const origArea = sw * sh;
  for (let j = 0; j < ROWS; j++) {
    for (let i = 0; i < COLS; i++) {
      const p00 = vertexPos(i, j, rr);
      const p10 = vertexPos(i + 1, j, rr);
      const p01 = vertexPos(i, j + 1, rr);
      // セルごとのアフィン近似(微小な隙間は転写元を1pxはみ出させて塞ぐ)
      const a = (p10[0] - p00[0]) / sw, b = (p10[1] - p00[1]) / sw;
      const c = (p01[0] - p00[0]) / sh, d = (p01[1] - p00[1]) / sh;
      tctx.setTransform(a, b, c, d, p00[0], p00[1]);
      tctx.drawImage(src, i * sw - 1, j * sh - 1, sw + 2, sh + 2, -1, -1, sw + 2, sh + 2);
      // 面の陰影:大折りの向き+細皺+圧縮。段階に量子化して「割れた平面」のトーンにする。
      const area = Math.abs(a * d - b * c) * origArea;
      const comp = Math.min(1, Math.max(0, 1 - area / origArea));
      const cs = coarse[Math.floor(j / CSTEP)][Math.floor(i / CSTEP)];
      let shade = (cs.f - 0.5) * 2 * 0.24 * rr
                + (fine[j][i].f - 0.5) * 2 * 0.14 * rr
                + comp * 0.34 * rr;
      shade = Math.round(shade * 7) / 7; // 量子化:紙の面は連続トーンでは割れない
      tctx.fillStyle = shade > 0
        ? `rgba(58,46,37,${Math.min(0.55, shade)})`
        : `rgba(255,255,255,${Math.min(0.5, -shade * 0.9)})`;
      tctx.fillRect(-1, -1, sw + 2, sh + 2);
    }
  }
  tctx.setTransform(1, 0, 0, 1, 0, 0);

  // 細かい稜線ネットワーク:およそ半分のセル辺に、短い折り目が走る
  if (rr > 0.22) {
    tctx.lineCap = "round";
    for (let j = 0; j < ROWS; j++) {
      for (let i = 0; i < COLS; i++) {
        const s = fine[j][i];
        if (s.f < 0.52) continue;
        const pA = vertexPos(i, j, rr);
        const pB = s.dx > 0 ? vertexPos(i + 1, j, rr) : vertexPos(i, j + 1, rr);
        tctx.strokeStyle = `rgba(255,255,255,${0.22 * rr})`;
        tctx.lineWidth = 0.9 * dpr;
        tctx.beginPath(); tctx.moveTo(pA[0] + dpr * 0.6, pA[1] + dpr * 0.6); tctx.lineTo(pB[0] + dpr * 0.6, pB[1] + dpr * 0.6); tctx.stroke();
        tctx.strokeStyle = `rgba(58,46,37,${0.15 * rr})`;
        tctx.lineWidth = 0.7 * dpr;
        tctx.beginPath(); tctx.moveTo(pA[0], pA[1]); tctx.lineTo(pB[0], pB[1]); tctx.stroke();
      }
    }
  }

  // 大きな皺の稜線:暗い線+半ピクセルずらした明るい線で、折り目の山谷(エンボス)を出す
  if (rr > 0.1) {
    for (const chain of creases) {
      const pts = chain.map(([ci, cj]) => vertexPos(ci, cj, rr));
      tctx.strokeStyle = `rgba(255,255,255,${0.30 * rr})`;
      tctx.lineWidth = 1.3 * dpr;
      tctx.beginPath();
      pts.forEach((p, k) => { k ? tctx.lineTo(p[0] + dpr, p[1] + dpr) : tctx.moveTo(p[0] + dpr, p[1] + dpr); });
      tctx.stroke();
      tctx.strokeStyle = `rgba(58,46,37,${0.32 * rr})`;
      tctx.lineWidth = 1 * dpr;
      tctx.beginPath();
      pts.forEach((p, k) => { k ? tctx.lineTo(p[0], p[1]) : tctx.moveTo(p[0], p[1]); });
      tctx.stroke();
    }
  }

  // 紙玉が丸まってきたら、球としての立体陰影(上手前から光・縁が沈む)を紙の上にだけ重ねる
  if (rr > 0.6) {
    const t = (rr - 0.6) / 0.4;
    const gR = Math.min(W, H) * 0.42;
    tctx.globalCompositeOperation = "source-atop";
    const grad = tctx.createRadialGradient(cx - gR * 0.25, cy - gR * 0.3, gR * 0.1, cx, cy, gR);
    grad.addColorStop(0, `rgba(255,255,255,${0.18 * t})`);
    grad.addColorStop(0.6, "rgba(58,46,37,0)");
    grad.addColorStop(1, `rgba(58,46,37,${0.5 * t})`);
    tctx.fillStyle = grad;
    tctx.fillRect(0, 0, W, H);
    tctx.globalCompositeOperation = "source-over";
  }
}

// 皺の名残りを描く(広げて書き続ける時、便箋に残る折り目)
function drawResidue(g, W, H, dpr, seeds, creases, vertexPos) {
  const rr = 0.22; // 皺の位置だけ借りる(紙自体は歪めない)
  for (const chain of creases) {
    const pts = chain.map(([ci, cj]) => vertexPos(ci, cj, rr));
    g.strokeStyle = "rgba(255,255,255,0.30)";
    g.lineWidth = 1.4 * dpr;
    g.beginPath();
    pts.forEach((p, k) => { k ? g.lineTo(p[0] + dpr, p[1] + dpr) : g.moveTo(p[0] + dpr, p[1] + dpr); });
    g.stroke();
    g.strokeStyle = "rgba(58,46,37,0.14)";
    g.lineWidth = 1 * dpr;
    g.beginPath();
    pts.forEach((p, k) => { k ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]); });
    g.stroke();
  }
  // 面のよれ:種の偏った面にだけ、ごく薄い明暗を置く
  const sw = W / COLS, sh = H / ROWS;
  for (let j = 0; j < ROWS; j++) {
    for (let i = 0; i < COLS; i++) {
      const f = seeds.fine[j][i].f;
      if (Math.abs(f - 0.5) < 0.36) continue;
      g.fillStyle = f > 0.5 ? "rgba(58,46,37,0.035)" : "rgba(255,255,255,0.06)";
      g.fillRect(i * sw, j * sh, sw, sh);
    }
  }
}

// 屑籠から「ひろげて書きつづける」時:その紙玉と同じ皺の跡を便箋に付ける。
// seed(紙玉のid)から同じ乱数を引くので、跡はあの玉の折り目と同じ走りになる。
export function attachResidue(hostEl, seedStr) {
  const rect = hostEl.getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const W = Math.max(1, Math.round(rect.width * dpr));
  const H = Math.max(1, Math.round(rect.height * dpr));
  const res = document.createElement("canvas");
  res.width = W; res.height = H;
  res.className = "wrinkle-residue";
  res.style.cssText =
    "position:absolute;inset:0;width:100%;height:100%;z-index:4;pointer-events:none;";
  const rng = seededRng(seedStr || "x");
  const seeds = makeSeeds(rng);
  const creases = makeCreases(rng);
  drawResidue(res.getContext("2d"), W, H, dpr, seeds, creases, vertexPosFactory(W, H, seeds));
  hostEl.appendChild(res);
  return res;
}

// hostEl(便箋領域)に覆い被さる canvas を作り、握り具合で潰していく。
// 返り値: { el, update(r), ratio, detachToBody(), makeResidue(), destroy() }
export function createCrumple(hostEl, { text, color, vertical }) {
  const rect = hostEl.getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const W = Math.max(1, Math.round(rect.width * dpr));
  const H = Math.max(1, Math.round(rect.height * dpr));

  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  canvas.className = "crumple-canvas";
  canvas.style.cssText =
    "position:absolute;inset:0;width:100%;height:100%;z-index:5;pointer-events:none;";
  hostEl.appendChild(canvas);
  const ctx = canvas.getContext("2d");

  // 元の便箋(潰す前の姿)をオフスクリーンに一度だけ描く
  const src = document.createElement("canvas");
  src.width = W; src.height = H;
  renderLetter(src.getContext("2d"), W, H, { text, color, vertical }, dpr);

  // セル描画用の中間バッファ(silhouette に影を落とすため、透明地に描いてから合成する)
  const tmp = document.createElement("canvas");
  tmp.width = W; tmp.height = H;
  const tctx = tmp.getContext("2d");

  const rng = Math.random;
  const seeds = makeSeeds(rng);
  const creases = makeCreases(rng);
  const vertexPos = vertexPosFactory(W, H, seeds);

  let ratio = 0;
  let detached = false;

  function compose() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!detached) {
      // 便箋の中にいる間は地の色で下の本物の便箋を隠す
      ctx.fillStyle = PAPER2;
      ctx.fillRect(0, 0, W, H);
    }
    ctx.save();
    ctx.shadowColor = `rgba(58,46,37,${0.18 + 0.22 * ratio})`;
    ctx.shadowBlur = (6 + 14 * ratio) * dpr;
    ctx.shadowOffsetY = 3 * dpr;
    ctx.drawImage(tmp, 0, 0);
    ctx.restore();
  }

  function update(r) {
    ratio = Math.min(1, Math.max(0, r));
    renderWarp(tctx, src, W, H, seeds, creases, vertexPos, ratio, dpr);
    compose();
  }

  // 封筒は overflow:hidden なので、投げる前に body 直下の fixed 要素へ付け替える。
  // 以後は透明地(紙玉の外は透ける)。投げは transform で動かす。
  function detachToBody() {
    const r2 = hostEl.getBoundingClientRect();
    detached = true;
    canvas.style.cssText =
      `position:fixed;left:${r2.left}px;top:${r2.top}px;width:${r2.width}px;height:${r2.height}px;` +
      "z-index:98;pointer-events:none;";
    document.body.appendChild(canvas);
    compose();
  }

  function makeResidue() {
    const res = document.createElement("canvas");
    res.width = W; res.height = H;
    res.className = "wrinkle-residue";
    res.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;z-index:4;pointer-events:none;";
    drawResidue(res.getContext("2d"), W, H, dpr, seeds, creases, vertexPos);
    return res;
  }

  function destroy() { canvas.remove(); }

  update(0);
  return {
    el: canvas,
    update,
    get ratio() { return ratio; },
    get detached() { return detached; },
    detachToBody,
    makeResidue,
    destroy,
  };
}

// 屑籠ビュー用:その手紙を実際に丸めた紙玉を canvas で描いて返す。
// seed(レコードid)から形が決まるので、同じ紙玉はいつ見ても同じ潰れ方。
export function renderPaperBall({ text, color, vertical, seed, size }) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  // 紙玉の直径 ≈ 0.69 × 元便箋の短辺。切り出し枠 0.74 に収まるよう逆算する。
  const SQ = Math.round((size * dpr) / 0.74) * 2; // ×2の高解像度で描いてから縮小し、質を保つ
  const src = document.createElement("canvas");
  src.width = SQ; src.height = SQ;
  // 文字は便箋比よりやや大きめに描く(潰した後も文字の断片がインクの痕として読めるように)
  renderLetter(src.getContext("2d"), SQ, SQ, { text, color, vertical }, SQ / 200);

  const tmp = document.createElement("canvas");
  tmp.width = SQ; tmp.height = SQ;
  const rng = seededRng(seed || "x");
  const seeds = makeSeeds(rng);
  const creases = makeCreases(rng);
  const vertexPos = vertexPosFactory(SQ, SQ, seeds);
  renderWarp(tmp.getContext("2d"), src, SQ, SQ, seeds, creases, vertexPos, 1, dpr);

  const out = document.createElement("canvas");
  const px = Math.round(size * dpr);
  out.width = px; out.height = px;
  const crop = SQ * 0.74, off = (SQ - crop) / 2;
  out.getContext("2d").drawImage(tmp, off, off, crop, crop, 0, 0, px, px);
  out.style.cssText = "width:100%;height:100%;display:block;";
  return out;
}

// 便箋そのものの描き起こし:紙・罫線・本文・気分の色。
// 縦書きは右の列から下へ。ユーザー本文には改行以外の手を入れない(書かれたまま写す)。
function renderLetter(g, W, H, { text, color, vertical }, dpr) {
  g.fillStyle = PAPER;
  g.fillRect(0, 0, W, H);
  if (color) {
    g.globalAlpha = 0.06;
    g.fillStyle = color;
    g.fillRect(0, 0, W, H);
    g.globalAlpha = 1;
  }
  g.strokeStyle = LINE;
  g.lineWidth = 1 * dpr;
  g.strokeRect(0.5 * dpr, 0.5 * dpr, W - dpr, H - dpr);

  const fs = 17 * dpr, lh = 36 * dpr, pad = 22 * dpr;
  g.fillStyle = INK;
  g.font = `${fs}px 'Shippori Mincho','Hiragino Mincho ProN','Yu Mincho',serif`;
  g.textBaseline = "top";
  const body = text || "";

  if (vertical) {
    const colStep = fs * 1.9;
    const chStep = fs * 1.32;
    const maxCh = Math.max(1, Math.floor((H - 2 * pad) / chStep));
    let x = W - pad - fs;
    for (const line of body.split("\n")) {
      let n = 0;
      for (const ch of line) {
        if (n >= maxCh) { x -= colStep; n = 0; }
        g.fillText(ch, x, pad + n * chStep);
        n++;
      }
      x -= colStep;
      if (x < pad) break;
    }
  } else {
    // 横書き:textarea と同じ雰囲気の下罫線
    g.strokeStyle = LINE;
    g.lineWidth = 0.5 * dpr;
    for (let y = pad + lh; y < H - pad / 2; y += lh) {
      g.beginPath(); g.moveTo(pad, y); g.lineTo(W - pad, y); g.stroke();
    }
    const maxX = W - pad;
    let x = pad, y = pad + (lh - fs) / 2;
    for (const ch of body) {
      if (ch === "\n") { x = pad; y += lh; continue; }
      const w = g.measureText(ch).width;
      if (x + w > maxX) { x = pad; y += lh; }
      if (y > H - pad) break;
      g.fillText(ch, x, y);
      x += w;
    }
  }

  // 気分の色は左下に小さな一粒(スウォッチの気配だけ残す)
  if (color) {
    g.beginPath();
    g.arc(pad, H - pad, 4.5 * dpr, 0, Math.PI * 2);
    g.fillStyle = color;
    g.globalAlpha = 0.85;
    g.fill();
    g.globalAlpha = 1;
  }
}
