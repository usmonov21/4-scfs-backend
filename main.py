"""
4 BURA — Game Server (Updated Rules v2)
========================================
Rules:
  - 36-card deck (6 → Ace)
  - 4 cards dealt to each player
  - Clockwise (right of admin plays first)
  - Trump system:
      * active_trump_card  → first card flipped open after dealing (current trump suit)
      * reserve_trump_card → last card placed face-down at deck bottom;
                             when deck empties, its suit becomes the next trump
  - After each round: winner draws first → others draw clockwise → hands refill to 4
  - Bura: 3+ same-suit cards in hand
"""

import random
import string
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import socketio

# ─── Setup ───────────────────────────────────────────────────────────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)
app = FastAPI(title="4 Bura Game Server v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

# ─── Constants ───────────────────────────────────────────────────────────────
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]
SUIT_NAMES = {"S": "♠ Pik", "H": "♥ Ko'rak", "D": "♦ Karo", "C": "♣ Treff"}
RANK_VALUES = {r: i for i, r in enumerate(RANKS)}
CARDS_PER_HAND = 4

rooms: dict[str, dict] = {}
player_rooms: dict[str, str] = {}


# ─── Helpers ─────────────────────────────────────────────────────────────────
def make_deck() -> list[dict]:
    deck = [{"rank": r, "suit": s} for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def card_image_url(card: dict) -> str:
    return f"https://deckofcardsapi.com/static/img/{card['rank']}{card['suit']}.png"


def card_back_url() -> str:
    return "https://deckofcardsapi.com/static/img/back.png"


def detect_bura(hand: list[dict]) -> list[list[dict]]:
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for c in hand:
        groups[c["suit"]].append(c)
    return [cards for cards in groups.values() if len(cards) >= 3]


def next_player_idx(players: list, current_sid: str) -> int:
    idx = next((i for i, p in enumerate(players) if p["sid"] == current_sid), 0)
    return (idx + 1) % len(players)


def get_room_state(room_id: str, viewer_sid: str = None) -> dict:
    room = rooms[room_id]
    players_info = []
    for p in room["players"]:
        is_me = p["sid"] == viewer_sid
        players_info.append({
            "sid": p["sid"],
            "name": p["name"],
            "avatar": p.get("avatar", "🎴"),
            "hand_count": len(p.get("hand", [])),
            "hand": p.get("hand", []) if is_me else None,
            "score": p.get("score", 0),
            "is_admin": p["sid"] == room["admin"],
            "is_active": p["sid"] == room.get("current_turn"),
        })

    atc = room.get("active_trump_card")
    active_trump_info = None
    if atc:
        active_trump_info = {
            "rank": atc["rank"],
            "suit": atc["suit"],
            "image_url": card_image_url(atc),
        }

    rtc = room.get("reserve_trump_card")
    deck_empty = len(room.get("deck", [])) == 0
    reserve_trump_info = None
    if rtc:
        reserve_trump_info = {
            "suit": rtc["suit"] if deck_empty else None,
            "rank": rtc["rank"] if deck_empty else None,
            "image_url": card_image_url(rtc) if deck_empty else card_back_url(),
            "revealed": deck_empty,
        }
    elif room.get("reserve_used"):
        # reserve already used but remember its suit
        reserve_trump_info = {"suit": None, "rank": None, "image_url": None, "revealed": True, "used": True}

    return {
        "room_id": room_id,
        "status": room["status"],
        "players": players_info,
        "table_cards": room.get("table_cards", []),
        "trump_suit": room.get("trump_suit"),
        "active_trump_card": active_trump_info,
        "reserve_trump_card": reserve_trump_info,
        "deck_count": len(room.get("deck", [])),
        "current_turn": room.get("current_turn"),
        "round_starter": room.get("round_starter"),
        "admin": room["admin"],
        "bura_announced": room.get("bura_announced", []),
        "winner": room.get("winner"),
        "round_phase": room.get("round_phase", "play"),
    }


# ─── Draw Logic ──────────────────────────────────────────────────────────────
async def draw_cards_after_round(room_id: str, winner_sid: str):
    room = rooms[room_id]
    players = room["players"]
    n = len(players)

    winner_idx = next((i for i, p in enumerate(players) if p["sid"] == winner_sid), 0)
    draw_order = [players[(winner_idx + i) % n] for i in range(n)]

    trump_changed = False

    for p in draw_order:
        needed = CARDS_PER_HAND - len(p["hand"])
        for _ in range(needed):
            if room["deck"]:
                drawn = room["deck"].pop(0)
                p["hand"].append(drawn)
            elif room.get("reserve_trump_card"):
                reserve = room.pop("reserve_trump_card")
                room["reserve_trump_card"] = None
                new_trump = reserve["suit"]
                room["trump_suit"] = new_trump
                room["active_trump_card"] = reserve
                room["reserve_used"] = True
                p["hand"].append(reserve)
                trump_changed = True

    if trump_changed:
        ns = room["trump_suit"]
        await sio.emit(
            "trump_changed",
            {
                "new_trump_suit": ns,
                "suit_name": SUIT_NAMES.get(ns, ns),
                "card": active_trump_info if (active_trump_info := room.get("active_trump_card")) else None,
            },
            room=room_id,
        )
        await sio.emit(
            "system_message",
            {"text": f"🔄 Kozir o'zgardi! Yangi kozir: {SUIT_NAMES.get(ns)} 🃏", "type": "trump"},
            room=room_id,
        )


# ─── REST ─────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "game": "4 Bura v2", "rooms": len(rooms)}


@app.get("/room/{room_id}")
async def get_room(room_id: str):
    if room_id not in rooms:
        return {"error": "Room not found"}
    r = rooms[room_id]
    return {"room_id": room_id, "status": r["status"], "players": len(r["players"])}


# ─── Socket Events ────────────────────────────────────────────────────────────
@sio.event
async def connect(sid, environ, auth):
    print(f"[CONNECT] {sid}")


@sio.event
async def disconnect(sid):
    print(f"[DISCONNECT] {sid}")
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]
    leaving = next((p for p in room["players"] if p["sid"] == sid), None)
    room["players"] = [p for p in room["players"] if p["sid"] != sid]
    player_rooms.pop(sid, None)

    if not room["players"]:
        del rooms[room_id]
        return
    if room["admin"] == sid:
        room["admin"] = room["players"][0]["sid"]
    if room["status"] == "playing" and len(room["players"]) < 2:
        room["status"] = "finished"
        room["winner"] = room["players"][0]["name"]
    if room.get("current_turn") == sid and room["players"]:
        room["current_turn"] = room["players"][0]["sid"]

    name = leaving["name"] if leaving else "O'yinchi"
    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])
    await sio.emit("system_message", {"text": f"{name} xonadan chiqdi.", "type": "warning"}, room=room_id)


