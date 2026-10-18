# -*- coding: utf-8 -*-
"""ひだまり島ぐらし：友だちと遊ぶための中継サーバー（Render の Web Service で動かす）

1. 合言葉の部屋：合言葉ごとに「部屋」を作り、同じ部屋にいる人どうしのメッセージを中継する（島の持ち主が出ると部屋はなくなる）
     島の持ち主  {"t":"host","code":合言葉,"name":名前}      遊びに行く人 {"t":"join","code":合言葉,"name":名前}
     そのあと {"t":"st"|"emo"|"look"|"snap"|"time"|"isl", ...} を部屋の人に配る（snap・time・isl は持ち主だけ。snap は "to" の相手だけ）
     isl は島の変化（木を切った・花を植えたなど、変わった項目だけ）
2. フレンドとメール（store.py に保存。DATABASE_URL があれば Neon）
     {"t":"register","name"} → {"t":"acct","id","secret"}       はじめての人に友だちIDを作る
     {"t":"auth","id","secret","name"} → {"t":"authed","id"}    つなぐたびに名のる（ここからフレンドの操作ができる）
       名のれないとき：その友だちIDが削除されていれば {"t":"err","why":"gone"}、鍵がちがう（ほかの端末に引き継がれた）なら {"t":"err","why":"moved"}
       友だちIDはアカウントごとに1つで変わらない（ゲームは moved のとき新しいIDを作らない）
     friends / freq{to} / faccept{id} / fdecline{id} / fcancel{id} / fremove{id} / rename{name}
     report{mid, reason, detail, block}（フレンドから届いたメールを通報。block ならその人をブロック）/ unblock{id}
     メールに自傷をほのめかす言葉があれば止めて、相談窓口を知らせる（{"t":"err","why":"selfharm","msg"}）
     {"t":"namecheck","name","id"} → {"t":"name","ok"}        その名前をほかの人が使っていないか（名のらなくても聞ける）
     {"t":"textcheck","text"} → {"t":"text","ok"}               写真の下の文字などに使えない言葉がないか（保存・共有の前に。名のらなくても聞ける）
   名前は 7文字まで。同じ名前はほかの人が使えない（register・auth・rename で確かめる。auth で重なっていたら前の名前のまま）
     mail{to,body,gift} / mailbox / mailread{ids} / invite{to,code} / mailgift{mid}（届いた包みを開ける → {"t":"giftget","mid","gift"}。1通1回だけ）
   メールには持ち物を1つ付けられる（gift＝品物の id。1日 GIFT_PER_DAY 通まで。品物を手放すのは送った人のゲームの側）
   相手がつないでいれば、すぐに知らせる（friends・mailnew・invite）。招待は 10分で消える
3. 運営の管理画面（pwa/admin.html）
     {"t":"admin","token":ADMIN_TOKEN,"op":…} → {"t":"admin","op":…,"ok":true,…}  ADMIN_TOKEN（環境変数）が無ければ使えない
     op: stats（人数・部屋）/ players{q,page} / player{id} / ban{id,on}（ブロック）/ delete{id}（削除）/ xfer{id}（引継ぎコード発行）/
         rename{id,name} / mails{q} / maildel{ids} / notice{to:'all'|友だちID, body}（運営からのお知らせ。ポストに届く）/ closeroom{code}
         ops{id,on}（運営アカウントに指定・解除）
   運営アカウント（hd_players.ops）には制限をかけない：名前・メール・合言葉の禁止ワード、メールの長さ（OPS_MAIL_MAX まで）・1日の数、
     贈り物の1日の数、フレンドの数、自動削除。名前がほかの人と重ならないこと・相手のブロック・島の人数（4人）はそのまま
4. 端末・プレイ時間・セーブ・引継ぎ
     {"t":"sync","play":秒,"dev":利用端末,"save":"HIDA2:…"}   名のった人が定期的に送る（セーブは数分おき）
     {"t":"claim","code":"ABCD-EFGH"} → {"t":"claimed","id","secret","save","name"}  運営が発行した引継ぎコードで受け取る（前の端末は使えなくなる）
   利用停止のアカウントは auth で {"t":"err","why":"banned"} を返す
5. お問い合わせ（SIDERA VEIL と同じ窓口。届いたら運営者にメールで知らせる。mailer.py）
     {"t":"inquiry","name","body","id","secret"} → {"t":"inquired","no":受付番号}   名のっていなくても id・secret で送れる（利用停止中も送れる）
       内容は INQ_BODY_MAX（100）文字まで・24時間に2通まで（store.py の INQ_PER_DAY）
       "reply":メールの番号 を付けると、ポストに届いた運営からのメールへの返信（24時間の数に入れない。1通に1回まで）
     {"t":"inqlist","id","secret"} → {"t":"inqlist","items":[{id,body,at,reply,repliedAt}]}
     運営の返事（管理画面の inqreply{iid,body}）は、運営からのメールとしてポストにも届ける
6. 利用環境とアカウント作成の制限（2026-10-17〜）
     register・auth・sync・host・join のとき、IP・端末とブラウザ（dev）・回線の種類（net）をアカウントごとに記録する（store.py の hd_envs）
     register は、利用停止中のアカウントが使った IP・端末からは断る（regban）。同じ IP か同じ端末からは 48時間に1つ（reg48）・30日で2つ（reg30）まで
     端末＝ゲームがブラウザに保存する「端末の識別番号」（did）。記録の使いみちは、ブロックと、別アカウント・複数アカウントの防止だけ
     合言葉の部屋（host・join）は、友だちIDと鍵（id・secret）で名のった人だけ。利用停止中は使えない（needacct／banned）
起動： python server.py （PORT は環境変数。無ければ 8787）
"""
import asyncio, json, os, re, time, unicodedata, secrets
from http import HTTPStatus
from websockets.asyncio.server import serve
import ngfilter
from store import Store, norm_id
from mailer import Mailer

