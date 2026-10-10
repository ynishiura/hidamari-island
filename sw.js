// ひだまり島ぐらし：オフラインでも遊べるようにする仕組み（Service Worker）
// ・ゲーム本体（画像と音も中に入っている）は「index.html」という1つの名前でだけ保存する（二重に持たない）
// ・起動のときは新しい版を取りに行くが、3秒で返事がなければあきらめて、保存してある版ですぐ起動する。
//   取りに行った分は裏で受け取りつづけ、届いたら保存しておく（次の起動から新しい版になる）
// ・版が上がるとキャッシュ名が変わり、古い版の保存分は消える。文字のフォントは版に関係なく残す
// キャッシュ名の版の番号は build.sh が埋める
const CACHE = 'hidamari-0.951';
const FONTS = 'hidamari-fonts';
const PAGE = './index.html';
const CORE = [PAGE, './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png', './icons/maskable-512.png', './icons/apple-touch-icon.png'];
const WAIT_MS = 3000;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== FONTS).map(k => caches.delete(k)))).then(() => self.clients.claim()));
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
  const store = /fonts\.(googleapis|gstatic)\.com$/.test(url.hostname) ? FONTS : CACHE;
  // そのほか（アイコン・フォントなど）：保存してあればそれを使い、なければ取ってきて保存
  e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => {
    if (r && (r.ok || r.type === 'opaque')) { const cp = r.clone(); e.waitUntil(caches.open(store).then(c => c.put(req, cp))); }
    return r;
  })));
});
