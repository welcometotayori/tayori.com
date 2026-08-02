/* 一枚の宙のふるまい。2026-07-31 に templates/canvas.html の <script> から出した
   （理由は canvas.css の頭と同じ）。読み込みは <head> の defer——面の終わりに置くのと
   走り出す時（解析の後）は同じで、**見つかるのが早い**ぶんだけ本文と並んで落ちてくる。
   利用者ごとに違う値（START_ROOM / LOGGED_IN / BOOT / OPEN_ID）だけは HTML 側の
   小さな括りに残してある。ここからは素の大域変数として見える。 */
'use strict';
const REDUCED=matchMedia('(prefers-reduced-motion: reduce)').matches;
const vp=document.getElementById('vp'), world=document.getElementById('world');
/* 画面の寸法は測って持っておく（2026-07-31 カクツキ修正）。clientWidth を
   毎フレーム読むと、そのたびレイアウトの確定を強いることがある——寸法が
   変わるのは resize の時だけなのだから、その時だけ測り直す。 */
let VW=vp.clientWidth, VH=vp.clientHeight;
/* 携帯はアドレスバーの出入りでも resize が飛ぶ（スクロールのたび何度も）。
   測り直しと敷き直しは落ち着いてから一度だけ（2026-07-31 夜）。 */
let rsT=0;
addEventListener('resize',()=>{
  VW=vp.clientWidth;VH=vp.clientHeight;
  clearTimeout(rsT);
  rsT=setTimeout(()=>{
    // 画面の向きが変われば紙片の敷き詰めも変わる（縦長は一行2〜3枚）
    if(sheetId!=null&&islands.has(sheetId))sheetLayout(islands.get(sheetId));
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
        const on=new Set(d.rooms);
        islands.forEach((isl,id)=>isl.el.classList.toggle('lit',on.has(id)));
      }
    }
  }).catch(()=>{});
}
setTimeout(pollLantern,4000);
setInterval(pollLantern,30000);

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

/* ── 島とことばを置く ──────────────────────────────
   座標はサーバの決定論（単位円）。島の位置もことばの押し出しも、順序を
   固定した同じ手順で解くので、同じ宙は誰の画面でもほぼ同じ地形になる。 */
let islands=new Map();   // room_id -> {el,wordsEl,cx,cy,R,words:[]}
function islandR(n){ return 150+62*Math.sqrt(n); }
function placeIslands(rooms,counts){
  // 黄金角の渦に沿って歩き、先客と重ならない最初の席に座る（決定論・部屋は生まれた順）
  const placed=[];
  rooms.forEach((r,i)=>{
    const R=islandR(counts.get(r.id)||0);
    let t=i===0?0:1.2;
    let x=0,y=0;
    for(;;){
      x=64*t*Math.cos(t*2.39996); y=64*t*Math.sin(t*2.39996)*0.86;
      if(placed.every(p=>Math.hypot(x-p.x,y-p.y)>p.R+R+320))break;
      t+=0.18;
    }
    placed.push({x,y,R,room:r});
  });
  return placed;
}
function build(rooms,words){
  const counts=new Map();
  words.forEach(w=>counts.set(w.room,(counts.get(w.room)||0)+1));
  const seats=placeIslands(rooms.filter(r=>counts.get(r.id)||!r.archived),counts);
  seats.forEach(s=>{
    const isl=document.createElement('div');
    isl.className='isl';
    isl.style.left=s.x+'px'; isl.style.top=s.y+'px';
    // 灯の気配（部屋の灯の色・最大3）。遠景で島を見分ける唯一の色
    const glow=document.createElement('div'); glow.className='isl-glow';
    (s.room.lights||[]).slice(0,3).forEach((c,j)=>{
      const g=document.createElement('span');
      const d=Math.round(s.R*1.5);
      g.style.width=g.style.height=d+'px';
      g.style.left=((j-1)*s.R*0.35)+'px'; g.style.top=(j%2?s.R*0.2:-s.R*0.15)+'px';
      g.style.background='radial-gradient(circle,'
        +String(c).replace('hsl(','hsla(').replace(')',',.13)')+' 0%,transparent 70%)';
      glow.appendChild(g);
    });
    isl.appendChild(glow);
    const wl=document.createElement('div'); wl.className='isl-w'; isl.appendChild(wl);
    const nm=document.createElement('div'); nm.className='isl-nm'; nm.textContent=s.room.name;
    isl.appendChild(nm);
    world.appendChild(isl);
    islands.set(s.room.id,{el:isl,wordsEl:wl,nm:nm,cx:s.x,cy:s.y,R:s.R,words:[],room:s.room});
  });
  words.forEach(w=>{
    const isl=islands.get(w.room); if(!isl)return;
    mkWord(isl,w);
  });
  // まず家の座標に置く（押し出しは描画後に一度だけ・relax()）
  islands.forEach(isl=>isl.words.forEach(el=>{
    el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  }));
}
/* 一枚のことばを、島の上に建てる。build() から切り出した（2026-07-31）——
   島に降りるたびに岸を組み直すので、建てるのは立ち上がりの一度きりではなくなった。 */