MAX_ROOM = 4
MAX_MSG = 600_000
MAX_SMALL = 4_000
RATE = 40
FRIEND_MAX, REQ_MAX = 50, 50
MAIL_MAX, MAIL_PER_DAY = 50, 50
GIFT_PER_DAY = 10
GIFT_RE = re.compile(r'^[a-z0-9_]{1,40}$')
INVITE_TTL = 600
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
OPS_ID, OPS_NAME = "HD-OPS", "運営"          # 運営からのお知らせの差出人
NOTICE_MAX = 200
OPS_MAIL_MAX = 500                            # 運営アカウントのメールの長さ（ふつうは50文字）
admin_fail = {"t": 0.0}
claim_fail = {"t": 0.0}
SAVE_MAX = 500_000
INQ_NAME_MAX, INQ_BODY_MAX, INQ_REPLY_MAX = 30, 100, 1000   # お問い合わせの内容は100文字まで（2026-10-18）
inq_fail = {"t": 0.0}
mailer = Mailer()

rooms = {}                   # code -> {"host": ws, "hostId": id, "members": {id: ws}, "names": {id: name}}
online = {}                  # 友だちID -> set(ws)
invites = {}                 # 招待された人の友だちID -> {from, name, code, at}
db = None


def norm_code(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    return s if 3 <= len(s) <= 20 else None


def clean_name(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    s = s[:7] or "だれか"          # 名前は 7文字まで
    return "だれか" if ngfilter.has_ng_name(s) else s


def plain_name(s):
    """運営アカウントの名前：禁止ワードで「だれか」にしない（7文字までは同じ）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    return s[:7] or "だれか"


async def is_ops(pid):
    return bool(db and pid and await run(db.is_ops, pid))


def name_ng(s):
    """名前に使えない言葉が入っているか（clean_name で「だれか」に変わる名前）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).strip()[:7]
    return bool(s) and ngfilter.has_ng_name(s)


INV_CODE = re.compile(r"inv-[a-z0-9]{6}")   # フレンドを島に誘うときにゲームが作る合言葉（だれにも見せない）


def code_ng(c):
    """合言葉に使えない言葉が入っているか（名前と同じ調べ方）。ゲームが作る招待の合言葉は調べない。"""
    return not INV_CODE.fullmatch(c) and ngfilter.has_ng_name(c)


async def send(ws, obj):
    try:
        await ws.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


async def push(pid, obj):
    for w in list(online.get(pid, ())):
        await send(w, obj)


async def broadcast(room, obj, skip=None):
    await asyncio.gather(*(send(w, obj) for i, w in room["members"].items() if i != skip))


def run(fn, *a):
    return asyncio.to_thread(fn, *a)


def client_ip(ws):
    """つないできた人の IP。Render の前の Cloudflare が付ける見出しを優先する"""
    try:
        h = ws.request.headers
        for k in ('CF-Connecting-IP', 'True-Client-IP', 'X-Forwarded-For'):
            v = h.get(k)
            if v:
                return v.split(',')[0].strip()[:64]
        ra = ws.remote_address
        return str(ra[0]) if ra else ''
    except Exception:
        return ''


DID_RE = re.compile(r'^[0-9a-f]{24}$')


def did_of(m):
    d = m.get("did")
    return d if isinstance(d, str) and DID_RE.match(d) else ""


def env_of(m):
    dev = str(m.get("dev") or "")[:120] or "不明"
    net = str(m.get("net") or "")[:20] or "不明"
    return dev, net, did_of(m)


# 断るときの知らせは、どの制限でも同じ文にする（数や理由が分かると、待てばよい・ブロックされた端末だと分かってしまうため。2026-10-17 ユーザーの指示）
REG_MSG_ALL = "この端末・通信環境からは、しばらく新しい友だちIDを作れません。"
REG_MSG = {"regban": REG_MSG_ALL, "reg48": REG_MSG_ALL, "reg30": REG_MSG_ALL}


def jst(ts=None):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime((ts or time.time()) + 9 * 3600)) + "（日本時間）"


