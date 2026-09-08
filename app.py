"""
app.py
------
Flask application entry point for the HyperAid Emergency Response Assistant.

Run with:
    python app.py
Then visit http://127.0.0.1:5000
"""

import os
import io
import csv
import uuid
import secrets
import functools
import requests
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import init_db, get_db, DB_PATH
from utils import (
    rank_nearby, chatbot_reply, analyse_accident_image,
    DISCLAIMER, SYMPTOM_TREE, haversine, pet_chatbot_reply,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # 6 MB upload cap

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Google Maps API key - add your own key as an environment variable.
# The map page works in a graceful "offline" demo mode without one.
app.config["GOOGLE_MAPS_API_KEY"] = os.environ.get("GOOGLE_MAPS_API_KEY", "")

EMERGENCY_NUMBERS = {
    "All-in-one Emergency": "112",
    "Ambulance": "108",
    "Police": "100",
    "Fire": "101",
    "Women's Helpline": "1091",
    "Child Helpline": "1098",
}

# Minimal offline cache so emergency numbers + first-aid pages are reachable
# with no signal. Deliberately small - we cache shells, not live data.
SERVICE_WORKER_JS = """
const CACHE_NAME = 'rescueai-v2';
const OFFLINE_URLS = ['/', '/faq', '/blog', '/about', '/services', '/disaster-guides', '/offline',
  '/static/css/style.css', '/static/js/main.js',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', event => {
  // Cache each URL individually (not addAll) so one failure - e.g. a page
  // that redirects to /login for a signed-out visitor - doesn't stop the
  // rest of the app shell from being precached for offline use.
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      Promise.all(OFFLINE_URLS.map(url =>
        fetch(url, { redirect: 'manual' })
          .then(resp => { if (resp && (resp.ok || resp.type === 'opaqueredirect')) return cache.put(url, resp); })
          .catch(() => {})
      ))
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).then(resp => {
      const copy = resp.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
      return resp;
    }).catch(() =>
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        if (event.request.mode === 'navigate') return caches.match('/offline');
        return new Response('', { status: 503 });
      })
    )
  );
});
"""


# ----------------------------------------------------------------------
# Database lifecycle
# ----------------------------------------------------------------------
@app.before_request
def _open_db():
    g.db = get_db()


@app.teardown_appcontext
def _close_db(exception=None):
    db = g.pop("db", None) if hasattr(g, "pop") else None
    if db is not None:
        db.close()


# ----------------------------------------------------------------------
# Site-wide login gate
# The whole site now requires an account. The only exceptions are:
#   - the auth pages themselves (login/register/forgot password/logout)
#   - static assets, the PWA manifest, and the service worker
#   - a handful of public *share-link* pages that exist specifically so
#     someone WITHOUT an account (a responder, family member, pet-sitter)
#     can still view a card someone shared with them
#   - the SOS trigger and the /watch companion button, so a person in an
#     actual emergency is never blocked from calling for help by a login
#     screen. If you'd rather lock these down too, remove them below.
# ----------------------------------------------------------------------
PUBLIC_ENDPOINTS = {
    "login", "register", "forgot_password", "logout", "static",
    "pwa_manifest", "service_worker", "offline_page",
    "watch_view", "api_sos",
    "emergency_card", "fridge_magnet", "pet_card", "dependent_card",
    "track_public", "support_public",
    "sos_chat_page", "api_sos_chat_get", "api_sos_chat_post", "api_track_latest",
}


@app.before_request
def _require_login():
    if request.endpoint is None:
        return  # let unmatched URLs fall through to the normal 404 handler
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))


# ----------------------------------------------------------------------
# Auth helpers
# ----------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return g.db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def log_admin_action(action, target=None, details=None):
    """Record an admin action for the audit trail."""
    admin = current_user()
    g.db.execute(
        "INSERT INTO admin_audit_log (admin_id, action, target, details) VALUES (?,?,?,?)",
        (admin["id"] if admin else None, action, target, details),
    )
    g.db.commit()


@app.context_processor
def inject_globals():
    return {
        "logged_in_user": current_user(),
        "emergency_numbers": EMERGENCY_NUMBERS,
        "current_year": datetime.now().year,
        "gmaps_key": app.config["GOOGLE_MAPS_API_KEY"],
    }


# ----------------------------------------------------------------------
# Public pages
# ----------------------------------------------------------------------
@app.route("/")
def index():
    stats = {
        "hospitals": g.db.execute("SELECT COUNT(*) c FROM hospitals").fetchone()["c"],
        "ambulances": g.db.execute("SELECT COUNT(*) c FROM ambulances").fetchone()["c"],
        "police_stations": g.db.execute("SELECT COUNT(*) c FROM police_stations").fetchone()["c"],
        "users_helped": 12500,  # illustrative headline stat for the hero section
    }
    testimonials = g.db.execute(
        "SELECT * FROM feedback ORDER BY id DESC LIMIT 6"
    ).fetchall()
    return render_template("index.html", stats=stats, testimonials=testimonials)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/services")
def services():
    return render_template("services.html")


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/blog")
def blog():
    return render_template("blog.html")


@app.route("/disaster-guides")
def disaster_guides():
    return render_template("disaster_guides.html")


# ----------------------------------------------------------------------
# AI Chatbot
# ----------------------------------------------------------------------
@app.route("/chatbot")
def chatbot_page():
    return render_template("chatbot.html")


@app.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    lang = (data.get("lang") or "en").split("-")[0]  # 'hi-IN' -> 'hi'
    mode = data.get("mode", "general")
    lat, lng = data.get("lat"), data.get("lng")
    if not message:
        return jsonify({"error": "Message is required."}), 400

    # --- Pet mode: routed to a separate species-appropriate knowledge base
    # rather than the human intent library. ---
    if mode == "pet":
        result = pet_chatbot_reply(message)
        reply = result["reply"]
        if result["intent_id"] == "pet_vet" and lat is not None and lng is not None:
            try:
                rows = [dict(r) for r in g.db.execute("SELECT * FROM vet_clinics").fetchall()]
                nearest = rank_nearby(rows, float(lat), float(lng), limit=1)
                if nearest:
                    v = nearest[0]
                    reply = (f"The nearest vet clinic to you is {v['name']}, about {v['distance_km']} km away "
                             f"(~{v['eta_min']} min). {'Open 24-hour emergency care.' if v['emergency_24h'] else 'Regular hours - call ahead if it is after hours.'} "
                             f"Call {v['phone']} or open the Live Map for directions.")
            except (TypeError, ValueError):
                pass

        user = current_user()
        g.db.execute(
            "INSERT INTO chat_logs (user_id, message, response) VALUES (?,?,?)",
            (user["id"] if user else None, f"[pet] {message}", reply),
        )
        g.db.commit()
        return jsonify({
            "reply": reply, "disclaimer": DISCLAIMER,
            "critical": result["critical"], "follow_ups": result["follow_ups"],
        })

    # --- Multi-turn context: remember the last few user messages in the
    # session so a short follow-up ("now he's not responding") can be
    # understood without repeating the whole situation. ---
    history = session.get("chat_history", [])
    result = chatbot_reply(message, lang=lang, history=history)
    history.append(message)
    session["chat_history"] = history[-5:]

    reply = result["reply"]

    # --- Location-aware answer: if they're asking about the nearest
    # hospital and shared their location, give the real answer instead of
    # a generic pointer to the dashboard. ---
    if result["intent_id"] == "hospital" and lat is not None and lng is not None:
        try:
            rows = [dict(r) for r in g.db.execute("SELECT * FROM hospitals").fetchall()]
            nearest = rank_nearby(rows, float(lat), float(lng), limit=1)
            if nearest:
                h = nearest[0]
                reply = (f"The nearest hospital to you is {h['name']}, about {h['distance_km']} km away "
                         f"(~{h['eta_min']} min). {h['beds_available']} beds currently available. "
                         f"Call {h['phone']} or open the Live Map for directions.")
        except (TypeError, ValueError):
            pass

    mode_notes = {
        "pregnancy": " Since you noted pregnancy: avoid lying flat on your back, mention it to responders immediately, and do not take aspirin unless a doctor has told you to.",
        "pediatric": " Since this involves a child: doses, techniques and thresholds differ from adults - tell responders the child's age and weight right away.",
    }
    if mode in mode_notes and lang == "en":
        reply += mode_notes[mode]

    user = current_user()
    g.db.execute(
        "INSERT INTO chat_logs (user_id, message, response) VALUES (?,?,?)",
        (user["id"] if user else None, message, reply),
    )
    g.db.commit()

    return jsonify({
        "reply": reply,
        "disclaimer": DISCLAIMER,
        "critical": result["critical"],
        "follow_ups": result["follow_ups"],
        "category": result["category"],
    })


