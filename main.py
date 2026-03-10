import socketio
import random
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Socket server sozlamalari
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins="*")
app = FastAPI()

# CORS sozlamalari (Vercel/v0 ulanishi uchun)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/socket.io", socketio.ASGIApp(sio))

rooms = {}

@sio.event
async def connect(sid, environ):
    print(f"Ulanish: {sid}")

@sio.event
async def create_room(sid):
    room_id = str(random.randint(1000, 9999))
    # Xona ma'lumotlarini yaratish
    rooms[room_id] = {
        "room_id": room_id, # Bu qator qo'shildi (muhim!)
        "admin": sid,
        "players": [{"id": sid, "name": "Host"}],
        "game_started": False
    }
    await sio.enter_room(sid, room_id)
    # Frontendga barcha xona ma'lumotlarini yuboramiz
    await sio.emit('room_data', rooms[room_id], room=sid)

@sio.event
async def join_room(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms:
        room = rooms[room_id]
        if not room['game_started'] and len(room['players']) < 4:
            new_player = {"id": sid, "name": f"O'yinchi {len(room['players'])+1}"}
            room['players'].append(new_player)
            await sio.enter_room(sid, room_id)
            # Yangi o'yinchi uchun xona ma'lumotlari
            await sio.emit('room_data', room, room=sid)
            # Barcha o'yinchilar uchun ro'yxatni yangilash
            await sio.emit('update_players', room['players'], room=room_id)
        else:
            await sio.emit('error', 'Xona to\'la yoki o\'yin boshlangan!', room=sid)
    else:
        await sio.emit('error', 'Xona topilmadi!', room=sid)

@sio.event
async def kick_player(sid, data):
    room_id = str(data.get('room_id'))
    target_id = data.get('target_id')
    if room_id in rooms and rooms[room_id]['admin'] == sid:
        rooms[room_id]['players'] = [p for p in rooms[room_id]['players'] if p['id'] != target_id]
        await sio.emit('kicked', room=target_id)
        await sio.emit('update_players', rooms[room_id]['players'], room=room_id)

@sio.event
async def start_game(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms and rooms[room_id]['admin'] == sid:
        if len(rooms[room_id]['players']) >= 2:
            rooms[room_id]['game_started'] = True
            await sio.emit('game_started', room=room_id)