async def ops_mail(pid, body):
    """運営からのメールをポストに届ける（つないでいれば、すぐに知らせる）"""
    await run(db.add_mail, OPS_ID, pid, OPS_NAME, "", body)
    if online.get(pid):
        await push(pid, {"t": "mailnew", "from": OPS_ID, "name": OPS_NAME, "unread": await run(db.unread, pid)})


async def inquiry_op(ws, acct, m):
    """お問い合わせ。名のっていればその人、そうでなければ id・secret で本人を確かめる（利用停止中でも使える）"""
    t = m.get("t")
    pid = acct
    if not pid:
        pid = norm_id(m.get("id"))
        if not pid or not await run(db.check, pid, str(m.get("secret") or "")):
            wait = max(0.0, inq_fail["t"] + 1.0 - time.time()); inq_fail["t"] = time.time() + wait
            await asyncio.sleep(1.0 + wait)
            await send(ws, {"t": "err", "why": "inqauth"}); return
    if t == "inqlist":
        await send(ws, {"t": "inqlist", "items": await run(db.my_inquiries, pid, 20)}); return
    name = " ".join(str(m.get("name") or "").split())[:INQ_NAME_MAX]
    body = str(m.get("body") or "").replace("\r", "").strip()
    if not name:
        await send(ws, {"t": "err", "why": "inq", "msg": "お名前（呼び名）を書いてください"}); return
    if not body:
        await send(ws, {"t": "err", "why": "inq", "msg": "お問い合わせの内容を書いてください"}); return
    if len(body) > INQ_BODY_MAX:
        await send(ws, {"t": "err", "why": "inq", "msg": f"お問い合わせの内容は{INQ_BODY_MAX}文字までです"}); return
    mid, quote = None, None
    if m.get("reply") is not None:                  # 運営からのメールへの返信：その人のポストに届いた、運営からのメールだけ
        try:
            mail = await run(db.mail_in_inbox, pid, int(m.get("reply")))
        except (TypeError, ValueError):
            mail = None
        if not mail or mail["from"] != OPS_ID:
            await send(ws, {"t": "err", "why": "inq", "msg": "返信できるのは、ポストに届いた運営からのメールです"}); return
        mid, quote = mail["id"], str(mail["body"] or "")[:300]
    iid, err = await run(db.add_inquiry, pid, name, body, mid, quote)
    if err:
        await send(ws, {"t": "err", "why": "inq", "msg": err}); return
    mailer.notify("inquiry", iid, jst(), lambda: db.mark_mailed(iid))
    await send(ws, {"t": "inquired", "no": iid})


async def friends_msg(pid):
    p = await run(db.get, pid)
    if not p:
        return {"t": "friends", "list": [], "reqIn": [], "reqOut": []}
    ids = list(dict.fromkeys(p["friends"] + p["reqIn"] + p["reqOut"]))
    names = await run(db.names, ids)
    row = lambda i: {"id": i, "name": names.get(i, "だれか"), "online": bool(online.get(i))}
    bn = await run(db.names, p.get("blocked", []))
    return {"t": "friends", "list": [row(i) for i in p["friends"]], "reqIn": [row(i) for i in p["reqIn"]], "reqOut": [row(i) for i in p["reqOut"]],
            "blocked": [{"id": i, "name": bn.get(i, "だれか")} for i in p.get("blocked", [])]}