@app.route("/api/chatbot/reset", methods=["POST"])
def api_chatbot_reset():
    """Clears the short-term conversation context (e.g. when starting a new topic)."""
    session.pop("chat_history", None)
    return jsonify({"status": "cleared"})


# ----------------------------------------------------------------------
# Guided Symptom Checker (decision-tree flow, no free typing needed)
# ----------------------------------------------------------------------
@app.route("/symptom-checker")
def symptom_checker_page():
    return render_template("symptom_checker.html")


@app.route("/api/symptom-checker/<node_id>")
def api_symptom_node(node_id):
    node = SYMPTOM_TREE.get(node_id)
    if not node:
        return jsonify({"error": "Unknown step."}), 404
    if "result" in node:
        return jsonify({"type": "result", **node["result"]})
    return jsonify({"type": "question", "question": node["question"],
                     "options": [{"label": lbl, "next": nxt} for lbl, nxt in node["options"]]})


# ----------------------------------------------------------------------
# Emergency Dashboard + nearby-services API
# ----------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


SERVICE_TABLES = {
    "hospitals": "hospitals",
    "ambulances": "ambulances",
    "police": "police_stations",
    "fire": "fire_stations",
    "pharmacies": "pharmacies",
    "bloodbanks": "blood_banks",
    "vets": "vet_clinics",
}


@app.route("/api/nearby")
def api_nearby():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng query params are required."}), 400

    category = request.args.get("category", "hospitals")
    table = SERVICE_TABLES.get(category)
    if not table:
        return jsonify({"error": "Unknown category."}), 400

    rows = [dict(r) for r in g.db.execute(f"SELECT * FROM {table}").fetchall()]
    nearby = rank_nearby(rows, lat, lng, limit=int(request.args.get("limit", 5)))
    return jsonify({"category": category, "results": nearby})


@app.route("/api/nearby/all")
def api_nearby_all():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng query params are required."}), 400

    out = {}
    for category, table in SERVICE_TABLES.items():
        rows = [dict(r) for r in g.db.execute(f"SELECT * FROM {table}").fetchall()]
        out[category] = rank_nearby(rows, lat, lng, limit=3)
    return jsonify(out)


# ----------------------------------------------------------------------
# Services listing pages (search + full list per category)
# ----------------------------------------------------------------------
@app.route("/services/<category>")
def service_category(category):
    table = SERVICE_TABLES.get(category)
    if not table:
        flash("Unknown service category.", "danger")
        return redirect(url_for("services"))

    q = request.args.get("q", "").strip()
    if q:
        rows = g.db.execute(
            f"SELECT * FROM {table} WHERE name LIKE ? OR address LIKE ?",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = g.db.execute(f"SELECT * FROM {table}").fetchall()

    # Attach average rating + review count for each row
    ratings = {r["service_id"]: (r["avg_r"], r["cnt"]) for r in g.db.execute(
        """SELECT service_id, ROUND(AVG(rating),1) avg_r, COUNT(*) cnt
           FROM reviews WHERE service_category = ? GROUP BY service_id""",
        (category,),
    ).fetchall()}
    rows_out = []
    for r in rows:
        d = dict(r)
        avg_r, cnt = ratings.get(r["id"], (None, 0))
        d["avg_rating"], d["review_count"] = avg_r, cnt
        rows_out.append(d)

    titles = {
        "hospitals": "Hospital Finder", "ambulances": "Ambulance Finder",
        "police": "Police Station Finder", "fire": "Fire Station Finder",
        "pharmacies": "Pharmacy Finder", "bloodbanks": "Blood Bank Finder",
        "vets": "Vet Clinic Finder",
    }
    return render_template("service_list.html", rows=rows_out, category=category,
                            title=titles.get(category, "Services"), q=q)


@app.route("/api/reviews", methods=["POST"])
def api_add_review():
    data = request.form
    category = data.get("service_category")
    try:
        service_id = int(data.get("service_id"))
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        flash("Invalid review submission.", "danger")
        return redirect(request.referrer or url_for("services"))

    if category not in SERVICE_TABLES or not (1 <= rating <= 5):
        flash("Invalid review submission.", "danger")
        return redirect(request.referrer or url_for("services"))

    user = current_user()
    g.db.execute(
        """INSERT INTO reviews (user_id, service_category, service_id, rating, comment)
           VALUES (?,?,?,?,?)""",
        (user["id"] if user else None, category, service_id, rating, data.get("comment", "").strip()[:300]),
    )
    g.db.commit()
    flash("Thanks for the review!", "success")
    return redirect(url_for("service_category", category=category))


# ----------------------------------------------------------------------
# Accident Detection
# ----------------------------------------------------------------------
def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


@app.route("/accident-detection", methods=["GET"])
def accident_page():
    return render_template("accident.html")


@app.route("/api/accident/analyze", methods=["POST"])
def api_accident_analyze():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded."}), 400
    file = request.files["image"]
    if file.filename == "" or not _allowed_file(file.filename):
        return jsonify({"error": "Please upload a PNG/JPG/WEBP image."}), 400

    filename = secure_filename(f"{datetime.now().timestamp()}_{file.filename}")
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)

    severity, score, notes = analyse_accident_image(path)

    # Recommend nearest hospital if location supplied
    nearest_hospital = None
    try:
        lat = float(request.form.get("lat"))
        lng = float(request.form.get("lng"))
        rows = [dict(r) for r in g.db.execute("SELECT * FROM hospitals").fetchall()]
        ranked = rank_nearby(rows, lat, lng, limit=1)
        nearest_hospital = ranked[0] if ranked else None
    except (TypeError, ValueError):
        lat = lng = None

    user = current_user()
    g.db.execute(
        """INSERT INTO reports (user_id, report_type, description, severity, latitude, longitude)
           VALUES (?, 'accident', ?, ?, ?, ?)""",
        (user["id"] if user else None, f"Uploaded image: {filename}", severity, lat, lng),
    )
    g.db.commit()

    return jsonify({
        "severity": severity,
        "confidence": score,
        "notes": notes,
        "image_url": url_for("static", filename=f"uploads/{filename}"),
        "nearest_hospital": nearest_hospital,
    })


# ----------------------------------------------------------------------
# Emergency Contacts + SOS
# ----------------------------------------------------------------------
@app.route("/contacts", methods=["GET"])
@login_required
def contacts_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC, id DESC", (user["id"],)
    ).fetchall()
    return render_template("contacts.html", rows=rows)


@app.route("/api/contacts", methods=["POST"])
@login_required
def api_add_contact():
    user = current_user()
    data = request.form
    name, phone = data.get("name", "").strip(), data.get("phone", "").strip()
    if not name or not phone:
        flash("Name and phone are required.", "danger")
        return redirect(url_for("contacts_page"))
    try:
        priority = int(data.get("priority", 1))
    except (TypeError, ValueError):
        priority = 1

    g.db.execute(
        "INSERT INTO emergency_contacts (user_id, name, phone, relationship, email, priority) VALUES (?,?,?,?,?,?)",
        (user["id"], name, phone, data.get("relationship", ""), data.get("email", ""), priority),
    )
    g.db.commit()
    flash("Emergency contact added.", "success")
    return redirect(url_for("contacts_page"))


