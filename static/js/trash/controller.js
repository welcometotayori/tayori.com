// ── 捨てられない屑籠：結線(状態機械・長押しフォールバック・屑籠ビュー)──
// 依存：window.tayoriTrash（index.html 内の投函スクリプトが渡す橋。本文・色・縦書き・クリア・flash）
// カメラ経路：かざす→便箋が応える→握ると紙が手に付いてくる→振って開くと飛ぶ。
// 一度くしゃくしゃにした紙は、そっと開いても元に戻らない（その場に落ちて残る）。
// 戻すのは「落ちた紙玉をクリック/タップする」意図的な操作だけ。紙の物理に嘘をつかない。

import { initHandLandmarker, startCamera, stopCamera, detectFrame } from "./hand_tracker.js";
import { graspRatio, isFist, palmCenter, toScreen, isOverRect, VelocityTracker } from "./gesture.js";
import { createCrumple, renderPaperBall, attachResidue } from "./crumple.js";
import { throwPaper, throwInto } from "./throw.js";
import { saveToTrash, fetchTrash } from "./trash_api.js";

const hooks = window.tayoriTrash;
const $ = (id) => document.getElementById(id);

const handBtn = $("handBtn");
const handLabel = $("handLabel");
const binBtn = $("binBtn");
const poemHost = document.querySelector("#compose .poem");

if (hooks && poemHost) init();

