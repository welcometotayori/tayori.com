/* 一枚の宙のふるまい。2026-07-31 に templates/canvas.html の <script> から出した
   （理由は canvas.css の頭と同じ）。読み込みは <head> の defer——面の終わりに置くのと
   走り出す時（解析の後）は同じで、**見つかるのが早い**ぶんだけ本文と並んで落ちてくる。
   利用者ごとに違う値（START_ROOM / LOGGED_IN / BOOT / OPEN_ID）だけは HTML 側の
   小さな括りに残してある。ここからは素の大域変数として見える。 */
'use strict';
const REDUCED=matchMedia('(prefers-reduced-motion: reduce)').matches;
const vp=document.getElementById('vp'), world=document.getElementById('world');
/* ── プランで変わるもの（収益モデル v1・2026-08-07）───────────────────
   限度は**サーバの表（app.py の _PLAN_FEATURES）が唯一の出どころ**で、ここは写しを
   受け取るだけ。画面側に数字を書き写すと、値を変えた日にどちらかが古いまま残る。
   立ち上がりに同梱してある（_boot_payload）＝書く柱が開いた瞬間から正しい形で立つ。
   bootTake ではなく直に読むのは、この値を後から何度も引くため（消してはいけない）。 */
const FEATURES=Object.assign(
  {poem_max:80, title:false, shelves:3, saved:50, search_wide:false, grid:false,
   export:true, beta:false},
  (typeof BOOT==='object'&&BOOT&&BOOT.features)||{});
const PLAN=(typeof BOOT==='object'&&BOOT&&BOOT.plan)||'free';
/* 画面の寸法は測って持っておく（2026-07-31 カクツキ修正）。clientWidth を
   毎フレーム読むと、そのたびレイアウトの確定を強いることがある——寸法が
   変わるのは resize の時だけなのだから、その時だけ測り直す。 */
let VW=vp.clientWidth, VH=vp.clientHeight;
/* 携帯はアドレスバーの出入りでも resize が飛ぶ（スクロールのたび何度も）。
   測り直しと敷き直しは落ち着いてから一度だけ（2026-07-31 夜）。 */
let rsT=0;
/* アドレスバーの出入りは「画面が変わった」ではない（2026-08-14）。
   携帯で宙を上下にひきずると、バーが引っ込む／出るたびに resize が飛び、
   そのたびに束の敷き直し（sheetLayout）と全景の引き直し（homeFit）が走っていた
   ——指を動かしているまさにその最中に、島ぜんぶを組み直す仕事が挟まる。
   幅が同じで丈だけが 160px 以内で動いたなら、それはバーの出入りなので、
   測り直すだけにして敷き直しはしない。向きが変われば幅が変わる＝ちゃんと通る。 */
const BAR_H=160;
addEventListener('resize',()=>{
  const w0=VW,h0=VH;
  VW=vp.clientWidth;VH=vp.clientHeight;
  if(VW===w0&&Math.abs(VH-h0)<=BAR_H){ apply(); return; }
  clearTimeout(rsT);
  rsT=setTimeout(()=>{
    // 画面の向きが変われば紙片の敷き詰めも変わる（縦長は一行2〜3枚）
    if(sheetId!=null&&islands.has(sheetId)){
      const isl=islands.get(sheetId);
      isl._fitK=fitK(isl);     // 帯の形は新しい画面の比で引き直す
      sheetLayout(isl);
    }
    if(islands.size)KFIT=homeFit();  // 全景＝手で引ける下限も画面の寸法から引き直す
    apply();
  },160);
});
// しるしタップ＝戸（2026-08-02 に右上へ）。開け閉めだけ。外を触れば閉じる
(function(){
  const sb=document.getElementById('logoBtn'), sl=document.getElementById('logoMenu');
  sb.addEventListener('click',()=>{
    const opening=sl.hasAttribute('hidden');
    if(opening){ sl.removeAttribute('hidden'); markMenuRoom(); }
    else sl.setAttribute('hidden','');
    sb.setAttribute('aria-expanded',opening?'true':'false');
  });
  document.addEventListener('pointerdown',e=>{
    if(!sl.hasAttribute('hidden')&&!sl.contains(e.target)&&!sb.contains(e.target)){
      sl.setAttribute('hidden','');sb.setAttribute('aria-expanded','false');
    }
  },true);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&!sl.hasAttribute('hidden')){ menuClose(); sb.focus(); }
  });
})();
function menuClose(){
  const sl=document.getElementById('logoMenu'), sb=document.getElementById('logoBtn');
  sl.setAttribute('hidden','');sb.setAttribute('aria-expanded','false');
}
/* ジャンルの名を、戸の中にも並べる（2026-07-31 夜／2026-08-02 に島渡りの主役へ）。
   宙の上の名は「そこに在るから押せる」もので、いま画面に無い島へは行けなかった。
   さらに、紙を敷いた島に降りている間（＝片を読んでいる間）は、まわりの島の字が
   退いているので、引き算をしないかぎり別の島へ移れなかった——利用者の「片を見ている
   時に別の島へ移れない」はこれ。ここに並ぶ名は、どの状態からでも押せる島の戸。
   並ぶのは名前だけ——数も、中身の気配も出さない（数えた瞬間に棚になる）。
   順は部屋の生まれた順＝宙の地形と同じ並び。押せば、その島へ滑って降りる。 */
function fillMenuRooms(){
  const box=document.getElementById('menuRooms');
  if(!box)return;
  box.textContent='';
  ROOMS.forEach(r=>{
    const isl=islands.get(r.id);
    if(!isl)return;
    const b=document.createElement('button');
    b.type='button'; b.textContent=r.name; b.dataset.id=r.id;
    /* 読んでいる面（集まってきた片・受け止めている一枚）は、島を渡る前に畳む。
       畳まずに飛ぶと、別の島の上に前の島で開いた面が残る。 */
    b.addEventListener('click',()=>{ menuClose(); gatherHide(); release(); flyTo(isl); });
    box.appendChild(b);
  });
  markMenuRoom();
}
/* いま居る島に印をつける（戸を開けるたび引き直す）。数は言わない・順も変えない */
function markMenuRoom(){
  const box=document.getElementById('menuRooms');
  if(!box)return;
  const now=sheetId!=null?String(sheetId):(focusIsl?String(focusIsl.id):'');
  box.querySelectorAll('button').forEach(b=>{
    if(b.dataset.id===now)b.setAttribute('aria-current','true');
    else b.removeAttribute('aria-current');
  });
}
const holdBox=document.getElementById('holdBox'),
      seekEl=document.getElementById('seek'),
      seekQ=document.getElementById('seekQ'),
      seekBack=document.getElementById('seekBack'),
      whisperEl=document.getElementById('whisper');

function esc(s){
  return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}
function fnv(s){let h=2166136261;s=String(s);
  for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619);}return h>>>0;}
let whisperT=0;
function whisper(html,hold){
  clearTimeout(whisperT);
  if(!html){whisperEl.classList.remove('on');return;}
  whisperEl.innerHTML=html;
  whisperEl.classList.add('on');
  if(hold)whisperT=setTimeout(()=>whisperEl.classList.remove('on'),hold);
}

/* ── 自分の控え（棚に在る・もう見ない）。mood.html と同じ集合を見る ── */
const kept=new Set(), muted=new Set();
let ROOMS=[];   // 部屋の一覧（書く柱の「どこへ」が使う。立ち上がりと開いた時に取り直す）
function fetchMine(){
  if(!LOGGED_IN)return;
  bootOr('mine','/api/sky/mine').then(d=>{
    if(!d)return;
    kept.clear();(d.kept||[]).forEach(k=>kept.add(k.src+':'+k.ref));
    if(held)renderHold();
  }).catch(()=>{});
}
/* ── きょう触れたことばの控え（宙v1 §3.2）。一望は「見た」に数えない——
      受け止めた（タップした）ことばだけを、まとめてそっと置いていく ── */
const seenQ=new Set();let seenT=0;
function noteSeen(id){
  if(!id||!LOGGED_IN)return;
  seenQ.add(id);
  if(!seenT)seenT=setTimeout(flushSeen,8000);
}
function flushSeen(){
  seenT=0;
  if(!seenQ.size)return;
  const ids=[...seenQ];seenQ.clear();
  fetch('/api/sky/seen',{method:'POST',headers:{'Content-Type':'application/json'},
    keepalive:true,body:JSON.stringify({ids:ids})}).catch(()=>{});
}
addEventListener('pagehide',flushSeen);
/* 触れた気配を灯へ（どのことばかは送らない・残らない）。5秒に一度まで */
let lastTouch=0;
function noteTouch(room){
  const now=Date.now();
  if(now-lastTouch<5000)return;
  lastTouch=now;
  fetch('/api/sky/touch',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({room:room})}).catch(()=>{});
}
/* ── 灯のあかり（宙v1 §4.2・mood.html の pollLantern の移植）──
   lit … 直近30秒に、自分以外の誰かがことばに触れた（まれで強い・7秒で引く）
   here … いま焦点の島に、自分以外の誰かが居る（ありふれて弱い・引かない）
   rooms … 気配のある島（遠景では、その島の灯が息をする）。数は聞かないし、返ってこない */
const lantern=document.getElementById('lantern');
function pollLantern(){
  /* 見ていない面の気配は、鳴らさない（2026-08-14）。灯は 30 秒ごとに問い続ける
     だけの仕掛けで、畳んだ面・裏に回った面でも同じだけ問うていた——携帯では
     そのたびに無線が起きる。裏に居るあいだは黙り、表に戻った時に一度だけ問い直す
     （既に arrivals が同じ作法を採っている）。 */
  if(document.hidden)return;
  const q=focusIsl?('?here='+focusIsl.id):'?rooms=1';
  fetch('/api/sky/lantern'+q).then(r=>r.ok?r.json():null).then(d=>{
    if(!(d&&lantern))return;
    if(d.lit&&focusIsl){
      lantern.classList.add('on');
      setTimeout(()=>lantern.classList.remove('on'),7000);
    }
    lantern.classList.toggle('near',!!(focusIsl&&d.here));
    if(!focusIsl){
      lantern.classList.remove('near');
      if(d.rooms){
        /* 息をする灯は、多くて6島まで（2026-08-03 遠景のカクツキ）。呼吸は
           島の半径1.5倍の放射グラデーションを 6.5 秒かけて薄め続ける仕事で、
           息をする島の数だけ合成の面が常駐する。数は元々言わない仕様なので、
           六つでも二十でも「気配がある」以上のことは何も変わらない。 */
        const on=new Set(d.rooms.slice(0,6));
        islands.forEach((isl,id)=>isl.el.classList.toggle('lit',on.has(id)));
      }
    }
  }).catch(()=>{});
}
setTimeout(pollLantern,4000);
setInterval(pollLantern,30000);
document.addEventListener('visibilitychange',()=>{ if(!document.hidden)pollLantern(); });

/* ── 見え方の定数 ──
   濃さ＝沈降（1=いま昇ったばかり）。3年で1/4に沈むが、ゼロにはしない——
   沈んだことばも、近づいてじっと見れば読める。 */
const alphaOf=s=>0.30+0.62*s;   // 沈降＝薄さ。ぼかし（blurOf）は 2026-07-31 に廃止
function tintOf(c){
  const m=/hsl\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)%,\s*(\d+(?:\.\d+)?)%\)/.exec(c||'');
  if(!m)return null;
  const h=+m[1],s=+m[2],l=Math.max(+m[3],52);   // 宙の闇でも読める明るさまで持ち上げる
  return {text:'hsl('+h+','+s+'%,'+l+'%)',glow:'hsla('+h+','+s+'%,'+l+'%,.35)'};
}
/* 紙片の紙の色（2026-07-31）。選ばれた色は手染めの紙に淡く染み、
   無彩（サーバの _AIR_GRAY_S=12 と同じ線）は生成り〜灰白の三種から
   本文のハッシュで一つ——同じことばは、いつ来ても同じ紙。漂流物は灰の紙。 */
function paperOf(w){
  const m=/hsl\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)%/.exec(w.color||'');
  if(m&&+m[2]>=12)return 'hsl('+(+m[1])+',24%,90%)';
  if(w.pd)return '#E0DDD6';
  return ['#EFEBE1','#E9E5DC','#EFE7D9'][fnv(String(w.id||w.poem))%3];
}
/* ── 紙片の寸法規律（2026-08-03「ひとつづきの宙」1b／2026-08-07 に紙を広げた）──
   これまで紙片は自然寸（中身の丈がそのまま紙の丈）だった——高さがばらばらなので
   段の見積りと実物がずれ、下端で切れ、詰まって見えた。寸法を**字数から純関数で**
   決める：幅は列数、高さは1列あたりの字数。DOMを測らないので、島を一瞬紙片の姿に
   して測る細工（fitK）も要らなくなる。
   字送りの根拠：font 15px / line-height 2 ＝ 1列30px、letter-spacing .12em ＝
   1字16.8px。padding は 24px 20px（縦48・横40）で box-sizing は全域 border-box。
   丈の値に少し裾を余らせてあるのは、字送りの丸めで最後の一字が折れないため。

   ── 2026-08-07 Kosei「影があって途中から読めない」──────────────────
   旧実装は3列×15字＝45字で打ち止めで、それを超える紙は3列に固定して行送り方向へ
   フェードさせていた。ことばは80字まで書けるので、**半分近くが途中で切れていた**
   ——切れた紙にだけ薄れる影がかかり、それが「読めない」の正体。
   紙のほうを広げて、切れる紙そのものを無くす：列は4本まで（書く面の4罫と同じ＝
   80字はちょうど4列に収まる）、丈は20字まで。フェードは全面的に廃止した。
   3列に収まることば（45字まで）の姿は**一字も変えていない**——先に旧来の3値で
   探し、そこで収まらなかったものだけを広い側へ回す。
   有料会員の120字（収益モデル v1）を開ける日は、SNAP_W に列を足すだけで届く。 */
const SNAP_W=[70,100,130,160,190,220];  // 1〜6列（列30px＋padding40）
const SNAP_H=[152,236,304,388];         // 1列あたり 6字・11字・15字・20字（＋padding48）
const SNAP_CAP=[6,11,15,20];
const SNAP_COLS=4;                      // 紙の列は4本まで（書く面の4罫＝80字）
function _snapFit(lines,maxC){
  for(let hi=0;hi<SNAP_H.length;hi++){
    let c=0;
    lines.forEach(L=>{c+=Math.max(1,Math.ceil(L/SNAP_CAP[hi]));});
    if(c<=maxC)return {w:SNAP_W[c-1],h:SNAP_H[hi]};
  }
  return null;
}
function sizeOf(w){
  const lines=String(w.poem||'').split('\n').map(s=>s.length);
  const n=lines.reduce((a,b)=>a+b,0);
  // 望みの列数は総字数から（1列＝11字まで）。改行が列を強いる時はそのぶんまで許す
  const lim=Math.min(3,Math.max(Math.ceil(n/SNAP_CAP[1])||1,lines.length));
  // ① 旧来の3値（45字まで）。ここで決まる紙は 8/3 の姿のまま
  const near=_snapFit(lines,lim);
  if(near)return near;
  // ② 収まらなかったことばは、列を足して全部を見せる（4列＝80字）
  const wide=_snapFit(lines,SNAP_COLS);
  if(wide)return wide;
  // ③ 改行だらけで列がそれ以上要る紙（漂流物にだけ起きうる）。列だけ伸ばして切らない
  return _snapFit(lines,SNAP_W.length)||{w:SNAP_W[SNAP_W.length-1],h:SNAP_H[SNAP_H.length-1]};
}
/* ── 読む面の紙の寸法（2026-08-07 Kosei「紙片内のバランスが悪すぎる。二列にしてもいい」）──
   島の紙片（sizeOf）と同じ考えを、読む面（並べて読む・棚）へも降ろす：
   **寸法はことばの長さから純関数で決める**。狙いは二列。

   1列に入れる字数を選び、**二列に折れるいちばん浅い丈**を採る。二列で収まらない
   長いことばだけ、丈は最大のまま列を増やす。改行は書き手の形なので、行ごとに列を
   数える（島と同じ）。列の下限は二——一列の紙は、足（棚にとっておく）より細くなる。
   字送りの根拠：本文 font 15px / line-height 2.5 ＝ 1列 37.5px、
   letter-spacing .06em ＝ 縦1字 15.9px（実測 15.9017）。

   ── 2026-08-07 夜・Kosei「スクショの文章のバランスが悪い」──────────────
   丈を四段（7・11・15・19）から選んでいた。二列に折れる**いちばん浅い**段を採る
   ので、9字のことばは 7字の段に落ちる——一列目が7字、二列目が2字。段が粗いほど、
   最後の列に一字二字だけが残る。実際にそうなっていた（「曇りの朝に流す一曲」の
   二列目が「曲」の一字だけ）。
   段はやめて、**均す丈**を字数から直に出す：二列に折れる最小の丈＝ceil(字数/2)。
   一列目と二列目は必ず同じか一字違いになり、孤り字は原理的に出ない。丈の散らばりが
   増える心配は要らない——上限（19字）は据え置きなので、いちばん高い紙の丈は変わらず、
   低いほうが**ことばの短さのぶんだけ**低くなるだけ。下限は4字：これより浅い紙は
   足（棚にとっておく）の一段より低くなって、字より道具が大きい紙になる。 */
const RS_COLW=37.5, RS_CHH=15.9;
const RS_MIN=4, RS_MAX=19;
function rsSize(poem){
  const lines=String(poem||'').split('\n').map(s=>s.length);
  const cols=cap=>lines.reduce((a,L)=>a+Math.max(1,Math.ceil(L/cap)),0);
  for(let cap=RS_MIN;cap<=RS_MAX;cap++){ if(cols(cap)<=2)return {cap:cap,cols:2}; }
  /* 二列に折れないことば。ここで丈をいちばん深いものに決め打つと、行を三つに
     分けて書かれた24字の紙が「三列 × 19字」の背高の紙になり、また八割が空く。
     列がいちばん少なくなる丈のうち、**いちばん浅いもの**を採る（＝ここでも均す）。 */
  const n=cols(RS_MAX);
  for(let cap=RS_MIN;cap<=RS_MAX;cap++){
    if(cols(cap)<=n)return {cap:cap,cols:Math.max(2,Math.min(6,n))};
  }
  return {cap:RS_MAX,cols:Math.max(2,Math.min(6,n))};
}
/* 紙に寸法を書き留める。題があれば、その一列ぶん（＋行間）を幅に足す。

   漂流物の三列の下限は降ろした（2026-08-07 夜）。置いた理由は「奥付が横一行だから、
   二列の紙では五行に折れて紙が塔になる」だったが、いまは紙の丈を中身が決めるので
   塔にはならず、しかも三列に広げても奥付は**12枚が12枚とも切れていた**（実測）
   ＝ 広げたぶんの一列は、何の役にも立たない空白だった。奥付は折る側へ回した。

   丈に遊び（+8px）を足さない（2026-08-07 夜）。版面は --vh ちょうどで（下の CSS の
   .rsheet-v）、縦1字は 15.9px——ceil で足りるのは 1px 未満なので、**一字も余分に
   入らない**。ここに 8px を足していたことが、均した丈を一字ぶん崩していた
   （版面はさらに足の負のマージンぶん 8px 広く、合わせて 16px ＝ ちょうど一字）。 */
function rsFit(li,w){
  const sz=rsSize(w&&w.poem);
  li.style.setProperty('--rcol',String(sz.cols));
  li.style.setProperty('--vh',Math.ceil(sz.cap*RS_CHH)+'px');
  li.style.setProperty('--rtl',(w&&w.title)?'34px':'0px');
}
/* ── 読む面の紙も、切らずに伸ばす（2026-08-07 Kosei「影があって途中から読めない」）──
   「並べて読む」と棚の紙は丈が固定（--vh）で、入り切らない列は紙の外へ出ていた。
   そこに掛けていた左フェードを廃止した以上、**丈のほうを足す**しかない。
   縦書きは丈が「1列に何字入るか」を決めるので、列が溢れる＝丈が足りない、という
   ことでしかない。要る丈は、はみ出した幅の比でほぼ一発で出る（列数 ∝ 1/丈）。
   測る→書く を二巡まで。丸めと題のぶんの遊びを足しておく。 */
function fitTall(cells,sel){
  const MAXV=1600;   // 際限なく伸ばさない（改行だらけの漂流物の保険）
  for(let pass=0;pass<2&&cells.length;pass++){
    const need=[];
    cells.forEach(li=>{                       // ── 測る（書かない）
      const v=li&&li.querySelector&&li.querySelector(sel);
      if(!v||v.clientWidth<1)return;
      const r=v.scrollWidth/v.clientWidth;
      if(r<=1.01)return;
      const cur=parseFloat(getComputedStyle(li).getPropertyValue('--vh'))||v.clientHeight;
      need.push([li,Math.min(MAXV,Math.ceil(cur+v.clientHeight*(r-1)+16))]);
    });
    if(!need.length)return;
    need.forEach(([li,h])=>{ li.style.setProperty('--vh',h+'px'); });   // ── 書く
    cells=need.map(x=>x[0]);
  }
}
/* 寸法を紙に書き留める。効くのは島に降りた姿（.isl.sheet .w が var を読む）だけで、
   散らばりの姿は今までどおり自然寸。横書き（.h）は数えられないので測る作法のまま。 */
function snapSize(el){
  const w=el._w;
  if(!w||!w.vertical)return;
  const sz=sizeOf(w);
  el.style.setProperty('--sw',sz.w+'px');
  el.style.setProperty('--sh',sz.h+'px');
  el._pw=sz.w; el._ph=sz.h;          // 控え＝sheetPlan と間引きがそのまま読む
}

/* ── 島とことばを置く ──────────────────────────────
   座標はサーバの決定論（単位円）。島の位置もことばの押し出しも、順序を
   固定した同じ手順で解くので、同じ宙は誰の画面でもほぼ同じ地形になる。 */
let islands=new Map();   // room_id -> {el,wordsEl,cx,cy,R,words:[]}
function islandR(n){ return 150+62*Math.sqrt(n); }
const ISL_GAP=150;       // 島と島のあいだ（岸から岸まで）。輪の半径はここと島の大きさから出る
/* 島の席（2026-08-05・Kosei指示で 7/27 の同心円へ戻した）。
   8/2 の「意味の地図」と 8/3 の「手つなぎの岸」を上書きする——地図の遠近より、
   「あの部屋はあそこ」と体で覚えられる輪の席を採る（サーバの mx/my は読まない）。
   歩き方は旧宙（mood.html）の roomSeat と同じ：12時から時計回り、輪は 6,12,18…席、
   奇数の輪は半コマずらす。席は生まれた順（payload の並び＝元からある部屋→作られた順）。
   半径だけは旧の 105+70×ring では足りない——名前だけの席だったところに、いまは
   ことばを抱えた島が座る。だから輪ごとに「隣の席と重ならない」「内の輪とも
   重ならない」の二つの下限から半径を出す。 */
/* ただし、席を決める時の島の大きさには頭を押さえる（SEAT_CAP・2026-08-05）。
   ことばの多い島は islandR で半径1200を超える。その実寸で輪を開くと最外の輪は
   5000を超え、全景がどこまでも広がる——名の逆スケールが上限に噛んで字が縮み、
   7/27 の一覧（名と名の間合いがひと目に入る）から遠ざかる。
   遠景ではもう字を出していないのだから、見えない島の円で輪を開く理由はない。
   輪が測るべきは**名と灯の間合い**だけ。260 で止めると輪は実測 651/1334/2066
   ＝参照スクショ（7/27）の詰まり方にいちばん近い。
   散らばりの字は実半径のまま（isl.R は触らない）ので、中間の倍率では隣の島の
   字と混ざるが、そこは既に一島が画面の大半を占める寄りで、緩みは目に立たない。 */
const SEAT_CAP=260;
function placeIslands(rooms,counts){
  const seats=rooms.map((r,i)=>{
    let n=i,ring=0;
    while(n>=6*(ring+1)){n-=6*(ring+1);ring++;}
    const R=islandR(counts.get(r.id)||0);
    return {room:r,R:R,seatR:Math.min(SEAT_CAP,R),ring:ring,slot:n,slots:6*(ring+1)};
  });
  /* 埋まらなかった輪は、居る数で一周を割り直す（2026-07-28 の 8261b73 と同じ判断）。
     席を詰めたままだと、いちばん外の輪は「一箇所に固まった弧」になる——輪の定員は
     18でも実際に座るのは7つ、という時に、その7つが隣り合って名も灯も重なる。
     輪は席の数え方であって、並び方の縛りではない。 */
  const cnt=[];
  seats.forEach(s=>{cnt[s.ring]=(cnt[s.ring]||0)+1;});
  seats.forEach(s=>{s.slots=cnt[s.ring];});
  const maxR=[];
  seats.forEach(s=>{maxR[s.ring]=Math.max(maxR[s.ring]||0,s.seatR);});
  const radii=[];
  maxR.forEach((m,ring)=>{
    // 隣席との弦 2r·sin(π/席数) が、いちばん大きい二島＋間合いぶん開くように
    const byChord=cnt[ring]>1?(2*m+ISL_GAP)/(2*Math.sin(Math.PI/cnt[ring])):0;
    const byRing=ring?radii[ring-1]+maxR[ring-1]+m+ISL_GAP:0;
    radii[ring]=Math.max(byChord,byRing);
  });
  return seats.map(s=>{
    const off=(s.ring%2)?Math.PI/s.slots:0;
    const t=off+s.slot*(2*Math.PI/s.slots);
    /* 席の揺らぎ（旧 SEAT_JITTER=8/105＝7.6% を 3% へ）。輪が定規で引いた円に
       見えないための飾りだが、7.6% だと外の輪で ±390 も径が動き、内の輪の名と
       縦に重なった（実測：狭い画面で3組）。3% なら輪の間合いに負けない。 */
    const j=1+(((fnv('seat:'+s.room.id)%2001)/2000)*2-1)*0.03;
    const r=radii[s.ring]*j;
    return {x:Math.round(Math.sin(t)*r),y:Math.round(-Math.cos(t)*r),R:s.R,room:s.room};
  });
}
function build(rooms,words){
  const counts=new Map();
  words.forEach(w=>counts.set(w.room,(counts.get(w.room)||0)+1));
  const seats=placeIslands(rooms.filter(r=>counts.get(r.id)||!r.archived),counts);
  /* 立ち上がりは、生きている版面に一枚ずつ足さない（2026-08-03 カクツキ）。
     島と数百のことばを appendChild で直に足すと、足すたびに宙の版面が汚れる。
     先に手元（DocumentFragment）で全部組んでから、一度だけ差す。 */
  const wfrag=new Map();
  const ifrag=document.createDocumentFragment();
  seats.forEach(s=>{
    const isl=document.createElement('div');
    isl.className='isl';
    isl.style.left=s.x+'px'; isl.style.top=s.y+'px';
    const wl=document.createElement('div'); wl.className='isl-w'; isl.appendChild(wl);
    const nm=document.createElement('div'); nm.className='isl-nm'; nm.textContent=s.room.name;
    /* 部屋の灯（2026-08-05・旧宙の部屋一覧の「炎」へ戻した。8/3 の一枚畳みを上書き）。
       島の半径いっぱいの放射ではなく、名のまわりに封の色の粒が最大3つ。
       名の中に置くので、倍率に抗う逆スケール（scaleNames）も名と同じ一枚の
       transform に乗る＝寄り引きでの仕事は増えない。席は旧 FIRE_SEATS＝
       左上・右中・中下（%）＋部屋idのハッシュで±6%。
       色は hsl(…) と #RRGGBB の二通りで来る（本番の本の一節は hex——8/3 に
       ここを取りこぼして真っ白な玉が出た）。どちらもそのまま CSS の色として通す。 */
    (s.room.lights||[]).slice(0,3).forEach((c,k)=>{
      const cs=String(c||'').trim();
      if(!/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(cs)&&cs.indexOf('hsl(')!==0)return;
      const seat=[{x:4,y:26},{x:96,y:56},{x:52,y:88}][k];
      const h=fnv('fire:'+s.room.id+':'+k);
      const f=document.createElement('i');
      f.className='fire';
      f.style.setProperty('--c',cs);
      // >>> であって >> ではない：fnv は 2^31 を超える。符号付きで畳むと剰余が
      // 負に出て、縦の揺らぎだけ -18〜+6% に偏る（上へ吊り上がる）
      f.style.setProperty('--fx',(seat.x+(h%13)-6)+'%');
      f.style.setProperty('--fy',(seat.y+((h>>>5)%13)-6)+'%');
      nm.appendChild(f);
    });
    isl.appendChild(nm);
    ifrag.appendChild(isl);
    islands.set(s.room.id,{el:isl,wordsEl:wl,nm:nm,cx:s.x,cy:s.y,R:s.R,words:[],room:s.room});
    wfrag.set(s.room.id,document.createDocumentFragment());
  });
  words.forEach(w=>{
    const isl=islands.get(w.room); if(!isl)return;
    mkWord(isl,w,wfrag.get(w.room));
  });
  islands.forEach((isl,id)=>{ const f=wfrag.get(id); if(f)isl.wordsEl.appendChild(f); });
  world.appendChild(ifrag);
  // まず家の座標に置く（押し出しは描画後に一度だけ・relax()）
  islands.forEach(isl=>isl.words.forEach(el=>{
    el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  }));
  /* 寸法だけは、ここで一度まとめて測っておく（2026-08-14）。押し出し（relax）は
     一フレーム後でよいが、間引きの物差しは**一枚目のフレームから**要る。
     置いた直後の一括読み＝強制的な組み直しは一度きり（実測 15ms）。 */
  measureScatter();
  /* 順点灯（2026-08-05・旧一覧の 120+i*70ms を戻した）。名と炎は一斉にポンと
     出ない——輪の内から外へ、順に灯る。inline の 0 を外すだけ＝あとは CSS の
     遷移（.8s）が .82 まで運ぶ。炎は名の中に居るので、親と一緒に現れる。
     動きを望まない人には、ただ在るだけ（REDUCED では .8s のフェードも消えるので、
     順に外すと「一つずつポンと出る」＝一斉より動きの多い面になってしまう）。 */
  if(!REDUCED)seats.forEach((s,i)=>{
    const isl=islands.get(s.room.id); if(!isl)return;
    isl.nm.style.opacity='0';
    setTimeout(()=>{ isl.nm.style.opacity=''; },120+i*70);
  });
}
/* 一枚のことばを、島の上に建てる。build() から切り出した（2026-07-31）——
   島に降りるたびに岸を組み直すので、建てるのは立ち上がりの一度きりではなくなった。 */
