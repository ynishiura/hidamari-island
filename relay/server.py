# -*- coding: utf-8 -*-
"""ひだまり島ぐらし：友だちと一緒に歩くための中継サーバー（Render の Web Service で動かす）

合言葉ごとに「部屋」を作り、同じ部屋にいる人どうしのメッセージを中継するだけ。セーブは持たない。
  島の持ち主（host）  {"t":"host","code":合言葉,"name":名前}
  遊びに行く人（guest）{"t":"join","code":合言葉,"name":名前}
  そのあとは {"t":"st"|"emo"|"look"|"snap"|"time"|"bye", ...} を部屋の人に配る（送り主の id を付けて）
  ・snap（島のようす）と time（時刻）は持ち主だけが送れる。snap は "to" の相手だけに届ける
  ・持ち主が出ていくと、部屋はなくなる（来ていた人には "closed"）
起動： python server.py （PORT は環境変数。無ければ 8787）
"""
import asyncio, json, os, time, unicodedata, secrets
from http import HTTPStatus
from websockets.asyncio.server import serve

MAX_ROOM = 4                 # 1つの島に入れる人数（持ち主をふくむ）
MAX_MSG = 600_000            # 1つのメッセージの大きさの上限（島のようすは大きい）
MAX_SMALL = 4_000            # 島のようす以外のメッセージの上限
RATE = 40                    # 1秒あたりのメッセージの上限（ひとりあたり）
rooms = {}                   # code -> {"host": ws, "members": {id: ws}, "names": {id: name}}


def norm_code(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip().lower()
    return s if 3 <= len(s) <= 20 else None


def clean_name(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    return (s[:8] or "だれか")


async def send(ws, obj):
    try:
        await ws.send(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


async def broadcast(room, obj, skip=None):
    await asyncio.gather(*(send(w, obj) for i, w in room["members"].items() if i != skip))


async def handler(ws):
    me = secrets.token_hex(4)
    code = None
    is_host = False
    count, t0 = 0, time.monotonic()
    try:
        async for raw in ws:
            # 送りすぎを止める
            now = time.monotonic()
            if now - t0 >= 1:
                count, t0 = 0, now
            count += 1
            if count > RATE:
                continue
            if len(raw) > MAX_MSG:
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if not isinstance(m, dict):
                continue
            t = m.get("t")
            if code is None:
                c = norm_code(m.get("code"))
                if not c:
                    await send(ws, {"t": "err", "why": "code"}); continue
                if t == "host":
                    old = rooms.get(c)
                    if old:
                        await send(ws, {"t": "err", "why": "taken"}); continue
                    rooms[c] = {"host": ws, "hostId": me, "members": {me: ws}, "names": {me: clean_name(m.get("name"))}}
                    code, is_host = c, True
                    await send(ws, {"t": "hosted", "id": me})
                elif t == "join":
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
            if t in ("snap", "time") and not is_host:
                continue
            if t != "snap" and len(raw) > MAX_SMALL:
                continue
            if t not in ("st", "emo", "look", "snap", "time", "chat"):
                continue
            m["from"] = me
            if t == "snap":
                to = room["members"].get(m.get("to"))
                if to:
                    await send(to, m)
            else:
                await broadcast(room, m, skip=me)
    finally:
        room = rooms.get(code) if code else None
        if room:
            room["members"].pop(me, None)
            room["names"].pop(me, None)
            if is_host:
                rooms.pop(code, None)
                await broadcast(room, {"t": "closed"})
            else:
                await broadcast(room, {"t": "leave", "id": me})


def health(connection, request):
    # ふつうの HTTP（目を覚ます・生きているかの確かめ）には "ok" を返す
    if request.headers.get("Upgrade", "").lower() != "websocket":
        r = connection.respond(HTTPStatus.OK, f"ok {len(rooms)}\n")
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Cache-Control"] = "no-store"
        return r
    return None


async def main():
    port = int(os.environ.get("PORT", "8787"))
    async with serve(handler, "0.0.0.0", port, process_request=health, max_size=MAX_MSG, ping_interval=20, ping_timeout=20):
        print(f"relay listening on {port}", flush=True)
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    asyncio.run(main())