@app.route("/api/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
def api_delete_contact(contact_id):
    user = current_user()
    g.db.execute(
        "DELETE FROM emergency_contacts WHERE id = ? AND user_id = ?", (contact_id, user["id"])
    )
    g.db.commit()
    flash("Contact removed.", "info")
    return redirect(url_for("contacts_page"))


@app.route("/api/sos", methods=["POST"])
def api_sos():
    """Simulates dispatching SMS/email alerts to all saved emergency contacts,
    in priority order, and opens a lightweight group-chat room for coordination."""
    user = current_user()
    data = request.get_json(silent=True) or {}
    lat, lng = data.get("lat"), data.get("lng")

    contacts = []
    if user:
        contacts = g.db.execute(
            "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC, id ASC", (user["id"],)
        ).fetchall()

    cur = g.db.execute(
        """INSERT INTO reports (user_id, report_type, description, severity, latitude, longitude)
           VALUES (?, 'sos', 'SOS button triggered', 'High', ?, ?)""",
        (user["id"] if user else None, lat, lng),
    )
    report_id = cur.lastrowid

    room_token = uuid.uuid4().hex[:10]
    g.db.execute(
        "INSERT INTO sos_sessions (user_id, report_id, room_token) VALUES (?,?,?)",
        (user["id"] if user else None, report_id, room_token),
    )
    g.db.commit()

    # --- SIMULATED notification dispatch (no real SMS/email gateway wired up) ---
    # Contacts are listed in priority order - contact #1 is who a real cascade would try first.
    notified = [{"name": c["name"], "phone": c["phone"], "channel": "SMS+Email", "priority": c["priority"]}
                for c in contacts]

    maps_link = f"https://maps.google.com/?q={lat},{lng}" if lat and lng else None

    return jsonify({
        "status": "dispatched",
        "notified_contacts": notified,
        "maps_link": maps_link,
        "chat_room": room_token,
        "chat_url": url_for("sos_chat_page", token=room_token, _external=True),
        "message": ("SOS alert simulated successfully." if notified else
                    "SOS logged. Add emergency contacts in your profile so real alerts have someone to reach."),
    })


@app.route("/api/im-safe", methods=["POST"])
@login_required
def api_im_safe():
    """One-tap broadcast to every saved contact that you're okay - useful after a
    wider event (storm, quake, etc.) so people don't have to call individually."""
    user = current_user()
    contacts = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC", (user["id"],)
    ).fetchall()

    notified = [{"name": c["name"], "phone": c["phone"], "channel": "SMS+Email"} for c in contacts]

    return jsonify({
        "status": "dispatched",
        "notified_contacts": notified,
        "message": (f"'I'm safe' sent to {len(notified)} contact(s)." if notified else
                    "No saved contacts yet - add some in your profile so this reaches someone."),
    })


# ----------------------------------------------------------------------
# SOS Group Chat - lightweight coordination room, no login required to join
# ----------------------------------------------------------------------
@app.route("/sos-chat/<token>")
def sos_chat_page(token):
    session_row = g.db.execute("SELECT * FROM sos_sessions WHERE room_token = ?", (token,)).fetchone()
    if not session_row:
        flash("This SOS chat room was not found.", "danger")
        return redirect(url_for("index"))
    return render_template("sos_chat.html", room_token=token)


@app.route("/api/sos-chat/<token>/messages", methods=["GET"])
def api_sos_chat_get(token):
    session_row = g.db.execute("SELECT * FROM sos_sessions WHERE room_token = ?", (token,)).fetchone()
    if not session_row:
        return jsonify({"error": "Room not found."}), 404
    msgs = g.db.execute(
        "SELECT * FROM sos_chat_messages WHERE session_id = ? ORDER BY id ASC", (session_row["id"],)
    ).fetchall()
    return jsonify([dict(m) for m in msgs])


@app.route("/api/sos-chat/<token>/messages", methods=["POST"])
def api_sos_chat_post(token):
    session_row = g.db.execute("SELECT * FROM sos_sessions WHERE room_token = ?", (token,)).fetchone()
    if not session_row:
        return jsonify({"error": "Room not found."}), 404
    data = request.get_json(silent=True) or {}
    sender = (data.get("sender_name") or "Anonymous").strip()[:40]
    message = (data.get("message") or "").strip()[:500]
    if not message:
        return jsonify({"error": "Message is required."}), 400
    g.db.execute(
        "INSERT INTO sos_chat_messages (session_id, sender_name, message) VALUES (?,?,?)",
        (session_row["id"], sender, message),
    )
    g.db.commit()
    return jsonify({"status": "sent"})


# ----------------------------------------------------------------------
# Danger-Zone Check-in Timer
# ----------------------------------------------------------------------
@app.route("/checkin-timer")
@login_required
def checkin_timer_page():
    user = current_user()
    active = g.db.execute(
        "SELECT * FROM checkin_timers WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (user["id"],),
    ).fetchone()
    return render_template("checkin_timer.html", active=active)


@app.route("/api/checkin/start", methods=["POST"])
@login_required
def api_checkin_start():
    user = current_user()
    data = request.get_json(silent=True) or {}
    minutes = int(data.get("minutes", 30))
    label = (data.get("label") or "Check-in").strip()[:80]

    g.db.execute("UPDATE checkin_timers SET status = 'cancelled' WHERE user_id = ? AND status = 'active'",
                 (user["id"],))
    deadline = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    cur = g.db.execute(
        "INSERT INTO checkin_timers (user_id, label, deadline) VALUES (?,?,?)",
        (user["id"], label, deadline),
    )
    g.db.commit()
    return jsonify({"id": cur.lastrowid, "deadline": deadline})


@app.route("/api/checkin/<int:timer_id>/checkin", methods=["POST"])
@login_required
def api_checkin_confirm(timer_id):
    user = current_user()
    g.db.execute(
        "UPDATE checkin_timers SET status = 'checked_in' WHERE id = ? AND user_id = ?",
        (timer_id, user["id"]),
    )
    g.db.commit()
    return jsonify({"status": "checked_in"})


@app.route("/api/checkin/<int:timer_id>/missed", methods=["POST"])
@login_required
def api_checkin_missed(timer_id):
    """Called by the client if the deadline passes with no check-in - triggers
    the same alert flow as SOS. Only works while the browser tab stays open,
    since there's no server-side scheduler in this build."""
    user = current_user()
    timer = g.db.execute(
        "SELECT * FROM checkin_timers WHERE id = ? AND user_id = ? AND status = 'active'",
        (timer_id, user["id"]),
    ).fetchone()
    if not timer:
        return jsonify({"error": "No active timer."}), 404

    g.db.execute("UPDATE checkin_timers SET status = 'alerted' WHERE id = ?", (timer_id,))
    contacts = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC", (user["id"],)
    ).fetchall()
    g.db.execute(
        """INSERT INTO reports (user_id, report_type, description, severity)
           VALUES (?, 'checkin_missed', ?, 'High')""",
        (user["id"], f"Missed check-in: {timer['label']}"),
    )
    g.db.commit()
    notified = [{"name": c["name"], "phone": c["phone"]} for c in contacts]
    return jsonify({"status": "alerted", "notified_contacts": notified})


# ----------------------------------------------------------------------
# Live Map
# ----------------------------------------------------------------------
@app.route("/map")
@login_required
def live_map():
    return render_template("map.html")


@app.route("/api/favorites", methods=["GET"])
@login_required
def api_list_favorites():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM favorite_places WHERE user_id = ? ORDER BY id DESC", (user["id"],)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/favorites", methods=["POST"])
@login_required
def api_add_favorite():
    user = current_user()
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()[:60]
    lat, lng = data.get("lat"), data.get("lng")
    if not label or lat is None or lng is None:
        return jsonify({"error": "label, lat and lng are required."}), 400
    cur = g.db.execute(
        "INSERT INTO favorite_places (user_id, label, latitude, longitude) VALUES (?,?,?,?)",
        (user["id"], label, lat, lng),
    )
    g.db.commit()
    return jsonify({"id": cur.lastrowid, "label": label, "latitude": lat, "longitude": lng})


