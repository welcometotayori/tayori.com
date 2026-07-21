// ── 捨てられない屑籠：API薄皮 ──
// サーバへ渡るのは本文・気分の色・縦書きフラグ・散乱座標だけ。カメラ由来のものは何も送らない。

export async function saveToTrash(text, opts = {}) {
  try {
    const res = await fetch("/api/trash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text,
        mood_color: opts.moodColor ?? null,
        vertical: opts.vertical ? 1 : 0,
        trace: opts.trace ?? null,
        random_x: 6 + Math.random() * 88,
        random_y: 8 + Math.random() * 84,
      }),
    });
    return res.ok;
  } catch (e) {
    return false;
  }
}

export async function fetchTrash() {
  try {
    const res = await fetch("/api/trash");
    if (!res.ok) return [];
    const j = await res.json();
    return j.items || [];
  } catch (e) {
    return [];
  }
}