function mkWord(isl,w,frag){
  const el=document.createElement('div');
  el.className='w'+(w.vertical?'':' h')+(w.pd?' pd':'');
  el.textContent=w.poem;
  el.style.setProperty('--a',alphaOf(w.sink).toFixed(3));
  const c=tintOf(w.color);
  if(c){ el.style.color=c.text; el.style.textShadow='0 0 16px '+c.glow; }
  el.style.setProperty('--pp',paperOf(w));   // 島に降りた時の、紙の色
  el._w=w;
  snapSize(el);                              // 島に降りた時の、紙の寸法（3値スナップ）
  el.setAttribute('role','button'); el.setAttribute('tabindex','0');
  el.setAttribute('aria-label',(w.pd
    ? '流れ着いたことば：'+w.poem+'（'+w.author+'『'+w.work+'』より）'
    : 'tayori-たより- のことば：'+w.poem)
    +'。ひらくと、棚にとっておく ができます。');
  // 家の座標：色相が方位、あとはハッシュ。**島の中心からの相対**で持つ——
  // .w の包含ブロックは .isl（もう島の位置に居る）なので、世界座標で書くと
  // 島の中心が二重に足されて、ことばが島の外へ筋になって流れ出す（初版で実際に起きた）
  let ux=w.x,uy=w.y;
  /* またぐことばは、遠景でも相手の島の側へ寄る（2026-08-02）。降りた島の中で
     やっていること（中心＝固有／縁＝またぐ）を、島の外からも見えるようにする
     ＝島と島は、あいだに掛かることばで繋がって見える。
     色相の方位は捨てない：寄せるのは「またいでいる度合い」の分だけで、
     ほとんどのことば（実測 6割）は今までどおり色の方角に居る。 */
  const cr=w.cr!=null?w.cr:0;
  if(cr<0&&w.pr!=null){
    const d=partnerDir(isl,el);
    if(d!=null){
      const t=Math.min(1,-cr/0.12)*0.8;
      const a0=Math.atan2(uy,ux), r0=Math.min(1,Math.hypot(ux,uy));
      let dd=d-a0;
      while(dd>Math.PI)dd-=2*Math.PI;
      while(dd<-Math.PI)dd+=2*Math.PI;
      const a=a0+dd*t, r=r0+(1-r0)*t*0.8;   // 方角は相手へ、隔たりは縁へ
      ux=Math.cos(a)*r; uy=Math.sin(a)*r;
    }
  }
  el._hx=ux*(isl.R-70);
  el._hy=uy*(isl.R-70);
  /* 種の席は別に控える（2026-08-14）。押し出し（relax）は _hx/_hy を**上書き**
     するので、二度目からは「もう逃がした席」を種にして解くことになり、そのたび
     地形が遠ざかっていく（実測 527枚が動き、いちばん遠いもので634px）。
     一度しか呼ばないうちは表に出なかったが、書体が着いた時に組み直すには
     何度呼んでも同じ答えが要る——決定論の約束は、回数にも掛かる。 */
  el._ox=el._hx; el._oy=el._hy;
  el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  (frag||isl.wordsEl).appendChild(el);
  isl.words.push(el);
  return el;
}
/* 島に降りるたび、その島の岸を組み直す（2026-07-31・Kosei指示）。
   20万片あるので、同じ島でも二度と同じ顔ぶれにはならない。
   人のことばには触らない——組み替わるのは流れ着いたものだけ。 */
let shoreBusy=null;
function reshore(isl){
  if(!isl||shoreBusy===isl)return;
  shoreBusy=isl;
  fetch('/api/sky/shore?room='+encodeURIComponent(isl.room.id))
    .then(r=>r.ok?r.json():null).then(d=>{
      shoreBusy=null;
      if(!d||!d.words||!d.words.length)return;
      if(!isl.sheeted)return;            // もう出てしまった島の岸は組み替えない
      // 受け止めている一枚が消えると、添えた行いの札が宙に浮く。先に手を放す。
      if(held&&held._w&&held._w.pd&&isl.words.indexOf(held)>=0)release();
      isl.words=isl.words.filter(el=>{
        if(!el._w||!el._w.pd)return true;
        el.remove();
        return false;
      });
      d.words.forEach(w=>mkWord(isl,w));
      /* 岸が着くと束は一回り大きくなる（実測 46片→66片）。降りる時に測った倍率は
         その手前の寸法なので、そのままだと紙が画面から溢れる。**滑って来た時だけ**
         倍率を引き直す（自分でつまんで寄せた人の手は取らない）。
         読んでいる最中（レンズが開いている）なら、カメラには触れない——岸が着いた
         というだけの理由で、読んでいる紙を中央から引き剥がしてはいけない。
         比べるのは道中の過渡の K ではなく**落ち着いた先**（glideK）。掃引が的を
         通過する数十msに岸が返ると、乗り換えを取りこぼして帯だけ広く組まれる。 */
      const reading=lensEl&&lensEl._w.room===isl.room.id;
      /* カメラを動かしてよいのは、**この島がいまの行き先である時だけ**（2026-08-05 夜）。
         岸の fetch は数百ms〜2秒かかる——その間に人が別の島や全景へ向かっていたら、
         返ってきた岸は「もう誰も見ていない島」のものだ。それで倍率を引き直すと、
         押した先へ着けずに元の島へ引き戻される。全景へ引き返している最中（行き先は
         「どの島でもない」＝sheetLock は null）も同じ。飛んでいない時は、いま居る
         島の話なので今までどおり引き直してよい。 */
      const mine=(!flightT||sheetLock===isl.room.id);
      if(isl._flown){
        isl._flown=false;
        const k=fitK(isl);
        isl._fitK=k;       // 岸が着いて束が育った——帯の形の物差しも引き直す
        // 詰め直しも glide で（滑走の途中に岸が着いたら、そのまま新しい的へ乗り換える）
        if(mine&&!reading&&Math.abs(k-glideK())>0.02)glide(k,-isl.cx*k,-isl.cy*k,700,true);
      }
      sheetLayout(isl,true);             // 新しい紙も、いまの束に混ぜて並べ直す
      // 焦点レンズで読んでいる最中なら、動いた席へ照準を引き直す（紙を見失わない）
      if(reading&&typeof lensAim==='function')lensAim(lensEl);
      apply();
    }).catch(()=>{shoreBusy=null;});
}
/* 重なりの押し出し。新しいことば（表層）から順に席を取り、あとから来た沈んだ
   ことばが空いた席へ逃げる。逃げ方は一直線ではなく家のまわりの渦巻き——
   直線に押すと、同じ向きに並んだことばが島の外まで筋になって流れ出す（初版で実際に
   起きた）。渦なら家の近くの空席に収まり、島は塊のまま。順序固定＝決定論。 */
/* 散らばりの姿の寸法をまとめて測る（2026-08-14 に relax から切り出した）。
   切り出した理由：この寸法（_sw/_sh）は押し出しのためだけのものではなく、
   **画面外の間引き（cullWords）が読む唯一の物差し**でもある。relax は build の
   一フレーム後（rAF）に走るので、それまでのあいだ _sw/_sh は undefined ——
   間引きは「寸法をまだ測っていない紙は間引かない」の枝に落ちて、一枚も畳めない。
   立ち上がりの最初の数フレームだけ、画面の外の数百枚（実測 804枚中 474枚）を
   まるごと塗っていた。測るのは 15ms、押し出しを解いて書くのが残り——重いほうは
   rAF に残したまま、軽い「測る」だけを build の側へ寄せれば、間引きは一枚目の
   フレームから効く。 */
function measureScatter(){
  const measured=new Map();
  // 再生中の一枚は寸法を留めてある（playType の pin）。測る前に、まず外す
  islands.forEach(isl=>isl.words.forEach(el=>{
    if(el._pin){el._pin=0;el.style.width='';el.style.height='';}
  }));
  islands.forEach(isl=>isl.words.forEach(el=>{
    const w=el.offsetWidth,h=el.offsetHeight;
    el._sw=w; el._sh=h;                // 散らばりの姿の寸法（画面外の間引きに使う）
    measured.set(el,[w,h]);
  }));
  return measured;
}
function relax(){
  /* 測るのと書くのを混ぜない（2026-07-31 夜・立ち上がりの固まりの真因）。
     旧実装は「一枚測って、一枚置く」を数百回くり返していた。置いた瞬間に版面は
     汚れるので、次の一枚を測るたびブラウザは版面を組み直す——数百回の強制レイアウト
     ＝立ち上がりで宙が数秒沈黙する。先に全部測り、計算し、最後にまとめて置く。 */
  const measured=measureScatter();
  const writes=[];
  islands.forEach(isl=>{
    const order=[...isl.words].sort((a,b)=>(b._w.sink-a._w.sink)||(a._w.id<b._w.id?-1:1));
    /* 席の当たり判定を、置いた全部との突き合わせから**近所だけ**へ（2026-08-14）。
       旧実装は一枚置くごとに「もう置いた全部」を順に見ていた。渦は最大160周
       まわるので、一周ごとにその走査が走る——実測219,245回の突き合わせ。
       島ごとに粗い升目を敷いて、当たりうる升（自分と八方）だけを見る。
       升の一辺は「いちばん大きな紙＋遊び」なので、隣の升より外の紙は
       **どう置いても届かない**＝落ちる相手はいない。判定の式も、置く順も
       元のまま＝出来上がる地形は一枚も変わらない（配置は決定論の約束）。 */
    let maxW=0,maxH=0;
    order.forEach(el=>{ const m=measured.get(el); if(m){ if(m[0]>maxW)maxW=m[0]; if(m[1]>maxH)maxH=m[1]; } });
    const cell=Math.max(maxW,maxH)+36||1;
    const grid=new Map();
    const key=(cx,cy)=>cx+','+cy;
    const put=d=>{
      const k=key(Math.floor(d.x/cell),Math.floor(d.y/cell));
      const b=grid.get(k); if(b)b.push(d); else grid.set(k,[d]);
    };
    order.forEach(el=>{
      const m=measured.get(el)||[0,0], w=m[0], h=m[1];
      // 解くのは、いつも種の席から（_ox/_oy）。前に逃がした席からではない
      const hx=el._ox!=null?el._ox:el._hx, hy=el._oy!=null?el._oy:el._hy;
      let x=hx,y=hy;
      const base=Math.atan2(hy,hx);
      const hits=()=>{
        const cx=Math.floor(x/cell), cy=Math.floor(y/cell);
        for(let gx=cx-1;gx<=cx+1;gx++)for(let gy=cy-1;gy<=cy+1;gy++){
          const b=grid.get(key(gx,gy)); if(!b)continue;
          for(let i=0;i<b.length;i++){ const d=b[i];
            if(Math.abs(x-d.x)<(w+d.w)/2+18&&Math.abs(y-d.y)<(h+d.h)/2+18)return true; }
        }
        return false;
      };
      let t=0;
      while(hits()&&t<160){
        t++;
        const rr=34+t*7.5;
        const aa=base+t*2.39996;
        // 縦書きの塊は縦に長いので、横へ広めに・縦へ狭めに逃がす（島を楕円に保つ）
        x=hx+Math.cos(aa)*rr*1.15;
        y=hy+Math.sin(aa)*rr*0.6;
      }
      /* 逃げてよい。ただし**隣の島には入らない**（2026-08-02）。島の席を意味の
         地図に置いて間合いを詰めたので、押し出された一枚がそのまま隣の島へ紛れる
         ようになった（実測31片・いちばん深いもので332）。自分の島の半径で頭を
         押さえると、こんどは中で団子になる（人の多い島は、そもそも入り切らない）。
         だから止めるのは「他の島の中」だけ——あいだの海へは、いくらでも出てよい。 */
      islands.forEach(o=>{
        if(o===isl)return;
        const dx=isl.cx+x-o.cx, dy=isl.cy+y-o.cy, d=Math.hypot(dx,dy);
        if(d<o.R&&d>1e-3){ const k=(o.R-d)/d; x+=dx*k; y+=dy*k; }
      });
      el._hx=x; el._hy=y;
      writes.push([el,x,y]);
      put({x:x,y:y,w:w,h:h});
    });
  });
  writes.forEach(a=>{a[0].style.left=a[1]+'px';a[0].style.top=a[2]+'px';});
}

/* ── 紙片の島（2026-07-31 敷き詰め → 2026-08-02 円）──────────────
   島に降りる（＝焦点の島になる）と、ことばたちは散らばりから紙片の束へ並び替わる。
   位置は transform で動かす＝家（_hx/_hy）は保ったまま。離れれば静かに帰る。
   寸法は紙片の姿（.sheet のpadding込み）で測るので、classを立ててから読む。

   束の姿は**水平の帯**（2026-08-03「ひとつづきの宙」1a。8/2 の円は降ろした）。
   楕円は弦が段ごとに違うので「どこまで行けば次の段か」が姿から読めず、
   下端の見積りもずれた。全段同じ幅・同じ丈の帯なら、横に動けば読み進む・
   縦は段の乗り換え、が体で覚えられる。短冊は段の中心線に上下センター＝
   丈の違いが「切れ」ではなく「揺れ」として読める。

   並びは「新しさ」ではなく「その部屋らしさ」（2026-08-02・Kosei確定）。
     中心 … この部屋にしかないことば（cr が大きい）
     縁　 … 他の部屋にも掛かることば。掛かっている相手の島がある側へ寄る
     外側 … 流れ着いたもの（部屋を持たない＝岸）
   これで「新しいものが右上・右端をそろえる」（7/31）は降ろした。読み始めの一点を
   決める代わりに、中心から外へ読む面になる。 */
const SHEET_GAP=26;          // 紙と紙のあいだ
const SHEET_PAD=150;         // 頭の帯と足元に譲る丈
const RUN_SLACK=1.06;        // 段の詰め残り（入らなかった一枚ぶん）の遊び
let sheetId=null, sheetWant=null, sheetT=0;
function posOf(isl,el){
  return (isl&&isl.sheeted&&el._gx!=null)
    ?'translate('+(el._gx-el._hx)+'px,'+(el._gy-el._hy)+'px)':'';
}
/* 「この部屋のもの」度。大きいほど中心へ。流れ着いたものは、どの部屋にも属さない
   ＝いちばん外（-2 は、人のことばのいちばんまたいでいる片より必ず外側）。 */
function crossOf(el){ return el._w.pd?-2:(el._w.cr!=null?el._w.cr:0); }
/* 掛かっている相手の島の方角（世界座標の角）。相手が居なければ null */
function partnerDir(isl,el){
  const t=el._w.pr!=null?islands.get(el._w.pr):null;
  return (!t||t===isl)?null:Math.atan2(t.cy-isl.cy,t.cx-isl.cx);
}
/* 束を組む（席は決めるが、まだ動かさない）。倍率もこの寸法から決めるので、
   「どう並ぶか」と「どこまで寄るか」は同じ一つの計算から出る。

   帯の選び方：段数 n を決めれば帯の幅（run/n）と総丈（n×段高）が決まる。
   その姿がいまの画面の比にいちばん近い n を採るだけ。 */
/* cached＝控えてある紙片の寸法（_pw/_ph）で組む（2026-08-03 カクツキ）。
   紙片の寸法は倍率にも画面にも依らない（max-width:18em・max-height:330px の固定）
   ので、一度測れば次からは測り直さなくてよい。これが効くのは fitK ——降りる先の
   倍率を決めるために、島を一瞬だけ紙片の姿にして数百枚を測っていた。class を立てて
   読んで戻す＝島を渡るたび、版面の強制的な組み直しが二度走っていた。 */
/* 2026-08-14：cached は「全部か、ゼロか」だった。控えを持たないのは横書きの紙だけ
   （snapSize は字数から数えるので縦書きにしか書けない）なのに、島に一枚でも横書きが
   混じると**その島の全部**を測り直していた——控えのある数百枚まで巻き添えにして。
   だから控えの有無は島ではなく一枚ずつで見る：足りない紙だけ測り、あとは控えを読む。
   控えと実測が食い違う心配は要らない（box-sizing:border-box ＋ .isl.sheet .w が
   width/height に --sw/--sh をそのまま敷くので、測っても同じ値が返る＝実測で確認済み）。 */
function coldSizes(isl){
  return isl.words.filter(el=>
    !el.classList.contains('mutedout')&&!(el._pw&&el._ph));
}
function sheetPlan(isl,maxW,cached){
  const live=isl.words.filter(el=>!el.classList.contains('mutedout'));
  if(!live.length)return null;
  let items;
  if(cached){
    const cold=live.filter(el=>!(el._pw&&el._ph));
    if(!cold.length){
      items=live.map(el=>({el:el,w:el._pw,h:el._ph}));
    }else{
      // 測るのと書くのを混ぜない：留めを外す（書く）→ まとめて測る（読む）
      cold.forEach(el=>{ if(el._pin){el._pin=0;el.style.width='';el.style.height='';} });
      const dim=new Map();
      cold.forEach(el=>dim.set(el,[el.offsetWidth,el.offsetHeight]));
      items=live.map(el=>{
        const d=dim.get(el);
        return d?{el:el,w:d[0],h:d[1]}:{el:el,w:el._pw,h:el._ph};
      });
    }
  }else{
    // 再生中の一枚は寸法を留めてある（playType の pin）。測る前に、まず外す
    live.forEach(el=>{ if(el._pin){el._pin=0;el.style.width='';el.style.height='';} });
    items=live.map(el=>({el:el,w:el.offsetWidth,h:el.offsetHeight}));
  }
  items.forEach(it=>{it.el._pw=it.w;it.el._ph=it.h;});   // 紙片の姿の寸法（間引きに使う）
  let tall=0,run=0;
  items.forEach(it=>{tall=Math.max(tall,it.h);run+=it.w+SHEET_GAP;});
  const rowH=tall+SHEET_GAP;
  run*=RUN_SLACK;
  const want=VW/Math.max(VH-SHEET_PAD,200);
  let N=1,BW=run,best=1e9;
  for(let n=1;n<=24;n++){
    const bw=run/n;
    /* 画面より広い束は作らない（maxW）。中身が多くて一画面に入りきらない島では、
       溢れる向きを縦にする——横へ溢れると、縦書きの読み順（右→左）そのものが
       画面の外へ出る。段を増やせば帯は細くなるので、幅の収まる形の中から
       いちばん画面の比に近いものを選ぶ。どれも収まらなければ、いちばん細いもの。 */
    if(maxW&&bw>maxW){ if(n<24||best<1e9)continue; N=n;BW=bw;break; }
    const e=Math.abs(Math.log((bw/(n*rowH))/want));
    if(e<best){best=e;N=n;BW=bw;}
  }
  // 段＝帯。中心に近い段から埋める（中心がいちばん「その部屋のもの」）
  const rows=[];
  for(let i=0;i<N;i++)rows.push({y:(i+0.5)*rowH-N*rowH/2,ch:BW,items:[]});
  rows.sort((p,q)=>Math.abs(p.y)-Math.abs(q.y));
  const q=items.slice().sort((x,y)=>
    (crossOf(y.el)-crossOf(x.el))                        // その部屋らしい順
    ||(y.el._w.sink-x.el._w.sink)                        // 同じなら、沈んでいない順
    ||(String(x.el._w.id)<String(y.el._w.id)?-1:1));     // 決定論（同じ宙は同じ姿）
  let i=0;
  rows.forEach(r=>{
    let w=0;
    while(i<q.length&&w+q[i].w+SHEET_GAP<=r.ch){ w+=q[i].w+SHEET_GAP; r.items.push(q[i]); i++; }
  });
  while(i<q.length)rows[rows.length-1].items.push(q[i++]);   // 余りは最も外の段へ
  /* 段の丈は詰め直さない（2026-08-03 帯）。段高一定が帯の本体で、短冊は段の
     中心線に上下センター＝丈の違いは「切れ」ではなく「揺れ」として読める。
     空の段（中身が少ない島）だけ畳む。 */
  const line=rows.filter(r=>r.items.length).sort((p,q)=>p.y-q.y);
  const H=line.length*rowH-SHEET_GAP;
  let cur=-H/2;
  line.forEach(r=>{ r.y=cur+(rowH-SHEET_GAP)/2; cur+=rowH; });
  /* 段の中でも中心から外へ。またぐ一片は、相手の島のある側（右か左か）へ寄る。
     ただし片側が弦の半分を越えたら、寄せたい側でも反対へ回す——「みんな同じ方角へ
     掛かっている」島（隣が一つしか無い島では実際に起きる）で、束が片側だけ倍に
     伸びて画面から出るのを防ぐ。方角は希望であって、場所の取り合いには勝てない。 */
  let W=0;
  line.forEach(r=>{
    const half=r.ch/2, R=[],L=[];
    let rw=0,lw=0;
    r.items.forEach((it,k)=>{
      const d=partnerDir(isl,it.el);
      let right=(d==null)?(k%2===0):(Math.cos(d)>=0);
      if(right?(rw+it.w>half&&lw<rw):(lw+it.w>half&&rw<lw))right=!right;
      if(right){R.push(it);rw+=it.w+SHEET_GAP;}else{L.push(it);lw+=it.w+SHEET_GAP;}
    });
    let x=0;
    R.forEach(it=>{ it.x=x+it.w/2; it.y=r.y; x+=it.w+SHEET_GAP; W=Math.max(W,2*x); });
    x=0;
    L.forEach(it=>{ x-=it.w+SHEET_GAP; it.x=x+it.w/2; it.y=r.y; W=Math.max(W,-2*x); });
  });
  return {items:items,W:W,H:H};
}
/* 組んだ席へ、実際に動かす。「次々流れてくる」（2026-07-31）は残すが、流れる向きは
   読み順から**中心→外**へ変えた＝島がひらく。遅らせるのは transform だけ（.w の
   transition-property の並びは opacity, transform の二つ）。薄さまで遅らせると、
   闇に光る字が紙の上に居残って読めない瞬間ができる。 */
function sheetLayout(isl,stagger){
  /* 帯の形は**降りた時の眺め**（fitK）で決め、寄っても変えない（2026-08-03）。
     幅の上限をいまの倍率から出すと、焦点レンズで寄った最中の敷き直し（岸の
     組み替え）で上限が三百px台まで縮み、束が幅一枚ぶんの縦長の紐に化けた
     （実測16段・丈5248——読んでいた島ごと画面から消えた）。 */
  const kRef=isl._fitK!=null?isl._fitK:Math.max(K,0.08);
  /* 控えで組む（2026-08-14 カクツキ）。ここは第三引数を渡し忘れていて、島に降りる
     たび・岸が着くたび・画面が変わるたびに、その島の紙を全部測り直していた
     （実測 62枚で 12〜24ms＝携帯なら 60〜120ms。控えなら 0.1ms）。しかも直前の
     fitK は控えで倍率を決めているので、**倍率を決めた寸法と、並べた寸法が別**
     という取り合わせになっていた。同じ控えで見れば、決める側と並べる側が揃う。 */
  const pl=sheetPlan(isl,(VW*0.94)/Math.max(kRef,0.08),true); if(!pl)return;
  isl._bw=pl.W; isl._bh=pl.H;   // 束の箱（「島に居る」の物差し。帯は半径より縦に長い）
  const ax=Math.max(pl.W/2,1), ay=Math.max(pl.H/2,1);
  pl.items.forEach(it=>{
    it.el._gx=it.x-it.w/2; it.el._gy=it.y-it.h/2;
    /* 画面の外へ並ぶ紙は、流れてこない（2026-08-03 カクツキ）。降りた島には数百枚
       あるのに、束のうち画面に載るのは十数枚——残りの1.1秒の滑走は、誰にも見えない
       ところで数百枚ぶんの動きを合成し続ける仕事でしかない。見えないものは、置く。
       判定は着いた先の眺めで見る（滑走の間は views に出発と到着の二つの眺めが
       入っている＝glide で K が道中の値でも、onScreen は「到着した時にそこに
       見えるか」を答えられる）。 */
    const seen=onScreen(isl.cx+it.el._gx,isl.cy+it.el._gy,it.w,it.h);
    it.el.style.transitionDuration=seen?'':'.8s,0s';
    if(stagger&&!REDUCED&&seen){
      const rho=Math.min(1,Math.hypot(it.x/ax,it.y/ay));
      it.el.style.transitionDelay='0s,'+(rho*0.42).toFixed(2)+'s';
    }else it.el.style.transitionDelay='';
    it.el.style.transform=posOf(isl,it.el);
  });
}
/* 席替えの最中（家⇄紙片の席）は、紙は二つの座のあいだを飛んでいる。
   その間だけ「家でも席でも画面に掛かるなら描く」で間引く＝飛んでいる紙が
   途中で消えない。1.9s は transform 1.1s ＋ 流れ込みの遅れ 0.5s の外側。 */
function moving(isl){
  isl._moving=true;
  clearTimeout(isl._mvT);
  isl._mvT=setTimeout(()=>{isl._moving=false;apply();},1900);
}
function sheet(isl){
  isl.sheeted=true;
  // つまみで降りた時（flyTo を通らない道）も、帯の形の物差しを持つ
  if(isl._fitK==null)isl._fitK=fitK(isl);
  moving(isl);
  // 読んでいる間、宙は一つの様式だけになる（まわりの島は名と灯＝地形に還る）
  document.body.classList.add('reading');
  // 足元の名＝いま居る場所。つまみで流れ着いた島でもURLを書き換えておく
  // （リロードで同じ島に立てる）。積まない＝ジェスチャは履歴を伸ばさない。
  footNames(isl.room.name);
  if(typeof noteRoomURL==='function')noteRoomURL(isl.room.id,false);
  isl.el.classList.add('sheet');
  sheetLayout(isl,true);
  // 流れ着き終わったら遅れは畳む（残すと、受け止めや検索の応答まで遅れて見える）
  clearTimeout(isl._stgT);
  isl._stgT=setTimeout(()=>isl.words.forEach(el=>{
    el.style.transitionDelay='';el.style.transitionDuration='';
  }),1700);
  apply();
  reshore(isl);          // 降りるたび、岸には別のものが寄っている
}
function unsheet(isl){
  isl.sheeted=false;
  isl._fitK=null;        // 次に降りる時の眺めで、帯の形は引き直す
  lensClose(true);       // 席はこのあと自分でほどく＝組み直しは要らない
  moving(isl);
  document.body.classList.remove('reading');
  footNames(null);
  if(typeof noteRoomURL==='function'&&sheetWant==null)noteRoomURL(null,false);
  isl.el.classList.remove('sheet');
  clearTimeout(isl._stgT);
  // 帰る先（家）が画面の外なら、動きは見えない＝置いて帰す（sheetLayout と同じ作法）
  isl.words.forEach(el=>{
    const w=el._sw||el._pw||0, h=el._sh||el._ph||0;
    el.style.transitionDuration=(!w||onScreen(isl.cx+el._hx,isl.cy+el._hy,w,h))?'':'.8s,0s';
    el.style.transform='';el.style.transitionDelay='';
  });
  isl._stgT=setTimeout(()=>isl.words.forEach(el=>{
    el.style.transitionDuration='';
  }),1400);
  apply();
}
/* 足元の一行＝いま居る場所の名。島に降りている間はその島の名、全景では面の名。
   「どこに居るのか分からない」への、いちばん静かな答え（2026-08-03 操作性）。 */