@app.route("/api/favorites/<int:fav_id>/delete", methods=["POST"])
@login_required
def api_delete_favorite(fav_id):
    user = current_user()
    g.db.execute("DELETE FROM favorite_places WHERE id = ? AND user_id = ?", (fav_id, user["id"]))
    g.db.commit()
    return jsonify({"status": "deleted"})


# ----------------------------------------------------------------------
# Community Alerts (crowd-sourced incident pins)
# ----------------------------------------------------------------------
INCIDENT_TYPES = ["accident", "fire", "flood", "road_block", "crime", "other"]


@app.route("/community-alerts")
def community_alerts_page():
    return render_template("community_alerts.html", incident_types=INCIDENT_TYPES)


@app.route("/api/community-reports", methods=["GET"])
def api_list_community_reports():
    since = (datetime.now() - timedelta(hours=48)).isoformat()
    rows = g.db.execute(
        """SELECT * FROM community_reports WHERE created_at >= ? AND flags < 3
           ORDER BY created_at DESC LIMIT 100""",
        (since,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/community-reports", methods=["POST"])
def api_add_community_report():
    data = request.get_json(silent=True) or {}
    incident_type = data.get("incident_type")
    lat, lng = data.get("lat"), data.get("lng")
    if incident_type not in INCIDENT_TYPES or lat is None or lng is None:
        return jsonify({"error": "incident_type, lat and lng are required."}), 400

    user = current_user()
    cur = g.db.execute(
        """INSERT INTO community_reports (user_id, incident_type, description, latitude, longitude)
           VALUES (?,?,?,?,?)""",
        (user["id"] if user else None, incident_type, (data.get("description") or "").strip()[:300], lat, lng),
    )
    g.db.commit()
    return jsonify({"id": cur.lastrowid, "status": "reported"})


@app.route("/api/community-reports/<int:report_id>/upvote", methods=["POST"])
def api_upvote_community_report(report_id):
    g.db.execute("UPDATE community_reports SET upvotes = upvotes + 1 WHERE id = ?", (report_id,))
    g.db.commit()
    row = g.db.execute("SELECT upvotes FROM community_reports WHERE id = ?", (report_id,)).fetchone()
    return jsonify({"upvotes": row["upvotes"] if row else None})


@app.route("/api/community-reports/<int:report_id>/flag", methods=["POST"])
def api_flag_community_report(report_id):
    """Lets other users mark a pin as resolved/inaccurate; once flags pass a
    threshold the pin is filtered out of the public list automatically."""
    g.db.execute("UPDATE community_reports SET flags = flags + 1 WHERE id = ?", (report_id,))
    g.db.commit()
    row = g.db.execute("SELECT flags FROM community_reports WHERE id = ?", (report_id,)).fetchone()
    return jsonify({"flags": row["flags"] if row else None})


# ----------------------------------------------------------------------
# Live Location Tracker
# ----------------------------------------------------------------------
DURATION_CHOICES = {"30m": 30, "1h": 60, "4h": 240, "12h": 720}


@app.route("/location-tracker")
@login_required
def location_tracker_page():
    user = current_user()
    active_share = g.db.execute(
        """SELECT * FROM location_shares
           WHERE user_id = ? AND is_active = 1 AND expires_at > ?
           ORDER BY id DESC LIMIT 1""",
        (user["id"], datetime.now().isoformat()),
    ).fetchone()
    return render_template("location_tracker.html", active_share=active_share)


@app.route("/api/location/start", methods=["POST"])
@login_required
def api_location_start():
    user = current_user()
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "Live location").strip()[:80]
    duration_key = data.get("duration", "1h")
    minutes = DURATION_CHOICES.get(duration_key, 60)

    # Deactivate any previous active shares for this user first
    g.db.execute(
        "UPDATE location_shares SET is_active = 0 WHERE user_id = ? AND is_active = 1",
        (user["id"],),
    )

    token = uuid.uuid4().hex[:12]
    expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    cur = g.db.execute(
        """INSERT INTO location_shares (user_id, share_token, label, is_active, expires_at)
           VALUES (?,?,?,1,?)""",
        (user["id"], token, label, expires_at),
    )
    g.db.commit()

    return jsonify({
        "share_id": cur.lastrowid,
        "token": token,
        "expires_at": expires_at,
        "track_url": url_for("track_public", token=token, _external=True),
    })


@app.route("/api/location/ping", methods=["POST"])
@login_required
def api_location_ping():
    user = current_user()
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    lat, lng = data.get("lat"), data.get("lng")

    share = g.db.execute(
        "SELECT * FROM location_shares WHERE share_token = ? AND user_id = ? AND is_active = 1",
        (token, user["id"]),
    ).fetchone()
    if not share:
        return jsonify({"error": "No active share for this token."}), 404
    if share["expires_at"] < datetime.now().isoformat():
        g.db.execute("UPDATE location_shares SET is_active = 0 WHERE id = ?", (share["id"],))
        g.db.commit()
        return jsonify({"error": "This tracking session has expired."}), 410

    g.db.execute(
        """INSERT INTO location_pings (share_id, latitude, longitude, accuracy, speed)
           VALUES (?,?,?,?,?)""",
        (share["id"], lat, lng, data.get("accuracy"), data.get("speed")),
    )
    g.db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/location/stop", methods=["POST"])
@login_required
def api_location_stop():
    user = current_user()
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    g.db.execute(
        "UPDATE location_shares SET is_active = 0 WHERE share_token = ? AND user_id = ?",
        (token, user["id"]),
    )
    g.db.commit()
    return jsonify({"status": "stopped"})


@app.route("/track/<token>")
def track_public(token):
    """Public, no-login page for family/contacts to view a live-shared location."""
    share = g.db.execute(
        "SELECT ls.*, u.full_name FROM location_shares ls JOIN users u ON u.id = ls.user_id WHERE share_token = ?",
        (token,),
    ).fetchone()
    if not share:
        flash("This tracking link is invalid.", "danger")
        return redirect(url_for("index"))
    return render_template("track_public.html", share=share)


@app.route("/api/track/<token>/latest")
def api_track_latest(token):
    share = g.db.execute("SELECT * FROM location_shares WHERE share_token = ?", (token,)).fetchone()
    if not share:
        return jsonify({"error": "Invalid tracking link."}), 404

    is_expired = share["expires_at"] < datetime.now().isoformat()
    is_active = bool(share["is_active"]) and not is_expired

    pings = g.db.execute(
        "SELECT * FROM location_pings WHERE share_id = ? ORDER BY id ASC", (share["id"],)
    ).fetchall()
    trail = [{"lat": p["latitude"], "lng": p["longitude"], "recorded_at": p["recorded_at"]} for p in pings]

    return jsonify({
        "active": is_active,
        "expired": is_expired,
        "label": share["label"],
        "expires_at": share["expires_at"],
        "trail": trail,
        "latest": trail[-1] if trail else None,
    })


# ----------------------------------------------------------------------
# Contact form
# ----------------------------------------------------------------------
@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or "@" not in email or not message:
            flash("Please fill in a valid name, email and message.", "danger")
            return redirect(url_for("contact"))

        user = current_user()
        g.db.execute(
            "INSERT INTO feedback (user_id, name, email, message, rating) VALUES (?,?,?,?,5)",
            (user["id"] if user else None, name, email, message),
        )
        g.db.commit()
        flash("Thanks for reaching out - our team will respond within 24 hours.", "success")
        return redirect(url_for("contact"))

    return render_template("contact.html")


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        sec_q = request.form.get("security_question", "").strip()
        sec_a = request.form.get("security_answer", "").strip()

        errors = []
        if not full_name or len(full_name) < 2:
            errors.append("Please enter your full name.")
        if "@" not in email or "." not in email:
            errors.append("Please enter a valid email address.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if g.db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("An account with this email already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=request.form)

        g.db.execute(
            """INSERT INTO users (full_name, email, phone, password_hash, role,
                                   security_question, security_answer_hash, last_active_at)
               VALUES (?,?,?,?, 'user', ?, ?, ?)""",
            (full_name, email, phone, generate_password_hash(password),
             sec_q, generate_password_hash(sec_a.lower()) if sec_a else None,
             datetime.now().isoformat()),
        )
        g.db.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = g.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]

            # --- Anomaly check-in: if this user opted in and there's an
            # unusually long gap since their last visit, flag it so the
            # dashboard can show a gentle "everything okay?" banner. ---
            if user["anomaly_checkin_enabled"] and user["last_active_at"]:
                try:
                    gap_days = (datetime.now() - datetime.fromisoformat(user["last_active_at"])).days
                    if gap_days >= 14:
                        session["anomaly_gap_days"] = gap_days
                except ValueError:
                    pass

            g.db.execute("UPDATE users SET last_active_at = ? WHERE id = ?",
                         (datetime.now().isoformat(), user["id"]))
            g.db.commit()

            flash(f"Welcome back, {user['full_name']}!", "success")
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/api/anomaly-checkin/dismiss", methods=["POST"])
def api_dismiss_anomaly_checkin():
    session.pop("anomaly_gap_days", None)
    return jsonify({"status": "dismissed"})


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    stage = request.form.get("stage", "find")

    if request.method == "POST" and stage == "find":
        email = request.form.get("email", "").strip().lower()
        user = g.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not user["security_question"]:
            flash("No account with a recovery question found for that email.", "danger")
            return render_template("forgot_password.html", stage="find")
        return render_template("forgot_password.html", stage="answer", email=email,
                                question=user["security_question"])

    if request.method == "POST" and stage == "answer":
        email = request.form.get("email", "").strip().lower()
        answer = request.form.get("security_answer", "").strip().lower()
        new_password = request.form.get("new_password", "")
        user = g.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not user or not check_password_hash(user["security_answer_hash"], answer):
            flash("That answer didn't match our records.", "danger")
            return render_template("forgot_password.html", stage="answer", email=email,
                                    question=user["security_question"] if user else "")

        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return render_template("forgot_password.html", stage="answer", email=email,
                                    question=user["security_question"])

        g.db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                      (generate_password_hash(new_password), user["id"]))
        g.db.commit()
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html", stage="find")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        blood_group = request.form.get("blood_group", "").strip()
        allergies = request.form.get("allergies", "").strip()
        medications = request.form.get("medications", "").strip()
        conditions = request.form.get("conditions", "").strip()
        insurance_provider = request.form.get("insurance_provider", "").strip()
        insurance_policy_number = request.form.get("insurance_policy_number", "").strip()
        last_blood_donation = request.form.get("last_blood_donation", "").strip()
        anomaly_checkin_enabled = 1 if request.form.get("anomaly_checkin_enabled") else 0
        g.db.execute(
            """UPDATE users SET full_name=?, phone=?, blood_group=?, allergies=?,
                                  medications=?, conditions=?, insurance_provider=?,
                                  insurance_policy_number=?, last_blood_donation=?,
                                  anomaly_checkin_enabled=? WHERE id=?""",
            (full_name, phone, blood_group, allergies, medications, conditions,
             insurance_provider, insurance_policy_number, last_blood_donation,
             anomaly_checkin_enabled, user["id"]),
        )
        g.db.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    # Ensure every user has a medical card token to share/print
    if not user["medical_card_token"]:
        token = secrets.token_hex(6)
        g.db.execute("UPDATE users SET medical_card_token = ? WHERE id = ?", (token, user["id"]))
        g.db.commit()
        user = current_user()

    reports = g.db.execute(
        "SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user["id"],)
    ).fetchall()
    contacts_count = g.db.execute(
        "SELECT COUNT(*) c FROM emergency_contacts WHERE user_id = ?", (user["id"],)
    ).fetchone()["c"]

    # --- Personal safety snapshot (self-awareness, not a judgement score) ---
    since_90d = (datetime.now() - timedelta(days=90)).isoformat()
    sos_count = g.db.execute(
        "SELECT COUNT(*) c FROM reports WHERE user_id = ? AND report_type = 'sos' AND created_at >= ?",
        (user["id"], since_90d),
    ).fetchone()["c"]
    profile_complete = sum([
        bool(user["blood_group"]), bool(user["allergies"] or user["conditions"]),
        contacts_count > 0, bool(user["phone"]),
    ])
    safety_score = min(100, profile_complete * 20 + (10 if contacts_count >= 2 else 0) + 10)

    # --- Blood donation eligibility (~90 days between whole-blood donations) ---
    donation_status = None
    if user["last_blood_donation"]:
        try:
            last_date = datetime.fromisoformat(user["last_blood_donation"])
            next_eligible = last_date + timedelta(days=90)
            donation_status = {
                "next_eligible": next_eligible.date().isoformat(),
                "eligible_now": datetime.now() >= next_eligible,
                "days_left": max(0, (next_eligible - datetime.now()).days),
            }
        except ValueError:
            donation_status = None

    return render_template("profile.html", user=user, reports=reports,
                            contacts_count=contacts_count, safety_score=safety_score,
                            sos_count=sos_count, donation_status=donation_status)


