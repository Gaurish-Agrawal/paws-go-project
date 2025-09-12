from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid
import time
from datetime import datetime, timedelta
import json

# top
import os
from flask import Flask
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")  # keep message_queue off for now

# In-memory data storage (use Redis/Database for production)
items = {}
requests = {}
helpers = set()
seekers = set()

from pathlib import Path, PurePath
import json

# Load catalog from JSON file
STORE_PATH = Path(app.static_folder) / "store_data.json"
with STORE_PATH.open("r", encoding="utf-8") as f:
    catalog = json.load(f)

category_meta = catalog["categories"]
items = {}

for it in catalog["items"]:
    key = it["name"].lower()
    meta = category_meta[it["category"]]
    items[key] = {
        "name": it["name"],
        "available": None,
        "last_updated": None,
        "updated_by": None,
        "category": meta["name"],   # << important
        # "location": meta["location"],  # keep if you want, or drop
    }

@app.route('/')
def index():
    mode = request.args.get('mode', 'seeker')
    return render_template('index.html', mode=mode)

@app.route('/api/items')
def get_items():
    search = request.args.get('search', '').lower()
    filtered_items = {}
    
    for key, item in items.items():
        if search in item['name'].lower():
            filtered_items[key] = item
    
    return jsonify(filtered_items)

@app.route('/api/items/<item_key>/status', methods=['POST'])
def update_item_status(item_key):
    data = request.json
    available = data.get('available')
    helper_id = data.get('helper_id')
    
    if item_key in items:
        items[item_key]['available'] = available
        items[item_key]['last_updated'] = datetime.now().isoformat()
        items[item_key]['updated_by'] = helper_id
        
        # Notify seekers who requested this item
        socketio.emit('item_updated', {
            'item_key': item_key,
            'item': items[item_key]
        }, room='seekers')
        
        # Check if this answers any pending requests
        answered_requests = []
        for req_id, req in list(requests.items()):
            if req['item_key'] == item_key:
                socketio.emit('request_answered', {
                    'request_id': req_id,
                    'item': items[item_key],
                    'available': available
                }, room=req['seeker_id'])
                answered_requests.append(req_id)
        
        # Remove answered requests
        for req_id in answered_requests:
            del requests[req_id]
        
        return jsonify({'success': True})
    
    return jsonify({'error': 'Item not found'}), 404

@app.route('/api/request', methods=['POST'])
def create_request():
    data = request.json
    item_name = data.get('item_name', '').strip()
    seeker_id = data.get('seeker_id')
    
    if not item_name:
        return jsonify({'error': 'Item name required'}), 400
    
    # Check if item exists in our predefined list
    item_key = item_name.lower()
    if item_key not in items:
        return jsonify({'error': 'Item not found in store inventory'}), 404
    
    # Create request
    request_id = str(uuid.uuid4())
    requests[request_id] = {
        'id': request_id,
        'item_key': item_key,
        'item_name': items[item_key]['name'],  # Use the properly formatted name
        'seeker_id': seeker_id,
        'created_at': datetime.now().isoformat()
    }
    
    # Notify helpers
    socketio.emit('new_request', {
        'request_id': request_id,
        'item_name': items[item_key]['name'],
        'item_key': item_key
    }, room='helpers')
    
    return jsonify({'success': True, 'request_id': request_id})

@app.route('/api/requests')
def get_requests():
    # Only return requests from last 30 minutes
    cutoff = datetime.now() - timedelta(minutes=30)
    active_requests = {}
    
    for req_id, req in requests.items():
        req_time = datetime.fromisoformat(req['created_at'])
        if req_time > cutoff:
            active_requests[req_id] = req
    
    return jsonify(active_requests)

@socketio.on('join_mode')
def on_join_mode(data):
    mode = data['mode']
    user_id = data['user_id']
    
    if mode == 'helper':
        helpers.add(user_id)
        join_room('helpers')
        join_room(user_id)
    else:
        seekers.add(user_id)
        join_room('seekers')
        join_room(user_id)
    
    emit('mode_joined', {'mode': mode, 'user_id': user_id})

@socketio.on('leave_mode')
def on_leave_mode(data):
    mode = data['mode']
    user_id = data['user_id']
    
    if mode == 'helper':
        helpers.discard(user_id)
        leave_room('helpers')
    else:
        seekers.discard(user_id)
        leave_room('seekers')
    
    leave_room(user_id)

@socketio.on('disconnect')
def on_disconnect():
    pass

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)