function footNames(name){
  const f=document.querySelector('.foot');
  if(f)f.textContent=name||'ひとつづきの宙';
}
/* ── 寄る・引く・つまむ ─────────────────────────── */
let K=0.5, PX=0, PY=0;             // 倍率と、世界の原点の画面上の位置
/* KMIN は「全景（同心円の輪ぜんぶ）が画面に入る」ところまで引ける値（2026-08-05）。
   輪の席は意味の地図（〜5300）より広い（外の輪まで置くと半径4000超）——0.1 のままだと
   全景の式（min(VW,VH)/(mx·2.15)）が下限に噛んで、輪の外側が永遠に画面の外だった。
   ただし手で引ける下限は KFIT（＝全景そのもの）。0.02 まで素で引けると、輪が
   豆粒になった暗闇へ出られてしまうし、そこから戻る操作も長い。 */
const KMIN=0.02, KMAX=2.4;
let KFIT=0.1;                      // 全景の倍率。島を置いた後と resize 後に実測で更新
/* 全景の物差しは**席の輪**（2026-08-05）。島の半径（islandR＝ことばの数で1500超）
   ではない——遠景で字を隠したいま、島の外周は誰にも見えていないのに、その見えない
   円を画面に収めようとして、名の輪は画面の6割ほどしか使えていなかった。
   名は逆スケールで画面上の大きさが一定なので、輪が小さいほど名どうしが重なる。
   余白 1.35 は、外の輪に立つ名が画面の縁からはみ出さないためのぶん。 */
function homeFit(){
  let mx=0; islands.forEach(i=>{ mx=Math.max(mx,Math.hypot(i.cx,i.cy)); });
  return Math.max(KMIN,Math.min(1.2,Math.min(VW,VH)/((mx||900)*2*1.35)));
}
/* カクツキ修正（2026-07-31）その1：描き替えは1フレームに1度だけ。
   wheel も pointermove もフレームより速く飛んでくる——来た数だけ style を
   書き直すと、指の速さぶんだけ余計に描き直しが積まれてつっかえる。
   rAF で束ね、最後の値だけを次のフレームで一度書く。 */
let applyQ=false, ikLast=0, farOn=null, crispOn=null;
/* 手がふれているあいだ（2026-08-03 カクツキ）。つまむ・ひきずる・ホイール・＋−の
   最中は、宙を描き直す仕事だけで一フレームが埋まる——そこへ「気配の呼吸」や
   「一枚の字の書き直し」が重なると、ちょうど手が動いている時にだけ引っかかる。
   だから触れている間は、急がない仕事を止める。離して0.7秒で、静かに戻る。
   時計は使わない（rAFと performance.now を混ぜない作法／2026-07-27 の轍）。 */
let gesOn=false, gesT=0;
function gesture(){
  if(!gesOn){ gesOn=true; document.body.classList.add('gesturing'); }
  clearTimeout(gesT);
  gesT=setTimeout(()=>{ gesOn=false; document.body.classList.remove('gesturing');
                        lensCheck(); },700);   // 焦点レンズの出入りは、手が落ち着いてから
}
/* ── 宙の縁（2026-08-07 Kosei「端にある紙の片は端に当たる」）──────────────
   これまで宙は引きずり放題で、いちばん外の島を越えた先の、何も無い闇まで出られた
   ——そこには戻る道が無い（「宙へもどる」を知っていれば別だが、知らない人には
   ただの行き止まりの暗がりだった）。

   留める線は**島の席**（cx,cy）で引く。半径や束の箱で引いてはいけない：「画面が
   世界の箱の外へ出ない」にすると、いちばん外の島を画面の**中央に呼べなくなる**
   ——机の画面では VW/2 が島の見かけの半径より大きいので、flyTo が着いた先を
   縁の規律が押し戻してしまう（実測 1400×800 で 364px ずれる）。
   席で引けば、どの島も中央に来られて、なお「いちばん外の島の席は画面の外へ出ない」。
   ＝いちばん端の紙は、画面の端まで押せて、そこで当たって止まる。

   欲しい不等式は二本だけ：
     PX + x1·K ≧ pad        （いちばん右の席が、左の縁より内側に居る）
     PX + x0·K ≦ VW − pad   （いちばん左の席が、右の縁より内側に居る）
   これを PX について解くと a・b の二つの境になる。min/max で挟めば一行で済む。

   手がふれている間は、越えたぶんに抵抗をかけて**少しだけ**通す（ゴム）。
   縁でぴたりと止まると「壊れた」に見えるが、抵抗があると「ここが端だ」と手に伝わる
   ——Apple §1 の作法。離せば、封蝋が戻るのと同じ滑走で縁まで帰る。 */
const EDGE_PAD=56;          // 縁の余白。席が画面の縁に貼り付くと、島が半分に切れて見える
let edgeSoft=false;         // 手がふれているか（ゴムを効かせてよいか）
/* 席の箱は、島の**中心の点**だけでは足りない（2026-08-07）。
   紙を敷いた島は円ではなく帯で、束の丈（_bh）は 8000px に届く——半径 638 の島の
   12倍。中心一点で縁を引くと、帯の上下半分は「縁の外」になり、ひきずっても寄っても
   辿り着けない場所になっていた（部屋の後ろ半分のことばが、読めないまま在る）。
   焦点レンズがその席を狙うと、狙いだけ縁に押し戻されて、開いたはずの一枚が
   画面の外に置き去りになる——これが「触れたら、宙ごと消えた」の正体。
   だから敷いている島だけ、束の四隅も箱に入れる。畳んでいる島は今までどおり中心だけ。 */
function worldBox(){
  let x0=1/0,y0=1/0,x1=-1/0,y1=-1/0;
  const put=(x,y)=>{ if(x<x0)x0=x; if(x>x1)x1=x; if(y<y0)y0=y; if(y>y1)y1=y; };
  islands.forEach(i=>{
    put(i.cx,i.cy);
    if(i.sheeted&&i._bh){
      const hw=(i._bw||0)/2, hh=i._bh/2;
      put(i.cx-hw,i.cy-hh); put(i.cx+hw,i.cy+hh);
    }
  });
  return isFinite(x0)?{x0:x0,y0:y0,x1:x1,y1:y1}:null;
}
/* 越えたぶんは、ここまでしか行かない（漸近）。M は画面の2割強 */
function edgeRub(d,M){ return M*(1-1/(1+d/M)); }
function edgeHold(v,a,b,soft){
  const lo=Math.min(a,b), hi=Math.max(a,b);
  if(v>=lo&&v<=hi)return v;
  if(!soft)return v<lo?lo:hi;
  const M=Math.min(VW,VH)*0.22;
  return v<lo?lo-edgeRub(lo-v,M):hi+edgeRub(v-hi,M);
}
function clampPan(soft){
  const b=worldBox(); if(!b)return;
  PX=edgeHold(PX,EDGE_PAD-b.x1*K,VW-EDGE_PAD-b.x0*K,soft);
  PY=edgeHold(PY,EDGE_PAD-b.y1*K,VH-EDGE_PAD-b.y0*K,soft);
}
/* 縁を越えたまま指を離した時だけ、縁まで帰る（越えていなければ何もしない）。
   帰る道中もゴムを効かせたままにする——ここで硬く留めると、最初の1フレームで
   境の値に飛んで、戻る動きそのものが消える。 */
let edgeSettleT=0;
function edgeSettle(){
  const b=worldBox(); if(!b)return;
  const px=edgeHold(PX,EDGE_PAD-b.x1*K,VW-EDGE_PAD-b.x0*K,false);
  const py=edgeHold(PY,EDGE_PAD-b.y1*K,VH-EDGE_PAD-b.y0*K,false);
  if(Math.abs(px-PX)<0.5&&Math.abs(py-PY)<0.5)return;
  edgeSoft=true;
  clearTimeout(edgeSettleT);
  edgeSettleT=setTimeout(()=>{ edgeSoft=false; },560);
  glide(K,px,py,320,true);   // 読んでいる紙は取り上げない（keepLens）
}
function apply(){
  if(applyQ)return;
  applyQ=true;
  requestAnimationFrame(applyNow);
}
function applyNow(){
  applyQ=false;
  clampPan(edgeSoft);
  world.style.transform='translate('+PX+'px,'+PY+'px) scale('+K+')';
  /* その2（2026-07-31 夜に改訂）：しきい値をまたぐ class の付け外しには**遊び**を持たせる。
     .far/.crisp は宙ぜんぶの字の見え方を変える class なので、付け外しのたびに数百枚の
     スタイルが計算し直される。境目ちょうどでつまむと、一回のジェスチャの中で毎フレーム
     付いたり外れたりして——それ自体が固まる原因になっていた（ヒステリシス）。 */
  // near＝島ひとつが視界に収まる倍率から。0.42だと「島に降りた直後」（?room=の
  // 初期倍率≒0.35〜0.45）が far のままで、探すの一行が出なかった
  const far=(farOn===null)?(K<0.32):(K<0.30?true:(K>0.34?false:farOn));
  if(far!==farOn){
    farOn=far;
    document.body.classList.toggle('far',far);
    document.body.classList.toggle('near',!far);
  }
  // 引いた宙は、光暈を塗らない（見えない光のために宙ぜんぶの影は塗らない）
  const crisp=(crispOn===null)?(K<0.7):(K<0.66?true:(K>0.74?false:crispOn));
  if(crisp!==crispOn){ crispOn=crisp; document.body.classList.toggle('crisp',crisp); }
  cull();
  scaleNames();
  cullWords();
  if(typeof seekWatch==='function')seekWatch();   // 焦点の島＝検索の出どころ
}
/* 島の名の逆スケール（2026-07-31 夜）。値が動いた時と、島が畳みから戻った時だけ、
   名の要素（14枚）へ直に書く。継承する変数を world に書く旧法は、数百のことばの
   スタイルを毎フレーム無効にしていた。 */
function scaleNames(){
  /* 逆スケールの頭（2026-08-05 に 4.5→24 へ）。輪の全景は K≈0.03〜0.05 まで引く——
     4.5 で頭を押さえると、そこから先は名も灯も倍率と一緒に縮んで読めなくなる。
     0.62/K のまま伸ばせば、画面上の名は常に 19px×0.62≒12px。名は21枚しか無いので、
     大きく描き直しても字の総量は知れている（数百のことばとは桁が違う）。 */
  const ik=Math.min(24,Math.max(1,0.62/K));
  const ch=Math.abs(ik-ikLast)>0.004;
  if(ch)ikLast=ik;
  const t='translate(-50%,-50%) scale('+ikLast.toFixed(3)+')';
  islands.forEach(isl=>{
    if(isl.off)return;
    if(!ch&&!isl.nmDirty)return;
    isl.nmDirty=false;
    isl.nm.style.transform=t;
  });
}
/* ── 画面の外は描かない（2026-07-31・夜に二段へ）─────────────────
   display ではなく visibility：寸法は保ったまま描画だけを畳む＝戻る時に
   測り直しが走らない。
   ① 島ごと（余白は relax の渦で縁の外へ逃げたことば＋岸の漂流物ぶん）
   ② 島の中の一枚ごと。降りた島には数百枚が居るのに、画面に入るのは十数枚——
      残りの紙の地・影・光暈を毎フレーム塗るのは、見えないものを描いているだけ。
   滑走（flyTo）の間は「出発の眺め」と「到着の眺め」の二つで判定する。到着だけで
   畳むと、通り道の島が滑走の途中でふっと消える。 */
const CULL_M=160;
let views=null;                     // 非nullの間だけ、複数の眺めで判定する
function onScreen(x,y,w,h){         // world座標の箱が、どれかの眺めに掛かるか
  const V=views;
  if(!V){
    const sx=VW/2+PX+x*K, sy=VH/2+PY+y*K;
    return sx+w*K>=-CULL_M&&sy+h*K>=-CULL_M&&sx<=VW+CULL_M&&sy<=VH+CULL_M;
  }
  for(let i=0;i<V.length;i++){
    const px=V[i][0],py=V[i][1],k=V[i][2];
    const sx=VW/2+px+x*k, sy=VH/2+py+y*k;
    if(sx+w*k>=-CULL_M&&sy+h*k>=-CULL_M&&sx<=VW+CULL_M&&sy<=VH+CULL_M)return true;
  }
  return false;
}
function cull(){
  islands.forEach(isl=>{
    const r=isl.R*1.25+560;
    /* 紙を敷いた島は円ではない（2026-08-07）。束は半径の数倍に伸びるので、円の
       物差しで畳むと、帯の下のほうを読んでいる最中に**島ごと** visibility:hidden に
       なる——読んでいた紙も、まわりの紙も、一斉に消える。敷いている間は束の箱で見る。 */
    const rx=isl.sheeted?Math.max(r,(isl._bw||0)/2+CULL_M):r,
          ry=isl.sheeted?Math.max(r,(isl._bh||0)/2+CULL_M):r;
    const off=!onScreen(isl.cx-rx,isl.cy-ry,rx*2,ry*2);
    if(isl.off!==off){
      isl.off=off;
      if(!off)isl.nmDirty=true;     // 戻ってきた島の名は、いまの倍率で書き直す
      isl.el.style.visibility=off?'hidden':'';
    }
  });
}
/* 遠景では、一枚ずつの間引きは一枚も当たらない（2026-08-03 実測 784/784）。
   遠景の島は画面より小さいので、島が見えていれば中の字も全部見えている＝
   毎フレーム784回の当たり判定は、必ず「見えている」と答えるためだけに走っていた。
   だから遠景に入った一度だけ全部を戻して、あとは島の判定だけで済ませる。 */
function cullWords(){
  islands.forEach(isl=>{
    if(isl.off)return;              // 島ごと畳んである（中は測らない）
    if(farOn&&!isl.sheeted&&!isl._moving){
      if(isl._allOn)return;
      isl._allOn=true;
      isl.words.forEach(el=>{ if(el._off){el._off=false;el.style.visibility='';} });
      return;
    }
    isl._allOn=false;
    const mv=isl._moving, sh=isl.sheeted;
    isl.words.forEach(el=>{
      let off=false;
      const w=sh?el._pw:el._sw, h=sh?el._ph:el._sh;
      // 寸法をまだ測っていない紙（放った直後の一枚）は、間引かない
      if(w&&h&&!(el===held)&&!el.classList.contains('lit')&&!el.classList.contains('pull')){
        const gx=el._gx==null?el._hx:el._gx, gy=el._gy==null?el._hy:el._gy;
        const x=sh?gx:el._hx, y=sh?gy:el._hy;
        off=!onScreen(isl.cx+x,isl.cy+y,w,h);
        // 席替えの最中は、家と席の両方で見る（飛んでいる途中で消さない）
        if(off&&mv)off=!onScreen(isl.cx+(sh?el._hx:gx),isl.cy+(sh?el._hy:gy),w,h);
      }
      if(el._off!==off){ el._off=off; el.style.visibility=off?'hidden':''; }
    });
  });
}
/* 倍率が動いている間（2026-08-03 スクロール）。ひきずり（translate）は合成の上で
   済むが、倍率が変わると宙ぜんぶを**描き直す**——同じ「手がふれている」でも、
   この二つの重さは桁が違う。だから別の旗にして、寄り引きの最中だけ塗りを落とす。 */
let zoomT=0, zoomOn=false;
function zooming(){
  if(!zoomOn){ zoomOn=true; document.body.classList.add('zooming'); }
  clearTimeout(zoomT);
  zoomT=setTimeout(()=>{ zoomOn=false; document.body.classList.remove('zooming'); },700);
}
function zoomAt(cx,cy,factor){
  handTakes();         // ホイール・＋−も同じ：手が動いたら滑走は止まる
  // 手で引ける下限＝全景（KFIT）。既にそれより引いている時は、そこから寄るのは許す
  const k=Math.max(Math.min(KFIT,K),Math.min(KMAX,K*factor));
  if(k===K)return;                          // 端で止まっている時は、描き直さない
  const vw=VW/2, vh=VH/2;
  const wx=(cx-vw-PX)/K, wy=(cy-vh-PY)/K;   // カーソルの下の世界座標を動かさない
  PX=cx-vw-wx*k; PY=cy-vh-wy*k; K=k;
  gesture(); zooming();
  apply();
}
/* ── ホイールと二本指（2026-08-03・Kosei 確定「案A」）─────────────────────
   これまでは、ホイールも二本指も、来たものは全部「寄る・引く」だった。だが宙で
   いちばん重い仕事は倍率を変えることで（世界の800枚を描き直す）、いちばん軽いのは
   ひきずること（合成の上の平行移動）。**いちばん気軽な指の動きに、いちばん重い仕事が
   割り当たっていた**——机の指二本のひと振りで、40発のホイールが飛び、倍率は半分に
   なる（実測 0.55→0.255）。その40コマぜんぶが、宙の描き直しだった。

   だから地図の作法に揃える：
     二本指スクロール／ホイール … ひきずる（deltaX・deltaY をそのまま平行移動）
     つまみ（ctrl+wheel）・⌘+ホイール・実機のピンチ … 寄る・引く
   trackpad のつまみは、ブラウザが ctrlKey を立てた wheel として渡してくる（合成の
   ctrl であって、指はキーに触れていない）。実機の二本指ピンチは pointer 側が拾う。

   刻みの寸法：deltaMode は行（1）や頁（2）で来ることがあるので px へ直す。
   つまみの一発は ±50 で頭を押さえる——机のホイールに ctrl を添えた一段が
   exp(-50*0.0075)=0.687 ＝ ＋／− をひと押し（1/1.45=0.690）と同じ段になる。 */
const WHEEL_LINE=16;
vp.addEventListener('wheel',e=>{
  e.preventDefault();
  const m=e.deltaMode===1?WHEEL_LINE:(e.deltaMode===2?VH:1);
  if(e.ctrlKey||e.metaKey){
    const d=Math.max(-50,Math.min(50,e.deltaY*m));
    zoomAt(e.clientX,e.clientY,Math.exp(-d*0.0075));
    return;
  }
  handTakes();   // ひきずり（二本指スクロール）も手＝滑走は譲る。glide は PX/PY を
                 // 毎フレーム丸ごと書き直すので、譲らせないと指の動きは捨てられる
  PX-=e.deltaX*m; PY-=e.deltaY*m;
  gesture();
  apply();
},{passive:false});
/* ＋−は押し続けられる（2026-08-03 操作性）。ひと押し＝一段、350ms押し続けたら
   そこから小刻みに寄り続ける。click ではなく pointerdown で受ける——click だと
   一段ずつ何度も押し直すしかなく、遠くまで引くのに六連打が要った。 */
(function(){
  let rpT=0,rpI=0;
  function stopRepeat(){ clearTimeout(rpT); clearInterval(rpI); rpT=0; rpI=0; }
  function holdZoom(btn,f){
    btn.addEventListener('pointerdown',e=>{
      e.preventDefault();
      zoomAt(VW/2,VH/2,f);
      rpT=setTimeout(()=>{ rpI=setInterval(()=>zoomAt(VW/2,VH/2,f>1?1.13:1/1.13),120); },350);
    });
    ['pointerup','pointercancel','pointerleave'].forEach(t=>btn.addEventListener(t,stopRepeat));
    // 鍵盤（Enter/Space）からは click で来る。pointerdown を踏んだ指の click は
    // detail>0 ＝もう一段になるので、鍵盤（detail 0）だけを通す。
    btn.addEventListener('click',e=>{ if(!e.detail)zoomAt(VW/2,VH/2,f); });
  }
  holdZoom(document.getElementById('zin'),1.45);
  holdZoom(document.getElementById('zout'),1/1.45);
})();
// つまむ・ひきずる（1本＝パン、2本＝ピンチ。タップ判定は移動6px未満）
const ptrs=new Map(); let moved=0, pinchD=0;
vp.addEventListener('pointerdown',e=>{
  ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});
  handTakes();         // 手が触れたら、滑走は手に譲る（Kの取り合いをしない・Apple §1）
  edgeSoft=true;       // 手の間だけ、宙の縁はゴムになる（clampPan）
  clearTimeout(edgeSettleT);
  moved=0; vp.classList.add('drag');
  // 指を捕まえる（紙の外へ滑ってもひきずりを見失わない）。捕まえられない指も
  // 稀にある（合成のイベント・端末の癖）——そこで落とすと、以後の受け止めが全部死ぬ
  try{ vp.setPointerCapture(e.pointerId); }catch(err){}
  if(ptrs.size===2){ const p=[...ptrs.values()]; pinchD=Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y); }
});
vp.addEventListener('pointermove',e=>{
  const p=ptrs.get(e.pointerId); if(!p)return;
  if(ptrs.size===1){
    PX+=e.clientX-p.x; PY+=e.clientY-p.y;
    moved+=Math.hypot(e.clientX-p.x,e.clientY-p.y);
    gesture();
    apply();
  }
  p.x=e.clientX; p.y=e.clientY;
  if(ptrs.size===2){
    const q=[...ptrs.values()];
    const d=Math.hypot(q[0].x-q[1].x,q[0].y-q[1].y);
    if(pinchD>0){ zoomAt((q[0].x+q[1].x)/2,(q[0].y+q[1].y)/2,d/pinchD); }
    pinchD=d; moved+=8;
  }
// 指の動きは止めない（preventDefault しない＝passive）。触れる面の touch-action:none で
// ブラウザの巻き取りは既に切ってあるので、ここで待たせる理由がない（2026-07-31 夜）
},{passive:true});
/* ── 触れた、を click に頼らない（2026-07-31 夜）───────────────────
   宙は pointerdown で指を捕まえている（setPointerCapture＝指が紙の外へ滑っても
   ひきずりを見失わないため）。その代償として、離した時の click は**捕まえた側**
   （#vp）へ付け替えられて配られる——ことばにも島の名にも届かない。指で触れても
   受け止められない＝棚にも残せない、の正体はこれだった（マウスでは届く実装もあり、
   机の上では起きないので長く見つからなかった）。
   だから、離した場所を自分で見る：6px未満しか動いていなければ、その座標の下に
   居るものを elementFromPoint で引き当てて、受け止めか、島への戸として扱う。 */
let lastTap=null;   // 空白の二度叩き＝寄る（地図の常套。ことばの上では効かせない）
function tapAt(x,y){
  const t=document.elementFromPoint(x,y);
  if(!t)return;
  // 遠景では、島の名（ジャンル）がその島への戸
  const nm=t.closest('.isl-nm');
  if(nm){
    for(const isl of islands.values()){
      if(isl.el.contains(nm)){ release(); flyTo(isl); return; }
    }
  }
  const el=t.closest('.w');
  if(el){ lastTap=null; hold(el,x,y); return; }
  release();
  /* 何も無いところを、続けて二度。時計は performance.now だけ（rAFとは混ぜない）。
     48px は指の着地のぶれ。ことばの上は受け止め（上で return 済み）なので効かない。 */
  const now=performance.now();
  if(lastTap&&now-lastTap.t<400&&Math.hypot(x-lastTap.x,y-lastTap.y)<48){
    lastTap=null; zoomAt(x,y,1.9);
  }else lastTap={t:now,x,y};
}
function ptrUp(e){
  const had=ptrs.has(e.pointerId);
  ptrs.delete(e.pointerId); pinchD=0;
  if(!ptrs.size){
    vp.classList.remove('drag');
    edgeSoft=false;
    edgeSettle();      // 縁を越えたまま離していたら、縁まで帰る
  }
  // ひきずった指は受け止めではない（6px未満＝置いて、離しただけ）
  if(e.type==='pointerup'&&had&&!ptrs.size&&moved<=6)tapAt(e.clientX,e.clientY);
}
vp.addEventListener('pointerup',ptrUp); vp.addEventListener('pointercancel',ptrUp);

/* ── 受け止めと共鳴 ─────────────────────────────
   タップ＝受け止め（いつもの宙と同じ手つき）。その語だけ鮮明になり、波紋が
   ひらいて、同じ島の空気の近いことばが寄ってくる。意味は使わない（それは探すの領分）。 */
let held=null;
function release(){
  if(!held)return;
  const el=held; held=null;
  holdBox.classList.remove('on');
  setTimeout(()=>{ if(!held)holdBox.hidden=true; },300);
  const isl=islands.get(el._w.room);
  if(isl){ isl.el.classList.remove('hush');
           isl.words.forEach(x=>{x.classList.remove('pull');x.style.transform=posOf(isl,x);}); }
  el.classList.remove('held');
  lensClose();          // 焦点レンズも同じ一本で閉じる（開いていなければ何もしない）
}
/* 出典の刻印（§4.4）。触れて開いた、その一度だけ隅に刻む。外へ出る導線は作らない */
function srcMark(){
  if(!held||!held._w.pd)return '';
  return '<p class="hold-src">— '+esc(held._w.author)+'『'+esc(held._w.work)+'』より</p>';
}
/* 収蔵数（2026-08-07 §3.1）。「◯つの棚に収められています」——数だけ。
   誰の棚かも、いつ入ったかも言わない。0 のときは行ごと出さない（サーバが列を
   持たせない＝「0つの棚」という寂しさを、画面の条件分岐ではなく形で消す）。
   数は宙を組み直す版に乗って来るので、触れた瞬間にもう手元にある＝往復しない
   （Apple §1：押してから数えに行く経路を作らない）。 */
function keptMark(){
  if(!held)return '';
  const n=held._w.k|0;
  if(n<1)return '';
  return '<p class="hold-kept"><span class="n">'+n+'</span>つの棚に、収められています</p>';
}
const IC_SHELF='<span class="ic ic-shelf" aria-hidden="true"><span class="p"></span><span class="b"></span></span>';
const T_KEEP_OFF='棚にとっておく', T_KEEP_ON='棚にあります',
      T_MUTE_OFF='もう見ない',     T_MUTE_ON='また見る';
function renderHold(){
  if(!held)return;
  if(!LOGGED_IN){
    holdBox.innerHTML=srcMark()+keptMark()+'<p class="hold-note">このことばを、<wbr>'+
      '自分の<wbr>棚に<wbr>とっておけます。<br><a href="/?start=1&amp;new=1">はじめる</a></p>';
    return;
  }
  const s=kept.has('drift:'+held._w.id), mu=muted.has(held._w.id);
  holdBox.innerHTML=srcMark()+keptMark()+
    '<button type="button" class="hold-act keep'+(s?' on':'')+'" aria-pressed="'+(s?'true':'false')+'">'+
      IC_SHELF+'<span class="txt">'+(s?T_KEEP_ON:T_KEEP_OFF)+'</span></button>'+
    '<button type="button" class="hold-act sub mute'+(mu?' on':'')+'" aria-pressed="'+(mu?'true':'false')+'">'+
      '<span class="txt">'+(mu?T_MUTE_ON:T_MUTE_OFF)+'</span></button>';
  holdBox.querySelector('.keep').addEventListener('click',()=>toggleKeep(held));
  holdBox.querySelector('.mute').addEventListener('click',()=>toggleMute(held));
}
/* 行いは、そのことばの**左下**に置く（2026-07-31 夜・Kosei が三案から選んだ位置）。
   縦書きは右から左へ流れるので、左下＝読み終わったところ。行いはその真横に、
   紙の下端と揃えて添う——紙の字も、まだ読んでいない右の紙も隠さない。
   霞が濃いぶん重ねられないので、置けない時の順は左脇 → 真下（左端そろえ）→ 指の下。
   （旧実装は置けない時に画面の端まで飛ばしていて、触れた場所と行いが遠く離れた
     ＝「押しても何も出ない」に見えた原因）
   30px は霞の張り出し（::before の -34px）とほぼ同じ＝闇の縁が紙の際で止まる寸法。 */