function init() {

  // ══ 共通 ═══════════════════════════════════════════════

  let crumple = null;   // 進行中のクシャ（カメラ経路と長押し経路で共用。同時には走らない）
  let busy = false;     // 投げ・復元アニメ中の再入防止

  function letterText() { return (hooks.getPoem() || "").replace(/\s+$/, ""); }

  function startCrumple() {
    // 皺の跡が残った紙をもう一度握る時は、古い跡は新しい皺に呑まれる
    poemHost.querySelectorAll(".wrinkle-residue").forEach((x) => x.remove());
    crumple = createCrumple(poemHost, {
      text: letterText(),
      color: hooks.getColor(),
      vertical: hooks.getVertical(),
    });
  }

  // 保存して無言でクリア（決定事項：通知なし・undoなし。誤爆は速度と保持時間で防ぐ）
  async function archive() {
    const ok = await saveToTrash(letterText(), {
      moodColor: hooks.getColor(),
      vertical: hooks.getVertical(),
      trace: hooks.getTrace ? hooks.getTrace() : null,  // 筆跡（TypeTrace）ごと封じる
    });
    if (ok) hooks.clear();
    else hooks.flash("うまく捨てられませんでした。もう一度どうぞ。");
  }

  // ══ カメラ経路（「捨てる」＝手で掴む→紙が手に付いてくる→放ると飛ぶ）══════

  let camOn = false, camBusy = false, stream = null, video = null, raf = 0;
  let camState = "IDLE"; // IDLE → ARMING(260ms) → GRABBING → (THROWN | BALL_REST)
  let armAt = 0, lostAt = 0, smoothR = 0, spreadAt = 0;
  let grabFrom = null;           // 掴んだ瞬間の掌の画面座標（追従の基準）
  let followX = 0, followY = 0;  // 紙の追従オフセット（滑らかに掌へ寄せる）
  const vel = new VelocityTracker(7);
  let shadow = null, shadowCtx = null, shadowBuf = null;

  // 投げ判定（px/ms・ピーク速度）。手を開いた時の既定の挙動は「紙玉がその場に落ちる」なので、
  // 投げは「明確に振った」時だけ発火すればいい。高めに置いて誤爆を消す。
  const THROW_SPEED = 0.8;
  const REST_CRUMPLE = 0.55; // 落ちる時、最低ここまでは丸まる（一度握った紙は伸びない）

  async function camStart() {
    camBusy = true;
    handLabel.textContent = "手を読んでいます…";
    try {
      await initHandLandmarker();
      video = document.createElement("video");
      video.muted = true; video.playsInline = true;
      video.setAttribute("playsinline", "");
      // 非表示にすると止まる端末があるので、見えない大きさで置いておく
      video.style.cssText = "position:fixed;left:0;bottom:0;width:2px;height:2px;opacity:0.01;pointer-events:none;z-index:0;";
      document.body.appendChild(video);
      stream = await startCamera(video);
      shadow = document.createElement("canvas");
      shadow.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:60;";
      shadow.width = window.innerWidth;
      shadow.height = window.innerHeight;
      document.body.appendChild(shadow);
      shadowCtx = shadow.getContext("2d");
      shadowBuf = document.createElement("canvas");
      camOn = true;
      handBtn.setAttribute("aria-pressed", "true");
      handBtn.classList.add("on");
      handLabel.textContent = "手を離す";
      raf = requestAnimationFrame(camLoop);
    } catch (e) {
      camTeardown();
      const denied = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
      hooks.flash(denied
        ? "カメラの許可が得られませんでした。設定で許可すると、手で捨てられます。"
        : "この端末では手の検出を使えません。");
    }
    camBusy = false;
  }

  function camTeardown() {
    cancelAnimationFrame(raf);
    stopCamera(stream);
    stream = null;
    video?.remove(); video = null;
    shadow?.remove(); shadow = null; shadowCtx = null; shadowBuf = null;
    camOn = false;
    camState = "IDLE";
    poemHost.classList.remove("hand-near", "hand-taken");
    handBtn.setAttribute("aria-pressed", "false");
    handBtn.classList.remove("on");
    handLabel.textContent = "捨てる";
    if (crumple) { const c = crumple; crumple = null; c.destroy(); }
  }

  // 初回だけ使い方の栞を挟む。二回目からはすぐカメラへ。
  const guideOv = $("handGuideOv");
  function openGuideThenStart() {
    let seen = false;
    try { seen = localStorage.getItem("tayori_hand_guide") === "1"; } catch (e) {}
    if (seen || !guideOv) { camStart(); return; }
    guideOv.classList.add("on");
    setTimeout(() => $("handGuideStart")?.focus(), 60);
  }
  $("handGuideStart")?.addEventListener("click", () => {
    try { localStorage.setItem("tayori_hand_guide", "1"); } catch (e) {}
    guideOv.classList.remove("on");
    camStart();
  });
  guideOv?.addEventListener("click", (e) => { if (e.target === guideOv) guideOv.classList.remove("on"); });

  if (handBtn) {
    handBtn.addEventListener("click", () => {
      if (camBusy) return;
      if (camOn) { camTeardown(); return; }
      if (!navigator.mediaDevices?.getUserMedia) {
        hooks.flash("この端末ではカメラを使えません。");
        return;
      }
      openGuideThenStart();
    });
  }

  // 画面が隠れたらカメラは必ず止める（バッテリーとプライバシー）
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && camOn) camTeardown();
  });

  // 掴んだ紙を掌へ滑らかに寄せる（毎フレーム）。canvas は body 直下の fixed なので画面中どこへでも。
  function followHand(palm) {
    const dx = palm.x - grabFrom.x, dy = palm.y - grabFrom.y;
    followX += (dx - followX) * 0.42;
    followY += (dy - followY) * 0.42;
    crumple.el.style.transform =
      `translate(${followX}px,${followY}px) rotate(${followX * 0.02}deg)`;
  }

  // 広げて戻す：落ちた紙玉をタップした時。紙は便箋へ滑って戻り、皺がほどけていく。
  // ただし折り目の跡は便箋に残る——くしゃくしゃにした紙で、それでも続きを書ける。
  function restoreGrab(fromR) {
    if (!crumple) return;
    busy = true;
    const c = crumple; crumple = null;
    const x0 = followX, y0 = followY;
    const t0 = performance.now(), dur = 340;
    (function tick() {
      const t = Math.min(1, (performance.now() - t0) / dur);
      const e = 1 - (1 - t) * (1 - t); // ease-out
      c.el.style.transform = `translate(${x0 * (1 - e)}px,${y0 * (1 - e)}px)`;
      c.update(fromR * (1 - e));
      if (t < 1) requestAnimationFrame(tick);
      else {
        if (fromR > 0.3) poemHost.appendChild(c.makeResidue());
        c.destroy();
        poemHost.classList.remove("hand-taken");
        busy = false;
        // 「書き続けたい」の続きへ：便箋にそのまま筆を戻す
        try { document.getElementById("poemInput")?.focus(); } catch (e2) {}
      }
    })();
  }

  // 手を開いた時の既定の挙動：紙はくしゃくしゃのまま、ぽとりと落ちてその場に残る。
  // 一度握った紙は伸びない（落ちながら最低限まで丸まりきる）。
  // もう一度握れば投げられる。クリック/タップして初めて、意図的に広げて便箋へ戻せる。
  function dropBall() {
    if (!crumple) return;
    busy = true;
    const c = crumple;
    const x0 = followX, y0 = followY;
    const r0 = smoothR, r1 = Math.max(smoothR, REST_CRUMPLE);
    const drop = 34 + Math.random() * 18;
    const t0 = performance.now(), dur = 320;
    (function tick() {
      const t = Math.min(1, (performance.now() - t0) / dur);
      // 落下は加速、着地でわずかに弾む。落ちながら丸まりきる。
      const fall = t < 0.8 ? (t / 0.8) * (t / 0.8) : 1 - Math.sin((t - 0.8) / 0.2 * Math.PI) * 0.06;
      followY = y0 + drop * fall;
      smoothR = r0 + (r1 - r0) * t;
      c.update(smoothR);
      c.el.style.transform = `translate(${x0}px,${followY}px)`;
      if (t < 1) requestAnimationFrame(tick);
      else {
        busy = false;
        camState = "BALL_REST";
        c.el.style.pointerEvents = "auto";
        c.el.style.cursor = "pointer";
        c.el.title = "ひろげて、便箋に戻す";
      }
    })();
  }

  // 落ちている紙玉をクリック/タップ → 意図的に広げて戻す（これだけが「戻る」唯一の道）
  document.addEventListener("click", (e) => {
    if (camState !== "BALL_REST" || !crumple) return;
    if (e.target !== crumple.el) return;
    crumple.el.style.pointerEvents = "none";
    camState = "IDLE";
    restoreGrab(smoothR);
  });

  function camLoop() {
    if (!camOn) return;
    raf = requestAnimationFrame(camLoop);
    const lm = detectFrame(video);
    drawShadow(lm);

    if (busy) return; // 投げ・落下・復元中は状態機械を触らない
    const now = performance.now();
    const palm = lm ? toScreen(palmCenter(lm)) : null;
    const overLetter = palm && isOverRect(palm, poemHost.getBoundingClientRect());
    const hasText = !!letterText().trim();

    // 手をかざすと便箋が小さく応える（掴めることの合図）
    poemHost.classList.toggle("hand-near",
      camState === "IDLE" && !!palm && overLetter && hasText && !isFist(lm));

    if (camState === "IDLE") {
      if (lm && hasText && overLetter && isFist(lm)) {
        camState = "ARMING"; armAt = now;
      }
    } else if (camState === "ARMING") {
      // 掴み成立には「便箋の上でグーを260ms維持」を要求（誤爆ガード）
      if (!lm || !isFist(lm) || !isOverRect(toScreen(palmCenter(lm)), poemHost.getBoundingClientRect())) {
        camState = "IDLE";
      } else if (now - armAt > 260) {
        // 掴んだ：紙を便箋から「取り上げて」手に持たせる
        startCrumple();
        crumple.detachToBody();                    // 便箋の枠を出て、手と一緒に動けるように
        poemHost.classList.remove("hand-near");
        poemHost.classList.add("hand-taken");      // 便箋の上からは紙が消える（手の中にあるので）
        grabFrom = toScreen(palmCenter(lm));
        followX = followY = 0;
        smoothR = 0; vel.reset(); lostAt = 0;
        camState = "GRABBING";
      }
    } else if (camState === "GRABBING") {
      if (!crumple) { camState = "IDLE"; return; }
      if (!lm) {
        // 手を見失った：少し待って戻ってこなければ、紙玉のまま落とす（一度握った紙は伸びない）
        if (!lostAt) lostAt = now;
        else if (now - lostAt > 450) { dropBall(); }
        return;
      }
      lostAt = 0;
      vel.push(palm.x, palm.y);
      followHand(palm);                            // ← 紙が手に付いてくる
      const target = graspRatio(lm);
      smoothR += (target - smoothR) * 0.35;
      crumple.update(smoothR);
      if (!isFist(lm)) {
        // リリース検知。既定は「紙玉のままその場に落ちる」。
        // 明確に振っていた時（ピーク速度がしきい値超え）だけ、その方向へ飛んでいく。
        const v = vel.velocity();
        const speed = Math.max(Math.hypot(v.vx, v.vy), vel.peakSpeed());
        if (speed >= THROW_SPEED) {
          camState = "THROWN"; busy = true;
          const c = crumple; crumple = null;
          c.update(1);
          const dir = Math.hypot(v.vx, v.vy) > 0.02 ? v : { vx: 0.3, vy: -0.5 };
          throwPaper(c.el, dir, async () => {
            await archive();                        // clear() が便箋を空にする
            poemHost.classList.remove("hand-taken");
            c.destroy(); busy = false;
            camState = "IDLE";
          });
        } else {
          dropBall();                               // 手を開く＝くしゃくしゃの紙が落ちる
        }
      }
    } else if (camState === "BALL_REST") {
      // 落ちている紙玉：もう一度握れば持ち上げて続きができる。
      // 開いた手をかざしつづけると、紙が手の下でゆっくり緩み、やがてほどけて便箋に戻る（タップでも可）。
      if (!crumple) return;
      const overBall = lm && isOverRect(palm, crumple.el.getBoundingClientRect(), 40);
      if (overBall && isFist(lm)) {
        spreadAt = 0;
        crumple.el.style.pointerEvents = "none";
        crumple.el.style.cursor = "";
        grabFrom = { x: palm.x - followX, y: palm.y - followY }; // 今の位置から途切れず持ち上げる
        vel.reset(); lostAt = 0;
        camState = "GRABBING";
      } else if (overBall && graspRatio(lm) < 0.25) {
        // しっかり開いた手だけに応える（半端な形では緩まない）
        if (!spreadAt) spreadAt = now;
        const p = Math.min(1, (now - spreadAt) / 100);
        crumple.update(smoothR * (1 - 0.22 * p));   // 予兆：手の下で紙がすこし緩む
        if (p >= 1) {
          spreadAt = 0;
          crumple.el.style.pointerEvents = "none";
          camState = "IDLE";
          restoreGrab(smoothR);                      // ほどけて便箋へ（皺の跡は残る）
        }
      } else if (spreadAt) {
        spreadAt = 0;
        crumple.update(smoothR);                     // 手が離れたら締まり直す
      }
    }
  }

  // 手影：映像は映さない。輪郭を面で描いてぼかし、障子に落ちた影のような実影にする。
  // （骨組みの線を並べる描き方はやめた。指は太さのある帯、掌はひとつの面、全体を大きくぼかす）
  const FINGERS = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]];
  function drawShadow(lm) {
    if (!shadowCtx) return;
    if (shadow.width !== window.innerWidth) { shadow.width = window.innerWidth; shadow.height = window.innerHeight; }
    const g = shadowCtx;
    g.clearRect(0, 0, shadow.width, shadow.height);
    if (!lm) return;
    if (shadowBuf.width !== shadow.width) { shadowBuf.width = shadow.width; shadowBuf.height = shadow.height; }
    const b = shadowBuf.getContext("2d");
    b.clearRect(0, 0, shadowBuf.width, shadowBuf.height);
    const pts = lm.map((p) => toScreen(p));
    const palmW = Math.max(24, Math.hypot(pts[5].x - pts[17].x, pts[5].y - pts[17].y));
    b.fillStyle = "#000";
    b.strokeStyle = "#000";
    b.lineCap = "round";
    b.lineJoin = "round";
    // 掌：関節を巡るひとつの面（親指の付け根も含める）
    b.beginPath();
    [0, 1, 2, 5, 9, 13, 17].forEach((i, k) => {
      const p = pts[i];
      if (k === 0) b.moveTo(p.x, p.y); else b.lineTo(p.x, p.y);
    });
    b.closePath();
    b.fill();
    // 指：太さのある帯。親指はやや太く、小指はやや細く。
    FINGERS.forEach((f, fi) => {
      b.lineWidth = palmW * (fi === 0 ? 0.34 : fi === 4 ? 0.22 : 0.26);
      b.beginPath();
      b.moveTo(pts[f[0]].x, pts[f[0]].y);
      for (const i of f) b.lineTo(pts[i].x, pts[i].y);
      b.stroke();
    });
    // 本影＋半影の二層。少し右下へずらすと「光がある」ように見える。
    const a = camState === "GRABBING" ? 1.25 : 1;
    g.save();
    g.filter = "blur(18px)";
    g.globalAlpha = 0.10 * a;
    g.drawImage(shadowBuf, 14, 18);
    g.filter = "blur(7px)";
    g.globalAlpha = 0.14 * a;
    g.drawImage(shadowBuf, 6, 8);
    g.restore();
    // 墨色に転ぶよう色を掛ける
    g.save();
    g.globalCompositeOperation = "source-in";
    g.fillStyle = "rgba(58,46,37,1)";
    g.fillRect(0, 0, shadow.width, shadow.height);
    g.restore();
  }

  // ══ 屑籠ビュー（開けば読める。でも、消せない）═══════════════════

  const trashOv = $("trashOv");
  const scatter = $("trashScatter");
  const emptyNote = $("trashEmpty");
  const openOv = $("trashOpenOv");
  const openPaper = $("trashOpenPaper");
  const openDate = $("trashOpenDate");

  function hashSeed(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return () => { h = (h * 1664525 + 1013904223) >>> 0; return h / 4294967296; };
  }

  // 屑籠の眺め：捨てた日の順に、古い紙玉から籠の底へ地層のように積もる。
  // 籠は背と前の二枚（markup側）で紙玉を挟む。月の変わり目にだけ小さく年月を刻む。
  // 件数もグラフも出さない。時間だけが、静かに層になる。
  function sedimentPos(sorted) {
    const perRow = 4, rowH = 10.5;
    const TOP = 26, BOTTOM = 84;      // 籠の口と底（%・basket SVG と対）
    return sorted.map((item, k) => {
      const rnd = hashSeed((item.id || "y") + "s");
      const row = Math.floor(k / perRow);
      const col = k % perRow;
      const y = Math.max(TOP + 4, 79 - row * rowH + (rnd() * 3 - 1.5));
      // 籠は上が広く下がすぼまる台形。深さに応じて置ける幅を変える
      const frac = (BOTTOM - y) / (BOTTOM - TOP);
      const w = 44 + 16 * frac;
      const x = 50 - w / 2 + ((col + 0.5) * w) / perRow + (rnd() * 5 - 2.5);
      return { x, y };
    });
  }

  async function renderTrash() {
    const items = await fetchTrash();
    // 籠（.kuzu-basket）は残し、紙玉と年月だけ描き直す
    scatter.querySelectorAll(".paper-ball,.trash-era").forEach((x) => x.remove());
    emptyNote.style.display = items.length ? "none" : "";

    const sorted = items.slice().sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
    const pos = sedimentPos(sorted);
    const placed = sorted.map((item, k) => ({ item, x: pos[k].x, y: pos[k].y }));
    // 月の変わり目にだけ、籠の左に小さく年月を置く（数字はこれだけ）
    let prevKey = "";
    sorted.forEach((item, k) => {
      const d = new Date(item.created_at || "");
      if (isNaN(d)) return;
      const key = d.getFullYear() + "." + (d.getMonth() + 1);
      if (key !== prevKey) {
        prevKey = key;
        const era = document.createElement("div");
        era.className = "trash-era";
        era.textContent = key;
        era.style.top = pos[k].y + "%";
        scatter.appendChild(era);
      }
    });

    placed.forEach(({ item, x, y }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "paper-ball";
      b.setAttribute("aria-label", "丸められた手紙");
      const rnd = hashSeed(item.id || "y");
      const size = 48 + Math.round(rnd() * 26);
      b.style.cssText =
        `left:${x}%;top:${y}%;width:${size}px;height:${size}px;` +
        `transform:translate(-50%,-50%) rotate(${Math.round(rnd() * 60 - 30)}deg);`;
      // 紙玉は本物のクシャ描画エンジンで描く：その手紙の本文が実際に折り込まれた玉になる。
      // 形は id から決まるので、いつ開いても同じ潰れ方のまま底に沈んでいる。
      b.appendChild(renderPaperBall({
        text: item.content,
        color: item.mood_color,
        vertical: !!item.vertical,
        seed: item.id || "x",
        size,
      }));
      b.addEventListener("click", () => openBall(item));
      scatter.appendChild(b);
    });
  }


  function fmtDate(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return `${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}`;
  }

  // 筆跡（TypeTrace）の再生。便箋の書き起こしと同じ流儀：
  // 個々の「間」は最大1.2sに丸め、全体は最大14秒。紙をタップすると先まで飛ばせる。
  let traceRun = 0;
  async function playTrace(item) {
    const runId = ++traceRun;
    // 再生できない事情があれば、黙って完成形を置いて終わる
    const bail = () => { if (runId === traceRun) openPaper.textContent = item.content; };
    let steps = null;
    try {
      const res = await fetch("/api/trash/" + item.id + "/trace");
      if (res.ok) steps = (await res.json()).trace;
    } catch (e) {}
    if (!Array.isArray(steps) || steps.length < 2) { bail(); return; }
    if (runId !== traceRun || !openOv.classList.contains("on")) return;
    const norm = [];
    let prev = 0, acc = 0;
    for (const s of steps) {
      let gap = Math.max(0, (s.t || 0) - prev);
      prev = s.t || 0;
      gap = Math.min(gap, 1200);
      acc += gap;
      norm.push({ at: acc, v: String(s.v == null ? "" : s.v) });
    }
    const total = norm[norm.length - 1].at || 1;
    const scale = total > 14000 ? 14000 / total : 1;
    const caret = document.createElement("span");
    caret.className = "trace-caret";
    let idx = 0;
    const t0 = performance.now();
    (function frame() {
      if (runId !== traceRun || !openOv.classList.contains("on")) return; // 閉じた・飛ばした
      const el = performance.now() - t0;
      while (idx < norm.length && norm[idx].at * scale <= el) {
        openPaper.textContent = norm[idx].v;
        openPaper.appendChild(caret);
        idx++;
      }
      if (idx < norm.length) requestAnimationFrame(frame);
      else openPaper.textContent = item.content;
    })();
  }

  let currentItem = null;             // いま開いている紙玉（「書きつづける」用）
  function openBall(item) {
    currentItem = item;
    traceRun++;                       // 前の再生が残っていたら止める
    const willPlay = item.has_trace &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // 筆跡が封じられている紙玉は、白紙から書き起こす（完成形を先に見せない）
    openPaper.textContent = willPlay ? "" : item.content;
    openPaper.classList.toggle("vertical", !!item.vertical);
    openPaper.style.setProperty("--trash-mood", item.mood_color || "transparent");
    openDate.textContent = item.created_at ? "握りつぶした日 — " + fmtDate(item.created_at) : "";
    openOv.classList.add("on");
    setTimeout(() => $("trashOpenClose")?.focus(), 60);
    if (willPlay) setTimeout(() => playTrace(item), 350);
    // 紙をタップしたら再生を飛ばして、書き終わった姿にする
    openPaper.onclick = () => { traceRun++; openPaper.textContent = item.content; };
  }

  if (binBtn) {
    binBtn.addEventListener("click", () => {
      trashOv.classList.add("on");
      renderTrash();
      setTimeout(() => $("trashClose")?.focus(), 60);
    });
  }
  $("trashClose")?.addEventListener("click", () => trashOv.classList.remove("on"));
  $("trashOpenClose")?.addEventListener("click", () => openOv.classList.remove("on"));

  // ひろげて、書きつづける：本文が便箋へ戻り、あの紙玉と同じ皺の跡が残る。
  // 紙玉そのものは屑籠に残りつづける（消せない、が思想。拾えるのは言葉だけ）。
  $("trashResume")?.addEventListener("click", () => {
    if (!currentItem) return;
    traceRun++;
    hooks.setPoem && hooks.setPoem(currentItem.content);
    openOv.classList.remove("on");
    trashOv.classList.remove("on");
    hooks.goCompose && hooks.goCompose();
    const seed = currentItem.id || "x";
    // タブが切り替わって便箋の寸法が確定してから、皺の跡を焼き付ける
    setTimeout(() => {
      poemHost.querySelectorAll(".wrinkle-residue").forEach((x) => x.remove());
      attachResidue(poemHost, seed);
      try { document.getElementById("poemInput")?.focus(); } catch (e) {}
    }, 80);
  });
  trashOv?.addEventListener("click", (e) => { if (e.target === trashOv) trashOv.classList.remove("on"); });
  openOv?.addEventListener("click", (e) => { if (e.target === openOv) openOv.classList.remove("on"); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (guideOv?.classList.contains("on")) guideOv.classList.remove("on");
    else if (openOv?.classList.contains("on")) openOv.classList.remove("on");
    else if (trashOv?.classList.contains("on")) trashOv.classList.remove("on");
  });
}