@app.route("/api/accessibility/high-contrast", methods=["POST"])
@login_required
def api_toggle_high_contrast():
    user = current_user()
    data = request.get_json(silent=True) or {}
    enabled = 1 if data.get("enabled") else 0
    g.db.execute("UPDATE users SET high_contrast = ? WHERE id = ?", (enabled, user["id"]))
    g.db.commit()
    return jsonify({"high_contrast": bool(enabled)})


# ----------------------------------------------------------------------
# Emergency Medical Card (printable + QR-shareable, public read-only view)
# ----------------------------------------------------------------------
@app.route("/emergency-card/<token>")
def emergency_card(token):
    user = g.db.execute("SELECT * FROM users WHERE medical_card_token = ?", (token,)).fetchone()
    if not user:
        flash("Emergency card not found.", "danger")
        return redirect(url_for("index"))
    contacts = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC LIMIT 3", (user["id"],)
    ).fetchall()
    vaccinations = g.db.execute(
        "SELECT * FROM vaccinations WHERE user_id = ? ORDER BY date_administered DESC", (user["id"],)
    ).fetchall()
    return render_template("emergency_card.html", card_user=user, contacts=contacts, vaccinations=vaccinations)


@app.route("/fridge-magnet/<token>")
def fridge_magnet(token):
    """A compact, high-contrast layout designed for printing and sticking on
    a fridge/door - distinct from the wallet-sized emergency card."""
    user = g.db.execute("SELECT * FROM users WHERE medical_card_token = ?", (token,)).fetchone()
    if not user:
        flash("Card not found.", "danger")
        return redirect(url_for("index"))
    contacts = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC LIMIT 3", (user["id"],)
    ).fetchall()
    return render_template("fridge_magnet.html", card_user=user, contacts=contacts)


# ----------------------------------------------------------------------
# Vaccination / immunization records
# ----------------------------------------------------------------------
@app.route("/vaccinations")
@login_required
def vaccinations_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM vaccinations WHERE user_id = ? ORDER BY date_administered DESC", (user["id"],)
    ).fetchall()
    return render_template("vaccinations.html", rows=rows)


@app.route("/api/vaccinations", methods=["POST"])
@login_required
def api_add_vaccination():
    user = current_user()
    name = request.form.get("vaccine_name", "").strip()
    if not name:
        flash("Vaccine name is required.", "danger")
        return redirect(url_for("vaccinations_page"))
    g.db.execute(
        "INSERT INTO vaccinations (user_id, vaccine_name, date_administered, notes) VALUES (?,?,?,?)",
        (user["id"], name, request.form.get("date_administered", ""), request.form.get("notes", "")),
    )
    g.db.commit()
    flash("Vaccination record added.", "success")
    return redirect(url_for("vaccinations_page"))


@app.route("/api/vaccinations/<int:record_id>/delete", methods=["POST"])
@login_required
def api_delete_vaccination(record_id):
    user = current_user()
    g.db.execute("DELETE FROM vaccinations WHERE id = ? AND user_id = ?", (record_id, user["id"]))
    g.db.commit()
    flash("Record removed.", "info")
    return redirect(url_for("vaccinations_page"))


