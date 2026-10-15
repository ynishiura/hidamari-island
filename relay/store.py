# -*- coding: utf-8 -*-
"""フレンドとメールの保存先。

・環境変数 DATABASE_URL があれば PostgreSQL（Neon）に保存する（本番）
・なければ relay/data/local.db（SQLite）に保存する（手元での確認用）
表の名前は hd_ で始める（同じデータベースに、ほかのゲームの表があっても混ざらないように）。

  hd_players  1人1行。id（友だちID）・合言葉のハッシュ・名前・フレンド・申請（JSON）
  hd_mail     メール1通1行。30日たったものは消す
"""
import hashlib, json, os, secrets, sqlite3, threading, time

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

    def purge(self):
        self.q('DELETE FROM hd_mail WHERE at<?', (int(time.time()) - MAIL_KEEP_DAYS * 86400,))
