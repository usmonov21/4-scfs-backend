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

@sio.event
async def connect(sid, environ):
    print(f"Player connected: {sid}")

@sio.event
async def create_room(sid):
    room_id = str(random.randint(1000, 9999))
    rooms[room_id] = {
        "admin": sid,
        "players": [{"id": sid, "name": "Admin"}],
        "game_started": False
    }
    await sio.enter_room(sid, room_id)
    await sio.emit('room_data', rooms[room_id], room=sid)

@sio.event
async def join_room(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms:
        if len(rooms[room_id]['players']) < 4:
            new_player = {"id": sid, "name": f"Player {len(rooms[room_id]['players'])+1}"}
            rooms[room_id]['players'].append(new_player)
            await sio.enter_room(sid, room_id)
            await sio.emit('update_players', rooms[room_id]['players'], room=room_id)
        else:
            await sio.emit('error', 'Xona to\'la!', room=sid)
    else:
        await sio.emit('error', 'Xona topilmadi!', room=sid)

@sio.event
async def kick_player(sid, data):
    room_id = str(data.get('room_id'))
    target_id = data.get('target_id')
    if room_id in rooms and rooms[room_id]['admin'] == sid:
        rooms[room_id]['players'] = [p for p in rooms[room_id]['players'] if p['id'] != target_id]
        await sio.emit('kicked', 'Siz chiqarildingiz', room=target_id)
        await sio.emit('update_players', rooms[room_id]['players'], room=room_id)

@sio.event
async def start_game(sid, data):
    room_id = str(data.get('room_id'))
    if room_id in rooms and rooms[room_id]['admin'] == sid:
        if len(rooms[room_id]['players']) >= 2:
            await sio.emit('game_started', {'msg': 'O\'yin boshlandi!'}, room=room_id)
