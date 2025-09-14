import eventlet
eventlet.monkey_patch()

import os
import uuid
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_

# ----------------------- App & DB Setup -----------------------
def normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = (
    "postgresql+psycopg://paws_live_database_user:Vnkkg2x811ib6BVXMCSYT8g0tYrcKaiw@dpg-d333p88dl3ps738hu0n0-a.ohio-postgres.render.com/paws_live_database"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

socketio = SocketIO(app, cors_allowed_origins="*")
db = SQLAlchemy(app)

# ----------------------- Models -----------------------
class Item(db.Model):
    __tablename__ = "items"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, index=True, nullable=False)  # lowercased name
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(120))
    available = db.Column(db.Boolean, nullable=True)  # None = unknown
    last_updated = db.Column(db.DateTime, nullable=True)
    updated_by = db.Column(db.String(120), nullable=True)

class Request(db.Model):
    __tablename__ = "requests"
    id = db.Column(db.String(36), primary_key=True)  # uuid4 str
    item_key = db.Column(db.String(200), db.ForeignKey("items.key"), index=True, nullable=False)
    item_name = db.Column(db.String(200), nullable=False)
    seeker_id = db.Column(db.String(120), nullable=False)
    seeker_email = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(40), nullable=False, default="pending")

# ----------------------- One-time seed from JSON -----------------------
def seed_items_from_catalog():
    """Load static/store_data.json and upsert items by key (lowercased name)."""
    store_path = Path(app.static_folder) / "store_data.json"
    if not store_path.exists():
        return
    with store_path.open("r", encoding="utf-8") as f:
        catalog = json.load(f)

    category_meta = catalog.get("categories", {})
    existing_keys = {k for (k,) in db.session.query(Item.key).all()}

    new_rows = []
    for it in catalog.get("items", []):
        key = it["name"].lower()
        if key in existing_keys:
            # ensure category/name stay fresh if you edited the file
            db.session.query(Item).filter_by(key=key).update({
                Item.name: it["name"],
                Item.category: category_meta.get(it["category"], {}).get("name")
            })
        else:
            new_rows.append(
                Item(
                    key=key,
                    name=it["name"],
                    category=category_meta.get(it["category"], {}).get("name"),
                    available=None,
                    last_updated=None,
                    updated_by=None,
                )
            )
    if new_rows:
        db.session.add_all(new_rows)
    db.session.commit()

with app.app_context():
    db.create_all()
    seed_items_from_catalog()

# ----------------------- Helpers -----------------------
def utcnow():
    return datetime.now(timezone.utc)

def to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

def sendemailwashu(email, item, status):
    """Send available notification; only triggers if status is True (matches your original)."""
    if not status:
        return
    email_sender = 'pawsliveupdates@gmail.com'
    email_password = 'xfya sxug ntzj ubth'
    if not email_password:
        print("[WARN] EMAIL_PASSWORD not set; skipping email send.")
        return

    msg = EmailMessage()
    plural_is = "is" if not item.lower().endswith("s") else "are"
    msg['Subject'] = f'{item.title()} {plural_is} Available at Paws!'
    msg['From'] = email_sender
    msg['To'] = email

    from zoneinfo import ZoneInfo
    CENTRAL = ZoneInfo("America/Chicago")
    plural_was = "was" if not item.lower().endswith("s") else "were"
    timestamp = datetime.now(CENTRAL).strftime("%I:%M %p (%b %d, %Y)")

    plain_body = (
        f"{item.title()} {plural_was} reported as Available at Paws & Go at {timestamp}\n\n"
        "Note: This is based on a helper’s report and may not be a guarantee.\n\n"
        "Pawslive.onrender.com is not officially affiliated with WashU. Tip: To make sure updates don’t land in your junk folder, add "
        "pawsliveupdates@gmail.com to your contacts or move our email to inbox."
    )
    msg.set_content(plain_body)

    html_body = f"""
    <p><b>{item.title()} {plural_was}</b> reported as Available at Paws & Go at {timestamp}</p>
    <p><i>Note:</i> This is based on a helper’s report and may not be a guarantee. Pawslive.onrender.com is not officially affiliated with WashU.</p>
    <p><b>Tip:</b> To make sure updates don’t land in your junk folder, add
    pawsliveupdates@gmail.com to your contacts or move our email to inbox.</p>
    """
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(email_sender, email_password)
        smtp.send_message(msg)
        print(f"[EMAIL] Available email sent to {email}")

# ----------------------- In-memory presence tracking (unchanged) -----------------------
helpers = set()
seekers = set()

# ----------------------- Routes -----------------------
@app.route('/')
def index():
    mode = request.args.get('mode', 'seeker')
    return render_template('index.html', mode=mode)