# ----------------------------------------------------------------------
# Emergency Kit Inventory Tracker
# ----------------------------------------------------------------------
@app.route("/kit-inventory")
@login_required
def kit_inventory_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM kit_items WHERE user_id = ? ORDER BY (expiry_date IS NULL), expiry_date ASC", (user["id"],)
    ).fetchall()
    today = datetime.now().date().isoformat()
    soon = (datetime.now() + timedelta(days=30)).date().isoformat()
    return render_template("kit_inventory.html", rows=rows, today=today, soon=soon)


@app.route("/api/kit-items", methods=["POST"])
@login_required
def api_add_kit_item():
    user = current_user()
    name = request.form.get("item_name", "").strip()
    if not name:
        flash("Item name is required.", "danger")
        return redirect(url_for("kit_inventory_page"))
    try:
        qty = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    g.db.execute(
        "INSERT INTO kit_items (user_id, item_name, quantity, expiry_date, notes) VALUES (?,?,?,?,?)",
        (user["id"], name, qty, request.form.get("expiry_date", ""), request.form.get("notes", "")),
    )
    g.db.commit()
    flash("Item added to your kit.", "success")
    return redirect(url_for("kit_inventory_page"))


@app.route("/api/kit-items/<int:item_id>/delete", methods=["POST"])
@login_required
def api_delete_kit_item(item_id):
    user = current_user()
    g.db.execute("DELETE FROM kit_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    g.db.commit()
    flash("Item removed.", "info")
    return redirect(url_for("kit_inventory_page"))


# ----------------------------------------------------------------------
# Medication Reminders
# ----------------------------------------------------------------------
@app.route("/medication-reminders")
@login_required
def medication_reminders_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM medication_reminders WHERE user_id = ? ORDER BY reminder_time ASC", (user["id"],)
    ).fetchall()
    return render_template("medication_reminders.html", rows=rows)


@app.route("/api/medication-reminders", methods=["POST"])
@login_required
def api_add_medication_reminder():
    user = current_user()
    name = request.form.get("medication_name", "").strip()
    reminder_time = request.form.get("reminder_time", "").strip()
    if not name or not reminder_time:
        flash("Medication name and time are required.", "danger")
        return redirect(url_for("medication_reminders_page"))
    g.db.execute(
        "INSERT INTO medication_reminders (user_id, medication_name, reminder_time, notes) VALUES (?,?,?,?)",
        (user["id"], name, reminder_time, request.form.get("notes", "")),
    )
    g.db.commit()
    flash("Reminder added.", "success")
    return redirect(url_for("medication_reminders_page"))


@app.route("/api/medication-reminders/<int:reminder_id>/delete", methods=["POST"])
@login_required
def api_delete_medication_reminder(reminder_id):
    user = current_user()
    g.db.execute("DELETE FROM medication_reminders WHERE id = ? AND user_id = ?", (reminder_id, user["id"]))
    g.db.commit()
    flash("Reminder removed.", "info")
    return redirect(url_for("medication_reminders_page"))


@app.route("/api/medication-reminders/active")
@login_required
def api_active_medication_reminders():
    """Used by the client to poll and fire browser notifications at the right time."""
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM medication_reminders WHERE user_id = ? AND is_active = 1", (user["id"],)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ----------------------------------------------------------------------
# Human & financial support after an incident
# We never process payments ourselves (no gateway wired up here) - this
# creates a shareable page with a plain-language summary and a link the
# user controls (their own GoFundMe/PayPal.me/etc).
# ----------------------------------------------------------------------
@app.route("/support-requests")
@login_required
def support_requests_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM support_requests WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    recent_reports = g.db.execute(
        "SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user["id"],)
    ).fetchall()
    return render_template("support_requests.html", rows=rows, recent_reports=recent_reports)


@app.route("/api/support-requests", methods=["POST"])
@login_required
def api_add_support_request():
    user = current_user()
    title = request.form.get("title", "").strip()
    if not title:
        flash("A title is required.", "danger")
        return redirect(url_for("support_requests_page"))
    report_id = request.form.get("report_id") or None
    token = secrets.token_hex(8)
    g.db.execute(
        """INSERT INTO support_requests (user_id, report_id, title, description, external_link, share_token)
           VALUES (?,?,?,?,?,?)""",
        (user["id"], report_id, title, request.form.get("description", ""),
         request.form.get("external_link", ""), token),
    )
    g.db.commit()
    flash("Support request page created - share the link with anyone who wants to help.", "success")
    return redirect(url_for("support_requests_page"))


@app.route("/api/support-requests/<int:request_id>/delete", methods=["POST"])
@login_required
def api_delete_support_request(request_id):
    user = current_user()
    g.db.execute("DELETE FROM support_requests WHERE id = ? AND user_id = ?", (request_id, user["id"]))
    g.db.commit()
    flash("Support request removed.", "info")
    return redirect(url_for("support_requests_page"))


@app.route("/support/<token>")
def support_public(token):
    req = g.db.execute("SELECT * FROM support_requests WHERE share_token = ?", (token,)).fetchone()
    if not req:
        flash("This support page was not found.", "danger")
        return redirect(url_for("index"))
    owner = g.db.execute("SELECT full_name FROM users WHERE id = ?", (req["user_id"],)).fetchone()
    linked_report = None
    if req["report_id"]:
        linked_report = g.db.execute("SELECT * FROM reports WHERE id = ?", (req["report_id"],)).fetchone()
    return render_template("support_public.html", req=req, owner=owner, linked_report=linked_report)


# ----------------------------------------------------------------------
# Guardian / dependent relationships
# A guardian manages a dependent's profile directly - no separate login
# needed for the dependent (useful for children, elderly relatives, etc).
# ----------------------------------------------------------------------
@app.route("/dependents")
@login_required
def dependents_page():
    user = current_user()
    rows = g.db.execute(
        "SELECT * FROM dependents WHERE guardian_user_id = ? ORDER BY full_name ASC", (user["id"],)
    ).fetchall()
    return render_template("dependents.html", rows=rows)


@app.route("/api/dependents", methods=["POST"])
@login_required
def api_add_dependent():
    user = current_user()
    name = request.form.get("full_name", "").strip()
    if not name:
        flash("Name is required.", "danger")
        return redirect(url_for("dependents_page"))
    token = secrets.token_hex(6)
    g.db.execute(
        """INSERT INTO dependents (guardian_user_id, full_name, relationship, date_of_birth,
                                    blood_group, allergies, medications, conditions, notes, card_token)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user["id"], name, request.form.get("relationship", ""), request.form.get("date_of_birth", ""),
         request.form.get("blood_group", ""), request.form.get("allergies", ""),
         request.form.get("medications", ""), request.form.get("conditions", ""),
         request.form.get("notes", ""), token),
    )
    g.db.commit()
    flash(f"{name} has been added as a dependent.", "success")
    return redirect(url_for("dependents_page"))


@app.route("/api/dependents/<int:dependent_id>/delete", methods=["POST"])
@login_required
def api_delete_dependent(dependent_id):
    user = current_user()
    g.db.execute("DELETE FROM dependents WHERE id = ? AND guardian_user_id = ?", (dependent_id, user["id"]))
    g.db.commit()
    flash("Dependent profile removed.", "info")
    return redirect(url_for("dependents_page"))


@app.route("/dependent-card/<token>")
def dependent_card(token):
    dep = g.db.execute("SELECT * FROM dependents WHERE card_token = ?", (token,)).fetchone()
    if not dep:
        flash("Card not found.", "danger")
        return redirect(url_for("index"))
    guardian = g.db.execute("SELECT full_name, phone FROM users WHERE id = ?", (dep["guardian_user_id"],)).fetchone()
    return render_template("dependent_card.html", dep=dep, guardian=guardian)


@app.route("/api/dependents/<int:dependent_id>/sos", methods=["POST"])
@login_required
def api_dependent_sos(dependent_id):
    """Guardian triggers SOS on behalf of a dependent who has no login of their own."""
    user = current_user()
    dep = g.db.execute(
        "SELECT * FROM dependents WHERE id = ? AND guardian_user_id = ?", (dependent_id, user["id"])
    ).fetchone()
    if not dep:
        return jsonify({"error": "Dependent not found."}), 404

    data = request.get_json(silent=True) or {}
    lat, lng = data.get("lat"), data.get("lng")
    contacts = g.db.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority ASC", (user["id"],)
    ).fetchall()

    g.db.execute(
        """INSERT INTO reports (user_id, report_type, description, severity, latitude, longitude)
           VALUES (?, 'sos', ?, 'High', ?, ?)""",
        (user["id"], f"SOS triggered for dependent: {dep['full_name']}", lat, lng),
    )
    g.db.commit()

    notified = [{"name": c["name"], "phone": c["phone"]} for c in contacts]
    maps_link = f"https://maps.google.com/?q={lat},{lng}" if lat and lng else None
    return jsonify({
        "status": "dispatched", "notified_contacts": notified, "maps_link": maps_link,
        "message": f"SOS for {dep['full_name']} simulated - {len(notified)} contact(s) notified.",
    })


# ----------------------------------------------------------------------
# Pets - profiles, vaccinations, printable card
# ----------------------------------------------------------------------
@app.route("/pets")
@login_required
def pets_page():
    user = current_user()
    rows = g.db.execute("SELECT * FROM pets WHERE user_id = ? ORDER BY name ASC", (user["id"],)).fetchall()
    return render_template("pets.html", rows=rows)


@app.route("/api/pets", methods=["POST"])
@login_required
def api_add_pet():
    user = current_user()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Pet name is required.", "danger")
        return redirect(url_for("pets_page"))
    try:
        weight = float(request.form.get("weight_kg")) if request.form.get("weight_kg") else None
    except ValueError:
        weight = None
    token = secrets.token_hex(6)
    g.db.execute(
        """INSERT INTO pets (user_id, name, species, breed, date_of_birth, weight_kg,
                              microchip_number, allergies, medications, notes, card_token)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user["id"], name, request.form.get("species", ""), request.form.get("breed", ""),
         request.form.get("date_of_birth", ""), weight, request.form.get("microchip_number", ""),
         request.form.get("allergies", ""), request.form.get("medications", ""),
         request.form.get("notes", ""), token),
    )
    g.db.commit()
    flash(f"{name} has been added.", "success")
    return redirect(url_for("pets_page"))