/* 札の実寸（2026-08-06）。畳んでいる間は hidden で 0 になるので、測るあいだだけ開ける。
   焦点レンズはこの寸法を先に知って、そのぶん下を空けてから寄る（→ lensAim）。 */
function holdSize(){
  const was=holdBox.hidden;
  holdBox.hidden=false;
  const b={w:holdBox.offsetWidth,h:holdBox.offsetHeight};
  holdBox.hidden=was;
  return b;
}
/* 札が踏んではいけない下の線（2026-08-06）。足元の帯（＋−・宙へもどる・灯）は
   fixed で常にそこに居るので、画面の下端まで使うと札が帯の字に重なる。 */
function holdFloor(){
  const d=document.getElementById('dock');
  const h=d?d.offsetHeight:0;
  return innerHeight-12-Math.min(h,innerHeight*0.22);
}
function placeHold(tx,ty){
  if(!held)return;
  const r=held.getBoundingClientRect(), W=innerWidth, H=innerHeight, F=holdFloor();
  holdBox.hidden=false;
  holdBox.classList.remove('on');
  // 寸法は offsetWidth/Height で測る：畳んでいる間は scale(.97) が掛かっていて、
  // getBoundingClientRect だと3%小さい札を基準に置いてしまう（端をそろえると数px狂う）
  const b={width:holdBox.offsetWidth,height:holdBox.offsetHeight};
  let x,y,ax,oy;
  if(r.left-b.width-30>=12&&r.bottom-b.height>=12){   // ① 左脇・下そろえ（本命）
    x=r.left-b.width-30; y=r.bottom-b.height;
    ax=r.left; oy=b.height;                          // 立ち上がりの起点＝紙の左下の角
  }else if(r.bottom+30+b.height<=F){                 // ② 左に無ければ真下・左端そろえ
    x=r.left; y=r.bottom+30; ax=r.left; oy=0;
  }else if(tx!=null&&ty!=null){                      // ③ 紙が画面より高い＝触れた指の下
    y=(ty+20+b.height<=F)?ty+20:ty-20-b.height;
    x=tx-b.width/2; ax=tx; oy=0;
  }else{                                             // どこにも入らない時は、帯の**上**へ
    /* 2026-08-06：ここは長らく「紙の上へ回り込む」だった（r.top-b.height）。
       机の上では①が必ず通るので誰も見なかったが、携帯では①（左の幅）も②（下の空き）も
       通らないので**毎回ここへ落ちて**、いま読んでいる字の頭に「棚にとっておく」が
       乗っていた。行いが本文より上に立つのは、この宙の読み順（右上→左下）の逆走でもある。
       上へは行かせない——入らないなら下に沿わせる（レンズの側も下を空けて寄る）。 */
    x=r.left; y=F-b.height; ax=r.left+Math.min(r.width,b.width)/2; oy=0;
  }
  x=Math.min(Math.max(12,x),Math.max(12,W-b.width-12));
  y=Math.min(Math.max(12,y),Math.max(12,F-b.height));
  holdBox.style.left=x+'px'; holdBox.style.top=y+'px';
  holdBox.style.transformOrigin=(ax-x)+'px '+oy+'px';
  setTimeout(()=>{ if(held)holdBox.classList.add('on'); },16);
}
/* 開いたあとで丈が伸びた時に、下へはみ出したぶんだけ静かに上げる（横は動かさない
   ＝触れた場所との関係を崩さない）。収まっている時は何もしない。 */
function keepInView(){
  if(holdBox.hidden)return;
  const b=holdBox.getBoundingClientRect(), F=holdFloor();
  if(b.bottom<=F)return;
  const top=Math.max(12,Math.min(b.top,F-b.height));
  holdBox.style.top=top+'px';
}
function paintAct(sel,on,onTxt,offTxt){
  const b=holdBox.querySelector(sel); if(!b)return;
  b.classList.toggle('on',on);
  b.setAttribute('aria-pressed',on?'true':'false');
  b.querySelector('.txt').textContent=on?onTxt:offTxt;
}
async function toggleKeep(el){
  if(!el)return;
  const key='drift:'+el._w.id, on=!kept.has(key);
  if(on)kept.add(key); else kept.delete(key);
  paintAct('.keep',on,T_KEEP_ON,T_KEEP_OFF);
  if(!on)dropPad(holdBox);
  try{
    const r=await fetch('/api/shelf',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({src:'drift',ref:el._w.id,on:on})});
    if(!r.ok)throw new Error('keep');
    const d=await r.json().catch(()=>null);
    // 残した、その場に付箋の紙片が開く（貼らずに閉じてよい・v2.2 §3）
    if(on&&d&&d.id&&held===el){
      fusenPad(holdBox,d.id,[]);wherePad(holdBox,d.id,d.shelf,d.shelves);
      /* 付箋と置き場所のぶん、札の丈が倍ほどに伸びる。レンズで開いている紙は
         そのぶん退いて場所をつくる（札を上へ逃がすと、また本文の上に乗る）。 */
      if(lensEl===el)lensAim(el); else keepInView();
    }
  }catch(e){
    dropPad(holdBox);
    if(on)kept.delete(key); else kept.add(key);
    paintAct('.keep',!on,T_KEEP_ON,T_KEEP_OFF);
    whisper('いま、<wbr>棚に<wbr>届きませんでした。',6000);
  }
}
async function toggleMute(el){
  if(!el)return;
  const on=!muted.has(el._w.id);
  if(on)muted.add(el._w.id); else muted.delete(el._w.id);
  paintAct('.mute',on,T_MUTE_ON,T_MUTE_OFF);
  if(on)dropPad(holdBox);        // 消すと決めた紙に、付箋を貼らせない
  try{
    const r=await fetch('/api/sky/word/'+encodeURIComponent(el._w.id)+'/mute',
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({on:on})});
    if(!r.ok)throw new Error('mute');
    /* 「消えました」とは言わない。放した時に、その紙だけ静かに引く */
    el.classList.toggle('mutedout',on);
  }catch(e){
    if(on)muted.delete(el._w.id); else muted.add(el._w.id);
    paintAct('.mute',!on,T_MUTE_ON,T_MUTE_OFF);
    whisper('いま、<wbr>届きませんでした。',6000);
  }
}
/* ── 焦点レンズ（2026-08-03「ひとつづきの宙」1d／近）───────────────
   島に降りて（＝紙片の帯）、一枚に触れると、その紙が画面の中央へ滑って**全文へ**
   ひらく。寸法の3値スナップで畳まれていたことばは、ここでだけ規律を解く。
   まわりは息をひそめる（既存の hush がそのまま効く）。出典と行い（holdBox）は
   滑走が済んでから、読み終わりの左に添う。
   もうひとつの入り口は倍率：降りた島で一定より寄ると、画面中央にいちばん近い
   一枚がひとりでに開く（寄る＝読む）。引けば閉じる。しきい値に遊びを持たせるのは
   far/crisp と同じ作法。開閉の判定は手が離れて落ち着いてから（gesture の終い）——
   つまみの最中に帯を組み直すと、それ自体が固まる原因になる。 */
let lensEl=null, lensT=0, lensLo=0;
const LENS_IN=1.30, LENS_OUT=1.10;
function lensOpen(el){
  const isl=islands.get(el._w.room);
  if(!isl||!isl.sheeted||lensEl===el)return;
  if(lensEl)lensEl.classList.remove('focus');
  lensEl=el;
  el.classList.add('focus');
  document.body.classList.add('lens');
  moving(isl);
  sheetLayout(isl);          // 開いた実寸で帯を組み直す＝隣が滑って席を空ける
  lensAim(el);
}
/* 開いた一枚の席の中心へ、静かに寄る（読める丈まで。手で寄せた倍率は退かない）。
   照準を開くことから分けてあるのは、帯が敷き直された時（reshore＝岸の組み替え）に
   席が動くから——席だけ動いてカメラが古い席を向いたままだと、読んでいた紙が
   画面の外へ消える（実際に起きた）。敷き直した側が、もう一度これを呼ぶ。 */
function lensAim(el){
  const isl=islands.get(el._w.room); if(!isl)return;
  const w=el._pw||0,h=el._ph||0;
  const fit=r=>Math.max(K,Math.min(1.6,(VH-170-r)/Math.max(h,1)));
  /* 行いの札の場所を、寄る**前に**勘定する（2026-08-06）。
     札の本命は紙の左脇（placeHold ①）だが、携帯には左に入る幅が無い。左が駄目なら
     下へ回るしかないので、そのぶんを空けて寄る——空けずに寄ると紙が画面を縦に
     使い切り、札は行き場を失って本文の上に乗っていた（携帯でだけ起きる姿）。
     左に入るなら room は 0＝机の上の見え方は一切変えない。 */
  const b=holdSize();
  const room=(b.w&&(VW-w*fit(0))/2<b.w+42)?b.h+42:0;
  const k=fit(room);
  // 閉じる線は入った倍率から引く（丈の長い紙は 1.0 前後で開くこともある——
  // 固定の線だと、開いたその倍率が既に線の下で、ひと撫でで閉じてしまう）
  lensLo=Math.min(LENS_OUT,k*0.82);
  const wx=isl.cx+(el._gx!=null?el._gx:el._hx)+w/2,
        wy=isl.cy+(el._gy!=null?el._gy:el._hy)+h/2;
  world.style.transition=REDUCED?'none':'transform .7s var(--ease)';
  views=[[PX,PY,K],[-wx*k,-wy*k-room/2,k]];
  K=k; PX=-wx*K; PY=-wy*K-room/2;
  applyNow();
  // 縁が狙いを退けたぶんは、到着の眺めにも書き戻す（退いた先で間引く＝道中と
  // 着いた後で、見えているものが食い違わない）
  if(views)views[1]=[PX,PY,K];
  clearTimeout(lensT);
  lensT=setTimeout(()=>{ world.style.transition=''; views=null; applyNow();
                         if(held===el)placeHold(); },740);
}
/* quiet＝帯を組み直さない（unsheet が席をほどいた後に呼ぶ時） */
function lensClose(quiet){
  if(!lensEl)return;
  const el=lensEl; lensEl=null;
  el.classList.remove('focus');
  document.body.classList.remove('lens');
  // 滑走の途中で閉じられた時の後始末（flyTo はこの後で自分の滑走を敷き直す）
  clearTimeout(lensT);
  world.style.transition=''; views=null;
  const isl=islands.get(el._w.room);
  if(!quiet&&isl&&isl.sheeted){ moving(isl); sheetLayout(isl); apply(); }
}
/* 倍率の出入り（gesture の終いに一度だけ見る） */
function lensCheck(){
  const isl=sheetId!=null?islands.get(sheetId):null;
  if(!isl||!isl.sheeted)return;
  if(!lensEl&&K>LENS_IN){
    let best=null,bd=1e9;
    isl.words.forEach(el=>{
      if(el.classList.contains('mutedout'))return;
      const p=screenPos(el);
      const d=Math.hypot(p.x+(el._pw||0)*K/2-VW/2,p.y+(el._ph||0)*K/2-VH/2);
      if(d<bd){bd=d;best=el;}
    });
    if(best&&held!==best)hold(best);
  }else if(lensEl&&K<lensLo){
    release();
  }
}
/* 受け止めるのは、島の上の一枚とは限らない（2026-08-02）。探して集まってきた片は
   どの島にも属さない面（.gather）の上に居るので、島が無くても通る道にしておく。 */
function hold(el,tx,ty){
  if(held===el){ release(); return; }
  release();
  held=el; el.classList.add('held');
  const isl=islands.get(el._w.room);
  if(isl)isl.el.classList.add('hush');
  /* 放った直後の自分のことばは、まだ宙のidを持たない（公開idは次の読み込みで付く）。
     行いも共鳴も引けないので、鮮明になるだけ——それでいい（自分の字を確かめる間）。 */
  if(!el._w.id){ if(el._w.room!=null)noteTouch(el._w.room); return; }
  noteSeen(el._w.id);                 // 受け止めた時だけ「見た」に数える（一望は数えない）
  if(!isl){                           // 集まってきた片：行いだけを添える（共鳴は島の中の話）
    renderHold(); placeHold(tx,ty); return;
  }
  if(!REDUCED){
    const r=document.createElement('span'); r.className='ripple';
    // 波紋はことばの今いる場所から（紙片に並んでいる時はその席から）
    r.style.left=(isl.sheeted&&el._gx!=null?el._gx:el._hx)+'px';
    r.style.top =(isl.sheeted&&el._gy!=null?el._gy:el._hy)+'px';
    isl.el.appendChild(r); setTimeout(()=>r.remove(),1700);
  }
  noteTouch(el._w.room);              // 触れた気配（部屋の灯が呼吸する）
  renderHold();
  // 島に降りている時は焦点レンズ＝中央へ滑って全文へ。行いは滑走の後に添う
  if(isl.sheeted)lensOpen(el);
  else placeHold(tx,ty);
  fetch('/api/sky/near?id='+encodeURIComponent(el._w.id))
    .then(r=>r.json()).then(d=>{
      if(held!==el||!d.ids)return;
      const by=new Map(); isl.words.forEach(x=>by.set(x._w.id,x));
      d.ids.forEach(id=>{
        const t=by.get(id); if(!t||t===el)return;
        t.classList.add('pull');
        // 紙片に並んでいる間は席を動かさない（.pull の鮮明さだけで「近い」を言う）
        if(isl.sheeted)return;
        const dx=t._hx-el._hx, dy=t._hy-el._hy, len=Math.hypot(dx,dy)||1;
        const pull=Math.max(0,len-150)*0.62;            // 150pxまでは寄せ切らない（字は触れ合わない）
        t.style.transform='translate('+(-dx/len*pull)+'px,'+(-dy/len*pull)+'px)';
      });
    }).catch(()=>{});
}
/* 受け止めと島への戸は、上の tapAt（pointerup）が引き受けた。ここに click の
   listener は置かない——二重に効いて、触れた端から自分で取り消してしまう。 */

/* ── 打鍵の実時間再生（v2追補 §1 の移植）───────────────────
   一枚の宙でも、ことばは「置いてある」ではなく「いま書かれている」もの。
   仕組みは mood.html と同じ：実際の打鍵（ev1）＞旧スナップショット＞合成。
   合成は IME の手つき（漢字はまとめて現れる・乱数の種は本文＝毎回同じ筆致）。 */
function rndFrom(seed){
  let s=(seed>>>0)||0x9E3779B9;
  return function(){s^=s<<13;s>>>=0;s^=s>>>17;s^=s<<5;s>>>=0;return s/4294967296;};
}
const RE_KANJI=/[々㐀-䶿一-鿿豈-﫿]/, RE_HIRA=/[ぁ-ゟ]/;
function synthTrace(text){
  const chars=[...String(text||'')];
  if(chars.length<2) return [{at:0,v:chars.join('')}];
  const rnd=rndFrom(fnv(text));
  const cur=[], out=[{at:0,v:''}];
  let acc=0,wait=0;
  const put=d=>{acc+=Math.max(0,d)+wait;wait=0;out.push({at:acc,v:cur.join('')});};
  let burst=0, doubt=chars.length>=10?1:0, i=0;
  while(i<chars.length){
    const c=chars[i];
    if(RE_KANJI.test(c)){
      let j=i+1;
      while(j<chars.length&&RE_KANJI.test(chars[j]))j++;
      if(j<chars.length&&RE_HIRA.test(chars[j])&&
         (j+1>=chars.length||!RE_HIRA.test(chars[j+1]))) j++;
      const kana=Math.max(2,Math.round((j-i)*1.8));
      for(let k=i;k<j;k++) cur.push(chars[k]);
      put(kana*(56+rnd()*44)+130+rnd()*250);
      i=j; continue;
    }
    cur.push(c);
    if(burst>0){ burst--; put(44+rnd()*30); }
    else if(rnd()<0.34){ burst=1+Math.floor(rnd()*3); put(58+rnd()*46); }
    else put(86+rnd()*104);
    if(c==='\n') wait+=360+rnd()*420;
    else if(/[。！？…]/.test(c)) wait+=300+rnd()*400;
    else if(/[、，]/.test(c)) wait+=150+rnd()*260;
    else if(rnd()<0.05) wait+=200+rnd()*420;
    i++;
    if(doubt&&i>=6&&i<chars.length-2&&rnd()<0.1){
      const n=1+Math.floor(rnd()*2), back=cur.slice(-n);
      if(back.length===n&&back.every(ch=>!RE_KANJI.test(ch)&&ch!=='\n')){
        doubt--;
        wait+=180+rnd()*380;
        for(let k=0;k<n;k++){ cur.pop(); put(70+rnd()*66); }
        wait+=200+rnd()*430;
        for(const ch of back){ cur.push(ch); put(66+rnd()*80); }
      }
    }
  }
  const total=out[out.length-1].at;
  if(total>9000){const k=9000/total;out.forEach(o=>o.at*=k);}
  else if(total<1900){const k=Math.min(2,1900/Math.max(1,total));out.forEach(o=>o.at*=k);}
  return out;
}
function clampGap(dt){
  if(dt<=3000)return dt;
  return Math.min(5000,3000+380*Math.log(dt/3000));
}
function normEvents(ev){
  const out=[];let acc=0,cur='';
  for(const e of ev){
    const dt=+e[0]||0,op=e[1],ch=String(e[2]==null?'':e[2]);
    acc+=clampGap(dt);
    if(op==='s')cur=ch;
    else if(op==='i')cur+=ch;
    else if(op==='d')cur=cur.slice(0,Math.max(0,cur.length-ch.length));
    out.push({at:acc,v:cur,pause:dt>3000});
  }
  return out;
}
function traceFrames(text,steps){
  if(steps&&steps.fmt==='ev1'&&steps.ev&&steps.ev.length>1)return normEvents(steps.ev);
  return synthTrace(text);
}
function playType(el,text,steps,done){
  const run=(el._run=(el._run||0)+1);
  const norm=traceFrames(text,steps);
  if(REDUCED||norm.length<2){el.textContent=text;if(done)done();return;}
  /* 一コマごとに innerHTML を組み直すのはやめる（2026-07-31 夜）。
     字が一つ増えるたびに HTML を読み直させていた——筆先（tcaret）は一度だけ置き、
     以後は文字そのもの（テキストノード）の中身だけ差し替える。 */
  /* 字が一つ増えるたび、紙の寸法そのものが変わっていた（2026-08-03 カクツキ）。
     ことばの箱は width/height:max-content ＝ 中身で寸法が決まるので、一コマ進める
     たびに縦組みの行取りを測り直し、島の版面を組み直し、影と紙の地を塗り直していた。
     再生は8秒ごとに3枚・一枚あたり最大9秒＝**ほとんどいつも走っている**ので、
     これは常時の重さになる。始める前に出来上がりの寸法を測って留めておけば、
     中身が増えても箱は動かない（縦書きは右から埋まるので、見え方も変わらない）。
     留めるのは宙のことば（.w）だけ——読む柱の本文は寸法が固定なので要らない。 */
  let pinW=0,pinH=0;
  if(el.classList.contains('w')){ pinW=el.offsetWidth; pinH=el.offsetHeight; }
  let i=0,last=null,e=0,fin=false;
  const tn=document.createTextNode(norm[0].v);
  const car=document.createElement('span'); car.className='tcaret';
  // 留めを外すのは、いまも自分が筆を持っている時だけ（次の再生が始まっていたら触らない）
  const unpin=()=>{ if(!pinW||!el._pin||el._run!==run)return; el._pin=0;
                    el.style.width='';el.style.height=''; };
  const finish=()=>{
    if(fin)return; fin=true;
    unpin();
    el.textContent=text; el.classList.remove('nijimi');
    if(done)done();
  };
  if(pinW){ el._pin=1; el.style.width=pinW+'px'; el.style.height=pinH+'px'; }
  el.textContent=''; el.appendChild(tn); el.appendChild(car);
  setTimeout(()=>{ if(el._run===run&&el.isConnected&&last===null) finish(); },12000);
  requestAnimationFrame(function frame(now){
    /* 降ろされた時も、筆を渡した相手に「終わった」と言う（2026-08-06）。
       言わずに黙ると rotateOne の _typing が立ったまま残り、その一枚だけ
       二度と書き直されない紙になる。 */
    if(el._run!==run||!el.isConnected){el.classList.remove('nijimi');if(done)done();return;}
    if(fin)return;
    if(last===null)last=now;
    e+=Math.min(Math.max(0,now-last),100);   // 止まっていた間は「無かったこと」に（iOSの詰まり対策）
    last=now;
    if(i<norm.length&&norm[i].at<=e){
      // 一フレームで複数コマ進む時も、書き込むのは最後の姿だけ
      while(i<norm.length&&norm[i].at<=e)i++;
      tn.nodeValue=norm[i-1].v;
      if(el._nij){el.classList.remove('nijimi');el._nij=false;}
    }
    if(i<norm.length){
      if(norm[i].pause&&!el._nij){el._nij=true;el.classList.add('nijimi');}
      requestAnimationFrame(frame);
    }
    else finish();
  });
}
const traceCache=new Map();
function getTrace(id){
  if(traceCache.has(id))return Promise.resolve(traceCache.get(id));
  return fetch('/api/sky/word/'+encodeURIComponent(id)+'/trace')
    .then(r=>r.ok?r.json():null)
    .then(d=>{
      const ev=(d&&d.trace_ev&&d.trace_ev.length>1)?{fmt:'ev1',ev:d.trace_ev}:null;
      if(traceCache.size>120)traceCache.clear();
      traceCache.set(id,ev);
      return ev;
    }).catch(()=>null);
}
/* 書き直しのローテーション（v2追補 §2 の移植）。
   一枚の宙は数百のことばが同居するので、mood.html の「hash%4 の一群まるごと」は
   多すぎる——**いま画面に見えている近景のことば**に絞り、さらに一周期3枚まで。
   静けさを守りつつ、眺めている場所では常にどこかで trace が動いている。 */
let tick=0;
async function rotateOne(el){
  if(el._typing)return;
  el._typing=true;
  const tr=await getTrace(el._w.id);
  if(!el.isConnected){el._typing=false;return;}
  playType(el,el._w.poem,tr,()=>{el._typing=false;});
}
function screenPos(el){
  const isl=islands.get(el._w.room);
  const hx=(isl.sheeted&&el._gx!=null)?el._gx:el._hx,
        hy=(isl.sheeted&&el._gy!=null)?el._gy:el._hy;
  const wx=isl.cx+hx, wy=isl.cy+hy;
  return {x:VW/2+PX+wx*K, y:VH/2+PY+wy*K};
}
/* ── 放ったことばの着地（書く柱から）────────────────────────
   置いた島へ滑らかに降り、島の心の近くで自分の打鍵がそのまま再生される。
   まだ宙のidを持たない（サーバの公開idは次の読み込みで付く）＝受け止めても行いは出ない。 */
/* 島を渡る（2026-08-02）：どの状態から呼ばれても、まず前の島で開いていたものを畳む。
   受け止めていた一枚も、集まってきた片の面も、置き去りにしない。
   焦点の引き直し（＝紙を敷く島の入れ替え）は apply() を待たずその場で始める
   ——rAF が止まる環境では、待つと島に着いても紙が敷かれないままになる。 */
/* 降りる先の倍率は、島の半径ではなく**束の寸法**から決める（2026-08-02）。
   半径は遠景の姿でしかないので、中身が12片の島は画面がすかすかになり、80片の島は
   画面から溢れていた。束を測って「入るところまで寄る／退く」なら、どちらも余白は
   残らない。測るために一瞬だけ紙片の姿にする（同じフレームで戻す＝描かれない）。
   下限は「島に居る」の線（R*K>0.32*短辺）より上に置く——寄った拍子に島から
   追い出されては元も子もない。収める前に、まず居られること。 */
function fitK(isl){
  /* 控えが揃っているなら、島を紙片の姿にして測り直さない（2026-08-03 カクツキ）。
     揃っていないのは、まだ一度も降りていない島と、あとから来た岸の漂流物だけ。
     2026-08-14：足りない紙が一枚でもあれば紙片の姿を立てる（測るのはその一枚だけ
     ——sheetPlan が控えのある紙は読むだけで済ませる）。姿を立てずに測ると、
     散らばりの寸法を紙片の控えとして焼き付けてしまう＝以後ずっと狂う。 */
  const cold=coldSizes(isl).length>0;
  const was=isl.el.classList.contains('sheet');
  if(cold&&!was)isl.el.classList.add('sheet');
  // 下限は、字が読めるところ（0.42）と、島から押し出されない線（出る線 0.20）の外側
  const lo=Math.max(0.42,0.21*Math.min(VW,VH)/isl.R);
  /* 束の形は倍率で変わり（幅の上限）、倍率は束の形で決まる。数回まわせば落ち着く
     ——寄るほど束は細く高くなり、細いほど寄れる、の片側だけに動くので。 */
  let k=1.05,pl=null;
  try{
    for(let t=0;t<5;t++){
      pl=sheetPlan(isl,(VW*0.94)/k,true);
      if(!pl)break;
      const nk=Math.max(lo,Math.min(1.05,
        Math.min((VW*0.94)/Math.max(pl.W,1),((VH-SHEET_PAD)*0.94)/Math.max(pl.H,1))));
      if(Math.abs(nk-k)<0.01){ k=nk; break; }
      k=nk;
    }
  }catch(e){ pl=null; }
  if(cold&&!was)isl.el.classList.remove('sheet');
  if(!pl)return Math.max(0.55,Math.min(1.1,Math.min(VW,VH)/(isl.R*1.12*2.15)));
  return k;
}
let sheetLock=null;      // 滑走中の行き先（着くまでは「居る」として扱う）
/* ── 居場所とURLを揃える（2026-08-03 操作性）──────────────────────
   島へ降りるたび `/mood?room=<id>` を履歴へ積む。携帯の戻る＝「島から宙へ」
   「前の島へ」になり、リロードしても同じ島に立てる（?room= はサーバが既に読む）。
   積むのは flyTo（意志のある移動）だけ。つまみ・ひきずりで島を出入りした時は
   replace で書き換えるだけ＝ジェスチャの数だけ履歴が伸びることはない。 */
function urlRoom(){ const m=location.search.match(/[?&]room=(\d+)/); return m?+m[1]:null; }
let navQuiet=false;      // popstate から呼ばれた移動は、履歴へ積み直さない
function noteRoomURL(id,push){
  try{
    if(urlRoom()===id)return;
    const u=id!=null?('/mood?room='+id):'/mood';
    if(push&&!navQuiet)history.pushState({room:id},'',u);
    else history.replaceState({room:id},'',u);
  }catch(e){}
}
/* 滑走は K そのものを rAF で運ぶ（2026-08-05）。それまでは K を先に到着へ書き、
   world の transform だけを 1.1s の transition で滑らせていた——K が一瞬で飛ぶので
   逆スケール（ik）も一瞬で到着値になり、画面上の名の寸法 K(t)×ik は道中で山なりに
   膨らむ。上限4.5の頃は1.3倍で見えなかったが、全景（ik≈9〜15）まで引ける今は
   3倍を超えて、滑走のたび21の名と炎が一斉に脈打った。
   毎フレーム K を書き、scaleNames に逆を取らせれば、名は道中も 12px のまま。
   倍率は**対数**で、画面の中心が指す世界座標は線形で運ぶ（積 K×ik が道中も
   一定なのは対数だけ。素の線形だと、遠くから寄る滑走の前半が全部すっ飛ぶ）。
   毎フレームの仕事は applyNow ＝つまみ（pinch）一回ぶんと同じで、実測済みの道。
   時計は rAF の引数だけを使う（performance.now と混ぜない——7/27 の轍）。
   rAF の止まる環境の保険はタイマ：ms+180 で必ず到着の値を書く。 */
/* 滑走は**一人**（2026-08-05 夜）。道中の眺めを毎フレーム晒すようになった以上、
   「いま滑っているか」「どこへ向かっているか」を、下流の誰もが問えなければならない。
   glideAim ＝ いま向かっている倍率（reshore の詰め直しは、道中の過渡の K ではなく
   **この的**と比べる。素の K と比べると、掃引が的を通過する数十msに fetch が返った
   時だけ乗り換えを取りこぼし、帯だけ広く組まれて紙が画面から溢れる）。 */
let glideN=0, glideLive=0, glideAim=0;
function glideBusy(){ return glideLive!==0&&glideLive===glideN; }
function glideK(){ return glideBusy()?glideAim:K; }   // 「落ち着いた先の倍率」
/* 手が触れた＝滑走は手に譲る。飛行そのものも、そこで終わる——譲ったのに flying が
   残ると、Escape も 0 も効かない窓（実測1.2秒）ができる。行き先の約束（sheetLock）も
   一緒に降ろす：ここから先は、手で寄せた倍率だけで「島に居るか」を見る。
   飛んでいない時は何もしない（ホイールの一刻みごとに宙を描き直さない）。 */
