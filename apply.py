"""
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




import smtplib
from email.message import EmailMessage

                                                       
def sendemailwashu(email, item,status):
    if not status:
        return

    email_sender = 'pawsliveupdates@gmail.com'
    email_password = 'xfya sxug ntzj ubth'

    msg = EmailMessage()
    plural = "is" if item[-1]!="s" else "are"
    msg['Subject'] = '{} {} Available at Paws!'.format(item.title(),plural)
    msg['From'] = email_sender
    msg['To'] = email 
    
    
    from zoneinfo import ZoneInfo  # Python 3.9+
    plural = "was" if item[-1]!="s" else "were"
    status_text = "Available"
    CENTRAL = ZoneInfo("America/Chicago")
    timestamp = datetime.now(CENTRAL).strftime("%I:%M %p (%b %d, %Y)")  # e.g. 06:47 PM (Sep 12, 2025)
    
    plain_body = (
    f"{item.title()} {plural} reported as Available at Paws & Go at {timestamp}\n\n"
    "Note: This is based on a helper’s report and may not be a guarantee.\n\n"
    "Pawslive.onrender.com is not officially affiliated with WashU. Tip: To make sure updates don’t land in your junk folder, add "
    "pawsliveupdates@gmail.com to your contacts or move our email to inbox."
    )
    msg.set_content(plain_body)

    # HTML version
    html_body = f"
    <p><b>{item.title()} {plural}</b> reported as Available at Paws & Go at {timestamp}</p>
    <p><i>Note:</i> This is based on a helper’s report and may not be a guarantee. Pawslive.onrender.com is not officially affiliated with WashU.</p>
    <p><b>Tip:</b> To make sure updates don’t land in your junk folder, add
    pawsliveupdates@gmail.com to your contacts or move our email to inbox.</p>
    "
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(email_sender, email_password)
        smtp.send_message(msg)
        print(f"Available email sent to {email}")                                                   


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
                # Real-time notify seeker via WebSocket
                socketio.emit('request_answered', {
                    'request_id': req_id,
                    'item': items[item_key],
                    'available': available
                }, room=req['seeker_id'])
                
                # --- NEW: Email placeholder ---
                to_email = req.get('seeker_email')
                if to_email:
                    # TODO: implement actual email sending here
                    print(f"[INFO] Would send email to {to_email}: "
                          f"{items[item_key]['name']} is {'Available' if available else 'Not Available'}")
                    sendemailwashu(to_email, items[item_key]['name'],available)
                # ------------------------------
                
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
    seeker_email = (data.get('seeker_email') or '').strip()

    if not item_name:
        return jsonify({'error': 'Item name required'}), 400

    item_key = item_name.lower()
    if item_key not in items:
        return jsonify({'error': 'Item not found in store inventory'}), 404

    # Only accept @wustl.edu emails
    if seeker_email and not seeker_email.lower().endswith("@wustl.edu"):
        return jsonify({'error': 'Email must end with @wustl.edu'}), 400

    request_id = str(uuid.uuid4())
    now_iso = datetime.utcnow().isoformat() + 'Z'
    requests[request_id] = {
        'id': request_id,
        'item_key': item_key,
        'item_name': items[item_key]['name'],
        'seeker_id': seeker_id,
        'seeker_email': seeker_email or None,
        'created_at': now_iso,
        'status': 'pending'
    }

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


from datetime import datetime, timedelta, timezone

def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()

@app.route('/api/pending-requests')
def get_pending_requests():
    "Return all pending requests within a recent window (e.g., last 3 hours)."
    hours = int(request.args.get('hours', '3'))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    out = []
    for r in requests.values():
        if r.get('status', 'pending') != 'pending':
            continue
        created = datetime.fromisoformat(r['created_at'])
        if created >= cutoff:
            # attach current item snapshot (optional)
            item_snap = items.get(r['item_key'], {})
            out.append({
                'id': r['id'],
                'item_key': r['item_key'],
                'item_name': r['item_name'],
                'seeker_id': r['seeker_id'],
                'created_at': r['created_at'],
                'item': {
                    'name': item_snap.get('name'),
                    'available': item_snap.get('available'),
                    'last_updated': item_snap.get('last_updated'),
                    'updated_by': item_snap.get('updated_by'),
                    'category': item_snap.get('category'),
                }
            })

    # newest first
    out.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(out)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
    """