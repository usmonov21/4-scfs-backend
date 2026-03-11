import socketio
import random
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins="*")
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/socket.io", socketio.ASGIApp(sio))

rooms = {}

def create_deck():
    # S - Spades, C - Clubs, H - Hearts, D - Diamonds
    suits = ['S', 'C', 'H', 'D']
    # API qoidasi: 10 raqami '0' deb belgilanadi
    ranks = ['6', '7', '8', '9', '0', 'J', 'Q', 'K', 'A'] 
    deck = [{"rank": r, "suit": s} for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

@sio.event
async def connect(sid, environ):
    print(f"Ulanish: {sid}")

@sio.event
async def create_room(sid):
    room_id = str(random.randint(1000, 9999))
    rooms[room_id] = {
        "room_id": room_id,
        "admin": sid,
        "players": [{"id": sid, "name": "Host", "cards": []}],
        "game_started": False
    }
    await sio.enter_room(sid, room_id)
    await sio.emit('room_data', rooms[room_id], room=sid)

@sio.event
async def join_room(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms:
        room = rooms[room_id]
        if not room['game_started'] and len(room['players']) < 4:
            new_player = {"id": sid, "name": f"O'yinchi {len(room['players'])+1}", "cards": []}
            room['players'].append(new_player)
            await sio.enter_room(sid, room_id)
            await sio.emit('room_data', room, room=sid)
            await sio.emit('update_players', room['players'], room=room_id)
    else:
        await sio.emit('error', 'Xona topilmadi!', room=sid)

@sio.event
async def start_game(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms and rooms[room_id]['admin'] == sid:
        room = rooms[room_id]
        # O'yin faqat 2 yoki undan ko'p kishi bo'lganda boshlanadi
        if len(room['players']) >= 1: 
            deck = create_deck()
            for i, player in enumerate(room['players']):
                # Har bir o'yinchiga 9 tadan karta
                hand = deck[i*9 : (i+1)*9]
                player['cards'] = hand
                # Kartalarni aynan o'sha o'yinchining ID'siga yuboramiz
                await sio.emit('your_cards', hand, room=player['id'])
            
            room['game_started'] = True
            await sio.emit('game_started', room=room_id)

@sio.event
async def play_card(sid, data):
    room_id = str(data.get('room_id'))
    card = data.get('card')
    # Tashlangan kartani hamma ko'rishi uchun stolga chiqaramiz
    await sio.emit('card_on_table', {"player_id": sid, "card": card}, room=room_id)