async def account_op(ws, me, m):
    """フレンドとメールの操作。me は名のった人の友だちID。"""
    t = m.get("t")
    if t == "friends":
        await send(ws, await friends_msg(me)); return
    if t == "rename":
        ops = await is_ops(me)
        if not ops and name_ng(m.get("name")):
            await send(ws, {"t": "err", "why": "nameng"}); return
        nm = plain_name(m.get("name")) if ops else clean_name(m.get("name"))
        if await run(db.name_taken, nm, me):
            await send(ws, {"t": "err", "why": "nametaken"}); return
        await run(db.touch, me, nm)
        await send(ws, {"t": "info", "why": "renamed", "name": nm}); return
    if t == "mailbox":
        inbox, outbox = await run(db.mailbox, me, MAIL_MAX)
        await send(ws, {"t": "mailbox", "inbox": inbox, "outbox": outbox}); return
    if t == "mailread":
        ids = [i for i in (m.get("ids") or []) if isinstance(i, int)]
        await run(db.mark_read, me, ids); return
    if t in ("freq", "faccept", "fdecline", "fcancel", "fremove"):
        other = norm_id(m.get("to") or m.get("id"))
        if not other or other == me:
            await send(ws, {"t": "err", "why": "fid"}); return
        a, b = await run(db.get, me), await run(db.get, other)
        if not a or not b:
            await send(ws, {"t": "err", "why": "nofriend"}); return
        if t == "freq":
            if me in b.get("blocked", []) or other in a.get("blocked", []):
                await send(ws, {"t": "err", "why": "blocked"}); return
            if other in a["friends"]:
                await send(ws, {"t": "err", "why": "already"}); return
            if len(a["friends"]) >= FRIEND_MAX and not await is_ops(me):
                await send(ws, {"t": "err", "why": "fmax"}); return
            if other in a["reqIn"]:                     # 相手からも申請が来ていたら、そのままフレンド
                a["reqIn"].remove(other); b["reqOut"] = [x for x in b["reqOut"] if x != me]
                a["friends"].append(other); b["friends"].append(me)
                await send(ws, {"t": "info", "why": "friended", "name": b["name"]})
                await push(other, {"t": "info", "why": "friended", "name": a["name"]})
            else:
                if other not in a["reqOut"]:
                    a["reqOut"] = (a["reqOut"] + [other])[-REQ_MAX:]
                if me not in b["reqIn"]:
                    b["reqIn"] = ([me] + b["reqIn"])[:REQ_MAX]
                await send(ws, {"t": "info", "why": "reqsent", "name": b["name"]})
                await push(other, {"t": "info", "why": "reqin", "name": a["name"]})
        elif t == "faccept" and other in a["reqIn"]:
            if ((len(a["friends"]) >= FRIEND_MAX and not await is_ops(me))
                    or (len(b["friends"]) >= FRIEND_MAX and not await is_ops(other))):   # 運営アカウントはフレンドの数の上限なし
                await send(ws, {"t": "err", "why": "fmax"}); return
            a["reqIn"].remove(other); b["reqOut"] = [x for x in b["reqOut"] if x != me]
            if other not in a["friends"]: a["friends"].append(other)
            if me not in b["friends"]: b["friends"].append(me)
            await push(other, {"t": "info", "why": "friended", "name": a["name"]})
        elif t == "fdecline":
            a["reqIn"] = [x for x in a["reqIn"] if x != other]; b["reqOut"] = [x for x in b["reqOut"] if x != me]
        elif t == "fcancel":
            a["reqOut"] = [x for x in a["reqOut"] if x != other]; b["reqIn"] = [x for x in b["reqIn"] if x != me]
        elif t == "fremove":
            a["friends"] = [x for x in a["friends"] if x != other]; b["friends"] = [x for x in b["friends"] if x != me]
        await run(db.put, me, a); await run(db.put, other, b)
        await send(ws, await friends_msg(me))
        await push(other, await friends_msg(other)); return
    if t == "mail":
        to = norm_id(m.get("to"))
        body = str(m.get("body") or "").strip().replace("\r", "")
        gift = m.get("gift") if isinstance(m.get("gift"), str) and GIFT_RE.match(m.get("gift")) else None
        a = await run(db.get, me)
        if not to or not a or to not in a["friends"]:
            await send(ws, {"t": "err", "why": "notfriend"}); return
        ops = await is_ops(me)                                # 運営アカウント：長さ（OPS_MAIL_MAX まで）のほかは調べない
        if (not body and not gift) or len(body) > (OPS_MAIL_MAX if ops else 50):
            await send(ws, {"t": "err", "why": "mlen"}); return
        if not ops:
            if gift and await run(db.gifts_today, me) >= GIFT_PER_DAY:
                await send(ws, {"t": "err", "why": "gday"}); return
            if body and ngfilter.has_selfharm(body):          # 自傷をほのめかす言葉：止めて、相談窓口を知らせる
                await send(ws, {"t": "err", "why": "selfharm", "msg": ngfilter.SELFHARM_HELP}); return
            if body and (ngfilter.bad_chars(body) or ngfilter.has_ng(body)):
                await send(ws, {"t": "err", "why": "ng"}); return
            if await run(db.sent_today, me) >= MAIL_PER_DAY:
                await send(ws, {"t": "err", "why": "mday"}); return
        b = await run(db.get, to)
        await run(db.add_mail, me, to, a["name"], b["name"] if b else "だれか", body, gift)
        await send(ws, {"t": "info", "why": "mailsent", "name": b["name"] if b else "", "gift": gift})
        await push(to, {"t": "mailnew", "from": me, "name": a["name"], "unread": await run(db.unread, to), "gift": bool(gift)}); return
    if t == "mailgift":
        try:
            mid = int(m.get("mid"))
        except (TypeError, ValueError):
            mid = None
        g = mid is not None and await run(db.take_gift, me, mid)
        if not g:
            await send(ws, {"t": "err", "why": "giftgone"}); return
        await send(ws, {"t": "giftget", "mid": mid, "gift": g}); return
    if t == "report":
        try:
            mail = await run(db.mail_in_inbox, me, int(m.get("mid")))
        except (TypeError, ValueError):
            mail = None
        if not mail or mail["from"] == OPS_ID:
            await send(ws, {"t": "err", "why": "report"}); return
        reason = " ".join(str(m.get("reason") or "").split())[:30] or "その他"
        detail = str(m.get("detail") or "").replace("\r", "").strip()[:500]
        rid = await run(db.add_report, me, mail["from"], mail, reason, detail)
        mailer.notify("report", f"通報 {rid}", jst())
        blocked = bool(m.get("block"))
        if blocked:                                  # ブロック：フレンドをやめ、申請・メール・誘いを受けない
            a, b = await run(db.get, me), await run(db.get, mail["from"])
            if a:
                for k in ("friends", "reqIn", "reqOut"):
                    a[k] = [x for x in a[k] if x != mail["from"]]
                if mail["from"] not in a["blocked"]:
                    a["blocked"].append(mail["from"])
                await run(db.put, me, a)
            if b:
                for k in ("friends", "reqIn", "reqOut"):
                    b[k] = [x for x in b[k] if x != me]
                await run(db.put, mail["from"], b)
                await push(mail["from"], await friends_msg(mail["from"]))
            if invites.get(me, {}).get("from") == mail["from"]:
                invites.pop(me, None)
        await send(ws, {"t": "info", "why": "reported", "blocked": blocked})
        await send(ws, await friends_msg(me)); return
    if t == "unblock":
        other = norm_id(m.get("id")); a = await run(db.get, me)
        if a and other in a["blocked"]:
            a["blocked"] = [x for x in a["blocked"] if x != other]
            await run(db.put, me, a)
        await send(ws, await friends_msg(me)); return
    if t == "invite":
        to = norm_id(m.get("to")); code = norm_code(m.get("code"))
        a = await run(db.get, me)
        if not to or not code or not a or to not in a["friends"]:
            await send(ws, {"t": "err", "why": "notfriend"}); return
        invites[to] = {"from": me, "name": a["name"], "code": code, "at": time.time()}
        await push(to, {"t": "invite", "from": me, "name": a["name"], "code": code})
        await send(ws, {"t": "info", "why": "invited", "name": (await run(db.names, [to])).get(to, ""), "online": bool(online.get(to))}); return