@app.route('/api/items')
def get_items():
    search = (request.args.get('search') or '').strip().lower()
    q = Item.query
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Item.name.ilike(like), Item.key.ilike(like)))

    rows = q.order_by(Item.name.asc()).all()
    # Return the same dict-of-dicts shape you used before
    out = {}
    for it in rows:
        out[it.key] = {
            "name": it.name,
            "available": it.available,
            "last_updated": to_iso(it.last_updated),
            "updated_by": it.updated_by,
            "category": it.category,
        }
    return jsonify(out)

@app.route('/api/items/<item_key>/status', methods=['POST'])
def update_item_status(item_key):
    data = request.get_json(force=True)
    available = data.get('available')
    helper_id = data.get('helper_id')

    it = Item.query.filter_by(key=item_key.lower()).first()
    if not it:
        return jsonify({'error': 'Item not found'}), 404

    it.available = bool(available) if available is not None else None
    it.last_updated = utcnow()
    it.updated_by = helper_id
    db.session.commit()

    # Notify seekers
    socketio.emit('item_updated', {
        'item_key': it.key,
        'item': {
            "name": it.name,
            "available": it.available,
            "last_updated": to_iso(it.last_updated),
            "updated_by": it.updated_by,
            "category": it.category,
        }
    }, room='seekers')

    # Answer pending requests for this item
    pending = Request.query.filter_by(item_key=it.key, status='pending').all()
    for r in pending:
        socketio.emit('request_answered', {
            'request_id': r.id,
            'item': {
                "name": it.name,
                "available": it.available,
                "last_updated": to_iso(it.last_updated),
                "updated_by": it.updated_by,
                "category": it.category,
            },
            'available': it.available
        }, room=r.seeker_id)

        if r.seeker_email:
            try:
                sendemailwashu(r.seeker_email, it.name, it.available)
            except Exception as e:
                print(f"[EMAIL ERROR] {e}")

        r.status = 'answered'
    db.session.commit()

    return jsonify({'success': True})

@app.route('/api/request', methods=['POST'])
def create_request():
    data = request.get_json(force=True)
    item_name = (data.get('item_name') or '').strip()
    seeker_id = data.get('seeker_id')
    seeker_email = (data.get('seeker_email') or '').strip()

    if not item_name:
        return jsonify({'error': 'Item name required'}), 400

    item_key = item_name.lower()
    it = Item.query.filter_by(key=item_key).first()
    if not it:
        return jsonify({'error': 'Item not found in store inventory'}), 404

    if seeker_email and not seeker_email.lower().endswith("@wustl.edu"):
        return jsonify({'error': 'Email must end with @wustl.edu'}), 400

    request_id = str(uuid.uuid4())
    r = Request(
        id=request_id,
        item_key=item_key,
        item_name=it.name,
        seeker_id=seeker_id,
        seeker_email=seeker_email or None,
        created_at=utcnow(),
        status='pending'
    )
    db.session.add(r)
    db.session.commit()

    socketio.emit('new_request', {
        'request_id': request_id,
        'item_name': it.name,
        'item_key': item_key
    }, room='helpers')

    return jsonify({'success': True, 'request_id': request_id})

@app.route('/api/requests')
def get_requests():
    cutoff = utcnow() - timedelta(minutes=30)
    rows = (Request.query
            .filter(Request.created_at > cutoff, Request.status == 'pending')
            .order_by(Request.created_at.desc())
            .all())
    out = {}
    for r in rows:
        out[r.id] = {
            'id': r.id,
            'item_key': r.item_key,
            'item_name': r.item_name,
            'seeker_id': r.seeker_id,
            'seeker_email': r.seeker_email,
            'created_at': r.created_at.isoformat(),
            'status': r.status
        }
    return jsonify(out)

@app.route('/api/pending-requests')
def get_pending_requests():
    hours = int(request.args.get('hours', '3'))
    cutoff = utcnow() - timedelta(hours=hours)

    rows = (Request.query
            .filter(Request.status == 'pending', Request.created_at >= cutoff)
            .order_by(Request.created_at.desc())
            .all())

    # join item snapshot
    items_by_key = {k: v for k, v in db.session.query(Item.key, Item).all()}
    out = []
    for r in rows:
        it = items_by_key.get(r.item_key)
        out.append({
            'id': r.id,
            'item_key': r.item_key,
            'item_name': r.item_name,
            'seeker_id': r.seeker_id,
            'created_at': r.created_at.isoformat(),
            'item': {
                'name': it.name if it else None,
                'available': it.available if it else None,
                'last_updated': to_iso(it.last_updated) if it else None,
                'updated_by': it.updated_by if it else None,
                'category': it.category if it else None,
            }
        })
    return jsonify(out)

# ----------------------- Socket.IO -----------------------
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

# ----------------------- Main -----------------------
if __name__ == '__main__':
    # You can keep debug True locally; Render will ignore it
    socketio.run(app, debug=True, host='0.0.0.0', port=int(os.getenv("PORT", "5000")))