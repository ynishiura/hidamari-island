# -*- coding: utf-8 -*-
"""お問い合わせ・通報が届いたことを、運営者にメールで知らせる（Resend の送信 API。SIDERA VEIL と同じしくみ）。

設定（Render の環境変数。SIDERA VEIL と同じ値を入れる）:
  RESEND_API_KEY  Resend で発行した API キー
  INQUIRY_TO      お知らせを受け取るメールアドレス
  INQUIRY_FROM    差出人（省略時は Resend の試用アドレス onboarding@resend.dev）
  SITE_URL        管理画面のあるサイト（省略時は https://hidamari-island.onrender.com）

設定がなければメールは送らず、お問い合わせはデータベース（hd_inquiries 表）に残るだけ。
メールには利用者の名前・ID・本文を載せない（届いたことと受付番号だけ。内容は管理画面で読む）。
"""
import json, os, sys, threading, urllib.request


class Mailer:
    def __init__(self):
        conf = lambda k: os.environ.get(k, '').strip()
        self.key, self.to = conf('RESEND_API_KEY'), conf('INQUIRY_TO')
        self.sender = conf('INQUIRY_FROM') or 'ひだまり島ぐらし <onboarding@resend.dev>'
        self.site = (conf('SITE_URL') or 'https://hidamari-island.onrender.com').rstrip('/')

    @property
    def on(self):
        return bool(self.key and self.to)

    def notify(self, kind, rec_id, at_text, done=None):
        """裏で送る（プレイヤーを待たせない）。kind は 'inquiry'（お問い合わせ）か 'report'（通報）。送れたら done() を呼ぶ"""
        if not self.on:
            return
        threading.Thread(target=self._send, args=(kind, rec_id, at_text, done), daemon=True).start()

    def _send(self, kind, rec_id, at_text, done):
        what = 'お問い合わせ' if kind == 'inquiry' else '通報'
        text = (f"ひだまり島ぐらし に新しい{what}が届きました。\n\n"
                f"日時: {at_text}\n"
                f"受付番号: {rec_id}\n\n"
                f"内容は管理画面で確認してください。\n{self.site}/admin.html\n")
        payload = {'from': self.sender, 'to': [self.to], 'subject': f'【ひだまり島ぐらし】新しい{what}', 'text': text}
        req = urllib.request.Request('https://api.resend.com/emails', data=json.dumps(payload).encode('utf-8'), method='POST',
                                     headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json',
                                              'User-Agent': 'hidamari-relay/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if 200 <= r.status < 300 and done:
                    done()
        except Exception as e:                      # 送れなくても、お問い合わせはデータベースに残っている
            detail = ''
            if hasattr(e, 'read'):
                try:
                    detail = e.read().decode('utf-8', 'replace')[:300]
                except Exception:
                    pass
            sys.stderr.write(f'notify mail failed ({kind} {rec_id}): {e!r} {detail}\n')