@app.route("/api/pets/<int:pet_id>/delete", methods=["POST"])
@login_required
def api_delete_pet(pet_id):
    user = current_user()
    g.db.execute("DELETE FROM pets WHERE id = ? AND user_id = ?", (pet_id, user["id"]))
    g.db.commit()
    flash("Pet profile removed.", "info")
    return redirect(url_for("pets_page"))


@app.route("/pets/<int:pet_id>/vaccinations")
@login_required
def pet_vaccinations_page(pet_id):
    user = current_user()
    pet = g.db.execute("SELECT * FROM pets WHERE id = ? AND user_id = ?", (pet_id, user["id"])).fetchone()
    if not pet:
        flash("Pet not found.", "danger")
        return redirect(url_for("pets_page"))
    rows = g.db.execute(
        "SELECT * FROM pet_vaccinations WHERE pet_id = ? ORDER BY date_administered DESC", (pet_id,)
    ).fetchall()
    return render_template("pet_vaccinations.html", pet=pet, rows=rows)


@app.route("/api/pets/<int:pet_id>/vaccinations", methods=["POST"])
@login_required
def api_add_pet_vaccination(pet_id):
    user = current_user()
    pet = g.db.execute("SELECT * FROM pets WHERE id = ? AND user_id = ?", (pet_id, user["id"])).fetchone()
    if not pet:
        flash("Pet not found.", "danger")
        return redirect(url_for("pets_page"))
    name = request.form.get("vaccine_name", "").strip()
    if not name:
        flash("Vaccine name is required.", "danger")
        return redirect(url_for("pet_vaccinations_page", pet_id=pet_id))
    g.db.execute(
        "INSERT INTO pet_vaccinations (pet_id, vaccine_name, date_administered, notes) VALUES (?,?,?,?)",
        (pet_id, name, request.form.get("date_administered", ""), request.form.get("notes", "")),
    )
    g.db.commit()
    flash("Vaccination record added.", "success")
    return redirect(url_for("pet_vaccinations_page", pet_id=pet_id))


@app.route("/api/pet-vaccinations/<int:record_id>/delete", methods=["POST"])
@login_required
def api_delete_pet_vaccination(record_id):
    user = current_user()
    row = g.db.execute(
        """SELECT pv.id, pv.pet_id FROM pet_vaccinations pv
           JOIN pets p ON p.id = pv.pet_id WHERE pv.id = ? AND p.user_id = ?""",
        (record_id, user["id"]),
    ).fetchone()
    if row:
        g.db.execute("DELETE FROM pet_vaccinations WHERE id = ?", (record_id,))
        g.db.commit()
        flash("Record removed.", "info")
        return redirect(url_for("pet_vaccinations_page", pet_id=row["pet_id"]))
    return redirect(url_for("pets_page"))


@app.route("/pet-card/<token>")
def pet_card(token):
    pet = g.db.execute("SELECT * FROM pets WHERE card_token = ?", (token,)).fetchone()
    if not pet:
        flash("Pet card not found.", "danger")
        return redirect(url_for("index"))
    owner = g.db.execute("SELECT full_name, phone FROM users WHERE id = ?", (pet["user_id"],)).fetchone()
    vaccinations = g.db.execute(
        "SELECT * FROM pet_vaccinations WHERE pet_id = ? ORDER BY date_administered DESC", (pet["id"],)
    ).fetchall()
    return render_template("pet_card.html", pet=pet, owner=owner, vaccinations=vaccinations)


# ----------------------------------------------------------------------
# Weather alerts - free Open-Meteo API, no key required. We derive simple
# safety banners from current conditions since Open-Meteo's free tier
# doesn't include an official "alerts" feed.
# ----------------------------------------------------------------------
@app.route("/api/weather")
def api_weather():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng are required."}), 400

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lng, "current_weather": "true"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return jsonify({"error": "Weather service unavailable right now."}), 502

    cw = data.get("current_weather", {})
    temp_c = cw.get("temperature")
    windspeed = cw.get("windspeed")
    code = cw.get("weathercode")

    # WMO weather codes: https://open-meteo.com/en/docs
    severe_codes = {95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
                    65: "Heavy rain", 82: "Violent rain showers", 75: "Heavy snowfall", 86: "Heavy snow showers"}
    alerts = []
    if code in severe_codes:
        alerts.append({"level": "High", "message": f"{severe_codes[code]} in this area right now - avoid travel if possible."})
    if windspeed is not None and windspeed >= 50:
        alerts.append({"level": "Medium", "message": f"High winds ({windspeed} km/h) reported - secure loose objects."})
    if temp_c is not None and temp_c >= 42:
        alerts.append({"level": "Medium", "message": f"Extreme heat ({temp_c}°C) - stay hydrated, avoid strenuous activity outdoors."})
    if temp_c is not None and temp_c <= 2:
        alerts.append({"level": "Medium", "message": f"Near-freezing conditions ({temp_c}°C) - watch for icy roads."})

    return jsonify({"temperature": temp_c, "windspeed": windspeed, "weathercode": code, "alerts": alerts})


# ----------------------------------------------------------------------
# Export my data (transparency / portability)
# ----------------------------------------------------------------------
@app.route("/api/export-data")
@login_required
def api_export_data():
    user = current_user()
    uid = user["id"]

    def rows(table, clause="user_id = ?"):
        return [dict(r) for r in g.db.execute(f"SELECT * FROM {table} WHERE {clause}", (uid,)).fetchall()]

    payload = {
        "profile": dict(user),
        "emergency_contacts": rows("emergency_contacts"),
        "vaccinations": rows("vaccinations"),
        "reports": rows("reports"),
        "reviews": rows("reviews"),
        "chat_logs": rows("chat_logs"),
        "location_shares": rows("location_shares"),
        "checkin_timers": rows("checkin_timers"),
        "exported_at": datetime.now().isoformat(),
    }
    payload["profile"].pop("password_hash", None)
    payload["profile"].pop("security_answer_hash", None)

    resp = jsonify(payload)
    resp.headers["Content-Disposition"] = f"attachment; filename=rescueai_data_{uid}.json"
    return resp


