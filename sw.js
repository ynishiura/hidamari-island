// ひだまり島ぐらし：オフラインでも遊べるようにする仕組み（Service Worker）
// ・ゲーム本体は「index.html」という1つの名前でだけ保存する（二重に持たない）
// ・絵（img/）と音楽（bgm/）は別のファイル。名前に中身の印が付いているので、版が上がっても消さずに使いつづける（変わった物だけ新しい名前で読む）
// ・起動のときは新しい版を取りに行くが、3秒で返事がなければあきらめて、保存してある版ですぐ起動する。
//   取りに行った分は裏で受け取りつづけ、届いたら保存しておく（次の起動から新しい版になる）
// ・版が上がるとキャッシュ名が変わり、古い版の保存分は消える。文字のフォントは版に関係なく残す
// キャッシュ名の版の番号は build.sh が埋める
const CACHE = 'hidamari-1.058';
const FONTS = 'hidamari-fonts';
const ASSETS = 'hidamari-assets';   // 絵と音楽（版が上がっても残す）
const PAGE = './index.html';
const CORE = [PAGE, './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png', './icons/maskable-512.png', './icons/apple-touch-icon.png'];
const WAIT_MS = 3000;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== FONTS && k !== ASSETS && k !== 'hidamari-notify').map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// ゲーム本体：新しい版を取りに行く。3秒待っても届かなければ保存分を出す（保存分がなければ届くまで待つ）
function page(e) {
  const net = fetch(e.request).then(r => {
    if (r && r.ok) { const cp = r.clone(); e.waitUntil(caches.open(CACHE).then(c => c.put(PAGE, cp))); }
    return r;
  });
  e.waitUntil(net.catch(() => {}));
  const saved = caches.open(CACHE).then(c => c.match(PAGE));
  const good = net.then(r => r.ok ? r : saved.then(s => s || r));          // サーバーがエラーを返したときも保存分
  const late = new Promise(res => setTimeout(res, WAIT_MS)).then(() => saved).then(s => s || good);
  return Promise.race([good, late]).catch(() => saved.then(s => s || Response.error()));
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (req.mode === 'navigate') { e.respondWith(page(e)); return; }
  const url = new URL(req.url);
  const store = /fonts\.(googleapis|gstatic)\.com$/.test(url.hostname) ? FONTS : /\/(img|bgm)\//.test(url.pathname) ? ASSETS : CACHE;
  // そのほか（アイコン・フォントなど）：保存してあればそれを使い、なければ取ってきて保存
  e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => {
    if (r && (r.ok || r.type === 'opaque')) { const cp = r.clone(); e.waitUntil(caches.open(store).then(c => c.put(req, cp))); }
    return r;
  })));
});

// ---- お知らせ（notify.js） ----
// ゲームが Cache に置いた状態（__notify_state.json）を見て、毎日のおまけをまだ受け取っていなければ、決めた時刻のあとに1日1回知らせる
const NOTIFY_CACHE = 'hidamari-notify', NOTIFY_URL = './__notify_state.json';
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
async function dailyCheck() {
  const c = await caches.open(NOTIFY_CACHE), r = await c.match(NOTIFY_URL); if (!r) return;
  const st = await r.json(); if (!st.on) return;
  const d = today(); if (st.claimed === d || st.notified === d) return;
  if (new Date().getHours() < (st.hour || 19)) return;
  const y = new Date(); y.setDate(y.getDate() - 1); const yd = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, '0')}-${String(y.getDate()).padStart(2, '0')}`;
  const next = st.claimed === yd ? (st.streak || 0) + 1 : 1;
  await self.registration.showNotification('ひだまり島ぐらし', { body: next > 1 ? `今日のおまけが届いているよ（連続 ${next}日目）。島で待っているね！` : '今日のおまけが届いているよ。島で待っているね！', tag: 'daily', icon: 'icons/icon-192.png', badge: 'icons/icon-192.png' });
  st.notified = d; await c.put(NOTIFY_URL, new Response(JSON.stringify(st), { headers: { 'content-type': 'application/json' } }));
}
self.addEventListener('periodicsync', e => { if (e.tag === 'hidamari-daily') e.waitUntil(dailyCheck().catch(() => {})); });
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => { for (const w of ws) if ('focus' in w) return w.focus(); return self.clients.openWindow('./'); }));
});