async def admin_op(ws, m):
    """運営の管理画面からの操作。パスワードがちがえば待たせて断る（総当たりを遅くする）"""
    op = str(m.get("op") or "")

    async def reply(**kw):
        await send(ws, {"t": "admin", "op": op, **kw})
    if not ADMIN_TOKEN:
        await reply(ok=False, err="運営のパスワード（ADMIN_TOKEN）がサーバーに設定されていません"); return
    if not secrets.compare_digest(str(m.get("token") or ""), ADMIN_TOKEN):
        wait = max(0.0, admin_fail["t"] + 1.0 - time.time()); admin_fail["t"] = time.time() + wait
        await asyncio.sleep(1.0 + wait)
        await reply(ok=False, err="パスワードがちがいます"); return
    if not db:
        await reply(ok=False, err="保存先につながっていません"); return
    try:
        if op == "stats":
            st = await run(db.admin_stats)
            rm = [{"code": c, "host": r["names"].get(r["hostId"], "だれか"), "members": len(r["members"]),
                   "names": list(r["names"].values())} for c, r in rooms.items()]
            await reply(ok=True, stats=st, online=len(online), rooms=rm, store="pg" if db.pg else "sqlite"); return
        if op == "players":
            page = max(0, int(m.get("page") or 0))
            items, more = await run(db.admin_players, str(m.get("q") or "")[:30], 100, page * 100)
            for it in items:
                it["online"] = bool(online.get(it["id"]))
            await reply(ok=True, items=items, more=more, page=page); return
        pid = norm_id(m.get("id")) if m.get("id") not in (None, "", "all") else None
        if op == "player":
            p = pid and await run(db.admin_player, pid)
            if not p:
                await reply(ok=False, err="その友だちIDの人はいません"); return
            p["online"] = bool(online.get(pid))
            await reply(ok=True, player=p); return
        if op == "ban":
            if not pid or not await run(db.get, pid):
                await reply(ok=False, err="その友だちIDの人はいません"); return
            on = bool(m.get("on"))
            await run(db.set_banned, pid, on)
            if on:                                   # つないでいる端末は切る
                for w in list(online.get(pid, ())):
                    await send(w, {"t": "err", "why": "banned"})
                    try:
                        await w.close()
                    except Exception:
                        pass
            await reply(ok=True, id=pid, banned=on); return
        if op == "ops":                              # 運営アカウントに指定・解除
            if not pid or not await run(db.get, pid):
                await reply(ok=False, err="その友だちIDの人はいません"); return
            on = bool(m.get("on"))
            await run(db.set_ops, pid, on)
            await push(pid, {"t": "info", "why": "ops", "on": on})
            await reply(ok=True, id=pid, ops=on); return
        if op == "xfer":
            if not pid or not await run(db.get, pid):
                await reply(ok=False, err="その友だちIDの人はいません"); return
            code, save_at = await run(db.issue_xfer, pid)
            if not code:
                await reply(ok=False, err="この人のセーブがまだサーバーに届いていません（ゲームを新しい版で遊ぶと、数分おきに届きます）"); return
            await reply(ok=True, code=code, saveAt=save_at, days=7); return
        if op == "delete":
            if not pid:
                await reply(ok=False, err="その友だちIDの人はいません"); return
            for w in list(online.get(pid, ())):
                try:
                    await w.close()
                except Exception:
                    pass
            ok = await run(db.delete_player, pid)
            await reply(ok=ok, **({} if ok else {"err": "その友だちIDの人はいません"})); return
        if op == "create":   # アカウント作成の代行（家族などで、同じ端末・通信環境から作れないとき）
            nm = clean_name(m.get("name"))
            if not str(m.get("name") or "").strip():
                await reply(ok=False, err="名前を入れてください"); return
            if await run(db.name_taken, nm, None):
                await reply(ok=False, err=f"「{nm}」は、ほかの人が使っている名前です"); return
            pid, code = await run(db.admin_create, nm)
            await reply(ok=True, id=pid, name=nm, code=code, days=7); return
        if op == "rename":
            nm = plain_name(m.get("name")) if await is_ops(pid) else clean_name(m.get("name"))
            if not pid or not await run(db.get, pid):
                await reply(ok=False, err="その友だちIDの人はいません"); return
            if await run(db.name_taken, nm, pid):     # 名前はほかの人と重ならないように
                await reply(ok=False, err=f"「{nm}」は、ほかの人が使っている名前です"); return
            await run(db.touch, pid, nm)
            await push(pid, {"t": "info", "why": "opsrename", "name": nm})
            await reply(ok=True, id=pid, name=nm); return
        if op == "inquiries":
            await reply(ok=True, items=await run(db.inquiries, 300), mail=mailer.on); return
        if op in ("inqreply", "inqdone"):
            try:
                q = await run(db.inquiry, int(m.get("iid")))
            except (TypeError, ValueError):
                q = None
            if not q:
                await reply(ok=False, err="そのお問い合わせはありません"); return
            if op == "inqdone":
                await run(db.set_inquiry_done, q["id"], bool(m.get("on"))); await reply(ok=True); return
            body = str(m.get("body") or "").strip().replace("\r", "")
            if not body or len(body) > INQ_REPLY_MAX:
                await reply(ok=False, err=f"返事は1〜{INQ_REPLY_MAX}文字で書いてください"); return
            await run(db.reply_inquiry, q["id"], body)
            delivered = bool(await run(db.get, q["pid"]))
            if delivered:
                await ops_mail(q["pid"], f"【お問い合わせ（受付番号 {q['id']}）へのお返事】\n{body}")
            await reply(ok=True, delivered=delivered); return
        if op == "reports":
            await reply(ok=True, items=await run(db.reports, 300)); return
        if op == "mails":
            await reply(ok=True, items=await run(db.admin_mails, str(m.get("q") or "")[:40], 300)); return
        if op == "maildel":
            ids = [i for i in (m.get("ids") or []) if isinstance(i, int)]
            await reply(ok=True, deleted=await run(db.admin_delete_mails, ids)); return
        if op == "notice":
            body = str(m.get("body") or "").strip().replace("\r", "")
            if not body or len(body) > NOTICE_MAX:
                await reply(ok=False, err=f"お知らせは1〜{NOTICE_MAX}文字で書いてください"); return
            to = m.get("to")
            ids = await run(db.all_ids) if to == "all" else ([pid] if pid and await run(db.get, pid) else [])
            if not ids:
                await reply(ok=False, err="送り先が見つかりません"); return
            for i in ids:
                await ops_mail(i, body)
            await reply(ok=True, sent=len(ids)); return
        if op == "closeroom":
            code = norm_code(m.get("code")); r = rooms.get(code) if code else None
            if not r:
                await reply(ok=False, err="その部屋はありません"); return
            try:
                await r["host"].close()
            except Exception:
                pass
            await reply(ok=True); return
        await reply(ok=False, err="わからない操作です")
    except Exception as e:
        print("admin_op error:", repr(e), flush=True)
        await reply(ok=False, err="サーバーで問題が起きました")