# ----------------------------------------------------------------------
# Watch-sized companion view (SOS-only, minimal chrome)
# ----------------------------------------------------------------------
@app.route("/watch")
def watch_view():
    return render_template("watch.html")


# ----------------------------------------------------------------------
# PWA support - installable app + offline caching of key pages
# ----------------------------------------------------------------------
@app.route("/manifest.json")
def pwa_manifest():
    return jsonify({
        "name": "HyperAid - Emergency Response Assistant",
        "short_name": "HyperAid",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#F4F6FB",
        "theme_color": "#2452FF",
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.route("/service-worker.js")
def service_worker():
    resp = app.response_class(SERVICE_WORKER_JS, mimetype="application/javascript")
    return resp


@app.route("/offline")
def offline_page():
    return render_template("offline.html")


# ----------------------------------------------------------------------
# Admin panel
# ----------------------------------------------------------------------
def _csv_response(rows, fieldnames, filename):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))
    resp = app.response_class(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@app.route("/admin/export/reports.csv")
@admin_required
def admin_export_reports_csv():
    rows = g.db.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
    return _csv_response(rows, ["id", "user_id", "report_type", "description", "severity",
                                 "latitude", "longitude", "status", "created_at"], "reports.csv")


@app.route("/admin/export/feedback.csv")
@admin_required
def admin_export_feedback_csv():
    rows = g.db.execute("SELECT * FROM feedback ORDER BY created_at DESC").fetchall()
    return _csv_response(rows, ["id", "user_id", "name", "email", "message", "rating", "created_at"],
                          "feedback.csv")


@app.route("/admin/export/users.csv")
@admin_required
def admin_export_users_csv():
    rows = g.db.execute("SELECT id, full_name, email, phone, role, created_at FROM users").fetchall()
    return _csv_response(rows, ["id", "full_name", "email", "phone", "role", "created_at"], "users.csv")


@app.route("/admin")
@admin_required
def admin_dashboard():
    counts = {
        "users": g.db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "hospitals": g.db.execute("SELECT COUNT(*) c FROM hospitals").fetchone()["c"],
        "ambulances": g.db.execute("SELECT COUNT(*) c FROM ambulances").fetchone()["c"],
        "police_stations": g.db.execute("SELECT COUNT(*) c FROM police_stations").fetchone()["c"],
        "fire_stations": g.db.execute("SELECT COUNT(*) c FROM fire_stations").fetchone()["c"],
        "pharmacies": g.db.execute("SELECT COUNT(*) c FROM pharmacies").fetchone()["c"],
        "blood_banks": g.db.execute("SELECT COUNT(*) c FROM blood_banks").fetchone()["c"],
        "reports": g.db.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"],
        "feedback": g.db.execute("SELECT COUNT(*) c FROM feedback").fetchone()["c"],
    }

    reports_by_type = g.db.execute(
        "SELECT report_type, COUNT(*) c FROM reports GROUP BY report_type"
    ).fetchall()
    reports_by_severity = g.db.execute(
        "SELECT COALESCE(severity,'Unknown') severity, COUNT(*) c FROM reports GROUP BY severity"
    ).fetchall()

    users = g.db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    reports = g.db.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 25").fetchall()
    feedback = g.db.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 25").fetchall()
    hospitals = g.db.execute("SELECT * FROM hospitals ORDER BY name").fetchall()
    ambulances = g.db.execute("SELECT * FROM ambulances ORDER BY vehicle_no").fetchall()

    # --- SLA: average time-to-resolution for resolved reports (minutes) ---
    resolved = g.db.execute(
        "SELECT created_at FROM reports WHERE status = 'resolved'"
    ).fetchall()
    # (Resolution timestamps aren't separately tracked in the schema, so we
    # report resolved-vs-open counts rather than a fabricated duration.)
    resolved_count = len(resolved)
    open_count = counts["reports"] - resolved_count

    # --- Duplicate/spam detection: reports within ~150m and 30 minutes ---
    all_reports = g.db.execute(
        "SELECT * FROM reports WHERE latitude IS NOT NULL ORDER BY created_at DESC"
    ).fetchall()
    duplicate_ids = set()
    for i, r1 in enumerate(all_reports):
        for r2 in all_reports[i + 1:]:
            try:
                t1 = datetime.fromisoformat(r1["created_at"])
                t2 = datetime.fromisoformat(r2["created_at"])
            except ValueError:
                continue
            if abs((t1 - t2).total_seconds()) > 1800:
                continue
            dist = haversine(r1["latitude"], r1["longitude"], r2["latitude"], r2["longitude"])
            if dist is not None and dist < 0.15:
                duplicate_ids.add(r2["id"])

    # --- Heat-calendar: report counts by weekday x hour, for staffing insight ---
    heat_rows = g.db.execute("SELECT created_at FROM reports").fetchall()
    heat_grid = [[0] * 24 for _ in range(7)]  # 0=Mon .. 6=Sun
    for r in heat_rows:
        try:
            dt = datetime.fromisoformat(r["created_at"])
            heat_grid[dt.weekday()][dt.hour] += 1
        except ValueError:
            continue

    audit_log = g.db.execute(
        """SELECT aal.*, u.full_name AS admin_name FROM admin_audit_log aal
           LEFT JOIN users u ON u.id = aal.admin_id
           ORDER BY aal.id DESC LIMIT 30"""
    ).fetchall()

    return render_template(
        "admin.html", counts=counts, users=users, reports=reports, feedback=feedback,
        reports_by_type=reports_by_type, reports_by_severity=reports_by_severity,
        hospitals=hospitals, ambulances=ambulances,
        resolved_count=resolved_count, open_count=open_count,
        duplicate_ids=duplicate_ids, heat_grid=heat_grid, audit_log=audit_log,
    )


@app.route("/admin/hospitals/<int:hospital_id>/update-capacity", methods=["POST"])
@admin_required
def admin_update_capacity(hospital_id):
    try:
        beds = int(request.form.get("beds_available"))
    except (TypeError, ValueError):
        flash("Invalid bed count.", "danger")
        return redirect(url_for("admin_dashboard"))
    g.db.execute("UPDATE hospitals SET beds_available = ? WHERE id = ?", (beds, hospital_id))
    g.db.commit()
    log_admin_action("update_capacity", f"hospital:{hospital_id}", f"beds_available={beds}")
    flash("Hospital capacity updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/ambulances/<int:ambulance_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_ambulance(ambulance_id):
    row = g.db.execute("SELECT status FROM ambulances WHERE id = ?", (ambulance_id,)).fetchone()
    if row:
        new_status = "busy" if row["status"] == "available" else "available"
        g.db.execute("UPDATE ambulances SET status = ? WHERE id = ?", (new_status, ambulance_id))
        g.db.commit()
        log_admin_action("toggle_ambulance_status", f"ambulance:{ambulance_id}", f"status={new_status}")
        flash("Ambulance status updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/toggle-role", methods=["POST"])
@admin_required
def admin_toggle_role(user_id):
    row = g.db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        new_role = "user" if row["role"] == "admin" else "admin"
        g.db.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        g.db.commit()
        log_admin_action("toggle_role", f"user:{user_id}", f"role={new_role}")
        flash("User role updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You can't delete the account you're logged in as.", "danger")
        return redirect(url_for("admin_dashboard"))
    g.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    g.db.commit()
    log_admin_action("delete_user", f"user:{user_id}")
    flash("User deleted.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_report(report_id):
    g.db.execute("UPDATE reports SET status = 'resolved' WHERE id = ?", (report_id,))
    g.db.commit()
    log_admin_action("resolve_report", f"report:{report_id}")
    flash("Report marked resolved.", "success")
    return redirect(url_for("admin_dashboard"))


# ----------------------------------------------------------------------
# Error handlers
# ----------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404,
                            message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500,
                            message="Something went wrong on our end."), 500


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large. Max upload size is 6MB."}), 413


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
