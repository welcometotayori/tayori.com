// ── 捨てられない屑籠：ハンドトラッキング（MediaPipe Hand Landmarker）──
// カメラON時にだけ動的importする。映像もランドマークも一切サーバへ送らない（全部この場で捨てる）。
// GPU初期化に失敗する端末があるので、CPUへ落とすフォールバックを必ず通す。

const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

let handLandmarker = null;

export async function initHandLandmarker() {
  if (handLandmarker) return handLandmarker;
  const { HandLandmarker, FilesetResolver } = await import(`${CDN}/vision_bundle.mjs`);
  const vision = await FilesetResolver.forVisionTasks(`${CDN}/wasm`);
  const opts = (delegate) => ({
    baseOptions: { modelAssetPath: MODEL, delegate },
    runningMode: "VIDEO",
    numHands: 1, // 片手で十分。負荷を抑える
  });
  try {
    handLandmarker = await HandLandmarker.createFromOptions(vision, opts("GPU"));
  } catch (e) {
    handLandmarker = await HandLandmarker.createFromOptions(vision, opts("CPU"));
  }
  return handLandmarker;
}

export async function startCamera(videoEl) {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user" },
    audio: false,
  });
  videoEl.srcObject = stream;
  await videoEl.play();
  return stream;
}

let _lastVideoTime = -1;
let _lastLm = null;

// 毎フレームの検出。呼び出し側の rAF ループから叩く。
// 同じフレームを二度食わせると MediaPipe が警告を出すので currentTime でガードする。
export function detectFrame(videoEl) {
  if (!handLandmarker || !videoEl || videoEl.readyState < 2) return null;
  if (videoEl.currentTime === _lastVideoTime) return _lastLm;
  _lastVideoTime = videoEl.currentTime;
  const result = handLandmarker.detectForVideo(videoEl, performance.now());
  _lastLm = result.landmarks?.[0] ?? null;
  return _lastLm;
}

export function stopCamera(stream) {
  stream?.getTracks().forEach((t) => t.stop());
  _lastVideoTime = -1;
  _lastLm = null;
}