@sio.event
async def create_room(sid, data):
    room_id = "".join(random.choices(string.digits, k=4))
    while room_id in rooms:
        room_id = "".join(random.choices(string.digits, k=4))

    rooms[room_id] = {
        "admin": sid,
        "status": "waiting",
        "players": [{"sid": sid, "name": data.get("name", "O'yinchi"),
                     "avatar": data.get("avatar", "🎴"), "hand": [], "score": 0}],
        "deck": [], "table_cards": [],
        "trump_suit": None, "active_trump_card": None,
        "reserve_trump_card": None, "reserve_used": False,
        "current_turn": None, "round_starter": None,
        "bura_announced": [], "winner": None, "round_phase": "play",
    }
    player_rooms[sid] = room_id
    await sio.enter_room(sid, room_id)
    await sio.emit("room_created", {"room_id": room_id}, to=sid)
    await sio.emit("room_update", get_room_state(room_id, sid), to=sid)


@sio.event
async def join_room(sid, data):
    room_id = data.get("room_id", "").strip()
    name = data.get("name", "O'yinchi")
    avatar = data.get("avatar", "🎴")

    if room_id not in rooms:
        await sio.emit("error", {"message": "Xona topilmadi!"}, to=sid)
        return
    room = rooms[room_id]
    if room["status"] != "waiting":
        await sio.emit("error", {"message": "O'yin allaqachon boshlangan!"}, to=sid)
        return
    if len(room["players"]) >= 4:
        await sio.emit("error", {"message": "Xona to'liq (max 4 o'yinchi)!"}, to=sid)
        return
    if any(p["sid"] == sid for p in room["players"]):
        await sio.emit("error", {"message": "Siz allaqachon bu xonasiz!"}, to=sid)
        return

    room["players"].append({"sid": sid, "name": name, "avatar": avatar, "hand": [], "score": 0})
    player_rooms[sid] = room_id
    await sio.enter_room(sid, room_id)
    await sio.emit("room_joined", {"room_id": room_id}, to=sid)
    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])
    await sio.emit("system_message", {"text": f"{name} xonaga qo'shildi! 🎉", "type": "info"}, room=room_id)


