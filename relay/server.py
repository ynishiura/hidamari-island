# -*- coding: utf-8 -*-
"""ひだまり島ぐらし：友だちと遊ぶための中継サーバー（Render の Web Service で動かす）

1. 合言葉の部屋：合言葉ごとに「部屋」を作り、同じ部屋にいる人どうしのメッセージを中継する（島の持ち主が出ると部屋はなくなる）
     島の持ち主  {"t":"host","code":合言葉,"name":名前}      遊びに行く人 {"t":"join","code":合言葉,"name":名前}
     そのあと {"t":"st"|"emo"|"look"|"snap"|"time"|"isl", ...} を部屋の人に配る（snap・time・isl は持ち主だけ。snap は "to" の相手だけ）
     isl は島の変化（木を切った・花を植えたなど、変わった項目だけ）
2. フレンドとメール（store.py に保存。DATABASE_URL があれば Neon）
     {"t":"register","name"} → {"t":"acct","id","secret"}       はじめての人に友だちIDを作る
     {"t":"auth","id","secret","name"} → {"t":"authed","id"}    つなぐたびに名のる（ここからフレンドの操作ができる）
     friends / freq{to} / faccept{id} / fdecline{id} / fcancel{id} / fremove{id}
     mail{to,body} / mailbox / mailread{ids} / invite{to,code}
   相手がつないでいれば、すぐに知らせる（friends・mailnew・invite）。招待は 10分で消える
起動： python server.py （PORT は環境変数。無ければ 8787）
"""
import asyncio, json, os, time, unicodedata, secrets
from http import HTTPStatus
from websockets.asyncio.server import serve
import ngfilter
from store import Store, norm_id

MAX_ROOM = 4
MAX_MSG = 600_000
MAX_SMALL = 4_000
RATE = 40
FRIEND_MAX, REQ_MAX = 50, 50
MAIL_MAX, MAIL_PER_DAY = 50, 50
INVITE_TTL = 600

rooms = {}                   # code -> {"host": ws, "hostId": id, "members": {id: ws}, "names": {id: name}}
online = {}                  # 友だちID -> set(ws)
invites = {}                 # 招待された人の友だちID -> {from, name, code, at}
db = None


def norm_code(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    return s if 3 <= len(s) <= 20 else None


def clean_name(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    s = s[:8] or "だれか"
    return "だれか" if ngfilter.has_ng_name(s) else s


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


async def friends_msg(pid):
    p = await run(db.get, pid)
    if not p:
        return {"t": "friends", "list": [], "reqIn": [], "reqOut": []}
    ids = list(dict.fromkeys(p["friends"] + p["reqIn"] + p["reqOut"]))
    names = await run(db.names, ids)
    row = lambda i: {"id": i, "name": names.get(i, "だれか"), "online": bool(online.get(i))}
    return {"t": "friends", "list": [row(i) for i in p["friends"]], "reqIn": [row(i) for i in p["reqIn"]], "reqOut": [row(i) for i in p["reqOut"]]}


async def account_op(ws, me, m):
    """フレンドとメールの操作。me は名のった人の友だちID。"""
    t = m.get("t")
    if t == "friends":
        await send(ws, await friends_msg(me)); return
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
            if other in a["friends"]:
                await send(ws, {"t": "err", "why": "already"}); return
            if len(a["friends"]) >= FRIEND_MAX:
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
            if len(a["friends"]) >= FRIEND_MAX or len(b["friends"]) >= FRIEND_MAX:
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
        a = await run(db.get, me)
        if not to or not a or to not in a["friends"]:
            await send(ws, {"t": "err", "why": "notfriend"}); return
        if not body or len(body) > 50:
            await send(ws, {"t": "err", "why": "mlen"}); return
        if ngfilter.bad_chars(body) or ngfilter.has_ng(body):
            await send(ws, {"t": "err", "why": "ng"}); return
        if await run(db.sent_today, me) >= MAIL_PER_DAY:
            await send(ws, {"t": "err", "why": "mday"}); return
        b = await run(db.get, to)
        await run(db.add_mail, me, to, a["name"], b["name"] if b else "だれか", body)
        await send(ws, {"t": "info", "why": "mailsent", "name": b["name"] if b else ""})
        await push(to, {"t": "mailnew", "from": me, "name": a["name"], "unread": await run(db.unread, to)}); return
    if t == "invite":
        to = norm_id(m.get("to")); code = norm_code(m.get("code"))
        a = await run(db.get, me)
        if not to or not code or not a or to not in a["friends"]:
            await send(ws, {"t": "err", "why": "notfriend"}); return
        invites[to] = {"from": me, "name": a["name"], "code": code, "at": time.time()}
        await push(to, {"t": "invite", "from": me, "name": a["name"], "code": code})
        await send(ws, {"t": "info", "why": "invited", "name": (await run(db.names, [to])).get(to, ""), "online": bool(online.get(to))}); return


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
            # ---- フレンド・メール ----
            if t == "register" and db and not acct:
                pid, sec = await run(db.create, clean_name(m.get("name")))
                acct = pid; online.setdefault(acct, set()).add(ws)
                await send(ws, {"t": "acct", "id": pid, "secret": sec}); continue
            if t == "auth" and db and not acct:
                pid = norm_id(m.get("id"))
                if not pid or not await run(db.check, pid, str(m.get("secret") or "")):
                    await send(ws, {"t": "err", "why": "auth"}); continue
                acct = pid; online.setdefault(acct, set()).add(ws)
                await run(db.touch, pid, clean_name(m.get("name")))
                await send(ws, {"t": "authed", "id": pid, "unread": await run(db.unread, pid)})
                inv = invites.get(pid)
                if inv and time.time() - inv["at"] < INVITE_TTL and inv["code"] in rooms:
                    await send(ws, {"t": "invite", "from": inv["from"], "name": inv["name"], "code": inv["code"]})
                continue
            if t in ("friends", "freq", "faccept", "fdecline", "fcancel", "fremove", "mail", "mailbox", "mailread", "invite"):
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
                if t == "host":
                    if rooms.get(c):
                        await send(ws, {"t": "err", "why": "taken"}); continue
                    rooms[c] = {"host": ws, "hostId": me, "members": {me: ws}, "names": {me: clean_name(m.get("name"))}}
                    code, is_host = c, True
                    await send(ws, {"t": "hosted", "id": me})
                else:
                    room = rooms.get(c)
                    if not room:
                        await send(ws, {"t": "err", "why": "noroom"}); continue
                    if len(room["members"]) >= MAX_ROOM:
                        await send(ws, {"t": "err", "why": "full"}); continue
                    room["members"][me] = ws
                    room["names"][me] = clean_name(m.get("name"))
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