function handTakes(){
  glideN++; glideLive=0;
  if(flightT)flightEnd(flightN);
}
function glide(k1,px1,py1,ms,keepLens){
  ms=ms||1100;
  const id=++glideN;
  glideLive=id; glideAim=k1;
  const fin=()=>{ if(id!==glideN)return; glideLive=0; glideN++;
                  K=k1; PX=px1; PY=py1; applyNow();
                  /* 滑走の間、島の敷き替えは行き先だけに絞ってある（seekWatch）。
                     着いたら、ここでもう一度ふつうの目で見直す。 */
                  if(typeof seekWatch==='function')seekWatch();
                  /* レンズ（開いた一枚）の出入りも滑走中は見送っている。ただし
                     **開いているレンズは閉じない**——閉じてよいのは、その滑走が
                     レンズを捨てて出ていく時（flyTo/overview）だけで、岸の詰め直しの
                     ような小さな寄り直しで読んでいる紙を取り上げてはいけない。
                     どちらかは呼ぶ側が言う（keepLens）。 */
                  if(!keepLens&&typeof lensCheck==='function')lensCheck(); };
  if(REDUCED){ fin(); return; }
  world.style.transition='';   // レンズの .7s が残っていると、毎フレーム書きが泥になる
  const k0=K, w0x=-PX/K, w0y=-PY/K, w1x=-px1/k1, w1y=-py1/k1;
  let t0=null;
  requestAnimationFrame(function step(ts){
    if(id!==glideN)return;             // 新しい滑走（reshore の詰め直し等）に譲った
    if(t0===null)t0=ts;
    const p=Math.min(1,(ts-t0)/ms);
    if(p>=1){ fin(); return; }
    const e=1-Math.pow(1-p,3);         // var(--ease) と同じ、出て早く・着いて静か
    K=k0*Math.pow(k1/k0,e);
    const cx=w0x+(w1x-w0x)*e, cy=w0y+(w1y-w0y)*e;
    PX=-cx*K; PY=-cy*K;
    applyNow();
    requestAnimationFrame(step);
  });
  setTimeout(fin,ms+180);
}
/* 飛行の後始末は、**その飛行のもの**（2026-08-05 夜）。旧実装は 1300ms の裸の
   タイマで、次の飛行が始まっても前のタイマは生きていた——K は glideN の乗り換えで
   守られていたが、sheetLock・views・flying は前の飛行が無差別に畳んでいた。
   行き先を連打する（戸の一覧・名を続けてタップ・戻るを連打）と、二つ目の飛行の
   道中で一つ目のタイマが sheetLock を落とし、入る線が 0.20→0.32 へ跳ね上がって
   ——降り立った島に、紙が永遠に敷かれないまま置き去りになる。
   1500ms は滑走 1100 ＋ 保険 180 ＋ 敷き替えの遊び 140 の外側。 */
let flightN=0, flightT=0;
function flightBegin(ms){
  clearTimeout(flightT);
  document.body.classList.add('flying');
  const id=++flightN;
  flightT=setTimeout(()=>flightEnd(id),ms||1500);
  return id;
}
function flightEnd(id){
  if(id!==flightN)return;            // もう次の飛行が始まっている＝畳むのは自分の役ではない
  clearTimeout(flightT); flightT=0;
  document.body.classList.remove('flying');
  sheetLock=null;                    // ここから先は、敷かれているかどうかだけで見る
  views=null; applyNow(); markMenuRoom();
}
function flyTo(isl){
  release();
  gatherHide();
  const k=fitK(isl);
  isl._fitK=k;           // 帯の形の物差し（sheetLayout が読む）
  noteRoomURL(isl.room.id,true);
  flightBegin();
  sheetLock=isl.room.id;
  isl._flown=true;     // 岸が着いたら、もう一度だけ寸法を見直してよい印
  // 滑走の間は「出発の眺め」と「到着の眺め」の二つで間引く（通り道が消えないように）
  views=[[PX,PY,K],[-isl.cx*k,-isl.cy*k,k]];
  glide(k,-isl.cx*k,-isl.cy*k);
}
/* ── 宙へもどる（全景）──────────────────────────────────
   島に降りたあと、引き返す道が「つまむ・−を何度も」しか無かった。
   全景の倍率は立ち上がりと同じ式（全島が収まるところ）。滑走の作法は flyTo と同じ。 */
function overview(){
  release();
  gatherHide();
  const k=homeFit();
  noteRoomURL(null,true);
  flightBegin();
  sheetLock=null;        // 行き先は「どの島でもない」＝道中でほどけてよい
  views=[[PX,PY,K],[0,0,k]];
  glide(k,0,0);
}
(function(){
  const b=document.getElementById('skyHome');
  if(b)b.addEventListener('click',()=>overview());
})();
/* 左上の名＝家の印（2026-08-06）。「宙へもどる」は島に降りている間しか立たないので、
   探した結果や読む柱の中からは帰る道が無かった。名はいつでもそこに在る。
   道の上で開いているものは畳んでいく——順は Escape の段と同じ（開いている姿を
   先に閉じ、最後に宙を引く）。ただし Escape と違って一押しで済ませる：家の印は
   「一段だけ戻る」ではなく「帰る」で、二度三度押させるものではない。
   書いている間（body.casting）は頭の帯ごと退いているので、ここには届かない。 */
(function(){
  const b=document.getElementById('brandHome');
  if(!b)return;
  b.addEventListener('click',()=>{
    menuClose();
    const rc=document.getElementById('readClose'), rp=document.getElementById('readPane');
    if(rc&&rp&&rp.classList.contains('on'))rc.click();   // 柱は柱の戸から閉じる
    seekReset();     // 探した語も置いていく（面だけ畳んで語が器に残ると、×だけが宙に立つ）
    overview();      // 受け止めはこの中で放される
  });
})();
/* 戻る＝ひとつ前の眺めへ。島のidが載っていればその島へ、無ければ全景へ。 */
addEventListener('popstate',e=>{
  const r=(e.state&&typeof e.state.room==='number')?e.state.room:urlRoom();
  navQuiet=true;
  try{
    if(r!=null&&islands.has(+r))flyTo(islands.get(+r));
    else overview();
  }finally{ navQuiet=false; }
});
function castLand(isl,poem,color,steps){
  const el=document.createElement('div');
  el.className='w';
  el.style.setProperty('--a','0.92');
  const c=tintOf(color);
  if(c){ el.style.color=c.text; el.style.textShadow='0 0 16px '+c.glow; }
  el.setAttribute('role','button'); el.setAttribute('tabindex','0');
  el.setAttribute('aria-label','tayori-たより- のことば：'+poem);
  el._w={id:null,poem:poem,color:color,vertical:true,room:isl.room.id,sink:1};
  el.style.setProperty('--pp',paperOf(el._w));   // 紙片の紙の色（降りた島で使う）
  snapSize(el);                                  // 紙片の寸法（3値スナップ）
  // 島の心の近く。種は本文＝置き直しても同じ場所（決定論の作法に合わせる）
  const aa=(fnv(poem)%360)*Math.PI/180, rr=40+(fnv(poem+'r')%60);
  el._hx=Math.cos(aa)*rr; el._hy=Math.sin(aa)*rr;
  el._ox=el._hx; el._oy=el._hy;                  // 種の席（relax は毎回ここから解く）
  el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  el.style.zIndex=6;
  isl.wordsEl.appendChild(el);
  isl.words.push(el);
  // 紙片に並んでいる島なら、新しい一枚の席も敷き直す（寸法は本文の丈で測る）
  if(isl.sheeted){ el.textContent=poem; sheetLayout(isl); }
  else{ el.textContent=poem; el._sw=el.offsetWidth; el._sh=el.offsetHeight; }
  playType(el,poem,steps,()=>{});
}
/* 書き直しの下限は、島に降りた時の倍率より**下**に置く（2026-08-06）。
   0.55 は散らばりの姿だった頃の線で、島に降りると K は 1.0 前後だった。いまは
   紙片の帯（8/3）で、降りた先の倍率は fitK が決める——その下限は「字が読めるところ」
   ＝0.42 で、実測でもどの島も 0.42 に貼りついている（携帯でも机の上でも）。
   つまり**島に降りても 0.55 に届かず、書き直しは一度も始まっていなかった**。
   線は fitK の下限のさらに下（0.40）へ。手で引いて字が読めなくなる手前で止まる。 */
setInterval(()=>{
  tick=(tick+1)%4;
  if(REDUCED||document.hidden||K<0.40)return;
  /* 手が動いている間・島へ滑っている間は、字を書き直さない（2026-08-03 カクツキ）。
     書き直しは一コマごとに一枚の版面を組み直す仕事で、宙を動かす仕事といちばん
     混ざってはいけない。静かになってから始めればいい——8秒後にまた来る。 */
  // 滑走中（glide）はレンズを開かない：lens は world に transition を掛ける別経路で、
  // 毎フレームの transform 書きと重なると尻尾の250msほどが泥になる（flying の解除
  // 1300ms より、岸の詰め直しの glide 700ms が後まで走ることがある）
  if(gesOn||glideBusy()||document.body.classList.contains('flying'))return;
  const W=VW,H=VH;
  const due=[];
  islands.forEach(isl=>isl.words.forEach(el=>{
    if(el===held||el._w.pd||el.classList.contains('mutedout'))return;  // 漂流物は書き直さない（誰も打っていない）
    if(fnv(el._w.id)%4!==tick)return;
    const p=screenPos(el);
    if(p.x<-80||p.y<-80||p.x>W+80||p.y>H+80)return;
    due.push({el,d:Math.hypot(p.x-W/2,p.y-H/2)});
  }));
  due.sort((a,b)=>a.d-b.d).slice(0,3).forEach(x=>rotateOne(x.el));
},8000);

/* ── 探す（2026-08-02 に「集まってくる」へ全面差し替え）──────────────
   これまでは「返ってきた片のうち、いま画面に載っているものだけ」を灯していた。
   宙は20万片あって、画面に載っているのは数百——実測で「海」は11件返ってきて
   表示0件、「ありがとう」は10件中3件しか灯らなかった。探せていなかったのは
   索引ではなく、**受け取り方**のほう。
   だから寄せる相手を、灯ではなく片そのものに変える：返ってきたことばは、宙の
   どこに在っても、この面へ飛んできて並ぶ。出どころが画面にあればその場所から、
   無ければ宙の外から。宙の地形（位置の意味）はそのまま、読む場所だけを別に作る。 */
let focusIsl=null;
function seekWatch(){
  const cx=(0-PX)/K, cy=(0-PY)/K;   // 画面中央の世界座標（worldの原点は画面中央）
  /* 敷いてある島の懐は、半径ではなく**束の箱**で見る（2026-08-03 帯）。
     帯は島の半径より縦に長いので、半径の物差し（1.6R）だと、上下の段や
     焦点レンズへ寄っただけで「島を出た」ことになり、読んでいる最中に
     島がほどけた（実際にレンズで起きた）。箱の中に居るかぎり、そこに居る。 */
  /* ただし、行き先が別にあるなら留まらない（2026-08-05 夜）。束の箱は島の間合い
     （輪では700〜1300）より広いことがあるので、隣へ飛んでも「まだ前の島の懐に
     居る」と読めてしまう——そのままだと着いた島がいつまでも敷かれない。 */
  const cur=sheetId!=null?islands.get(sheetId):null;
  if(cur&&cur.sheeted&&cur._bw&&
     !(sheetLock!=null&&sheetLock!==sheetId)&&
     Math.abs(cur.cx-cx)<cur._bw/2+300&&Math.abs(cur.cy-cy)<cur._bh/2+300&&
     cur.R*K>0.20*Math.min(VW,VH)){
    focusIsl={isl:cur,id:sheetId,d:Math.hypot(cur.cx-cx,cur.cy-cy)};
    if(sheetWant!==sheetId){ sheetWant=sheetId; clearTimeout(sheetT); }
    return;
  }
  // 焦点の島＝画面の中心にいちばん近く、その懐（1.6R）に入っている島
  let best=null,bd=1e9;
  islands.forEach((isl,id)=>{
    const d=Math.hypot(isl.cx-cx,isl.cy-cy);
    if(d<bd){bd=d;best={isl,id,d};}
  });
  // 「島に居る」の判定は倍率の絶対値ではなく、**島が視界の大半を占めているか**で見る
  // （K のしきい値は画面の広さで破綻する——424px の電話と 1280px の机では別の倍率になる）
  // 入る線と出る線は分ける（2026-08-02）。降りた先の倍率は束の寸法で決まるので、
  // 中身の多い島では入った直後の倍率が入る線を割ることがある——同じ一本で見ていると、
  // 降りた瞬間に押し出される。出る時はもっと退いてから（ついでに、線の上での
  // ちらつきも消える）。sheetLock は滑走中（まだ敷かれていない）の行き先。
  const stay=best&&(best.id===sheetId||best.id===sheetLock);
  const big=best&&best.isl.R*K>(stay?0.20:0.32)*Math.min(VW,VH);
  let f=(best&&big&&best.d<best.isl.R*1.6)?best:null;
  /* 滑走の道中で「ここに居る」と言えるのは、**行き先だけ**（2026-08-05 夜）。
     K を毎フレーム運ぶようになって、島から島へ飛ぶ1.1秒のあいだ、通り道の島が
     次々と焦点の条件を満たすようになった——数百枚を測って帯に敷き、140ms後に
     ほどく、を通過のたびにやる（実測：21島の総当たり420組のうち164組で成立）。
     足元の名もURLも一瞬その島になり、要らない岸のfetchまで飛ぶ。
     **焦点そのもの（focusIsl）を抑える**のが要点：敷き替えだけを抑えても、
     焦点は「いま居る場所」として他からも読まれている——書く柱を開いた時の
     置き場所（castRoom）と、気配の問い合わせ（?here=）がそれで決まる。
     通りすがっただけの部屋に、ことばが置かれてはいけない。
     行き先（sheetLock）と「どの島でもない」は通す＝降りる・出るは今までどおり。 */
  if(glideBusy()&&f&&f.id!==sheetLock){
    f=(sheetWant!=null&&islands.has(sheetWant))
      ?{isl:islands.get(sheetWant),id:sheetWant,
        d:Math.hypot(islands.get(sheetWant).cx-cx,islands.get(sheetWant).cy-cy)}
      :null;
  }
  focusIsl=f;
  // 降りた島は紙片の敷き詰めに、離れた島は散らばりの家へ（2026-07-31）。
  // 出入りは140ms落ち着いてから：しきい値の上でつまみが揺れると、一回のジェスチャの
  // 中で「全語を測って敷く／ほどく」が毎フレーム往復し、それ自体が固まる原因になる。
  const sid=f?f.id:null;
  if(sid!==sheetWant){
    sheetWant=sid;
    clearTimeout(sheetT);
    sheetT=setTimeout(()=>{
      if(sheetWant===sheetId)return;
      if(sheetId!=null&&islands.has(sheetId))unsheet(islands.get(sheetId));
      sheetId=sheetWant;
      if(sheetId!=null&&islands.has(sheetId))sheet(islands.get(sheetId));
    },140);
  }
  /* 常駐UIの位置は、島の内外で動かない（案A）。かつてはここで「探す行を出す・
     書くを一段上げる」を状態に応じて切り替えていたが、下辺の帯が常にある今、
     出したり動かしたりするものは何も無い。 */
}
/* ── 集まってくる面（.gather）──────────────────────────────────
   片は、島の上の紙片と同じ物として作る（同じ姿・同じ行い）。違うのは、どの島にも
   属さないこと——だから受け止め（hold）は島が無くても通る道にしてある。 */
const gatherEl=document.getElementById('gather'),
      gatherIn=document.getElementById('gatherIn'),
      gatherLead=document.getElementById('gatherLead'),
      gatherCloseBtn=document.getElementById('gatherClose');
let gatherT=0, seekBusy=false;
function mkGather(w){
  const el=document.createElement('div');
  el.className='gw'+(w.vertical?'':' h')+(w.pd?' pd':'');
  el.textContent=w.poem||'';
  // 沈降（薄さ）は宙と同じ値を使う。ただし紙の上では下限を持たせる（読めなくしない）
  el.style.setProperty('--a',Math.max(0.5,Math.min(1,+w.alpha||0.9)).toFixed(3));
  el.style.setProperty('--pp',paperOf(w));
  el.setAttribute('role','button'); el.setAttribute('tabindex','0');
  el.setAttribute('aria-label',(w.pd
    ? '流れ着いたことば：'+w.poem+'（'+w.author+'『'+w.work+'』より）'
    : 'tayori-たより- のことば：'+w.poem)
    +'。ひらくと、棚にとっておく ができます。');
  el._w=w;
  return el;
}
/* 出どころ。いま宙に見えている片ならその場所から、載っていないものは宙の外から
   （向きは本文のハッシュ＝同じことばは毎回同じ方角から来る）。 */
function gatherFrom(by,w){
  const src=by.get(w.id);
  if(src){
    const r=src.getBoundingClientRect();
    if(r.width||r.height)return {x:r.left+r.width/2,y:r.top+r.height/2};
  }
  const a=(fnv(String(w.id||w.poem))%360)*Math.PI/180, R=Math.max(VW,VH)*0.66;
  return {x:VW/2+Math.cos(a)*R, y:VH/2+Math.sin(a)*R};
}
function gatherShow(q,words){
  release();                       // 宙で受け止めていた一枚は、面が立つ前に放す
  clearTimeout(gatherT);
  const by=new Map();
  islands.forEach(isl=>isl.words.forEach(el=>{ if(el._w&&el._w.id)by.set(el._w.id,el); }));
  gatherIn.textContent='';
  const cards=words.map(mkGather);
  cards.forEach(el=>gatherIn.appendChild(el));
  gatherLead.innerHTML='<b>'+esc(q)+'</b> に<wbr>近い<wbr>ことばが、<wbr>集まって<wbr>きました。';
  gatherEl.hidden=false;
  gatherEl.setAttribute('aria-hidden','false');
  gatherEl.classList.add('on');
  seekEl.classList.add('seeking');
  seekBack.hidden=false;
  whisper('');
  if(REDUCED)return;               // 動きを望まない人には、ただ在るだけ
  /* 席を先に測り、出どころへ戻してから飛ばす（FLIP）。測ってから書くまでのあいだに
     描画は挟まらないので、席に居る姿は一度も見えない。 */
  const flip=cards.map(el=>{
    const r=el.getBoundingClientRect();
    const s=gatherFrom(by,el._w);
    return [el,s.x-(r.left+r.width/2),s.y-(r.top+r.height/2)];
  });
  flip.forEach(([el,dx,dy])=>{
    el.classList.add('flying');       // GPUに面を持たせるのは、飛んでいる間だけ
    el.style.transition='none';
    el.style.opacity='0';
    el.style.transform='translate('+dx.toFixed(1)+'px,'+dy.toFixed(1)+'px) scale(.32)';
  });
  // rAF ではなくタイマで戻す（rAFの止まる環境でも、片は必ず席に着く）
  gatherT=setTimeout(()=>{
    flip.forEach(([el],i)=>{
      const d=Math.min(i*0.05,0.6).toFixed(2);
      el.style.transition='transform 1s var(--ease) '+d+'s,opacity .55s var(--ease) '+d+'s';
      el.style.opacity='';
      el.style.transform='';
    });
    // 着いたら面は降ろす（1s の飛翔＋0.6s の遅れの外側で一度だけ）
    setTimeout(()=>flip.forEach(([el])=>el.classList.remove('flying')),1800);
  },24);
}
function gatherHide(){
  if(!gatherEl||gatherEl.hidden)return;
  if(held&&gatherIn.contains(held))release();
  gatherEl.classList.remove('on');
  gatherEl.setAttribute('aria-hidden','true');
  clearTimeout(gatherT);
  gatherT=setTimeout(()=>{ gatherEl.hidden=true; gatherIn.textContent=''; },420);
  seekEl.classList.remove('seeking');
}
function seekReset(){
  gatherHide();
  seekQ.value='';
  seekBack.hidden=true;
  whisper('');
}
/* 探すのは、いつでも宙ぜんたいから（2026-08-02）。島に降りている時だけ島の中を
   探す作法は畳んだ——探している人は「この島の中に在るか」ではなく「宙のどこかに
   在るか」を訊いている。どの島の片かは、集まった紙の色と出典が言う。 */
function seekRun(){
  const q=(seekQ.value||'').trim();
  if(!q||seekBusy)return;
  seekBusy=true;
  seekQ.blur();
  seekEl.classList.add('waiting');   // 選別のぶんだけ待つ（server 側で1秒ほど）
  fetch('/api/sky/search?q='+encodeURIComponent(q))
    .then(r=>r.json().then(d=>({ok:r.ok,d})))
    .then(({ok,d})=>{
      if(!ok||!d.words){
        whisper((d&&d.error)||'いまは、<wbr>寄せられません。',5200);
        return;
      }
      if(!d.words.length){
        whisper('この<wbr>ことばに<wbr>近い<wbr>空気は、<wbr>まだ<wbr>この宙に<wbr>ありません。',6000);
        return;
      }
      gatherShow(q,d.words);
    })
    .catch(()=>{ whisper('いまは、<wbr>寄せられません。',5200); })
    .finally(()=>{ seekBusy=false; seekEl.classList.remove('waiting'); });
}
/* IME変換中の Enter・Escape は横取りしない（2026-07-29 に mood.html で踏んだ轍） */
seekQ.addEventListener('keydown',e=>{
  if(e.isComposing||e.keyCode===229)return;
  if(e.key==='Enter'){ e.preventDefault(); seekRun(); }
  if(e.key==='Escape'){ seekReset(); }
});
seekQ.addEventListener('input',()=>{ seekBack.hidden=!seekQ.value; });
seekBack.addEventListener('click',()=>{ seekReset(); seekQ.focus(); });
document.querySelector('.seek-ic').addEventListener('click',()=>{
  if(seekQ.value.trim())seekRun(); else seekQ.focus();
});
gatherCloseBtn.addEventListener('click',()=>seekReset());
/* 集まった片に触れる＝宙の片に触れるのと同じ（受け止めて、棚にとっておける）。
   #vp の外なので指の捕まえ（setPointerCapture）は無く、click がそのまま届く。 */
gatherIn.addEventListener('click',e=>{
  const el=e.target.closest('.gw');
  if(el)hold(el,e.clientX,e.clientY);
});
gatherIn.addEventListener('keydown',e=>{
  if((e.key==='Enter'||e.key===' ')&&e.target.classList.contains('gw')){
    e.preventDefault(); hold(e.target);
  }
});
// 紙の無いところを触れば、受け止めを放す。もう一度触れば面ごと畳む
gatherEl.addEventListener('click',e=>{
  if(e.target.closest('.gw')||e.target.closest('.gather-top'))return;
  if(held){ release(); return; }
  gatherHide();
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!gatherEl.hidden&&!held)gatherHide();
});