@sio.event
async def kick_player(sid, data):
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]
    if room["admin"] != sid:
        await sio.emit("error", {"message": "Faqat admin chetlata oladi!"}, to=sid)
        return
    target_sid = data.get("target_sid")
    target = next((p for p in room["players"] if p["sid"] == target_sid), None)
    if not target:
        return
    room["players"] = [p for p in room["players"] if p["sid"] != target_sid]
    player_rooms.pop(target_sid, None)
    await sio.leave_room(target_sid, room_id)
    await sio.emit("kicked", {"message": "Siz admin tomonidan xonadan chiqarildingiz."}, to=target_sid)
    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])
    await sio.emit("system_message", {"text": f"{target['name']} xonadan chiqarildi.", "type": "warning"}, room=room_id)


@sio.event
async def start_game(sid, data):
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]
    if room["admin"] != sid:
        await sio.emit("error", {"message": "Faqat admin o'yinni boshlaydi!"}, to=sid)
        return
    if len(room["players"]) < 2:
        await sio.emit("error", {"message": "Kamida 2 o'yinchi kerak!"}, to=sid)
        return

    deck = make_deck()
    n = len(room["players"])

    # Deal 4 cards to each player
    for i, p in enumerate(room["players"]):
        p["hand"] = deck[i * CARDS_PER_HAND:(i + 1) * CARDS_PER_HAND]
        p["score"] = 0

    remaining = deck[n * CARDS_PER_HAND:]

    # Active trump: first remaining card (face up)
    active_trump_card = remaining.pop(0) if remaining else None
    # Reserve trump: last remaining card (face down at bottom)
    reserve_trump_card = remaining.pop(-1) if remaining else None

    room.update({
        "deck": remaining,
        "active_trump_card": active_trump_card,
        "reserve_trump_card": reserve_trump_card,
        "reserve_used": False,
        "trump_suit": (active_trump_card or reserve_trump_card or {}).get("suit") or random.choice(SUITS),
        "status": "playing",
        "table_cards": [],
        "bura_announced": [],
        "winner": None,
        "round_phase": "play",
    })

    # First turn: player to the RIGHT of admin (index + 1 clockwise)
    admin_idx = next((i for i, p in enumerate(room["players"]) if p["sid"] == sid), 0)
    first_idx = (admin_idx + 1) % n
    room["current_turn"] = room["players"][first_idx]["sid"]
    room["round_starter"] = room["current_turn"]

    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])
    await sio.emit(
        "system_message",
        {"text": f"O'yin boshlandi! Kozir: {SUIT_NAMES.get(room['trump_suit'])} 🃏", "type": "success"},
        room=room_id,
    )


@sio.event
async def play_card(sid, data):
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]

    if room["status"] != "playing":
        await sio.emit("error", {"message": "O'yin hali boshlanmagan!"}, to=sid)
        return
    if room.get("round_phase") != "play":
        await sio.emit("error", {"message": "Hozir karta olish vaqti!"}, to=sid)
        return
    if room["current_turn"] != sid:
        await sio.emit("error", {"message": "Sizning navbatingiz emas!"}, to=sid)
        return

    player = next((p for p in room["players"] if p["sid"] == sid), None)
    if not player:
        return

    card = next(
        (c for c in player["hand"] if c["rank"] == data.get("rank") and c["suit"] == data.get("suit")),
        None,
    )
    if not card:
        await sio.emit("error", {"message": "Bu karta sizda yo'q!"}, to=sid)
        return

    player["hand"].remove(card)
    room["table_cards"].append({
        "rank": card["rank"], "suit": card["suit"],
        "player_sid": sid, "player_name": player["name"],
        "image_url": card_image_url(card),
    })

    await sio.emit(
        "card_played",
        {"player_sid": sid, "player_name": player["name"],
         "card": {"rank": card["rank"], "suit": card["suit"], "image_url": card_image_url(card)}},
        room=room_id,
    )

    if len(room["table_cards"]) >= len(room["players"]):
        await asyncio.sleep(1.2)
        await resolve_round(room_id)
    else:
        nxt = next_player_idx(room["players"], sid)
        room["current_turn"] = room["players"][nxt]["sid"]
        for p in room["players"]:
            await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])