function mkWord(isl,w){
  const el=document.createElement('div');
  el.className='w'+(w.vertical?'':' h')+(w.pd?' pd':'');
  el.textContent=w.poem;
  el.style.setProperty('--a',alphaOf(w.sink).toFixed(3));
  const c=tintOf(w.color);
  if(c){ el.style.color=c.text; el.style.textShadow='0 0 16px '+c.glow; }
  el.style.setProperty('--pp',paperOf(w));   // 島に降りた時の、紙の色
  el.setAttribute('role','button'); el.setAttribute('tabindex','0');
  el.setAttribute('aria-label',(w.pd
    ? '流れ着いたことば：'+w.poem+'（'+w.author+'『'+w.work+'』より）'
    : 'tayori-たより- のことば：'+w.poem)
    +'。ひらくと、棚にとっておく ができます。');
  el._w=w;
  // 家の座標：色相が方位、あとはハッシュ。**島の中心からの相対**で持つ——
  // .w の包含ブロックは .isl（もう島の位置に居る）なので、世界座標で書くと
  // 島の中心が二重に足されて、ことばが島の外へ筋になって流れ出す（初版で実際に起きた）
  el._hx=w.x*(isl.R-70);
  el._hy=w.y*(isl.R-70);
  el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  isl.wordsEl.appendChild(el);
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
      sheetLayout(isl,true);             // 新しい紙も、いまの束に混ぜて並べ直す
      apply();
    }).catch(()=>{shoreBusy=null;});
}
/* 重なりの押し出し。新しいことば（表層）から順に席を取り、あとから来た沈んだ
   ことばが空いた席へ逃げる。逃げ方は一直線ではなく家のまわりの渦巻き——
   直線に押すと、同じ向きに並んだことばが島の外まで筋になって流れ出す（初版で実際に
   起きた）。渦なら家の近くの空席に収まり、島は塊のまま。順序固定＝決定論。 */
function relax(){
  /* 測るのと書くのを混ぜない（2026-07-31 夜・立ち上がりの固まりの真因）。
     旧実装は「一枚測って、一枚置く」を数百回くり返していた。置いた瞬間に版面は
     汚れるので、次の一枚を測るたびブラウザは版面を組み直す——数百回の強制レイアウト
     ＝立ち上がりで宙が数秒沈黙する。先に全部測り、計算し、最後にまとめて置く。 */
  const measured=new Map();
  islands.forEach(isl=>isl.words.forEach(el=>{
    const w=el.offsetWidth,h=el.offsetHeight;
    el._sw=w; el._sh=h;                // 散らばりの姿の寸法（画面外の間引きに使う）
    measured.set(el,[w,h]);
  }));
  const writes=[];
  islands.forEach(isl=>{
    const done=[];
    const order=[...isl.words].sort((a,b)=>(b._w.sink-a._w.sink)||(a._w.id<b._w.id?-1:1));
    order.forEach(el=>{
      const m=measured.get(el)||[0,0], w=m[0], h=m[1];
      let x=el._hx,y=el._hy;
      const base=Math.atan2(el._hy,el._hx);
      const hits=()=>done.some(d=>Math.abs(x-d.x)<(w+d.w)/2+18&&Math.abs(y-d.y)<(h+d.h)/2+18);
      let t=0;
      while(hits()&&t<160){
        t++;
        const rr=34+t*7.5;
        const aa=base+t*2.39996;
        // 縦書きの塊は縦に長いので、横へ広めに・縦へ狭めに逃がす（島を楕円に保つ）
        x=el._hx+Math.cos(aa)*rr*1.15;
        y=el._hy+Math.sin(aa)*rr*0.6;
      }
      el._hx=x; el._hy=y;
      writes.push([el,x,y]);
      done.push({x,y,w,h});
    });
  });
  writes.forEach(a=>{a[0].style.left=a[1]+'px';a[0].style.top=a[2]+'px';});
}