/* ════ 以下、mood.html から移植（付箋・保存先・書く一式）════ */
  /* 保存にまつわる続き（付箋・保存先）は、「近いことば」を分ける罫より **前** に置く。
     後ろに足すと、別の場所へ行く行いを挟んで離れてしまう（2026-07-27 に一度そうなった）。 */
  function padSlot(box){ return box?box.querySelector(':scope > .hold-sep'):null; }
  function putPad(box,el){
    const before=padSlot(box);
    if(before)box.insertBefore(el,before); else box.appendChild(el);
  }
  function dropPad(box){
    const p=box&&box.querySelector(':scope > .fusen-pad');
    if(p)p.remove();
    const w=box&&box.querySelector(':scope > .where-saved');
    if(w)w.remove();
  }
  /* ── 保存したあとの、置き場所（2026-07-27）────────────────────────────
     保存の「前」に棚を選ばせない。一押しで既定の棚へ入り、そのあとに
     「○○に保存しました ▸」だけを置いて、変えたい人だけが変える。
     （書く前に14部屋から選ばせて「何を書くか選ぶところから判断しなきゃ」と
     言われたのと同じ轍を、棚で踏まないため。）
     棚が一つしか無い人には、変える先が無いので出さない——ただし
     「新しい棚へ」だけは出す＝棚を増やす道は塞がない。 */
  function wherePad(box,savedId,shelfId,shelves){
    if(!box||!savedId)return;
    const list=(shelves||[]);
    const now=list.find(s=>String(s.id)===String(shelfId));
    const pad=document.createElement('div');pad.className='where-saved';
    const lead=document.createElement('span');lead.className='where-lead';
    lead.textContent=(now?now.name:'棚')+'に保存しました';
    const btn=document.createElement('button');
    btn.type='button';btn.className='where-change';btn.textContent='ほかの棚へ';
    const pick=document.createElement('div');pick.className='where-pick';pick.hidden=true;
    btn.addEventListener('click',()=>{
      pick.hidden=!pick.hidden;
      btn.setAttribute('aria-expanded',pick.hidden?'false':'true');
    });
    btn.setAttribute('aria-expanded','false');
    async function moveTo(payload,label){
      try{
        const r=await fetch('/api/shelf/'+savedId+'/move',{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await r.json().catch(()=>null);
        if(!(r.ok&&d&&d.ok))throw new Error('move');
        lead.textContent=label+'に保存しました';
        pick.hidden=true;btn.setAttribute('aria-expanded','false');
        // 棚が増えていることがあるので、選び直しの一覧も作り直す
        buildPick(d.shelves||list,d.shelf);
      }catch(e){ whisper('いま、<wbr>棚に<wbr>手が<wbr>届きませんでした。',6000); }
    }
    function buildPick(all,curId){
      pick.innerHTML='';
      all.filter(s=>String(s.id)!==String(curId)).forEach(s=>{
        const b=document.createElement('button');
        b.type='button';b.className='where-opt';b.textContent=s.name;
        b.addEventListener('click',()=>moveTo({shelf:s.id},s.name));
        pick.appendChild(b);
      });
      /* 新しい棚は、その場で名前を書いて作る（付箋の入力と同じ作法）。
         確認の面を立てない——ここは戻せる行いなので、問いを挟むほどではない。 */
      const nin=document.createElement('input');
      nin.type='text';nin.className='where-new';nin.maxLength=24;nin.autocomplete='off';
      nin.placeholder='新しい棚の名前';
      nin.setAttribute('aria-label','新しい棚の名前');
      nin.addEventListener('keydown',e=>{
        if(e.key!=='Enter'||e.isComposing)return;
        e.preventDefault();
        const v=nin.value.trim();
        if(v)moveTo({name:v},v);
      });
      pick.appendChild(nin);
    }
    buildPick(list,shelfId);
    pad.appendChild(lead);pad.appendChild(btn);pad.appendChild(pick);
    putPad(box,pad);
  }
  /* 注意：先頭で dropPad するので、保存先（.where-saved）を置くのは **このあと**。
     順を逆にすると、置いたそばから消える（2026-07-27 に一度踏んだ）。 */
  function fusenPad(box,savedId,initial){
    dropPad(box);
    if(!box||!savedId)return;
    const pad=document.createElement('div');pad.className='fusen-pad';
    const row=document.createElement('div');row.className='fusen-row';
    const inp=document.createElement('input');
    inp.type='text';inp.className='fusen-in';inp.maxLength=24;inp.autocomplete='off';
    inp.placeholder='付箋を貼る（3枚まで・そのままでも）';
    inp.setAttribute('aria-label','この控えに貼る付箋');
    const sug=document.createElement('div');sug.className='fusen-sug';sug.hidden=true;
    let tags=(initial||[]).slice(),sugT=0,busy=false;
    function paint(){
      row.innerHTML='';
      tags.forEach(t=>{
        const b=document.createElement('button');
        b.type='button';b.className='fusen';
        b.innerHTML='<span></span><span class="x" aria-hidden="true">×</span>';
        b.firstChild.textContent=t;
        b.setAttribute('aria-label','付箋「'+t+'」をはがす');
        b.addEventListener('click',()=>post({remove:t}));
        row.appendChild(b);
      });
      inp.style.display=tags.length>=3?'none':'';
      if(tags.length>=3)sug.hidden=true;
    }
    async function post(payload){
      if(busy)return; busy=true;
      try{
        const r=await fetch('/api/shelf/'+savedId+'/tags',{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await r.json().catch(()=>null);
        if(r.ok&&d&&d.ok){tags=d.tags||[];paint();}
      }catch(e){ whisper('いま、<wbr>棚に<wbr>手が<wbr>届きませんでした。',6000); }
      finally{ busy=false; }
    }
    function commit(raw){
      const t=String(raw||'').replace(/^[#＃]+/,'').trim().toLowerCase().slice(0,24);
      inp.value='';sug.hidden=true;
      if(!t||tags.indexOf(t)>=0||tags.length>=3)return;
      post({tags:[t]});
    }
    inp.addEventListener('keydown',e=>{
      if(e.isComposing)return;
      if(e.key==='Enter'||e.key===','||e.key==='、'){e.preventDefault();commit(inp.value);}
    });
    inp.addEventListener('input',()=>{
      clearTimeout(sugT);
      sugT=setTimeout(()=>{
        fetch('/api/tags/suggest?q='+encodeURIComponent(inp.value))
          .then(r=>r.ok?r.json():null).then(d=>{
            if(!d)return;
            sug.innerHTML='';
            (d.tags||[]).filter(t=>tags.indexOf(t)<0).slice(0,6).forEach(t=>{
              const b=document.createElement('button');
              b.type='button';b.className='fusen';b.textContent=t;
              b.addEventListener('mousedown',e=>e.preventDefault());  // blurで消える前に拾う
              b.addEventListener('click',()=>commit(t));
              sug.appendChild(b);
            });
            sug.hidden=!sug.children.length;
          }).catch(()=>{});
      },250);
    });
    inp.addEventListener('blur',()=>setTimeout(()=>{sug.hidden=true;},150));
    pad.appendChild(row);pad.appendChild(inp);pad.appendChild(sug);
    putPad(box,pad);
    paint();
  }
(function(){
  if(!LOGGED_IN) return;
  /* ── 放つ（宙の中で書く）── */
  const pane=document.getElementById('castPane'),btn=document.getElementById('castBtn'),
        ta=document.getElementById('castText'),cnt=document.getElementById('castCount'),
        cntBox=document.getElementById('castCountBox'),
        capEl=document.getElementById('castCap'),
        send=document.getElementById('castSend'),close=document.getElementById('castClose'),
        hueBar=document.getElementById('hueBar'),hueKnob=document.getElementById('hueKnob'),
        toneBar=document.getElementById('toneBar'),toneKnob=document.getElementById('toneKnob'),
        pickerNow=document.getElementById('pickerNow'),
        pickerDot=document.getElementById('pickerDot'),
        castSum=document.getElementById('castSum'),
        vcaret=document.getElementById('castCaret');
  /* 置き場所は下の renderWhere 一式が持つが、宣言だけ先に出しておく（2026-07-31）。
     要約の一行（S6）は色・向き・置き場所のどれが動いても描き直すので、
     色の paint() と向きの paintDir() からも呼ばれる——どちらも読み込み直後に
     一度走るので、let のまま下に置くと初期化前参照になる。 */
  let castRoom=null;
  let horiz=false;      // 書く向き（下の castDir 一式が持つ）。理由は castRoom と同じ

  /* ── 気分の色（v12）──
     色相 × トーン（淡い⇄鮮やか⇄深い）の二段。宙は真っ暗なので明度の下限は46%まで。
     v12で「色をえらばない」を畳んだ：放たれたことばには必ず色がのる。
     触らなければ既定の色（藍寄りの淡い青）のまま放たれる＝書く手は止まらない。 */
  const H0=210, T0=0.28;      // 既定＝淡いほうへ寄せた青。宙の闇でも読める明るさ
  let H=H0, T=T0;
  /* 色に「触れた」か（2026-07-28）。触れていない既定色は air の色項に入れない
     （サーバ側 seal_color_chosen）——選んでいない色を発言として流通させないため。
     箱を開いただけでは立たない。帯を動かした（指でも矢印キーでも）時だけ立つ。 */
  let colorTouched=false;
  function toneColor(h,p){
    let s,l;
    if(p<0.5){ const k=p/0.5;       s=24+(78-24)*k; l=88+(64-88)*k; }   // 淡い → 鮮やか
    else{      const k=(p-0.5)/0.5; s=78+(66-78)*k; l=64+(46-64)*k; }   // 鮮やか → 深い
    return {h:h,s:Math.round(s),l:Math.round(l)};
  }
  const cssOf=c=>'hsl('+c.h+', '+c.s+'%, '+c.l+'%)';
  /* 色の名。読み上げのためと、いま選んでいる色を返すため（2026-07-27）。
     凡例（色相／彩度…）ではなく結果の名前なので、宙に説明を置かない約束は破らない。
     境目は色相環をそのまま日本語の色名に割ったもので、厳密な色彩学ではなく耳で分かる粗さ。 */
  function hueName(h){
    const t=[[15,'赤'],[45,'橙'],[70,'黄'],[95,'黄緑'],[155,'緑'],[190,'青緑'],
             [235,'青'],[265,'藍'],[290,'紫'],[330,'桃'],[361,'赤']];
    for(const [to,name] of t) if(h<to) return name;
    return '赤';
  }
  function toneName(p){ return p<0.34?'淡い':(p<0.67?'鮮やかな':'深い'); }
  function paint(){
    const c=toneColor(H,T), col=cssOf(c);
    hueKnob.style.left=(H/360*100)+'%';
    hueKnob.style.background='hsl('+H+',80%,60%)';
    toneKnob.style.left=(T*100)+'%';
    toneKnob.style.background=col;
    // background ではなく backgroundImage に入れる：短縮形は background-clip を
    // 初期値に戻すので、帯が padding いっぱいまで太って「巨大な色の板」になる（v14で踏んだ）
    toneBar.style.backgroundImage='linear-gradient(90deg,hsl('+H+',24%,88%),hsl('+H+',78%,64%),hsl('+H+',66%,46%))';
    hueBar.setAttribute('aria-valuenow',H);
    toneBar.setAttribute('aria-valuenow',Math.round(T*100));
    /* 数値だけ読み上げても色は分からない。いま選んでいる色の名を添える
       （見えない人にとって、この帯は「0〜360の数字」ではなく色そのものなので）。 */
    hueBar.setAttribute('aria-valuetext',hueName(H));
    toneBar.setAttribute('aria-valuetext',toneName(T)+'　'+hueName(H));
    // 見えている人にも同じことばで返す（帯に触れた手応えが、名前として残る）
    if(pickerNow){ pickerNow.textContent=toneName(T)+'　'+hueName(H); pickerNow.style.color=col; }
    // 区画見出しの右端の見本。丸の内側だけを塗る（縁は紙の白のまま・S4）
    if(pickerDot) pickerDot.style.background=col;
    // 選んだ色は、書いている文字にそのままのる（宙に放たれた後と同じ見え方に）
    ta.style.color=col;
    ta.style.textShadow='0 0 22px hsla('+c.h+','+c.s+'%,'+c.l+'%,.45)';
    vcaret.style.background=col;      // 筆先も同じ色（ネイティブは透明にしてある）
    /* 選んだ色だけが、書く面の空気になる。触れていない既定色は空気にしない
       （air の色項と同じ線。放ったあとの colorTouched=false → paint() でここが畳む）。 */
    if(colorTouched){ pane.style.setProperty('--mood-air','hsla('+c.h+','+c.s+'%,'+c.l+'%,.07)'); }
    else{ pane.style.removeProperty('--mood-air'); }
    paintSum();      // 要約の一行にも、いまの色の名を返す（S6）
  }
  function wireBar(bar,onPick){
    const pick=ev=>{
      const r=bar.getBoundingClientRect();
      onPick(Math.max(0,Math.min(1,(ev.clientX-r.left)/(r.width||1))));
    };
    bar.addEventListener('pointerdown',e=>{bar.setPointerCapture(e.pointerId);pick(e);});
    bar.addEventListener('pointermove',e=>{if(e.buttons)pick(e);});
  }
  wireBar(hueBar,p=>{H=Math.round(p*360)%360;colorTouched=true;paint();});
  wireBar(toneBar,p=>{T=p;colorTouched=true;paint();});
  /* 帯のキー操作。role=slider が約束している一式（←→・↑↓・Home/End・PageUp/Down）を
     全部受ける。v2 までは ←→ だけで、上下キーを押すと画面が送られて帯から離れていた
     （＝同じ形のものが同じに振る舞わない／Apple §16 familiarity）。 */
  function wireKeys(bar,step,jump,apply){
    bar.addEventListener('keydown',e=>{
      const k=e.key;
      let d=0,abs=null;
      if(k==='ArrowRight'||k==='ArrowUp')d=step;
      else if(k==='ArrowLeft'||k==='ArrowDown')d=-step;
      else if(k==='PageUp')d=jump;
      else if(k==='PageDown')d=-jump;
      else if(k==='Home')abs=0;
      else if(k==='End')abs=1;
      else return;
      e.preventDefault();
      apply(d,abs);
      colorTouched=true;   // 矢印キーで動かすのも「触れた」（2026-07-28）
      paint();
    });
  }
  wireKeys(hueBar,6,30,(d,abs)=>{
    H=(abs===null)?(H+d+360)%360:Math.round(abs*359);});
  wireKeys(toneBar,0.05,0.2,(d,abs)=>{
    T=(abs===null)?Math.max(0,Math.min(1,T+d)):abs;});
  paint();   // 既定の色を最初から文字とつまみにのせておく

  /* ── 縦書きの筆先（自前カーソル・v13.1／v16でcontenteditable化／v17で測り方を入れ替え）──
     縦書きのネイティブカーソルは、行ボックス（列幅）ではなく字面（font-size）に連動して
     縮む。しかも列の中でどこに立つかが定まらないので、書いている位置が揺れて見えていた。
     カーソルの寸法を指定するCSSは存在しない（caret-color は色だけ）ので、ネイティブは
     透明にして、列幅いっぱいの横棒を入力位置に重ねる（便箋＝index.html と同じ作法）。

     v17：**測るために本文を触らない**。これがIME中に字が重なる不具合の原因だった。
     v16は「collapsed な Range が矩形を返さない環境」の保険として幅ゼロのマーカーを一瞬だけ
     挿していたが、実際には collapsed Range は *要素境界*（＝紙が空のとき・本文の末尾にいるとき
     ＝書いている間ずっと）で必ず空を返すので、保険が常用経路になっていた。しかも最後に
     選択を入れ直す＝selectionchange が飛ぶ→drawCaret→また挿す、の自走ループで、
     何もしていなくても毎秒約100往復ぶん本文のテキストノードを割って繋ぎ直していた。
     IMEは変換中「どのノードの何字目か」を握っているので、その割り直しで参照が古くなり、
     変換中の字が元の位置に描き直される＝**字が重なって残る**。

     代わりに、隣り合う一字を囲んだ **collapsedでない Range** で測る。これはどの環境でも
     素直に矩形を返し、DOMには一切触らない。字の矩形は字面ぶんしかない（列幅より狭い）ので、
     中心から「右から何列目か」を割り出して列の左端に吸い付かせる。 */

  // キャレットが本文の何字目にあるか。要素境界でもテキストノードでも同じ数に落ちる
  function caretIndex(){
    const sel=window.getSelection();
    if(!sel||!sel.rangeCount)return null;
    const r=sel.getRangeAt(0);
    if(!ta.contains(r.endContainer))return null;
    const pre=document.createRange();
    pre.selectNodeContents(ta);
    try{ pre.setEnd(r.endContainer,r.endOffset); }catch(e){ return null; }
    return pre.toString().length;
  }
  // 選んでいる範囲を字数で返す [はじめ, おわり]。caretIndex と同じ数え方（Range.toString は
  // リテラルの改行もそのまま数えるので、textContent の添字とずれない）。
  function selSpan(){
    const sel=window.getSelection();
    if(!sel||!sel.rangeCount)return null;
    const r=sel.getRangeAt(0);
    if(!ta.contains(r.startContainer)||!ta.contains(r.endContainer))return null;
    const a=document.createRange(),b=document.createRange();
    a.selectNodeContents(ta);b.selectNodeContents(ta);
    try{ a.setEnd(r.startContainer,r.startOffset);b.setEnd(r.endContainer,r.endOffset); }
    catch(e){ return null; }
    return [a.toString().length,b.toString().length];
  }
  // 本文の pos 字目 → [テキストノード, その中の位置]（変換中はノードが分かれていることがある）
  function pointAt(pos){
    const w=document.createTreeWalker(ta,NodeFilter.SHOW_TEXT);
    let n,acc=0,last=null;
    while(n=w.nextNode()){
      const len=n.nodeValue.length;
      if(pos<=acc+len)return [n,pos-acc];
      acc+=len;last=n;
    }
    return last?[last,last.nodeValue.length]:null;
  }
  // k字目の一字を囲んだ矩形。一字ぶんの幅があるので getClientRects() は空を返さない
  function charRect(k){
    const a=pointAt(k),b=pointAt(k+1);
    if(!a||!b)return null;
    const r=document.createRange();
    try{ r.setStart(a[0],a[1]);r.setEnd(b[0],b[1]); }catch(e){ return null; }
    const rects=r.getClientRects();
    return rects.length?rects[0]:null;
  }
  /* 描くのは同期で。v17の途中で「一打鍵につき input と selectionchange が両方飛ぶから」と
     rAFで1フレームに束ねたが、フレームが来ない状態（裏タブ・非表示）だと予約が消化されず
     筆先が止まったままになるのを検証で踏んだ。測るだけで本文も選択も触らないので、
     二度描いても副作用はない＝素直に毎回描くほうが安全。 */
  /* 一打鍵で三度描いていた（2026-08-03 カクツキ）。input・keyup・selectionchange は
     どれも同じ一打鍵の別々の知らせで、しかも一回ごとに getComputedStyle と
     Range の実測が走る。姿（本文・筆先の位置・向き）が前と同じなら、描くものは
     何も変わらない——変わった時だけ描く。 */
  let caretKey='';
  function drawCaret(){
    const k=(horiz?'h':'v')+'|'+(document.activeElement===ta?'1':'0')
            +'|'+caretIndex()+'|'+ta.textContent;
    if(k===caretKey)return;
    caretKey=k;
    drawCaretNow();
  }
  function drawCaretNow(){
    // 横書きの間はネイティブのカーソルが素直に出るので、自前の横棒は出さない
    if(horiz||document.activeElement!==ta){vcaret.style.display='none';return;}
    const cs=getComputedStyle(ta);
    const cw=parseFloat(cs.lineHeight)||36;                     // 列幅＝カーソルの長さ
    const box=ta.getBoundingClientRect(), origin=ta.parentNode.getBoundingClientRect();
    const x0=box.left-origin.left, y0=box.top-origin.top, w=box.width, h=box.height;
    const n=ta.textContent.length;
    let i=caretIndex();
    if(i===null){vcaret.style.display='none';return;}
    i=Math.max(0,Math.min(i,n));
    // 字の矩形の中心から「右から何列目か」を数え、その列の左端へ。vertical-rl は右から埋まる
    const colLeft=r=>{
      const c=Math.floor((box.right-(r.left+r.width/2))/cw);
      return box.right-(c+1)*cw-origin.left;
    };
    let x,y;
    const text=ta.textContent;
    if(n===0){                       // 空の便箋：右端の列の頭に置く（罫は4本立っている）
      x=x0+w-cw; y=y0;
    }else if(i>0&&text[i-1]==='\n'){
      /* 改行の直後＝新しい列の頭（2026-07-27）。この行にはまだ一字も無いので、
         測れる字が無い。改行そのものの矩形は「終わらせた行」の列に高さ0で出る
         （実測）ので、その列からひとつ左へ送った所が、いま筆先の立つ場所。
         連続した改行でも、直前の一つを見れば足りる——二つ目の改行の矩形は、
         一つ目が作った空の列に出るため（実測：0列目→1列目）。
         この枝が無いと、末尾の分岐が改行の矩形を「最後の字」として測り、
         筆先が前の列に居残ったまま動かない。 */
      const r=charRect(i-1);
      if(!r){vcaret.style.display='none';return;}
      x=colLeft(r)-cw; y=y0;
    }else if(i<n){                   // 途中：これから書かれる字の頭
      const r=charRect(i);
      if(!r){vcaret.style.display='none';return;}
      x=colLeft(r); y=r.top-origin.top;
    }else{                           // 末尾：最後の字のすぐ下。列が尽きていれば次の列の頭へ
      const r=charRect(n-1);
      if(!r){vcaret.style.display='none';return;}
      x=colLeft(r); y=r.bottom-origin.top;
      // 遊びは字送りの半分。列がぴったり埋まった時だけ送る（禁則で20字に満たない列もある）
      const adv=r.height||parseFloat(cs.fontSize)||17;
      if(r.bottom+adv/2>box.bottom){ x-=cw; y=y0; }
    }
    y-=1;                            // 2px の横棒を字と字の境の上に重ねる
    // 80字＝ちょうど4列。埋まり切ると「次の字の場所」は5列目＝紙の外になるので、
    // 最後の列の終わり（左端の列の下）に置く。筆先が紙から出ていかないための留め。
    if(x<x0-0.5){ x=x0; y=y0+h-3; }
    x=Math.min(Math.max(x,x0),x0+w-cw);
    y=Math.min(Math.max(y,y0),y0+h-3);
    vcaret.style.width=cw+'px';
    vcaret.style.left=x.toFixed(1)+'px';
    vcaret.style.top=y.toFixed(1)+'px';
    vcaret.style.display='block';
    /* 打鍵の直後は必ず点いている状態から数え直す（書いている最中に消えていると不安になる）。
       やり方は `animation:none` →**強制的に版面を確定させて**→戻す、だった。この
       `offsetHeight` の一行は、一打鍵ごとに文書ぜんぶの版面を組み直させる合図で、
       書く柱の下には宙の数百のことばがそのまま生きている。同じことは、時計を
       頭へ戻すだけでできる（版面には触らない）。 */
    const an=vcaret.getAnimations?vcaret.getAnimations():null;
    if(an&&an.length){ an.forEach(a=>{ try{a.currentTime=0;}catch(_){} }); }
    else{ vcaret.style.animation='none';void vcaret.offsetHeight;vcaret.style.animation=''; }
  }
  // 変換中も measure は読むだけなので、筆先は変換の字を追ってよい
  ['input','click','keyup','focus','scroll',
   'compositionstart','compositionupdate','compositionend']
    .forEach(ev=>ta.addEventListener(ev,drawCaret));
  // 自前で筆先を消した時は、控えも捨てる（次に同じ姿で戻ってきても、必ず描き直す）
  ta.addEventListener('blur',()=>{vcaret.style.display='none';caretKey='';});
  document.addEventListener('selectionchange',()=>{if(document.activeElement===ta)drawCaret();});
  addEventListener('resize',()=>{if(document.activeElement===ta)drawCaret();});

  /* ── 本文の取り出し・上限・改行の作法（v16：contenteditable／2026-07-27 改行を通す）──
     Enter で行が変わるようにした。ここには落とし穴が二つあって、両方踏まないと直らない。

     ①【execCommand は改行を飲む】`document.execCommand('insertText',false,'\n')` は
       リテラルの改行を入れず、`<div><br></div>` という**箱**に化ける。そして
       `textContent` は <br> を数えないので、画面には行が変わって見えるのに
       取り出した本文からは改行が消えている——「改行したのに反映されない」の正体。
       （実測：'あ'+execCommand('\n')+'い' → textContent は "あい"／長さ2）
       なので改行は execCommand に頼らず、**リテラルの改行文字のテキストノード**を
       自分で差す（insertPlain）。便箋は white-space:pre-wrap なので、それだけで行が変わる。
       本文がただの文字列のままなら、字数・筆先・打鍵の記録の全部が今までの理屈で動く。
     ②【紙は4列しかない】便箋は 20字×4列で寸法固定・overflow:hidden。改行は列を送るので、
       字数が80に届かなくても列が尽きる（実測：3字×5行＝19字で5列目が紙の外に出て消えた）。
       だから上限は字数ではなく**列**で見る（fitText）。

     IME変換中（isComposing）はEnterが変換の確定に使われるので横取りしない。
     貼り付けは書式を持ち込まずプレーンテキストだけ差し込む——改行は保つ（①と同じ理由で
     execCommand は使えないので、こちらも insertPlain を通す）。 */
  let composing=false;
  ta.addEventListener('compositionstart',()=>{composing=true;});
  ta.addEventListener('compositionend',()=>{composing=false;enforceLimit();syncTail();});

  /* ── 末尾の改行の番人（2026-07-27）──────────────────────────
     ③【末尾の改行は食われる】本文の最後がリテラルの改行だと、次の一字を打った瞬間に
       編集エンジンがその改行を落とす（実測：Enter のあと "forget\n" → "travel" と
       打つと "forgettravel" になり、改行が消える）。行末の空白は畳んでよい、という
       HTML編集の作法がそのまま効いてしまうため。
     防ぎ方は、改行を「末尾」でなくすこと——本文が改行で終わっている間だけ、
     いちばん後ろに <br> をひとつ立てておく。<br> は textContent に数えられないので
     本文の値は何も変わらないまま、改行だけが守られる（実測：番人つきなら
     "forget\n" → 打っても "forget\ntravel" のまま残る）。
     字が続いたら番人は退く（役目は終わり）。 */
  function syncTail(){
    const ends=ta.textContent.slice(-1)==='\n';
    const last=ta.lastChild, hasBr=!!last&&last.nodeName==='BR';
    if(ends&&!hasBr)ta.appendChild(document.createElement('br'));
    else if(!ends&&hasBr)ta.removeChild(last);
  }
  /* キャレットの所へ、素の文字をそのまま差す。DOMを直に触るので input は飛ばない
     ＝呼んだ側が afterEdit() を回すこと。 */
  function insertPlain(s){
    const sel=window.getSelection();
    if(!sel||!sel.rangeCount)return;
    const r=sel.getRangeAt(0);
    if(!ta.contains(r.startContainer))return;
    r.deleteContents();
    const n=document.createTextNode(s);
    r.insertNode(n);
    // 差した字の後ろへ筆先を送る
    const a=document.createRange();a.setStart(n,s.length);a.collapse(true);
    sel.removeAllRanges();sel.addRange(a);
    // 直前・直後のテキストノードと繋いでおく（節が増えると測りが散らかる）
    ta.normalize();
  }
  ta.addEventListener('keydown',e=>{
    if(e.key!=='Enter'||e.isComposing||composing)return;
    e.preventDefault();
    /* 入れてみたら紙に収まるか、先に確かめる。ここで見ずに差してから enforceLimit に
       任せると、はみ出した分＝**書いた本人の字の末尾**が黙って落ちる。
       紙が尽きている時は、何も起きないほうがいい（字は消えない）。 */
    const t=ta.textContent, sp=selSpan();
    const a=sp?sp[0]:t.length, b=sp?sp[1]:t.length;
    const next=t.slice(0,a)+'\n'+t.slice(b);
    if(fitText(next).len<next.length)return;   // 紙に次の列が無い
    insertPlain('\n');
    afterEdit();drawCaret();
  });
  ta.addEventListener('paste',e=>{
    e.preventDefault();
    const text=(e.clipboardData||window.clipboardData).getData('text')
                 .replace(/\r\n?/g,'\n').replace(/[\u2028\u2029]/g,'\n');
    insertPlain(text);
    afterEdit();drawCaret();
  });
  function normalizeEmpty(){
    // 変換中は触らない：IMEが握っているノードを消すと、変換そのものが壊れる（v17）
    if(composing)return;
    if(ta.textContent==='')ta.innerHTML='';   // <br>だけ残ると :empty が成立しない
  }
  /* ── 紙の容れる分（2026-07-27）───────────────────────────────
     便箋は 20字 × 4列。改行を通したので、上限を字数だけで見ると紙からはみ出す
     （実測：3字×5行＝19字で、5列目が紙の左端の外に出て overflow:hidden に消えた）。
     数えるのは**列**。返すのは
       len   … 紙に収まる先頭からの長さ（ここで切る）
       slots … いま紙をどれだけ使ったか（(列-1)*20 + その列の字数）
     改行はひとつで列をひとつ送る。ただし列がちょうど埋まっている時は、
     折返しがすでに列を送っているので二重には送らない——これは決めごとではなく
     ブラウザの実測（20字＋改行＋字 の字は2列目に立つ／3列目ではない）に合わせてある。
     字送りは書記素ではなくコードポイントで進める（サロゲートペアを割らない）。 */
  /* 罫は1本20字。本数はプランで決まる（無料80字＝4本／有料120字＝6本）。
     紙の幅（CSSの --ccol）と、打鍵を止める線（MAX_COL）は同じ数から出す
     ——別々に持つと、片方だけ直した日に「罫はあるのに打てない」紙ができる。 */
  const CH_PER_COL=20, MAX_COL=Math.max(1,Math.ceil(FEATURES.poem_max/CH_PER_COL));
  ta.style.setProperty('--ccol',MAX_COL);
  function fitText(t){
    let col=0,run=0,i=0;
    for(const ch of t){
      if(ch==='\n'){
        if(col+1>=MAX_COL)return {len:i,slots:col*CH_PER_COL+run};   // 次の列が紙の外
        col++;run=0;
      }else{
        if(run>=CH_PER_COL){
          if(col+1>=MAX_COL)return {len:i,slots:col*CH_PER_COL+run};
          col++;run=0;
        }
        run++;
      }
      i+=ch.length;
    }
    return {len:i,slots:col*CH_PER_COL+run};
  }
  function enforceLimit(){
    const t=ta.textContent;
    const cap=fitText(t).len;
    if(t.length<=cap)return;
    // textContent を入れ替えると選択は必ず失われるので、字数で覚えて字数で戻す。
    // v16は「末尾にいるか」をノード比較で見ていたが、キャレットが要素境界にいる時
    // （＝末尾で書いている時そのもの）は必ず外れ、カーソルが先頭へ飛んでいた。
    const i=caretIndex();
    ta.textContent=t.slice(0,cap);
    const p=pointAt(Math.min(i===null?cap:i,cap));
    if(p){
      const r=document.createRange();r.setStart(p[0],p[1]);r.collapse(true);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(r);
    }
    normalizeEmpty();
  }

  /* ── 書いた分だけ、画面が応える（Apple §1・§16）───────────────────
     ・一字も無いあいだ「放つ」は押せない。以前は常に押せたが、押しても何も起きなかった
       （＝死んだ一押し。壊れているのと見分けがつかない）。姿は .cast-send:disabled が既に持つ。
     ・紙が尽きたら打鍵は静かに止まる。止まる理由を、字数の色だけで伝える。
       数えるのは「紙をどれだけ使ったか」（slots）で、字数そのものではない（2026-07-27）。
       改行を通したので、この二つは一致しなくなった——改行はその列の残りを畳んで
       次の列へ送るので、19字でも紙が尽きることがある。止めるのは紙のほうなのだから、
       出す数も紙のほうに合わせる。改行を使わなければ slots は字数と同じ値なので、
       これまでの見え方は何も変わらない。 */
  function paintCast(){
    const n=fitText(ta.textContent).slots, cap=CH_PER_COL*MAX_COL;
    if(capEl&&capEl.textContent!==String(cap))capEl.textContent=cap;
    cnt.textContent=n;
    cntBox.classList.toggle('near',n>=cap-12&&n<cap);
    cntBox.classList.toggle('full',n>=cap);
    // ことばがあり、置き場所も決まって初めて放てる（サーバも部屋を必須にしている）
    send.disabled=!ta.textContent.trim()||!castRoom;
    paintSum();
  }
  /* 放つ直前の要約（S6）。区画の順（どこへ／いろ／かたち）でそのまま並べる。
     置き場所がまだなら、そこだけ「どこへ、まだ」——放てない理由が一行で分かる
     （「放つ」が押せない姿と同じことを、ことばの側からも言う）。 */
  function paintSum(){
    if(!castSum)return;
    const room=(ROOMS||[]).find(r=>String(r.id)===String(castRoom));
    castSum.textContent=[room?(room.name+' へ'):'どこへ、まだ',
                         toneName(T)+hueName(H),
                         horiz?'横書き':'縦書き'].join(' ／ ');
  }
  /* 打鍵イベント（TypeTrace／v2追補 §1）。打った過程は——消した文字も含めて——
     そのまま宙に流れる（そのことは書き始める前に .cast-note で明示してある）。
     ・記録は3文字目が入った時点から。それ以前に消してやり直した分は永久に残らない
       ＝書き手の最初のためらいは保護する。
     ・カーソル移動は追わない。末尾以外の編集は、その時点の全文スナップショット
       （op:'s'）で代替する。
     形式は [dt, op, ch]。dt=直前イベントからのms差分、op: i=打つ / d=消す / s=全文。 */
  let trace=[],traceLast=0,tracePrev='';
  function recTrace(){
    const now=performance.now(),text=ta.textContent;
    if(!trace.length){
      if([...text].length<3){tracePrev=text;return;}
      trace.push([0,'s',text]);traceLast=now;tracePrev=text;return;
    }
    if(text===tracePrev)return;
    if(trace.length>=3000){tracePrev=text;return;}   // 暴走の上限。再生は最後に全文へ整う
    const dt=Math.max(0,Math.round(now-traceLast));traceLast=now;
    const old=tracePrev;tracePrev=text;
    // 共通の頭を測る（サロゲートペアの途中では切らない）
    let p=0;const n=Math.min(old.length,text.length);
    while(p<n&&old.charCodeAt(p)===text.charCodeAt(p))p++;
    if(p>0){const c=text.charCodeAt(p-1);if(c>=0xD800&&c<=0xDBFF)p--;}
    const cut=old.slice(p),add=text.slice(p);
    // 消えた側と増えた側が同じ字で終わる＝変化は末尾ではなく途中で起きた → 全文で代替
    if(cut&&add&&cut[cut.length-1]===add[add.length-1]){trace.push([dt,'s',text]);return;}
    let first=dt;
    for(const ch of [...cut].reverse()){trace.push([first,'d',ch]);first=0;}
    for(const ch of add){trace.push([first,'i',ch]);first=0;}
  }
  /* 本文が変わったあとに、いつも同じ順で回すもの。打鍵（input）からも、
     自分でDOMへ差した時（Enter・貼り付け＝input が飛ばない）からも同じ道を通す。 */
  function afterEdit(){
    normalizeEmpty();
    if(!composing)enforceLimit();
    syncTail();        // enforceLimit は textContent を入れ替える＝番人ごと消えるので、必ずその後
    paintCast();
    recTrace();
    traceDot.hidden=!trace.length;   // 記録の在る無しだけを見る（open/shutに紐づけると状態とズレる）
  }
  ta.addEventListener('input',afterEdit);
  /* 柱を開いた／閉じたときのフォーカス。閉じたら「放つ」へ返す（来た道から出る／Apple §7）。
     書きかけの本文は消さない——閉じるのは取り消しではない（Apple §2 forgiveness）。 */
  function open(){release();                          // 受け止めていたことばは、そっと放す
                  gatherHide();                       // 集まってきた片も畳む（柱はその下に立つ）
                  pane.classList.add('on');pane.setAttribute('aria-hidden','false');
                  document.body.classList.add('casting');   // ロゴの戸は退く（S6 のとじると重なる）
                  btn.classList.add('away');ta.focus();
                  // 焦点の島に居るなら、そこが置き場所。遠景から開いたなら、まだ決めない
                  castRoom=(focusIsl&&focusIsl.isl.room&&!focusIsl.isl.room.archived)?focusIsl.id:null;
                  renderWhere();
                  /* 名前は取り直してから並べる（2026-07-28 の作法のまま）。
                     この画面を開いたままの間に増えたジャンルが書く柱にだけ出てこない、を防ぐ。
                     届いたら静かに並べ直す（castRoom は触らない＝選んだ所は動かさない）。 */
                  fetch('/api/rooms').then(r=>r.ok?r.json():null).then(d=>{
                    if(d&&d.rooms){ ROOMS=d.rooms;
                      if(pane.classList.contains('on'))renderWhere(); }
                  }).catch(()=>{});
                  paintCast();
                  /* 告知（S7）は初めて開いた時だけ展開して立てる。二度目以降は畳んだ姿 */
                  paintAbout(!toldOnce);
                  if(!toldOnce){ toldOnce=true;
                    try{ localStorage.setItem(NOTE_KEY,'1'); }catch(_){} }
                  requestAnimationFrame(drawCaret);}   // 柱が立った直後から筆先を置く
  function shut(){pane.classList.remove('on');pane.setAttribute('aria-hidden','true');
                  document.body.classList.remove('casting');
                  btn.classList.remove('away');vcaret.style.display='none';caretKey='';
                  btn.focus();}
  /* ── 告知の二段化（S7）────────────────────────────────────────
     常時見えているのは「匿名のまま漂い、いつか知らない誰かのもとへ。」の一行だけ。
     打鍵が記録されることと著作権は、その下の罫の語で開く。
     v2追補 §1 が「書き始める前に目に入る場所」を求めているので、**初めて柱を開いた時は
     展開状態で立てる**。畳んだ姿で立ってよいのは二度目以降だけ。
     覚えるのは端末（localStorage）。アカウントに持たせると、同じ人が別の端末で
     初めて書く時に畳んだ姿で立つ——「書き始める前に目に入る」は端末ごとの話なので、
     ここは端末に持たせるのが正しい（仕様書 §5 未決事項1）。 */
  const more=document.getElementById('castMore'),about=document.getElementById('castAbout'),
        NOTE_KEY='tayori.castnote';
  let toldOnce=false;
  try{ toldOnce=localStorage.getItem(NOTE_KEY)==='1'; }catch(_){}
  function paintAbout(open){
    about.hidden=!open;
    more.setAttribute('aria-expanded',open?'true':'false');
  }
  paintAbout(!toldOnce);
  more.addEventListener('click',()=>paintAbout(about.hidden));
  btn.addEventListener('click',open);
  close.addEventListener('click',shut);
  pane.addEventListener('click',e=>{if(e.target===pane)shut();});
  /* Escape で閉じる。宙の他の面（読む・この宙について）は全部そうなのに、
     書く柱だけ効かなかった＝同じ形のものが同じに振る舞わない（Apple §16 familiarity）。
     変換中（isComposing）は Escape が変換の取り消しなので、横取りしない。 */
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&!e.isComposing&&!composing&&pane.classList.contains('on'))shut();});
  paintCast();   // 開く前から、押せない姿で置いておく（開いた瞬間に切り替わって見えないように）

  /* 放ったあとの、ひと呼吸。自分で退く（2.6秒）か、触れれば即退く。
     退いたあとの焦点は「放つ」——shut() が既にそこへ返している。
     ケアの紙（showCareNote）が続く時は、そちらが前に立つので重ならない。 */
  const sentVeil=document.getElementById('sentVeil');
  let sentT=0;
  function hideSent(){
    clearTimeout(sentT);
    sentVeil.classList.remove('on');
    sentVeil.setAttribute('aria-hidden','true');
  }
  function showSent(){
    clearTimeout(sentT);
    sentVeil.setAttribute('aria-hidden','false');
    sentVeil.classList.add('on');
    sentT=setTimeout(hideSent,2600);
  }
  sentVeil.addEventListener('click',hideSent);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&sentVeil.classList.contains('on'))hideSent();});

  function showCareNote(){
    if(document.querySelector('.care-note'))return;
    const n=document.createElement('div');n.className='care-note';
    /* 2026-07-27：文言を書き換えた。以前は「預かりました／ここに置いておきますね」
       ——ことばを宙に出さず手元に留めていた頃の一文で、いまは事実と違う。
       放ったことばは、ほかと同じように宙へ出ている。だからここでは、そのことを
       まず言い切る。警告にしない・行いを求めない・数えない。ただ置いておく。 */
    n.innerHTML='<p>ことばは、宙へ放たれました。</p>'+
      '<p>もし、誰かに話したくなったら——<br>'+
      '<a href="https://www.mhlw.go.jp/mamorouyokokoro/" target="_blank" rel="noopener">まもろうよ こころ（相談窓口のまとめ）</a><br>'+
      'いのちの電話 <a href="tel:0570-783-556">0570-783-556</a> ／ よりそいホットライン <a href="tel:0120-279-338">0120-279-338</a></p>'+
      '<button type="button" class="care-close ruled">とじる</button>';
    n.querySelector('.care-close').addEventListener('click',()=>n.remove());
    document.body.appendChild(n);
    requestAnimationFrame(()=>n.classList.add('on'));
  }

  /* ── 書く向き（2026-07-27）────────────────────────────────────
     縦書きは宙の見た目の核だが、**打つあいだ**だけは横に倒せるようにした。
     スマホのIME変換候補は必ず横に出るので、縦の字の流れと直交して打ちにくい
     （「入力の画面が縦方向でとても入力しづらい」と言われた）。
     倒すのは入力欄だけ——漂う姿も読む柱も縦書きのままで、放たれた後は変わらない。
     選んだ向きは端末に覚えさせる（毎回選ばせない）。 */
  const dirBtn=document.getElementById('castDir'), DIR_KEY='tayori.castdir';
  try{ horiz=localStorage.getItem(DIR_KEY)==='h'; }catch(_){}
  /* S5（仕様書 v1.0 §2.2）：現在値だけを出す切替をやめ、「縦」「横」を並べて置く。
     4-6 の状態表示（罫のミニアイコン＋現在地）は「押すと何が起きるか」が
     aria-label にしか無く、目で見ているだけでは切替と気づかれなかった。
     二つ並べば、選べることも、いまどちらかも、姿だけで分かる。 */
  function paintDir(){
    ta.classList.toggle('h',horiz);
    dirBtn.querySelectorAll('button').forEach(b=>{
      const on=(b.dataset.dir==='h')===horiz;
      b.setAttribute('aria-checked',on?'true':'false');
      b.setAttribute('tabindex',on?'0':'-1');   // radiogroup は焦点を一つだけ持つ
    });
    // 自前カーソルは縦書きのための細工。横書きではネイティブに返す
    vcaret.style.display='none';caretKey='';
    if(!horiz)requestAnimationFrame(drawCaret);
    paintSum();
  }
  dirBtn.addEventListener('click',e=>{
    const b=e.target.closest('button[data-dir]');
    if(!b)return;
    const want=(b.dataset.dir==='h');
    if(want===horiz){ ta.focus(); return; }   // 同じ向きを押しても何も変えない
    horiz=want;
    try{ localStorage.setItem(DIR_KEY,horiz?'h':'v'); }catch(_){}
    paintDir();ta.focus();
  });
  paintDir();   // 端末が覚えている向きで立ち上げる（毎回選ばせない）

  const castTitle=document.getElementById('castTitle');

  /* ── 置き場所をえらぶ（2026-07-27）──────────────────────────────
     書いたあとに選ぶ。部屋の中から開いたなら、そこが選ばれた状態で立つ
     （今までどおり、何も選ばずに放てる）。アーカイブ部屋は置けないので出さない。 */
  const whereBox=document.getElementById('castWhere'),
        whereLead=document.getElementById('whereLead');
  function paintWhere(){
    whereBox.querySelectorAll('button').forEach(b=>{
      const on=String(b.dataset.id)===String(castRoom);
      b.classList.toggle('on',on);
      b.setAttribute('aria-checked',on?'true':'false');
    });
    // 4-10（2026-07-28）：1つ選んだら他を沈める。選んだものだけが立つ
    whereBox.classList.toggle('picked',castRoom!=null);
    paintCast();      // 置き場所が決まって初めて放てる＝押せる姿も連動させる
  }
  function renderWhere(){
    whereBox.innerHTML='';
    (ROOMS||[]).filter(r=>!r.archived).forEach(r=>{
      const b=document.createElement('button');
      b.type='button';b.setAttribute('role','radio');
      b.dataset.id=r.id;b.textContent=r.name;
      b.addEventListener('click',()=>{castRoom=r.id;paintWhere();});
      whereBox.appendChild(b);
    });
    /* 4-10（2026-07-28）：見出しはいつも「どこへ」。未選択の説明文は出さない
       ——状態は選択肢の姿（.picked で他が沈む）が言う。 */
    whereLead.textContent='どこへ';
    paintWhere();
  }

  send.addEventListener('click',async()=>{
    const poem=ta.textContent.replace(/\s+$/,'');
    if(!poem.trim())return;                  // ここへは来ない（空のあいだ send は押せない）
    // 題は任意。無いまま放ってよい（v2.2 §2.1）。無料会員には欄そのものが無い
    // （2026-08-07・収益モデル v1）ので、ここは空のまま送る＝サーバ側も落とす。
    const title=(FEATURES.title&&castTitle)?castTitle.value.trim():'';
    send.disabled=true;
    const color=cssOf(toneColor(H,T));   // 放たれることばには必ず色がのる（v12）
    const steps=(trace.length>1?{fmt:'ev1',ev:trace.slice()}:null);
    try{
      const res=await fetch('/api/letters',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({mode:'sky',poem:poem,title:title,seal_color:color,
                             color_chosen:colorTouched?1:0,   // 触れていない既定色は空気にしない（2026-07-28）
                             vertical:1,trace:steps,
                             room:castRoom})});
      const d=await res.json().catch(()=>null);
      if(res.ok&&d&&d.ok){
        shut();
        /* 置いた島へそのまま降りる。そうしないと、放ったことばが立ち上がる場所に
           自分が居ないことになる（「置いた」のに何も起きないように見える）。
           降りた島の心で、自分の打鍵がそのまま再生されながらことばが立つ。 */
        const destIsl=islands.get(+castRoom);
        if(destIsl){ flyTo(destIsl); castLand(destIsl,poem,color,steps); }
        showSent();   // ひと呼吸。退いた時には、自分のことばがもう昇っている
        ta.textContent='';normalizeEmpty();H=H0;T=T0;colorTouched=false;trace=[];traceLast=0;tracePrev='';paint();drawCaret();
        traceDot.hidden=true;   // 放った＝記録は流れ終わった。点も一緒に消える
        if(castTitle)castTitle.value='';
        if(window.resetCastOpt) window.resetCastOpt();   // 任意の三つを畳み直す（2026-07-28）
        if(d.care)setTimeout(showCareNote,1600);
      }else{
        /* 届かなかったことは、必ず言う。v2 まではここが無音で、押しても柱が閉じないまま
           何も起きなかった——書いた本文は残っているのに、それが伝わらなかった
           （Apple §16：feedback の四種のうち error を欠いていた）。柱は閉じない＝書き直せる。 */
        whisper((d&&d.error)?esc(d.error)
          :'いま、<wbr>tayori-たより- へ<wbr>放てませんでした。<br>ことばは<wbr>ここに<wbr>残っています。',7000);
      }
    }catch(e){
      whisper('いま、<wbr>tayori-たより- が<wbr>遠いようです。<br>ことばは<wbr>ここに<wbr>残っています。',7000);
    }finally{paintCast();}   /* 送り終わったら字数の状態から引き直す（空なら押せないまま） */
  });

})();
/* 任意の三つ（2026-07-28）。開閉だけを持つ。中身の配線（castTitle / hueBar …）は
   既存のまま——id を変えていないので、あちらのコードは何も知らなくてよい。 */
