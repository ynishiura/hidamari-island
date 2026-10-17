# -*- coding: utf-8 -*-
"""フレンドとメールの保存先。

・環境変数 DATABASE_URL があれば PostgreSQL（Neon）に保存する（本番）
・なければ relay/data/local.db（SQLite）に保存する（手元での確認用）
表の名前は hd_ で始める（同じデータベースに、ほかのゲームの表があっても混ざらないように）。

  hd_players  1人1行。id（友だちID）・合言葉のハッシュ・名前・フレンド・申請（JSON）
  hd_mail     メール1通1行。30日たったものは消す
  hd_xfer     運営が発行した引継ぎコード（7日間・1回限り）

hd_players には後から足した列がある：banned（ブロック）・play_sec（プレイ時間・秒。まだ届いていなければ NULL）・
device（利用端末）・save（ゲームのセーブ＝引継ぎコードの形）・save_at
自動削除：プレイ時間が3分未満のまま、3日以上つないでいないアカウントは消す（play_sec が NULL のものは消さない）
"""
import hashlib, json, os, secrets, sqlite3, threading, time

XFER_DAYS = 7                 # 引継ぎコードの有効期限（日）
AUTO_DEL_PLAY = 180           # 自動削除：プレイ時間がこれ（秒）未満で
AUTO_DEL_DAYS = 3             #           この日数以上つないでいないアカウント

ID_CHARS = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'    # まぎらわしい 0・1・I・O は使わない
MAIL_KEEP_DAYS = 30


def new_id():
    s = ''.join(secrets.choice(ID_CHARS) for _ in range(6))
    return f'HD-{s[:3]}{s[3:]}'


def norm_id(s):
    s = ''.join(c for c in str(s or '').upper() if c.isalnum())
    if s.startswith('HD'):
        s = s[2:]
    return f'HD-{s}' if len(s) == 6 and all(c in ID_CHARS for c in s) else None