/* ── 紙片のグリッド（2026-07-31）──────────────────────
   島に降りる（＝焦点の島になる）と、そのことばたちは散らばりから紙片の
   敷き詰めへ並び替わる。新しいものが右上、沈んだものほど左下、漂流物は最後
   ——縦書きの読み順（右→左）で流す。位置は transform で動かす＝家（_hx/_hy）は
   保ったまま。離れれば、それぞれの家へ静かに帰る。
   寸法は紙片の姿（.sheet のpadding込み）で測るので、classを立ててから読む。 */
let sheetId=null, sheetWant=null, sheetT=0;
function posOf(isl,el){
  return (isl&&isl.sheeted&&el._gx!=null)
    ?'translate('+(el._gx-el._hx)+'px,'+(el._gy-el._hy)+'px)':'';
}
function sheetLayout(isl,stagger){
  const gap=26;
  /* 束の幅は「島の大きさ」ではなく「いま見えている画面の幅」で決める（2026-07-31 夜）。
     旧実装は島の半径（R*2.1）から決めていたので、島の大きさ次第で束が画面の
     半分ほどの幅に痩せ、右側が大きく空いて、そのぶん下へ長く伸びていた
     ——「重さが片側に寄る」の正体。画面の幅（の88%）を世界の寸法に直して使えば、
     どの島でも束は画面いっぱいに広がり、丈もそのぶん縮む。
     縦長の画面（携帯）は下限が狭いので、結果として一行2〜3枚のままになる。 */
  const portrait=VH>VW;
  const maxW=Math.max(portrait?340:540,
                      Math.min(portrait?900:1400,(VW*0.88)/Math.max(K,0.08)));
  const order=isl.words
    .filter(el=>!el.classList.contains('mutedout'))
    .sort((a,b)=>{
      if(!!a._w.pd!==!!b._w.pd)return a._w.pd?1:-1;          // 漂流物は人のことばの後
      return (b._w.sink-a._w.sink)||(String(a._w.id)<String(b._w.id)?-1:1);
    });
  // 測るのは一度に全部（間に style を書かない＝強制レイアウトを起こさない）
  const sizes=order.map(el=>({el,w:el.offsetWidth,h:el.offsetHeight}));
  sizes.forEach(it=>{it.el._pw=it.w;it.el._ph=it.h;});   // 紙片の姿の寸法（間引きに使う）
  let x=0,y=0,rowH=0;const rows=[];let row=[];
  sizes.forEach(it=>{
    if(row.length&&x+it.w>maxW){rows.push({items:row,w:x-gap});row=[];x=0;y+=rowH+gap;rowH=0;}
    it.x=x;it.y=y;row.push(it);
    x+=it.w+gap;rowH=Math.max(rowH,it.h);
  });
  if(row.length)rows.push({items:row,w:x-gap});
  const totalH=y+rowH;
  /* 右の端を、どの行もそろえる（2026-07-31 夜・利用者FB「どこから読めばいいか分からない」）。
     旧実装は行ごとに中央へ寄せていたので、行の長さが違うぶん右の端がぎざぎざになり、
     読み始めの一本目（縦書きは右上）が行ごとに動いていた。紙の束の右端は一本の線に
     そろえ、余りは最後の行の左に出す——縦書きの紙面と同じ姿になる。 */
  const blockW=rows.reduce((m,r)=>Math.max(m,r.w),0);
  let idx=0;
  rows.forEach(r=>{
    r.items.forEach(it=>{
      // 右から埋める（vertical-rl の読み順）。行の中では上をそろえる
      const tx=blockW/2-it.x-it.w, ty=it.y-totalH/2;
      it.el._gx=tx; it.el._gy=ty;
      /* 「次々流れてくる」（2026-07-31）：島に降りた瞬間、紙片は一斉に整列するのでは
         なく、読み順（右上→左下）に少しずつ席へ流れ着く。遅らせるのは transform だけ
         （.w の transition-property の並びは opacity, transform の二つ——同日夜に
         色・地・影の遷移をやめたので、遅れの並びも二つに詰めた）。
         薄さまで遅らせると、闇に光る字が紙の上に居残って読めない瞬間ができる。 */
      if(stagger&&!REDUCED){
        it.el.style.transitionDelay='0s,'+Math.min(idx*0.03,0.5).toFixed(2)+'s';
        idx++;
      }
      it.el.style.transform=posOf(isl,it.el);
    });
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
  moving(isl);
  // 読んでいる間、宙は一つの様式だけになる（まわりの島は名と灯＝地形に還る）
  document.body.classList.add('reading');
  isl.el.classList.add('sheet');
  sheetLayout(isl,true);
  // 流れ着き終わったら遅れは畳む（残すと、受け止めや検索の応答まで遅れて見える）
  clearTimeout(isl._stgT);
  isl._stgT=setTimeout(()=>isl.words.forEach(el=>{el.style.transitionDelay='';}),1700);
  apply();
  reshore(isl);          // 降りるたび、岸には別のものが寄っている
}
function unsheet(isl){
  isl.sheeted=false;
  moving(isl);
  document.body.classList.remove('reading');
  isl.el.classList.remove('sheet');
  clearTimeout(isl._stgT);
  isl.words.forEach(el=>{el.style.transform='';el.style.transitionDelay='';});
  apply();
}
/* ── 寄る・引く・つまむ ─────────────────────────── */
let K=0.5, PX=0, PY=0;             // 倍率と、世界の原点の画面上の位置
const KMIN=0.1, KMAX=2.4;
/* カクツキ修正（2026-07-31）その1：描き替えは1フレームに1度だけ。
   wheel も pointermove もフレームより速く飛んでくる——来た数だけ style を
   書き直すと、指の速さぶんだけ余計に描き直しが積まれてつっかえる。
   rAF で束ね、最後の値だけを次のフレームで一度書く。 */
let applyQ=false, ikLast=0, farOn=null, crispOn=null;
function apply(){
  if(applyQ)return;
  applyQ=true;
  requestAnimationFrame(applyNow);
}
function applyNow(){
  applyQ=false;
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
  const ik=Math.min(4.5,Math.max(1,0.62/K));
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
    const off=!onScreen(isl.cx-r,isl.cy-r,r*2,r*2);
    if(isl.off!==off){
      isl.off=off;
      if(!off)isl.nmDirty=true;     // 戻ってきた島の名は、いまの倍率で書き直す
      isl.el.style.visibility=off?'hidden':'';
    }
  });
}
function cullWords(){
  islands.forEach(isl=>{
    if(isl.off)return;              // 島ごと畳んである（中は測らない）
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
function zoomAt(cx,cy,factor){
  const k=Math.max(KMIN,Math.min(KMAX,K*factor));
  const vw=VW/2, vh=VH/2;
  const wx=(cx-vw-PX)/K, wy=(cy-vh-PY)/K;   // カーソルの下の世界座標を動かさない
  PX=cx-vw-wx*k; PY=cy-vh-wy*k; K=k;
  apply();
}
vp.addEventListener('wheel',e=>{
  e.preventDefault();
  zoomAt(e.clientX,e.clientY,Math.exp(-e.deltaY*0.0016));
},{passive:false});
document.getElementById('zin').addEventListener('click',()=>zoomAt(VW/2,VH/2,1.45));
document.getElementById('zout').addEventListener('click',()=>zoomAt(VW/2,VH/2,1/1.45));
// つまむ・ひきずる（1本＝パン、2本＝ピンチ。タップ判定は移動6px未満）
const ptrs=new Map(); let moved=0, pinchD=0;
vp.addEventListener('pointerdown',e=>{
  ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});
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
  if(el)hold(el,x,y); else release();
}
function ptrUp(e){
  const had=ptrs.has(e.pointerId);
  ptrs.delete(e.pointerId); pinchD=0;
  if(!ptrs.size)vp.classList.remove('drag');
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
}
/* 出典の刻印（§4.4）。触れて開いた、その一度だけ隅に刻む。外へ出る導線は作らない */
function srcMark(){
  if(!held||!held._w.pd)return '';
  return '<p class="hold-src">— '+esc(held._w.author)+'『'+esc(held._w.work)+'』より</p>';
}
const IC_SHELF='<span class="ic ic-shelf" aria-hidden="true"><span class="p"></span><span class="b"></span></span>';
const T_KEEP_OFF='棚にとっておく', T_KEEP_ON='棚にあります',
      T_MUTE_OFF='もう見ない',     T_MUTE_ON='また見る';
function renderHold(){
  if(!held)return;
  if(!LOGGED_IN){
    holdBox.innerHTML=srcMark()+'<p class="hold-note">このことばを、<wbr>'+
      '自分の<wbr>棚に<wbr>とっておけます。<br><a href="/?start=1&amp;new=1">はじめる</a></p>';
    return;
  }
  const s=kept.has('drift:'+held._w.id), mu=muted.has(held._w.id);
  holdBox.innerHTML=srcMark()+
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
function placeHold(tx,ty){
  if(!held)return;
  const r=held.getBoundingClientRect(), W=innerWidth, H=innerHeight;
  holdBox.hidden=false;
  holdBox.classList.remove('on');
  // 寸法は offsetWidth/Height で測る：畳んでいる間は scale(.97) が掛かっていて、
  // getBoundingClientRect だと3%小さい札を基準に置いてしまう（端をそろえると数px狂う）
  const b={width:holdBox.offsetWidth,height:holdBox.offsetHeight};
  let x,y,ax,oy;
  if(r.left-b.width-30>=12&&r.bottom-b.height>=12){   // ① 左脇・下そろえ（本命）
    x=r.left-b.width-30; y=r.bottom-b.height;
    ax=r.left; oy=b.height;                          // 立ち上がりの起点＝紙の左下の角
  }else if(r.bottom+30+b.height<=H-12){              // ② 左に無ければ真下・左端そろえ
    x=r.left; y=r.bottom+30; ax=r.left; oy=0;
  }else if(tx!=null&&ty!=null){                      // ③ 紙が画面より高い＝触れた指の下
    y=(ty+20+b.height<=H-12)?ty+20:ty-20-b.height;
    x=tx-b.width/2; ax=tx; oy=0;
  }else{                                             // 触れた場所が分からない時（鍵盤から）
    x=r.left+r.width/2-b.width/2; y=Math.max(12,r.top-b.height-6);
    ax=r.left+r.width/2; oy=0;
  }
  x=Math.min(Math.max(12,x),Math.max(12,W-b.width-12));
  y=Math.min(Math.max(12,y),Math.max(12,H-b.height-12));
  holdBox.style.left=x+'px'; holdBox.style.top=y+'px';
  holdBox.style.transformOrigin=(ax-x)+'px '+oy+'px';
  setTimeout(()=>{ if(held)holdBox.classList.add('on'); },16);
}
/* 開いたあとで丈が伸びた時に、下へはみ出したぶんだけ静かに上げる（横は動かさない
   ＝触れた場所との関係を崩さない）。収まっている時は何もしない。 */
function keepInView(){
  if(holdBox.hidden)return;
  const b=holdBox.getBoundingClientRect(), H=innerHeight;
  if(b.bottom<=H-12)return;
  const top=Math.max(12,Math.min(b.top,H-12-b.height));
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
      keepInView();   // 付箋と置き場所のぶん丈が伸びる。画面の外へはみ出させない
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
  placeHold(tx,ty);
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
  let i=0,last=null,e=0,fin=false;
  const tn=document.createTextNode(norm[0].v);
  const car=document.createElement('span'); car.className='tcaret';
  const finish=()=>{
    if(fin)return; fin=true;
    el.textContent=text; el.classList.remove('nijimi');
    if(done)done();
  };
  el.textContent=''; el.appendChild(tn); el.appendChild(car);
  setTimeout(()=>{ if(el._run===run&&el.isConnected&&last===null) finish(); },12000);
  requestAnimationFrame(function frame(now){
    if(el._run!==run||!el.isConnected){el.classList.remove('nijimi');return;}
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
function flyTo(isl){
  release();
  gatherHide();
  const k=Math.max(0.55,Math.min(1.1,
    Math.min(VW,VH)/(isl.R*1.12*2.15)));
  world.style.transition=REDUCED?'none':'transform 1.1s var(--ease)';
  document.body.classList.add('flying');
  // 滑走の間は「出発の眺め」と「到着の眺め」の二つで間引く（通り道が消えないように）
  views=[[PX,PY,K],[-isl.cx*k,-isl.cy*k,k]];
  K=k; PX=-isl.cx*K; PY=-isl.cy*K;
  applyNow();
  setTimeout(()=>{ world.style.transition='';
                   document.body.classList.remove('flying');
                   views=null; applyNow(); markMenuRoom(); },1200);
}
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
  // 島の心の近く。種は本文＝置き直しても同じ場所（決定論の作法に合わせる）
  const aa=(fnv(poem)%360)*Math.PI/180, rr=40+(fnv(poem+'r')%60);
  el._hx=Math.cos(aa)*rr; el._hy=Math.sin(aa)*rr;
  el.style.left=el._hx+'px'; el.style.top=el._hy+'px';
  el.style.zIndex=6;
  isl.wordsEl.appendChild(el);
  isl.words.push(el);
  // 紙片に並んでいる島なら、新しい一枚の席も敷き直す（寸法は本文の丈で測る）
  if(isl.sheeted){ el.textContent=poem; sheetLayout(isl); }
  else{ el.textContent=poem; el._sw=el.offsetWidth; el._sh=el.offsetHeight; }
  playType(el,poem,steps,()=>{});
}
setInterval(()=>{
  tick=(tick+1)%4;
  if(REDUCED||document.hidden||K<0.55)return;
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
  // 焦点の島＝画面の中心にいちばん近く、その懐（1.6R）に入っている島
  let best=null,bd=1e9;
  const cx=(0-PX)/K, cy=(0-PY)/K;   // 画面中央の世界座標（worldの原点は画面中央）
  islands.forEach((isl,id)=>{
    const d=Math.hypot(isl.cx-cx,isl.cy-cy);
    if(d<bd){bd=d;best={isl,id,d};}
  });
  // 「島に居る」の判定は倍率の絶対値ではなく、**島が視界の大半を占めているか**で見る
  // （K のしきい値は画面の広さで破綻する——424px の電話と 1280px の机では別の倍率になる）
  const big=best&&best.isl.R*K>0.32*Math.min(VW,VH);
  const f=(best&&big&&best.d<best.isl.R*1.6)?best:null;
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
    .finally(()=>{ seekBusy=false; });
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
  function drawCaret(){
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
    // 打鍵の直後は必ず点いている状態から数え直す（書いている最中に消えていると不安になる）
    vcaret.style.animation='none';void vcaret.offsetHeight;vcaret.style.animation='';
  }
  // 変換中も measure は読むだけなので、筆先は変換の字を追ってよい
  ['input','click','keyup','focus','scroll',
   'compositionstart','compositionupdate','compositionend']
    .forEach(ev=>ta.addEventListener(ev,drawCaret));
  ta.addEventListener('blur',()=>{vcaret.style.display='none';});
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
  const CH_PER_COL=20, MAX_COL=4;
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
                  btn.classList.remove('away');vcaret.style.display='none';
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
    vcaret.style.display='none';
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
    const title=castTitle.value.trim();      // 題は任意。無いまま放ってよい（v2.2 §2.1）
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
        castTitle.value='';
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
  fillMenuRooms();   // 戸の中のジャンル（見えていない島への道）
  fetchMine();   // 自分の棚の控え（棚にあります の札のため）
  requestAnimationFrame(()=>{ relax();
    /* 2026-07-31：宙はミクロから始まる。全景（マクロ）から入ると、最初に見えるのが
       「地図」で、ことばそのものに触れるまでが遠かった——降りた島の紙片から始め、
       引けば（−・つまむ）いつでも全景へ出られる。降りる先は、いちばん新しい
       ことばが昇った島＝いま息をしている場所（?room= は今までどおりその島へ）。 */
    let fx=0,fy=0,fr=900;
    if(START_ROOM&&islands.has(+START_ROOM)){
      const i=islands.get(+START_ROOM); fx=i.cx; fy=i.cy; fr=i.R*1.12; }
    else{
      let best=null,bs=-1;
      islands.forEach(isl=>isl.words.forEach(el=>{
        if(!el._w.pd&&el._w.sink>bs){bs=el._w.sink;best=isl;}
      }));
      if(best){ fx=best.cx; fy=best.cy; fr=best.R*1.12; }
      else{ let mx=0; islands.forEach(i=>{mx=Math.max(mx,Math.hypot(i.cx,i.cy)+i.R);}); fr=mx||900; }
    }
    K=Math.max(KMIN,Math.min(1.2,Math.min(VW,VH)/(fr*2.15)));
    PX=-fx*K; PY=-fy*K;
    applyNow();   // 立ち上がりだけは同じフレームで描く（1フレームの素の宙を見せない）
  });
}).catch(()=>{
  const p=document.createElement('p');
  p.style.cssText='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);color:var(--ink-3);font-size:13px;letter-spacing:.14em';
  p.textContent='いまは、宙をひらけません。';
  document.body.appendChild(p);
});