(function(){
  const row=document.getElementById('castOptRow');
  if(!row) return;
  /* 題は有料会員の機能（収益モデル v1・2026-08-07）。無料会員には**引き金ごと出さない**
     ——押せない語や鍵のしるしを並べると、書く柱が「まだ買っていない」の一覧になる。
     ここは書く場所で、売り場ではない。案内は設定の中だけに置く。
     欄そのものも畳んでおく（サーバも無料会員の題は黙って落とす＝二重に閉じてある）。 */
  if(!FEATURES.title){
    const b=row.querySelector('[data-opt="castOptTitle"]'), box=document.getElementById('castOptTitle');
    if(b) b.remove();
    if(box) box.remove();
  }
  row.addEventListener('click',function(e){
    const b=e.target.closest('button[data-opt]');
    if(!b) return;
    const box=document.getElementById(b.dataset.opt);
    if(!box) return;
    const opening=box.hasAttribute('hidden');
    if(opening) box.removeAttribute('hidden'); else box.setAttribute('hidden','');
    b.setAttribute('aria-expanded',opening?'true':'false');
    if(opening){
      const f=box.querySelector('input,[role="slider"]');
      if(f) f.focus({preventScroll:true});
      /* 低い画面では開いた箱が出口(sticky)の裏に潜る（375×560 実測）。
         便箋を縮めて柱を詰めても 578px 中 46px しか稼げない＝柱の丈の主因は
         開いた箱と部屋一覧なので、縮めずに「開いたものを見せる」で応える。
         scroll-margin-bottom が出口のぶんの遊びを持っている（2026-07-28）。 */
      box.scrollIntoView({block:'nearest',
        behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
  });
  const t=document.getElementById('castTitle');
  if(t) t.addEventListener('input',function(){
    const b=row.querySelector('[data-opt="castOptTitle"]');
    if(b) b.dataset.filled = t.value.trim() ? '1' : '';
  });
  window.resetCastOpt=function(){
    row.querySelectorAll('button[data-opt]').forEach(function(b){
      const box=document.getElementById(b.dataset.opt);
      if(box) box.setAttribute('hidden','');
      b.setAttribute('aria-expanded','false');
      b.dataset.filled='';
    });
  };
})();
world.addEventListener('keydown',e=>{
  if((e.key==='Enter'||e.key===' ')&&e.target.classList.contains('w')){
    e.preventDefault(); hold(e.target);
  }
  if(e.key==='Escape')release();
});
/* ── 鍵盤で宙を動かす（2026-08-03 操作性）───────────────────
   矢印＝ひきずり、＋−＝寄り引き、0＝全景、Escape＝島から宙へ。
   入力中・柱や戸が開いている間は、宙には触らない。
   capture で受ける：Escape は「開いているものを閉じる」が常に先で、宙へ引くのは
   何も開いていない時だけ。柱や戸の Escape（bubble）より前に、開いている姿の
   まま見なければ、閉じたその同じ一押しで宙まで引いてしまう。 */
document.addEventListener('keydown',e=>{
  if(e.isComposing||e.metaKey||e.ctrlKey||e.altKey)return;
  const t=e.target;
  if(t&&t.closest&&t.closest('input,textarea,select,[contenteditable="true"]'))return;
  const cast=document.getElementById('castPane'), read=document.getElementById('readPane');
  if(cast&&cast.classList.contains('on'))return;
  if(read&&read.classList.contains('on'))return;
  if(!gatherEl.hidden)return;
  if(e.key==='Escape'){
    // 受け止め中・戸・放った合図は、それぞれの Escape が先に引き受ける
    const menu=document.getElementById('logoMenu'), veil=document.getElementById('sentVeil');
    if(held)return;
    if(menu&&!menu.hasAttribute('hidden'))return;
    if(veil&&veil.classList.contains('on'))return;
    // 飛んでいる最中でも引き返せる（0 と同じ理由・2026-08-05 夜）。行き先を
    // 間違えたと気づいた一押しが、1.5秒黙って捨てられるのが以前のふるまいだった。
    if(sheetId!=null||sheetLock!=null)overview();
    return;
  }
  const step=120;
  // 矢印のひきずりも手（wheel・つまみと同じ）。滑走に上書きさせない
  const pan=d=>{ handTakes(); d(); apply(); };
  switch(e.key){
    case 'ArrowLeft':  pan(()=>{PX+=step;}); e.preventDefault(); break;
    case 'ArrowRight': pan(()=>{PX-=step;}); e.preventDefault(); break;
    case 'ArrowUp':    pan(()=>{PY+=step;}); e.preventDefault(); break;
    case 'ArrowDown':  pan(()=>{PY-=step;}); e.preventDefault(); break;
    case '+': case '=': zoomAt(VW/2,VH/2,1.3); e.preventDefault(); break;
    case '-': case '_': zoomAt(VW/2,VH/2,1/1.3); e.preventDefault(); break;
    /* 0＝全景。飛んでいる最中でも受ける（2026-08-05 夜）。飛行に世代を持たせた今、
       途中で次の飛行を始めても前の飛行は自分の役ではないと知って黙る——押した瞬間に
       応えられるのに黙る理由はもう無い（同じ鍵盤の ＋− は zoomAt→handTakes で
       即座に応えている。「−は効くが0は効かない」を残さない）。 */
    case '0': overview(); e.preventDefault(); break;
  }
},true);
document.addEventListener('keydown',e=>{ if(e.key==='Escape')release(); });

/* ════ 読む柱・降りてきました・開く（mood.html 3979-4141 から移植）════ */
(function(){
  if(!LOGGED_IN) return;
  /* ── 開く柱 ──
     棚へ降ろさず、宙の中で封をほどく。帰ってきた自分のことばも、宙から降ってきた
     だれかのことばも、ここでタイプ再生されながら立ち上がる。
     v12：文言だけ出し分ける。だれかのことばは宙にずっと在るが、自分のことばは
     帰ってきたその時しか読めない——だから「あの日の、あなたのことば」と名指す。 */
  const rp=document.getElementById('readPane'),rb=document.getElementById('readBody'),
        rn=document.getElementById('readNote'),rc=document.getElementById('readClose'),
        rk=document.getElementById('readKeep');
  let readDid=null,readBack=null;
  function shutRead(){
    rp.classList.remove('on');rp.setAttribute('aria-hidden','true');
    rb._run=(rb._run||0)+1;      // 再生中なら止める
    readDid=null;readRef=null;
    // 開いた場所へ戻す（他の面と同じ作法）。開いた札そのものが描き直されて
    // 居なくなっていることもあるので、その時は何もしない（宙に戻るだけ）。
    const b=readBack;readBack=null;
    if(b&&b.isConnected&&typeof b.focus==='function')b.focus();
    if(location.search.indexOf('open=')>=0) history.replaceState(null,'','/mood');
  }
  rc.addEventListener('click',shutRead);
  rp.addEventListener('click',e=>{if(e.target===rp)shutRead();});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&rp.classList.contains('on'))shutRead();});
  /* ── 手元の棚（v13 §9）──
     残せるのは、いまこの柱で開いたことばだけ。宙を漂っていることばには手を伸ばせない
     （宙のidは逆引きできないハッシュのまま＝棚のために匿名性へ穴を開けない）。 */
  let readRef=null;              // {src:'sky'|'mine', ref:id}
  // 棚の控え（kept）は宙の受け止めと同じ集合を使う。書式も 'src:ref' で揃えてある
  function keptKey(r){return r?(r.src+':'+r.ref):'';}
  function setKeep(on){
    rk.classList.toggle('on',!!on);
    rk.setAttribute('aria-pressed',on?'true':'false');
    rk.querySelector('.txt').textContent=on?T_KEEP_ON:T_KEEP_OFF;
  }
  const readFusen=document.getElementById('readFusen');
  rk.addEventListener('click',async()=>{
    if(!readRef)return;
    const on=!rk.classList.contains('on');
    setKeep(on);
    if(!on)dropPad(readFusen);
    try{
      const r=await fetch('/api/shelf',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({src:readRef.src,ref:readRef.ref,on:on})});
      const d=await r.json().catch(()=>null);
      if(r.ok&&d&&d.ok){
        if(on)kept.add(keptKey(readRef)); else kept.delete(keptKey(readRef));
        if(on&&d.id){fusenPad(readFusen,d.id,[]);wherePad(readFusen,d.id,d.shelf,d.shelves);}
      }
      else setKeep(!on);
    }catch(e){ setKeep(!on);dropPad(readFusen); }
  });

  function showRead(o){
    release();                    // 宙で受け止めていたことばは、柱が立つ前に放す
    gatherHide();                 // 集まってきた片の面も畳む（柱はその下に立つ）
    if(!rp.classList.contains('on'))readBack=document.activeElement;
    dropPad(readFusen);           // 前に開いた付箋の紙片は持ち越さない
    readDid=o.did||null;
    rb.classList.toggle('h',!o.vertical);
    rb.textContent='';
    rn.textContent=o.note||'';
    // 棚は、他人のことばにも自分の帰ってきたことばにも結べる
    readRef=o.did?{src:'sky',ref:o.did}:(o.lid?{src:'mine',ref:o.lid}:null);
    rk.hidden=!readRef;
    if(readRef)setKeep(kept.has(keptKey(readRef)));
    rp.setAttribute('aria-label',o.mine?'あの日の、あなたのことば':'降りてきたことば');
    const c=tintOf(o.color);
    rb.style.color=c?c.text:'';
    rp.classList.add('on');rp.setAttribute('aria-hidden','false');
    rb.focus();
    playType(rb,o.poem||'',o.steps);
  }
  function showReadError(msg){
    readDid=null;readRef=null;rk.hidden=true;
    rb.classList.add('h');rb.style.color='';rb._run=(rb._run||0)+1;
    rb.textContent=msg;rn.textContent='';
    rp.classList.add('on');rp.setAttribute('aria-hidden','false');rb.focus();
  }

  /* ── 宙のすみで待っている、だれかのことば（旧・受信の棚）──
     数も、だれのものかも言わない。降りてきたことだけが、そこに灯る。 */
  const arrBox=document.getElementById('arrivals');
  let arrivals=[];
  function renderArrivals(){
    arrBox.innerHTML='';
    /* 出すのは、まだ開いていないことばだけ（2026-07-27）。
       以前は開封済みが「もう一度、開く」として残り続け、新しいことばが無い間ずっと
       宙のすみに居座っていた＝受信箱になっていた。読んで、手元に置きたければ
       「保存する」がある。開いたことばは、そこで宙のすみからは退く。
       多すぎても掲示板にならないよう、出すのは4つまで。 */
    arrivals.filter(A=>!A.opened).slice(0,4).forEach(A=>{
      const b=document.createElement('button');
      b.type='button';b.className='arrival';
      b.innerHTML='<span class="seal" aria-hidden="true"></span><span>'+
        'ことばが、降りてきました</span>';
      const c=tintOf(A.color);
      if(c)b.querySelector('.seal').style.background=c.solid;
      b.addEventListener('click',()=>openArrival(A));
      arrBox.appendChild(b);
    });
  }
  function fetchArrivals(){
    bootOr('arrivals','/api/sky/arrivals')
      .then(d=>{if(d&&d.arrivals){arrivals=d.arrivals;renderArrivals();}})
      .catch(()=>{});
  }
  const ANON_NOTE='だれの、いつのことばかは、だれにもわかりません。';
  async function openArrival(A){
    if(A.opened)return;   // 開いたものは、もう宙のすみに出ていない
    try{
      const res=await fetch('/api/sky/'+A.did+'/open',
        {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const d=await res.json().catch(()=>null);
      if(res.ok&&d&&d.ok){
        A.opened=true;A.poem=d.poem;A.vertical=d.vertical;A.liked=d.liked;A.color=d.color;
        // 打った過程もいっしょに降りてくる（v2追補 §1）。無ければ合成にまかせる
        A.steps=(d.trace_ev&&d.trace_ev.length>1)?{fmt:'ev1',ev:d.trace_ev}:null;
        renderArrivals();
        showRead({poem:d.poem,vertical:d.vertical,color:d.color,note:ANON_NOTE,
                  did:A.did,liked:d.liked,steps:A.steps});
      }else{
        showReadError((d&&d.error)||'そのことばは開けませんでした。');
      }
    }catch(e){ showReadError('そのことばは開けませんでした。'); }
  }
  fetchArrivals();
  setInterval(fetchArrivals,90000);
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden) fetchArrivals(); });

  /* ── 開く（メールの開封リンクから宙に着地したとき）── */
  if(OPEN_ID){
    (async()=>{
      try{
        const res=await fetch('/api/letters/'+OPEN_ID+'/open',
          {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        const d=await res.json().catch(()=>null);
        if(res.ok&&d&&d.ok){
          const L=d.letter||{};
          // 自分のことばだけは、打鍵そのものを再生できる（迷いも、書き直しも）
          let steps=null;
          try{
            const t=await fetch('/api/letters/'+OPEN_ID+'/trace').then(r=>r.ok?r.json():null);
            if(t&&t.trace_ev&&t.trace_ev.length>1)steps={fmt:'ev1',ev:t.trace_ev};
            else if(t&&Array.isArray(t.trace))steps=t.trace;
          }catch(e){}
          // 日付は機械の刻印ではなく、そっと添える一行に（ISOのままは出さない）
          const p=String(L.sent_date||'').slice(0,10).split('-');
          /* フェーズ5（2026-07-28）：「封をした日」→「放った日」。封は手紙モードの語彙。 */
          const day=p.length===3?('放った日　'+p[0]+'年'+(+p[1])+'月'+(+p[2])+'日'):'';
          let note='あの日の、あなたのことば'+(day?'\n'+day:'');
          // 放ったあとの話（宙v1 §5・§7）。該当しない時は行ごと無音
          //（「まだ誰にも読まれていません」は絶対に出さない）。
          if(L.first_seen)note+='\nこの言葉は、'+L.first_seen+'に、一度浮かびました。';
          if(L.in_someones_hands)note+='\nこのことばは、だれかの手元にあります。';
          showRead({poem:L.poem||'',vertical:L.vertical,color:L.seal_color,steps:steps,
                    mine:true,lid:OPEN_ID,note:note});
        }else{
          showReadError((d&&d.error)||'そのたよりは開けませんでした。');
        }
      }catch(e){ showReadError('そのたよりは開けませんでした。'); }
    })();
  }
})();