async def handler(ws):
    me = secrets.token_hex(4)        # 部屋の中で使う番号
    acct = None                      # 名のった友だちID
    code = None
    is_host = False
    count, t0 = 0, time.monotonic()
    try:
        async for raw in ws:
            now = time.monotonic()
            if now - t0 >= 1:
                count, t0 = 0, now
            count += 1
            if count > RATE or len(raw) > MAX_MSG:
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if not isinstance(m, dict):
                continue
            t = m.get("t")
            # ---- 運営の管理画面 ----
            if t == "admin":
                await admin_op(ws, m); continue
            # ---- 端末・プレイ時間・セーブ ----
            if t == "sync" and acct and db:
                play = m.get("play"); play = int(play) if isinstance(play, (int, float)) and 0 <= play < 10 ** 9 else None
                dev = str(m.get("dev") or "")[:120] or None
                save = m.get("save") if isinstance(m.get("save"), str) and m["save"].startswith("HIDA") and len(m["save"]) <= SAVE_MAX else None
                try:
                    await run(db.sync, acct, play, dev, save)
                    await run(db.record_env, acct, client_ip(ws), dev or "不明", str(m.get("net") or "")[:20] or "不明", did_of(m))
                except Exception as e:
                    print("sync error:", repr(e), flush=True)
                continue
            # ---- 運営が発行した引継ぎコードで受け取る ----
            if t == "claim" and db:
                got = await run(db.claim_xfer, m.get("code"))
                if not got:
                    wait = max(0.0, claim_fail["t"] + 1.0 - time.time()); claim_fail["t"] = time.time() + wait
                    await asyncio.sleep(1.0 + wait)        # 総当たりを遅くする
                    await send(ws, {"t": "err", "why": "xfer"}); continue
                for w in list(online.get(got["id"], ())):  # 前の端末は切る（鍵が変わったので、もうつなげない）
                    try:
                        await w.close()
                    except Exception:
                        pass
                await send(ws, {"t": "claimed", **got}); continue
            # ---- お問い合わせ ----
            if t in ("inquiry", "inqlist") and db:
                if len(raw) <= 12_000:
                    try:
                        await inquiry_op(ws, acct, m)
                    except Exception as e:
                        print("inquiry error:", repr(e), flush=True)
                        await send(ws, {"t": "err", "why": "server"})
                continue
            # ---- フレンド・メール ----
            if t == "textcheck":   # 写真の下の文字など、ゲームが保存・共有する前に調べる文字（使えるかだけ返す。理由は言わない）
                txt = str(m.get("text") or "")[:60]
                await send(ws, {"t": "text", "ok": not ngfilter.has_ng(txt)}); continue
            if t == "namecheck":
                if name_ng(m.get("name")) and not await is_ops(norm_id(m.get("id"))):   # 使えない言葉の名前（理由は言わない。運営アカウントは通す）
                    await send(ws, {"t": "name", "ok": False, "ng": True}); continue
                nm = clean_name(m.get("name"))
                ok = not (db and await run(db.name_taken, nm, norm_id(m.get("id"))))
                await send(ws, {"t": "name", "ok": ok}); continue
            if t == "register" and db and not acct:
                if name_ng(m.get("name")):
                    await send(ws, {"t": "err", "why": "nameng"}); continue
                nm = clean_name(m.get("name"))
                ip = client_ip(ws)
                why = await run(db.reg_check, ip, did_of(m))
                if why:
                    print("register refused:", why, flush=True)   # 理由はサーバーの記録にだけ残す
                    await send(ws, {"t": "err", "why": "regblock", "msg": REG_MSG[why]}); continue
                if await run(db.name_taken, nm, None):
                    await send(ws, {"t": "err", "why": "nametaken"}); continue
                pid, sec = await run(db.create, nm)
                await run(db.add_reg, ip, pid, did_of(m))
                await run(db.record_env, pid, ip, *env_of(m))
                acct = pid; online.setdefault(acct, set()).add(ws)
                await send(ws, {"t": "acct", "id": pid, "secret": sec}); continue
            if t == "auth" and db and not acct:
                pid = norm_id(m.get("id"))
                if not pid or not await run(db.check, pid, str(m.get("secret") or "")):
                    gone = not pid or not await run(db.get, pid)
                    await send(ws, {"t": "err", "why": "gone" if gone else "moved"}); continue
                await run(db.record_env, pid, client_ip(ws), *env_of(m))
                if await run(db.is_banned, pid):
                    await send(ws, {"t": "err", "why": "banned"}); continue
                acct = pid; online.setdefault(acct, set()).add(ws)
                ops = await is_ops(pid)
                nm = plain_name(m.get("name")) if ops else clean_name(m.get("name"))
                ng = not ops and name_ng(m.get("name"))
                if ng or await run(db.name_taken, nm, pid):   # 使えない名前・ほかの人と重なる名前なら、前の名前のまま（返事で知らせる）
                    await run(db.touch, pid)
                else:
                    await run(db.touch, pid, nm)
                cur = await run(db.get, pid)
                await send(ws, {"t": "authed", "id": pid, "unread": await run(db.unread, pid), "name": cur["name"] if cur else nm, "ng": ng, "ops": ops})
                inv = invites.get(pid)
                if inv and time.time() - inv["at"] < INVITE_TTL and inv["code"] in rooms:
                    await send(ws, {"t": "invite", "from": inv["from"], "name": inv["name"], "code": inv["code"]})
                continue
            if t in ("friends", "freq", "faccept", "fdecline", "fcancel", "fremove", "mail", "mailbox", "mailread", "mailgift", "invite", "rename", "report", "unblock"):
                if acct and len(raw) <= MAX_SMALL:
                    try:
                        await account_op(ws, acct, m)
                    except Exception as e:
                        print("account_op error:", repr(e), flush=True)
                        await send(ws, {"t": "err", "why": "server"})
                continue
            # ---- 合言葉の部屋 ----
            if code is None:
                if t not in ("host", "join"):
                    continue
                c = norm_code(m.get("code"))
                if not c:
                    await send(ws, {"t": "err", "why": "code"}); continue
                rp = None
                if db:   # 友だちIDと鍵で名のった人だけ。利用停止中は使えない
                    rp = norm_id(m.get("id"))
                    if not rp or not await run(db.check, rp, str(m.get("secret") or "")):
                        await send(ws, {"t": "err", "why": "needacct"}); continue
                    await run(db.record_env, rp, client_ip(ws), *env_of(m))
                    if await run(db.is_banned, rp):
                        await send(ws, {"t": "err", "why": "banned"}); continue
                ops = await is_ops(rp)
                if t == "host" and not ops and code_ng(c):   # 使えない言葉の合言葉（理由は言わない。運営アカウントは通す）
                    await send(ws, {"t": "err", "why": "codeng"}); continue
                rname = plain_name(m.get("name")) if ops else clean_name(m.get("name"))
                if t == "host":
                    if rooms.get(c):
                        await send(ws, {"t": "err", "why": "taken"}); continue
                    rooms[c] = {"host": ws, "hostId": me, "members": {me: ws}, "names": {me: rname}}
                    code, is_host = c, True
                    await send(ws, {"t": "hosted", "id": me})
                else:
                    room = rooms.get(c)
                    if not room:
                        await send(ws, {"t": "err", "why": "noroom"}); continue
                    if len(room["members"]) >= MAX_ROOM:
                        await send(ws, {"t": "err", "why": "full"}); continue
                    room["members"][me] = ws
                    room["names"][me] = rname
                    code = c
                    await send(ws, {"t": "joined", "id": me, "host": room["hostId"], "names": room["names"]})
                    await broadcast(room, {"t": "peer", "id": me, "name": room["names"][me]}, skip=me)
                continue
            room = rooms.get(code)
            if not room:
                break
            if t in ("snap", "time", "isl") and not is_host:
                continue
            if t not in ("snap", "isl") and len(raw) > MAX_SMALL:
                continue
            if t not in ("st", "emo", "look", "snap", "time", "isl"):
                continue
            m["from"] = me
            if t == "snap":
                to = room["members"].get(m.get("to"))
                if to:
                    await send(to, m)
            else:
                await broadcast(room, m, skip=me)
    finally:
        if acct and acct in online:
            online[acct].discard(ws)
            if not online[acct]:
                online.pop(acct, None)
        room = rooms.get(code) if code else None
        if room:
            room["members"].pop(me, None)
            room["names"].pop(me, None)
            if is_host:
                rooms.pop(code, None)
                for to in [k for k, v in invites.items() if v["code"] == code]:
                    invites.pop(to, None)
                await broadcast(room, {"t": "closed"})
            else:
                await broadcast(room, {"t": "leave", "id": me})


def health(connection, request):
    if request.headers.get("Upgrade", "").lower() != "websocket":
        r = connection.respond(HTTPStatus.OK, f"ok {len(rooms)} {len(online)} {('pg' if db.pg else 'sqlite') if db else 'nodb'}\n")
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Cache-Control"] = "no-store"
        return r
    return None


async def purge_loop():
    while True:
        try:
            await run(db.purge)
        except Exception:
            pass
        await asyncio.sleep(6 * 3600)


async def main():
    global db
    try:
        db = Store()
        print("store:", "postgres" if db.pg else "sqlite", flush=True)
    except Exception as e:
        print("store unavailable:", repr(e), flush=True)
    port = int(os.environ.get("PORT", "8787"))
    if db:
        asyncio.create_task(purge_loop())
    async with serve(handler, "0.0.0.0", port, process_request=health, max_size=MAX_MSG, ping_interval=20, ping_timeout=20):
        print(f"relay listening on {port}", flush=True)
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    asyncio.run(main())