async def resolve_round(room_id: str):
    room = rooms[room_id]
    table = room["table_cards"]
    trump = room["trump_suit"]
    lead_suit = table[0]["suit"]

    winner_entry = table[0]
    for entry in table[1:]:
        w, c = winner_entry, entry
        w_trump = w["suit"] == trump
        c_trump = c["suit"] == trump
        if c_trump and not w_trump:
            winner_entry = entry
        elif c_trump == w_trump:
            if c["suit"] == w["suit"]:
                if RANK_VALUES[c["rank"]] > RANK_VALUES[w["rank"]]:
                    winner_entry = entry
            elif c["suit"] == lead_suit and not w_trump:
                if RANK_VALUES[c["rank"]] > RANK_VALUES[w["rank"]]:
                    winner_entry = entry

    winner_sid = winner_entry["player_sid"]
    winner_player = next((p for p in room["players"] if p["sid"] == winner_sid), None)
    if winner_player:
        winner_player["score"] += len(table)
    winner_name = winner_player["name"] if winner_player else "Noma'lum"

    await sio.emit(
        "round_result",
        {"winner_sid": winner_sid, "winner_name": winner_name, "points": len(table), "table_cards": table},
        room=room_id,
    )
    await sio.emit(
        "system_message",
        {"text": f"🏆 {winner_name} raundni yutdi! (+{len(table)} ochko)", "type": "success"},
        room=room_id,
    )

    room["table_cards"] = []
    room["round_phase"] = "draw"

    await draw_cards_after_round(room_id, winner_sid)

    total_left = (
        len(room["deck"])
        + (1 if room.get("reserve_trump_card") else 0)
        + sum(len(p["hand"]) for p in room["players"])
    )

    if total_left == 0 or all(len(p["hand"]) == 0 for p in room["players"]):
        await end_game(room_id)
        return

    room["current_turn"] = winner_sid
    room["round_starter"] = winner_sid
    room["round_phase"] = "play"

    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])


async def end_game(room_id: str):
    room = rooms[room_id]
    room["status"] = "finished"
    scores = sorted([(p["name"], p["score"]) for p in room["players"]], key=lambda x: x[1], reverse=True)
    room["winner"] = scores[0][0]
    await sio.emit("game_over", {"winner": room["winner"], "scores": scores}, room=room_id)
    await sio.emit("system_message", {"text": f"🎊 O'yin tugadi! G'olib: {room['winner']}!", "type": "success"}, room=room_id)
    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])


@sio.event
async def announce_bura(sid, data):
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]
    player = next((p for p in room["players"] if p["sid"] == sid), None)
    if not player:
        return
    combos = detect_bura(player["hand"])
    if combos:
        bura_info = {
            "player_sid": sid, "player_name": player["name"],
            "combos": [[{"rank": c["rank"], "suit": c["suit"], "image_url": card_image_url(c)} for c in combo] for combo in combos],
        }
        room["bura_announced"].append(bura_info)
        await sio.emit("bura_announced", bura_info, room=room_id)
        await sio.emit("system_message", {"text": f"🔥 {player['name']} BURA e'lon qildi!", "type": "bura"}, room=room_id)
    else:
        await sio.emit("error", {"message": "Sizda Bura kombinatsiyasi yo'q!"}, to=sid)


@sio.event
async def restart_game(sid, data):
    room_id = player_rooms.get(sid)
    if not room_id or room_id not in rooms:
        return
    room = rooms[room_id]
    if room["admin"] != sid:
        return
    room.update({
        "status": "waiting", "deck": [], "table_cards": [],
        "trump_suit": None, "active_trump_card": None,
        "reserve_trump_card": None, "reserve_used": False,
        "current_turn": None, "round_starter": None,
        "bura_announced": [], "winner": None, "round_phase": "play",
    })
    for p in room["players"]:
        p["hand"] = []
        p["score"] = 0
    for p in room["players"]:
        await sio.emit("room_update", get_room_state(room_id, p["sid"]), to=p["sid"])
    await sio.emit("system_message", {"text": "Qayta boshlashga tayyor! Admin 'Boshlash' tugmasini bossin.", "type": "info"}, room=room_id)