/* ── 立ち上がり ── */
Promise.all([
  bootOr('rooms','/api/rooms'),
  bootOr('canvas','/api/sky/canvas'),
]).then(([rd,wd])=>{
  ROOMS=rd.rooms||[];
  build(ROOMS,wd.words||[]);
  KFIT=homeFit();    // 島の席が決まった＝手で引ける下限（全景）もここで決まる
  fillMenuRooms();   // 戸の中のジャンル（見えていない島への道）
  fetchMine();   // 自分の棚の控え（棚にあります の札のため）
  /* 書体が着いたら、寸法は測り直す（2026-08-14）。
     日本語の Shippori Mincho は字ごとに約120枚へ割ってあり、その面に出ている字の
     ぶんだけ順に落ちてくる（この宙では実測91枚）。display:swap なので字はすぐ出るが、
     出ているのは**代わりの書体**で、字幅も行の高さも違う。押し出し（relax）も
     画面外の間引きの箱（_sw/_sh）も、その代わりの寸法で決まってしまい、
     あとから本物が着いても二度と測り直されていなかった——重なったまま、あるいは
     見えているのに間引かれたまま、の紙が残る。
     着き終わりを一度だけ待って、押し出しと束を組み直す。fonts.ready は既に
     着いていれば即座に解けるので、机の上（書体が控えにある時）では何も遅れない。 */
  if(document.fonts&&document.fonts.ready){
    document.fonts.ready.then(()=>{
      if(!islands.size)return;
      relax();                                  // 散らばりの寸法と押し出しを引き直す
      if(sheetId!=null&&islands.has(sheetId)){  // 降りている島は、束も組み直す
        const isl=islands.get(sheetId);
        /* 控えを捨てるのは横書きの紙だけ。縦書きの紙片の寸法（snapSize）は
           字数と CSS の定数から出る純関数で、書体が変わっても一画も動かない。 */
        isl.words.forEach(el=>{ if(el._w&&!el._w.vertical){el._pw=0;el._ph=0;} });
        isl._fitK=fitK(isl);
        sheetLayout(isl);
      }
      apply();
    }).catch(()=>{});
  }
  requestAnimationFrame(()=>{ relax();
    /* 2026-07-31：宙はミクロから始まる。全景（マクロ）から入ると、最初に見えるのが
       「地図」で、ことばそのものに触れるまでが遠かった——降りた島の紙片から始め、
       引けば（−・つまむ）いつでも全景へ出られる。降りる先は、いちばん新しい
       ことばが昇った島＝いま息をしている場所（?room= は今までどおりその島へ）。 */
    let fx=0,fy=0,fr=900,k0=null;
    // 降りる先の倍率は束から（2026-08-02）。滑って降りる時と同じ寸法で始める。
    const land=i=>{
      fx=i.cx; fy=i.cy; fr=i.R*1.12; k0=fitK(i); i._flown=true;
      // 滑って降りた時と同じ扱い（＝「居る」）にしておく。束に合わせた倍率は
      // 入る線を割ることがあるので、行き先を告げずに置くと紙が敷かれないまま始まる。
      /* 立ち上がりも「飛行」として世代に入れる（2026-08-05 夜）。裸のタイマだった
         ころは、読み込みから1.2秒以内に別の島へ飛ぶと、この後始末が**その飛行の**
         行き先を落としていた——入る線が 0.20→0.32 へ跳ね、束の大きい島は着いても
         紙が敷かれないまま名だけの島に降ろされる。世代を持たせれば、後から始まった
         飛行が親になり、こちらの後始末は自分の役ではないと知って黙る。 */
      flightBegin(1200);
      sheetLock=i.room.id;
    };
    if(START_ROOM&&islands.has(+START_ROOM)){ land(islands.get(+START_ROOM)); }
    else{
      let best=null,bs=-1;
      islands.forEach(isl=>isl.words.forEach(el=>{
        if(!el._w.pd&&el._w.sink>bs){bs=el._w.sink;best=isl;}
      }));
      if(best){ land(best); }
      else{ let mx=0; islands.forEach(i=>{mx=Math.max(mx,Math.hypot(i.cx,i.cy)+i.R);}); fr=mx||900; }
    }
    K=k0!=null?k0:Math.max(KMIN,Math.min(1.2,Math.min(VW,VH)/(fr*2.15)));
    PX=-fx*K; PY=-fy*K;
    applyNow();   // 立ち上がりだけは同じフレームで描く（1フレームの素の宙を見せない）
  });
}).catch(()=>{
  const p=document.createElement('p');
  p.style.cssText='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);color:var(--ink-3);font-size:13px;letter-spacing:.14em';
  p.textContent='いまは、宙をひらけません。';
  document.body.appendChild(p);
});

/* ════ フレームの計器（2026-08-02・`?fps=1` を付けた時だけ立つ）════════════
   7/31 に「パン 0.06〜0.31ms/フレーム」と測って JS を無罪にしたが、あれは
   **自前のJSが走っている時間**だった。ガクツキは、そのJSが return したあとに
   ブラウザがやる仕事——スタイルの再計算・レイアウト・ペイント・ラスタライズ・
   合成——で起きる。自分の関数の中にストップウォッチを置く限り、そこは永遠に
   見えない。だから測るのは rAF と rAF の**あいだ**：その一区間に、自前の分も
   ブラウザの分も、全部が入っている。

   時計は rAF が渡してくる引数だけを使う（performance.now() と混ぜない——
   2026-07-27 に打鍵の再生が携帯で動かなかったのは、この二つを混ぜたせい）。

   「字を消す」は決め手の実験。字を消してなめらかに動くなら、重いのは
   **倍率が変わるたびに数百枚の字を描き直す仕事**で、直す場所は間引きの
   さじ加減ではなく、遠景の描き方そのものになる。 */
(function(){
  if(!/[?&]fps=1(&|$)/.test(location.search))return;

  const N=240;                       // 直近4秒ぶん（60fps換算）
  const dt=new Float32Array(N), ges=new Uint8Array(N);
  let n=0, prev=0, selfMs=0, selfMax=0, fastest=1e9;

  /* 手がふれている間だけを別に集める。止まっている宙の 16.7ms を何百個
     混ぜると、肝心のつまんでいる最中の山が中央値に埋もれる。
     2026-08-02 改訂：時計で窓を切らず、**フレームを数える**。イベント側の
     performance.now() と rAF の時刻を比べると、混ぜてはいけない二つの時計を
     混ぜることになる（7/27 に打鍵の再生が携帯で動かなかったのがそれ）。
     入力が来たら20フレームぶん旗を立て、フレームごとに1つ倒す——つまみ終わりの
     余韻まで拾えるし、＋／− のボタンでも1回ぶんは必ず数えられる。 */
  let gesFrames=0;
  const touch=()=>{gesFrames=20;};
  vp.addEventListener('pointerdown',touch,true);
  vp.addEventListener('pointermove',e=>{if(e.buttons)touch();},true);
  vp.addEventListener('wheel',touch,true);
  document.getElementById('zin').addEventListener('click',touch,true);
  document.getElementById('zout').addEventListener('click',touch,true);

  /* 自前の所要時間も残しておく（比べるものが無いと「ブラウザの分」が言えない）。
     applyNow は関数宣言＝書き換えられる束縛で、apply() は呼ぶ時にその時の値を
     読むので、ここで包んでも経路は変わらない。 */
  const rawApply=applyNow;
  applyNow=function(){
    const t0=performance.now();
    rawApply();
    selfMs=performance.now()-t0;
    if(selfMs>selfMax)selfMax=selfMs;
  };

  /* ── 最悪の一枚が「何だったのか」を残す（2026-08-02）──────────────
     664枚を出したままでも17msで回るのに、広い範囲を引いてきた回だけ350msが出た。
     つまり重いのは**在ること**ではなく、途中で起きた**出来事**のほう。
     宙には倍率でまたぐ境目があり、またぐと body や島の class が入れ替わる＝
     その下の要素のスタイルが一斉に計算し直される。どのフレームで何が入れ替わった
     かを控えておけば、最悪の一枚に名前が付く。
     class の変化は MutationObserver で拾う（本当に変わった時だけ呼ばれるので、
     毎フレーム何かを見に行く形にはならない＝計器が症状を作らない）。 */
  let lastEvent='', worstD=0, worstK=0, worstEvent='';
  new MutationObserver(ms=>{
    for(const m of ms){
      const t=m.target;
      if(t===document.body){
        const c=t.className||'';
        lastEvent=(c.includes('far')?'遠景':'近景')
          +(c.includes('crisp')?'・光暈なし':'・光暈あり')+'へ切替';
      }else if(t.classList&&t.classList.contains('isl')){
        lastEvent=t.classList.contains('sheet')?'島が紙片に':'島が散らばりに';
      }
    }
  }).observe(document.documentElement,
             {subtree:true,attributes:true,attributeFilter:['class']});

  const box=document.createElement('div');
  box.style.cssText='position:fixed;z-index:60;left:8px;bottom:calc(96px + env(safe-area-inset-bottom));'
    +'padding:8px 10px;border-radius:8px;background:rgba(8,8,13,.88);'
    +'border:1px solid rgba(179,143,111,.3);color:#FAF8F3;'
    +'font:11px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;'
    +'white-space:pre;pointer-events:auto';
  /* 読み取った数字は**選べるように**しておく（宙そのものは選べなくしてあるが、
     ここは持ち帰るための面）。携帯では選ぶのが難しいので「写す」も置く。 */
  const out=document.createElement('div');
  out.style.cssText='-webkit-user-select:text;user-select:text';
  const btns=document.createElement('div');
  btns.style.cssText='display:flex;gap:6px;margin-top:6px';
  const mk=(label,fn)=>{
    const b=document.createElement('button');
    b.textContent=label;
    b.style.cssText='flex:1;padding:7px 6px;min-height:32px;background:rgba(250,248,243,.08);'
      +'border:1px solid rgba(179,143,111,.3);border-radius:6px;color:#FAF8F3;'
      +'font:inherit;letter-spacing:.06em';
    b.addEventListener('click',fn);
    btns.appendChild(b);
    return b;
  };
  let hidden=false;
  const bHide=mk('字を消す',()=>{
    hidden=!hidden;
    document.body.classList.toggle('fps-nowords',hidden);
    bHide.textContent=hidden?'字を戻す':'字を消す';
    reset();
  });
  mk('ならす',()=>reset());
  /* 二つ目の分かれ道（2026-08-02）。字を消しても 33ms が動かなかったので、
     次は**宙ごと**消す。宙を消しても 33ms のままなら、その33msは宙の外にある
     ——画面の速度そのものか、宙以外の何かが毎フレーム走っている。
     visibility ではなく display:none にするのは、版面の計算ごと外すため
     （字を消した時に何も変わらなかったのは、visibility が版面を残すからでもある）。 */
  let noSky=false;
  const bSky=mk('宙を消す',()=>{
    noSky=!noSky;
    world.style.display=noSky?'none':'';
    bSky.textContent=noSky?'宙を戻す':'宙を消す';
    reset();
  });
  /* 携帯では5行を指で選ぶのが難しい。押した時の姿だけで「写った」と分かるようにする
     （クリップボードが使えない場でも、選べる面は上に残してある）。 */
  const bCopy=mk('写す',async()=>{
    const t='【'+(hidden?'字なし':'字あり')+'・'+(noSky?'宙なし':'宙あり')+'】\n'
      +'画面 '+(screen.width+'×'+screen.height)+' dpr'+devicePixelRatio+'\n'
      +navigator.userAgent+'\n'+out.textContent;
    try{ await navigator.clipboard.writeText(t); bCopy.textContent='写した'; }
    catch(_){ bCopy.textContent='選んで写して'; }
    setTimeout(()=>{bCopy.textContent='写す';},1600);
  });
  box.appendChild(out); box.appendChild(btns);
  const st=document.createElement('style');
  st.textContent='body.fps-nowords .w{visibility:hidden!important}';
  document.head.appendChild(st);
  document.body.appendChild(box);

  /* selfMs も一緒に捨てる。前は最悪だけ0に戻していたので、直近値だけが古いまま
     残り「1.0 ms（最悪 0.0）」という、読んだ人が二度見する行が出ていた。 */
  function reset(){ n=0; prev=0; selfMs=0; selfMax=0; fastest=1e9;
                    worstD=0; worstK=0; worstEvent=''; lastEvent=''; }

  function pct(a,p){
    if(!a.length)return 0;
    const i=Math.min(a.length-1,Math.max(0,Math.round((a.length-1)*p)));
    return a[i];
  }

  let paintAt=0;
  requestAnimationFrame(function frame(now){
    if(prev){
      const d=now-prev;
      const i=n%N;
      dt[i]=d; ges[i]=gesFrames>0?1:0;
      if(d<fastest&&d>0)fastest=d;   // 一度でも出た最速＝この画面が出せる上限
      /* 最悪の一枚には、その時の倍率と、直前に起きた出来事を添えておく。
         数字だけでは「たまたま重かった」と「境目をまたいだ」が区別できない。 */
      if(d>worstD){ worstD=d; worstK=K; worstEvent=lastEvent; }
      lastEvent='';                  // 出来事は一枚ぶんだけ持つ（次の枚に持ち越さない）
      n++;
      if(gesFrames>0)gesFrames--;
    }
    prev=now;

    if(now-paintAt>250){
      paintAt=now;
      const m=Math.min(n,N);
      const all=[], g=[];
      for(let i=0;i<m;i++){ all.push(dt[i]); if(ges[i])g.push(dt[i]); }
      all.sort((a,b)=>a-b); g.sort((a,b)=>a-b);
      let vis=0, tot=0, isl=0;
      islands.forEach(is=>{
        if(!is.off)isl++;
        is.words.forEach(el=>{ tot++; if(!is.off&&!el._off)vis++; });
      });
      const f=x=>x.toFixed(1);
      out.textContent=
        '手がふれている間  '+(g.length?f(pct(g,.5))+' / '+f(pct(g,.95))+' / '+f(g[g.length-1]):'—')+' ms\n'
        +'                  中央 / p95 / 最悪   ('+g.length+'枚)\n'
        +'ぜんぶ            '+(all.length?f(pct(all,.5))+' / '+f(pct(all,.95)):'—')+' ms\n'
        /* いちばん速かった一枚＝この画面が出せる上限。ここが 16.7 付近なら画面は
           60Hz で、33ms は**落としている**。ここも 33 なら画面が 30Hz ＝
           落としてなどおらず、測っていたのは画面の速度のほうだった。 */
        +'最速の一枚        '+(fastest<1e9?f(fastest)+' ms'+(fastest<20?'（画面は60Hz以上）':'（画面が'+Math.round(1000/fastest)+'Hz）'):'—')+'\n'
        +'うち自前のJS      '+f(selfMs)+' ms（最悪 '+f(selfMax)+'）\n'
        +'最悪の一枚        '+(worstD?f(worstD)+' ms  K='+worstK.toFixed(3)
                              +(worstEvent?'  '+worstEvent:'  （境目はまたいでいない）'):'—')+'\n'
        +'倍率 K            '+K.toFixed(3)+(document.body.classList.contains('far')?'  遠景':'  近景')
        +(document.body.classList.contains('crisp')?'・光暈なし':'')+'\n'
        +'ことば            '+vis+' / '+tot+' 枚を描画中（島 '+isl+'）';
    }
    requestAnimationFrame(frame);
  });
})();

/* ══ 並べて読む（2026-08-07 §3.2・§3.4）═══════════════════════════
   島に降りている間だけ開ける、読むための一枚。宙は置き換えない——畳めば島は
   同じ場所にそのまま在る（この面は上に重ねるだけで、何も壊さない）。

   出すのは題と本文と、漂流物なら出どころ。棚も印も「もう見ない」も出さない：
   読む面に押せるものを増やすと、目は必ずそちらへ行く（行いは島の紙片の側にある）。

   【並びを「新しい順」と呼ばない理由】宙のことばは日付を持って降りてこない
   ——「だれの、いつのことばかは、だれにもわかりません」がこの世界の約束で、
   canvas の配り物にも日付は入れていない。持っているのは沈降（sink）だけで、
   これは人のことばなら新しいほど浅いが、漂流物は時を持たない（常に一定）。
   だから字のとおり「浮かんでいる順」と呼ぶ。棚（/me）の側は saved_at＝
   その人が残した日を本当に持っているので、あちらだけが「新しい順」と言える。 */
(function(){
  const pane=document.getElementById('rsheet');
  if(!pane)return;
  const lead=document.getElementById('rsheetLead'),
        grid=document.getElementById('rsheetGrid'),
        field=document.getElementById('rsheetField'),
        sortBox=document.getElementById('rsheetSort'),
        moreBox=document.getElementById('rsheetMore'),
        moreBtn=document.getElementById('rsheetMoreBtn'),
        closeBtn=document.getElementById('rsheetClose'),
        openBtn=document.getElementById('skyRead');
  const PAGE=50;                 // 一度に並べる枚数（§5）。数は画面に出さない
  let list=[], shown=0, sort='up', rseed=1, back=null;

  /* ── 打った過程は、ここでも流れる（v2追補 §1・2026-08-07）──────────
     宙で漂っている紙片は8秒ごとに書き直される（rotateOne）。読む面でそれを
     落とすと、「打った過程がそのまま宙に流れます」という約束がこの面にだけ無い
     ことになる——だから残す。ただし、流し方は宙とは変える：

     ・**目に入ったその一度だけ**書く（IntersectionObserver）。読み終えた字が
       もう一度ほどけて書き直されるのは、眺める面では気配でも、読む面では邪魔。
     ・**同時に3枚まで**（宙のローテーションと同じ数）。机では最初の画面に十枚
       以上が入るので、全部いっぺんに走らせると読む前に画面が騒がしくなる。
     ・**漂流物には当てない**。本から拾った一節に打鍵は無く、合成の打鍵を当てると
       「誰も打っていないためらい」が生える（サーバ側 api_sky_word_trace も
       同じ理由で pd を塞いでいる）。字はそのまま置く。 */
  const TYPE_AT_ONCE=3;
  let typing=0; const queue=[];
  function pump(){
    while(typing<TYPE_AT_ONCE&&queue.length){
      const el=queue.shift();
      if(!el||!el.isConnected)continue;
      typing++;
      const w=el._w, done=()=>{ typing--; pump(); };
      getTrace(w.id).then(tr=>{
        if(!el.isConnected){done();return;}
        playType(el,w.poem||'',tr,done);
      }).catch(done);
    }
  }
  /* root は巻物（.rsheet-field）。下端の 15% は「まだ読んでいない所」なので、
     そこへ入った時ではなく、そこを越えて本当に目へ入った時に書きはじめる。 */
  const watch=('IntersectionObserver' in window)?new IntersectionObserver(es=>{
    es.forEach(e=>{
      if(!e.isIntersecting)return;
      watch.unobserve(e.target);
      const el=e.target;
      if(el._played)return;
      el._played=1;
      queue.push(el); pump();
    });
  },{root:field,rootMargin:'0px 0px -15% 0px'}):null;

  /* ── 棚にとっておく（2026-08-07 Kosei）─────────────────────────────
     この面は最初、押せるものを一つも持たなかった（「読む面に押せるものを増やすと、
     目は必ずそちらへ行く」）。けれど**読み通している最中こそ、残したいことばに出会う**
     ——そこで島へ戻って同じ一枚をもう一度探させるのは、読むことのほうを切っている。
     出すのは棚ひとつだけ。「もう見ない」も付箋も「ほかの棚へ」も出さない：
     残すのは読みながらでもできるが、選り分けと手入れは読み終えてからのことで、
     場所も島の紙片と /me に在る。

     姿は紙の**奥付の下**に一行——本文の版面（縦書き）には触れない。紙の丈は中身が
     決める（版面は --vh ちょうど）ので、足が増えても読める字数は変わらない。 */
  function keepFoot(w){
    if(!LOGGED_IN||!w.id)return '';
    const on=kept.has('drift:'+w.id);
    return '<div class="rsheet-foot"><button type="button" class="rsheet-keep'+(on?' on':'')+
      '" aria-pressed="'+(on?'true':'false')+'">'+IC_SHELF+
      '<span class="txt">'+footTxt(on)+'</span></button></div>';
  }
  /* 二列の紙では足の一語が一行に入らない。折れる所を語の切れ目に指しておく
     （keep-all と組で効く・この宙の日本語改行の作法）。「棚に／とっておく」。 */
  function footTxt(on){ return (on?T_KEEP_ON:T_KEEP_OFF).replace('棚に','棚に<wbr>'); }
  function paintKeep(b,on){
    b.classList.toggle('on',on);
    b.setAttribute('aria-pressed',on?'true':'false');
    b.querySelector('.txt').innerHTML=footTxt(on);
  }
  /* 控え（kept）は宙と同じ集合を書き換える＝この面で残したことばは、畳んで島の紙片に
     触れた時にはもう「棚にあります」になっている（同じ棚を、二つの面から見ている）。
     押した瞬間に姿を変えて、返事は待たない（Apple §1）。届かなかった時だけ、戻す。 */
  async function toggleCellKeep(b,w){
    if(b._busy)return;
    b._busy=1;
    const key='drift:'+w.id, on=!kept.has(key);
    if(on)kept.add(key); else kept.delete(key);
    paintKeep(b,on);
    try{
      const r=await fetch('/api/shelf',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({src:'drift',ref:w.id,on:on})});
      if(!r.ok)throw new Error('keep');
      await r.json().catch(()=>null);
    }catch(e){
      if(on)kept.delete(key); else kept.add(key);
      paintKeep(b,!on);
      whisper('いま、<wbr>棚に<wbr>届きませんでした。',6000);
    }finally{ b._busy=0; }
  }
  /* 受けるのは grid ひとつ（50枚ごとに増えていく紙に、一枚ずつ耳を付けない）。
     ここは #vp の外なので click でよい——setPointerCapture で click が付け替わる
     地雷は、宙の面の中だけの話（2026-07-31）。 */
  grid.addEventListener('click',e=>{
    const b=e.target.closest('.rsheet-keep');
    if(!b)return;
    const li=b.closest('.rsheet-cell');
    if(li&&li._w)toggleCellKeep(b,li._w);
  });

  /* ランダムの並び（§3.4）。押した時に種を一つ決めて、その種で混ぜる——
     めくるたびに引き直すと、続きをめくった時に前の50枚がもう一度出てくる。 */
  function shuffled(a,seed){
    a=a.slice(); let s=seed>>>0||1;
    for(let i=a.length-1;i>0;i--){
      s^=s<<13;s>>>=0;s^=s>>>17;s^=s<<5;s>>>=0;   // xorshift32
      const j=s%(i+1), t=a[i];a[i]=a[j];a[j]=t;
    }
    return a;
  }
  function order(ws){
    if(sort==='rand')return shuffled(ws,rseed);
    // 浮かんでいる順＝沈んでいないものから。同じ深さは id で決めて、開くたびに揺れない
    return ws.slice().sort((a,b)=>(b.sink-a.sink)||(a.id<b.id?-1:1));
  }
  function paint(more){
    if(!more){ queue.length=0; grid.textContent=''; shown=0; field.scrollTop=0; }
    const upto=Math.min(list.length,shown+PAGE);
    const frag=document.createDocumentFragment();
    for(let i=shown;i<upto;i++){
      const w=list[i], li=document.createElement('li');
      li.className='rsheet-cell';
      /* 紙の色は島の紙片と同じ paperOf を通す＝同じことばは、島でも読む面でも同じ紙。
         色が選ばれていないことば（彩度12未満・サーバの _AIR_GRAY_S と同じ線）は
         生成り〜灰白の三種、漂流物は灰の紙——どちらも paperOf の中で決まる。 */
      li.style.setProperty('--pp',paperOf(w));
      if(w.pd)li.classList.add('pd');
      rsFit(li,w);          // 紙の寸法は、ことばの長さから（狙いは二列）
      li.innerHTML='<div class="rsheet-v">'+
        (w.title?'<p class="rsheet-title"></p>':'')+'<p class="rsheet-poem"></p></div>'+
        (w.pd?'<p class="rsheet-src"></p>':'')+keepFoot(w);
      li._w=w;
      if(w.title)li.querySelector('.rsheet-title').textContent=w.title;
      const pe=li.querySelector('.rsheet-poem');
      pe.textContent=w.poem||'';
      // 奥付は折れてよい。折る所は作者名と書名のあいだ（keep-all と組で効く <wbr>）
      if(w.pd)li.querySelector('.rsheet-src').innerHTML=
        '— '+esc(w.author||'')+'<wbr>『'+esc(w.work||'')+'』より';
      // 打鍵の再生は人のことばだけ（上の watch の節）。漂流物は置くだけ。
      if(watch&&!w.pd&&w.id){ pe._w=w; watch.observe(pe); }
      frag.appendChild(li);
    }
    grid.appendChild(frag);
    /* 溢れた紙は、薄れさせずに**丈を伸ばす**（2026-08-07 Kosei「影があって途中から
       読めない」）。縦書きは丈が決まって初めて列に折り返せるので、列が紙からはみ出す
       ＝丈が足りない、ということ。要る丈は「いま溢れている幅の比」でほぼ一発で出る。
       読むのは**足したぶんだけ**・書くのはそのあとで一度に——測ると書くを混ぜると、
       一枚ごとに版面を組み直させることになる（2026-08-03 カクツキの作法）。 */
    fitTall([...grid.children].slice(more?shown:0),'.rsheet-v');
    shown=upto;
    moreBox.hidden=shown>=list.length;
  }
  function collect(isl){
    // 『もう見ない』にした紙は、この面にも出さない（島の上と同じ宙を見せる）
    return isl.words.filter(el=>!el.classList.contains('mutedout'))
                    .map(el=>el._w).filter(w=>w&&(w.poem||'').trim());
  }
  function open(){
    const isl=(typeof sheetId!=='undefined'&&sheetId!=null)?islands.get(sheetId):null;
    if(!isl)return;
    if(typeof release==='function')release();   // 受け止めていたことばは、紙が出る前に放す
    back=document.activeElement;
    list=order(collect(isl));
    lead.innerHTML='<b>'+esc(isl.room.name)+'</b>の、ことば';
    paint(false);
    pane.hidden=false;
    pane.setAttribute('aria-hidden','false');
    document.body.classList.add('rsheet-on');
    // hidden を外したそのフレームでは遷移が始まらない（表示と同時に終値になる）
    requestAnimationFrame(()=>pane.classList.add('on'));
    closeBtn.focus();
  }
  function shut(){
    if(!pane.classList.contains('on'))return;
    queue.length=0;              // 書き待ちの列は持ち越さない（次に開くのは別の島かもしれない）
    pane.classList.remove('on');
    pane.setAttribute('aria-hidden','true');
    document.body.classList.remove('rsheet-on');
    setTimeout(()=>{ if(!pane.classList.contains('on'))pane.hidden=true; },360);
    // 開いた場所へ戻す（他の面と同じ作法）
    const b=back;back=null;
    if(b&&b.isConnected&&typeof b.focus==='function')b.focus();
  }
  openBtn&&openBtn.addEventListener('click',open);
  closeBtn.addEventListener('click',shut);
  moreBtn.addEventListener('click',()=>paint(true));
  sortBox.addEventListener('click',e=>{
    const b=e.target.closest('button[data-s]'); if(!b||b.dataset.s===sort)return;
    sort=b.dataset.s;
    if(sort==='rand')rseed=(Math.random()*0x7fffffff)>>>0||1;   // 二度押せば別の並び
    sortBox.querySelectorAll('[data-s]').forEach(x=>{
      const on=x.dataset.s===sort;
      x.classList.toggle('on',on); x.setAttribute('aria-pressed',on?'true':'false');
    });
    list=order(list);
    paint(false);
  });
  /* Escape はいちばん上の面が引き受ける（この面は宙より上に立っている）。
     捕捉段で受けて止めるのは、宙側の Escape が全景へ飛ばしてしまわないため。 */
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&pane.classList.contains('on')){ e.stopPropagation(); shut(); }
  },true);
  // 島から離れたら（全景へ戻った・別の島へ渡った）、開いたままの紙は畳む
  addEventListener('popstate',shut);
})();