def hsec(secret):
    return hashlib.sha256(('hidamari-friend:' + str(secret)).encode()).hexdigest()


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.url = os.environ.get('DATABASE_URL', '').strip()
        self.pg = bool(self.url)
        self.conn = None
        self._connect()
        self._schema()

    # ---- つなぐ ----
    def _connect(self):
        if self.pg:
            import psycopg
            self.conn = psycopg.connect(self.url, autocommit=True)
        else:
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
            os.makedirs(d, exist_ok=True)
            self.conn = sqlite3.connect(os.path.join(d, 'local.db'), check_same_thread=False, isolation_level=None)

    @property
    def ph(self):
        return '%s' if self.pg else '?'

    def q(self, sql, args=(), one=False, many=False):
        sql = sql.replace('?', self.ph) if self.pg else sql
        with self.lock:
            for attempt in (0, 1):
                try:
                    cur = self.conn.cursor()
                    cur.execute(sql, args)
                    if one:
                        return cur.fetchone()
                    if many:
                        return cur.fetchall()
                    return None
                except Exception:
                    if attempt or not self.pg:
                        raise
                    try:                    # Neon はしばらく使わないと接続を切るので、つなぎ直してもう一度
                        self.conn.close()
                    except Exception:
                        pass
                    self._connect()

    def _schema(self):
        auto = 'BIGSERIAL PRIMARY KEY' if self.pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
        self.q('''CREATE TABLE IF NOT EXISTS hd_players (
            id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, name TEXT, data TEXT NOT NULL,
            created_at BIGINT NOT NULL, seen_at BIGINT NOT NULL)''')
        self.q(f'''CREATE TABLE IF NOT EXISTS hd_mail (
            id {auto}, from_id TEXT NOT NULL, to_id TEXT NOT NULL, from_name TEXT, to_name TEXT,
            body TEXT NOT NULL, at BIGINT NOT NULL, read INTEGER NOT NULL DEFAULT 0)''')
        for col in ('banned INTEGER NOT NULL DEFAULT 0', 'play_sec BIGINT', 'device TEXT', 'save TEXT', 'save_at BIGINT'):
            try:                                   # 古い表には後から足す（もうあれば何もしない）
                self.q(f'ALTER TABLE hd_players ADD COLUMN {col}')
            except Exception:
                pass
        self.q('CREATE TABLE IF NOT EXISTS hd_xfer (code TEXT PRIMARY KEY, pid TEXT NOT NULL, created_at BIGINT NOT NULL, '
               'expires_at BIGINT NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
        self.q('CREATE INDEX IF NOT EXISTS hd_mail_to ON hd_mail (to_id, at)')
        self.q('CREATE INDEX IF NOT EXISTS hd_mail_from ON hd_mail (from_id, at)')

    # ---- プレイヤー ----
    def create(self, name):
        for _ in range(20):
            pid = new_id()
            if not self.q('SELECT 1 FROM hd_players WHERE id=?', (pid,), one=True):
                sec = secrets.token_urlsafe(24)
                now = int(time.time())
                self.q('INSERT INTO hd_players (id, secret_hash, name, data, created_at, seen_at) VALUES (?,?,?,?,?,?)',
                       (pid, hsec(sec), name, json.dumps({'friends': [], 'reqIn': [], 'reqOut': []}), now, now))
                return pid, sec
        raise RuntimeError('id')

    def check(self, pid, secret):
        r = self.q('SELECT secret_hash FROM hd_players WHERE id=?', (pid,), one=True)
        return bool(r) and secrets.compare_digest(r[0], hsec(secret))

    def get(self, pid):
        r = self.q('SELECT name, data FROM hd_players WHERE id=?', (pid,), one=True)
        if not r:
            return None
        d = json.loads(r[1] or '{}')
        for k in ('friends', 'reqIn', 'reqOut'):
            d.setdefault(k, [])
        d['name'] = r[0] or 'だれか'
        return d

    def put(self, pid, d):
        name = d.get('name')
        data = {k: d[k] for k in ('friends', 'reqIn', 'reqOut')}
        self.q('UPDATE hd_players SET data=?, name=? WHERE id=?', (json.dumps(data, ensure_ascii=False), name, pid))

    def touch(self, pid, name=None):
        if name:
            self.q('UPDATE hd_players SET seen_at=?, name=? WHERE id=?', (int(time.time()), name, pid))
        else:
            self.q('UPDATE hd_players SET seen_at=? WHERE id=?', (int(time.time()), pid))

    def name_taken(self, name, except_id=None):
        """その名前を、ほかの人（except_id 以外）が使っていたら True。大文字小文字・全角半角のちがいは同じ名前とみなす。"""
        import unicodedata
        key = unicodedata.normalize('NFKC', str(name or '')).strip().casefold()
        if not key:
            return False
        rows = self.q('SELECT id, name FROM hd_players WHERE name IS NOT NULL', many=True) or []
        for pid, nm in rows:
            if pid != except_id and unicodedata.normalize('NFKC', nm or '').strip().casefold() == key:
                return True
        return False

    def names(self, ids):
        out = {}
        for i in ids:
            r = self.q('SELECT name FROM hd_players WHERE id=?', (i,), one=True)
            if r:
                out[i] = r[0] or 'だれか'
        return out

    # ---- メール ----
    def add_mail(self, frm, to, from_name, to_name, body):
        self.q('INSERT INTO hd_mail (from_id, to_id, from_name, to_name, body, at, read) VALUES (?,?,?,?,?,?,0)',
               (frm, to, from_name, to_name, body, int(time.time())))

    def sent_today(self, frm):
        r = self.q('SELECT COUNT(*) FROM hd_mail WHERE from_id=? AND at>?', (frm, int(time.time()) - 86400), one=True)
        return r[0] if r else 0

    def mailbox(self, pid, n=50):
        inbox = self.q('SELECT id, from_id, from_name, body, at, read FROM hd_mail WHERE to_id=? ORDER BY at DESC, id DESC LIMIT ?', (pid, n), many=True)
        outbox = self.q('SELECT id, to_id, to_name, body, at FROM hd_mail WHERE from_id=? ORDER BY at DESC, id DESC LIMIT ?', (pid, n), many=True)
        return ([{'id': r[0], 'from': r[1], 'fromName': r[2], 'body': r[3], 'at': r[4], 'read': bool(r[5])} for r in inbox],
                [{'id': r[0], 'to': r[1], 'toName': r[2], 'body': r[3], 'at': r[4]} for r in outbox])

    def unread(self, pid):
        r = self.q('SELECT COUNT(*) FROM hd_mail WHERE to_id=? AND read=0', (pid,), one=True)
        return r[0] if r else 0

    def mark_read(self, pid, ids):
        for i in ids[:100]:
            self.q('UPDATE hd_mail SET read=1 WHERE id=? AND to_id=?', (int(i), pid))

    # ---- 運営（管理画面） ----
    def is_banned(self, pid):
        r = self.q('SELECT banned FROM hd_players WHERE id=?', (pid,), one=True)
        return bool(r and r[0])

    def set_banned(self, pid, on):
        self.q('UPDATE hd_players SET banned=? WHERE id=?', (1 if on else 0, pid))

    def admin_stats(self):
        now = int(time.time())
        one = lambda sql, a=(): (self.q(sql, a, one=True) or [0])[0]
        return {
            'players': one('SELECT COUNT(*) FROM hd_players'),
            'active1': one('SELECT COUNT(*) FROM hd_players WHERE seen_at>?', (now - 86400,)),
            'active7': one('SELECT COUNT(*) FROM hd_players WHERE seen_at>?', (now - 7 * 86400,)),
            'new7': one('SELECT COUNT(*) FROM hd_players WHERE created_at>?', (now - 7 * 86400,)),
            'banned': one('SELECT COUNT(*) FROM hd_players WHERE banned<>0'),
            'mails': one('SELECT COUNT(*) FROM hd_mail'),
            'mails1': one('SELECT COUNT(*) FROM hd_mail WHERE at>?', (now - 86400,)),
        }

    def admin_players(self, q='', limit=100, offset=0):
        """名前か友だちIDの部分一致。新しくつないだ順"""
        like = '%' + str(q or '').strip() + '%'
        rows = self.q('SELECT id, name, data, created_at, seen_at, banned, play_sec, device, save_at FROM hd_players WHERE (name LIKE ? OR id LIKE ?) '
                      'ORDER BY seen_at DESC LIMIT ? OFFSET ?', (like, like.upper(), int(limit) + 1, int(offset)), many=True) or []
        out = []
        for r in rows[:limit]:
            d = json.loads(r[2] or '{}')
            out.append({'id': r[0], 'name': r[1] or 'だれか', 'friends': len(d.get('friends', [])), 'reqIn': len(d.get('reqIn', [])),
                        'createdAt': r[3], 'seenAt': r[4], 'banned': bool(r[5]), 'playSec': r[6], 'device': r[7] or '', 'saveAt': r[8]})
        return out, len(rows) > limit

    # ---- 端末・プレイ時間・セーブ（ゲームから定期的に届く） ----
    def sync(self, pid, play=None, device=None, save=None):
        now = int(time.time())
        if play is not None:                       # プレイ時間は増えるだけ（別の端末の古い値で減らさない）
            self.q('UPDATE hd_players SET play_sec=? WHERE id=? AND (play_sec IS NULL OR play_sec<?)', (int(play), pid, int(play)))
        if device:
            self.q('UPDATE hd_players SET device=? WHERE id=?', (device, pid))
        if save:
            self.q('UPDATE hd_players SET save=?, save_at=? WHERE id=?', (save, now, pid))
        self.q('UPDATE hd_players SET seen_at=? WHERE id=?', (now, pid))

    # ---- 引継ぎコード（運営が発行。7日間・1回限り） ----
    def issue_xfer(self, pid):
        r = self.q('SELECT save_at FROM hd_players WHERE id=? AND save IS NOT NULL', (pid,), one=True)
        if not r:
            return None, None
        now = int(time.time())
        for _ in range(20):
            raw = ''.join(secrets.choice(ID_CHARS) for _ in range(8))
            code = f'{raw[:4]}-{raw[4:]}'
            if not self.q('SELECT 1 FROM hd_xfer WHERE code=?', (code,), one=True):
                self.q('INSERT INTO hd_xfer (code, pid, created_at, expires_at, used) VALUES (?,?,?,?,0)', (code, pid, now, now + XFER_DAYS * 86400))
                return code, r[0]
        raise RuntimeError('xfer')

    def claim_xfer(self, code):
        """引継ぎコードを使う：セーブと、新しい鍵（前の端末は使えなくなる）を返す。使えなければ None"""
        code = ''.join(c for c in str(code or '').upper() if c.isalnum())
        code = f'{code[:4]}-{code[4:]}' if len(code) == 8 else ''
        r = self.q('SELECT pid, expires_at, used FROM hd_xfer WHERE code=?', (code,), one=True)
        if not r or r[2] or r[1] < time.time():
            return None
        p = self.q('SELECT save, name FROM hd_players WHERE id=?', (r[0],), one=True)
        if not p or not p[0]:
            return None
        sec = secrets.token_urlsafe(24)
        self.q('UPDATE hd_players SET secret_hash=? WHERE id=?', (hsec(sec), r[0]))
        self.q('UPDATE hd_xfer SET used=1 WHERE code=?', (code,))
        return {'id': r[0], 'secret': sec, 'save': p[0], 'name': p[1]}

    # ---- アカウントの削除（運営・自動削除） ----
    def delete_player(self, pid):
        """アカウントと、その人のメール・引継ぎコードを消し、ほかの人のフレンド・申請からも外す"""
        if not self.q('SELECT 1 FROM hd_players WHERE id=?', (pid,), one=True):
            return False
        for oid, data in (self.q('SELECT id, data FROM hd_players WHERE data LIKE ?', ('%' + pid + '%',), many=True) or []):
            if oid == pid:
                continue
            d = json.loads(data or '{}')
            for k in ('friends', 'reqIn', 'reqOut'):
                d[k] = [x for x in d.get(k, []) if x != pid]
            self.q('UPDATE hd_players SET data=? WHERE id=?', (json.dumps({k: d.get(k, []) for k in ('friends', 'reqIn', 'reqOut')}, ensure_ascii=False), oid))
        self.q('DELETE FROM hd_mail WHERE from_id=? OR to_id=?', (pid, pid))
        self.q('DELETE FROM hd_xfer WHERE pid=?', (pid,))
        self.q('DELETE FROM hd_players WHERE id=?', (pid,))
        return True

    def auto_delete(self):
        """プレイ時間が3分未満のまま、3日以上つないでいないアカウントを消す。消した数を返す"""
        rows = self.q('SELECT id FROM hd_players WHERE play_sec IS NOT NULL AND play_sec<? AND seen_at<? AND banned=0',
                      (AUTO_DEL_PLAY, int(time.time()) - AUTO_DEL_DAYS * 86400), many=True) or []
        for (pid,) in rows:
            self.delete_player(pid)
        return len(rows)

    def admin_player(self, pid):
        r = self.q('SELECT id, name, data, created_at, seen_at, banned, play_sec, device, save_at FROM hd_players WHERE id=?', (pid,), one=True)
        if not r:
            return None
        d = json.loads(r[2] or '{}')
        names = self.names(list(dict.fromkeys(d.get('friends', []) + d.get('reqIn', []) + d.get('reqOut', []))))
        row = lambda i: {'id': i, 'name': names.get(i, 'だれか')}
        sent = (self.q('SELECT COUNT(*) FROM hd_mail WHERE from_id=?', (pid,), one=True) or [0])[0]
        got = (self.q('SELECT COUNT(*) FROM hd_mail WHERE to_id=?', (pid,), one=True) or [0])[0]
        return {'id': r[0], 'name': r[1] or 'だれか', 'createdAt': r[3], 'seenAt': r[4], 'banned': bool(r[5]),
                'playSec': r[6], 'device': r[7] or '', 'saveAt': r[8],
                'friends': [row(i) for i in d.get('friends', [])], 'reqIn': [row(i) for i in d.get('reqIn', [])],
                'reqOut': [row(i) for i in d.get('reqOut', [])], 'mailSent': sent, 'mailGot': got}

    def admin_mails(self, q='', limit=200):
        """メールの一覧（新しい順）。q は友だちID・名前・本文の部分一致"""
        q = str(q or '').strip()
        if q:
            like = '%' + q + '%'
            rows = self.q('SELECT id, from_id, from_name, to_id, to_name, body, at, read FROM hd_mail WHERE from_id LIKE ? OR to_id LIKE ? '
                          'OR from_name LIKE ? OR to_name LIKE ? OR body LIKE ? ORDER BY at DESC, id DESC LIMIT ?',
                          (like.upper(), like.upper(), like, like, like, int(limit)), many=True) or []
        else:
            rows = self.q('SELECT id, from_id, from_name, to_id, to_name, body, at, read FROM hd_mail ORDER BY at DESC, id DESC LIMIT ?',
                          (int(limit),), many=True) or []
        return [{'id': r[0], 'from': r[1], 'fromName': r[2], 'to': r[3], 'toName': r[4], 'body': r[5], 'at': r[6], 'read': bool(r[7])} for r in rows]

    def admin_delete_mails(self, ids):
        n = 0
        for i in ids[:500]:
            self.q('DELETE FROM hd_mail WHERE id=?', (int(i),)); n += 1
        return n

    def all_ids(self):
        return [r[0] for r in (self.q('SELECT id FROM hd_players WHERE banned=0', many=True) or [])]

    def purge(self):
        self.q('DELETE FROM hd_mail WHERE at<?', (int(time.time()) - MAIL_KEEP_DAYS * 86400,))
        self.q('DELETE FROM hd_xfer WHERE expires_at<?', (int(time.time()) - 86400,))
        n = self.auto_delete()
        if n:
            print('auto_delete:', n, flush=True)
