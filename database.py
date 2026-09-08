"""
database.py
------------
Handles SQLite schema creation and seeding of sample data for the
Emergency Response Assistant.

Design notes:
  * We use Python's built-in `sqlite3` module directly (no ORM) so the
    schema stays transparent and easy to audit/extend.
  * `get_db()` returns a connection with `row_factory = sqlite3.Row` so
    query results can be accessed like dictionaries in templates/JSON.
  * `init_db()` is idempotent - safe to call every time the app starts.
    It only seeds sample rows the first time (when tables are empty).
"""

import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',           -- 'user' or 'admin'
    security_question TEXT,
    security_answer_hash TEXT,
    -- Medical profile (for the printable/QR emergency card) ----------
    blood_group TEXT,
    allergies TEXT,
    medications TEXT,
    conditions TEXT,
    medical_card_token TEXT UNIQUE,               -- public share token for emergency card
    high_contrast INTEGER DEFAULT 0,
    insurance_provider TEXT,
    insurance_policy_number TEXT,
    last_blood_donation TEXT,                       -- ISO date, for donation-eligibility tracker
    last_active_at TEXT,                             -- for the anomaly check-in nudge
    anomaly_checkin_enabled INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hospitals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    beds_available INTEGER DEFAULT 0,
    has_emergency_ward INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ambulances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_no TEXT,
    driver_name TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    status TEXT DEFAULT 'available'              -- available / busy
);

CREATE TABLE IF NOT EXISTS police_stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL
);

CREATE TABLE IF NOT EXISTS fire_stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL
);

CREATE TABLE IF NOT EXISTS pharmacies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    open_24_hours INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blood_banks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    blood_types_available TEXT
);

CREATE TABLE IF NOT EXISTS emergency_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    relationship TEXT,
    email TEXT,
    priority INTEGER DEFAULT 1,                    -- 1 = notified first, higher = later
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    email TEXT,
    message TEXT NOT NULL,
    rating INTEGER DEFAULT 5,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    report_type TEXT,                             -- 'accident' / 'sos' / 'contact'
    description TEXT,
    severity TEXT,
    latitude REAL,
    longitude REAL,
    status TEXT DEFAULT 'open',                   -- open / resolved
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    response TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Live Location Tracker -------------------------------------------------
CREATE TABLE IF NOT EXISTS location_shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    share_token TEXT UNIQUE NOT NULL,
    label TEXT,                                    -- e.g. "Walking home", "Drive to hospital"
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS location_pings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL,
    speed REAL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (share_id) REFERENCES location_shares(id) ON DELETE CASCADE
);

-- Service ratings & reviews ---------------------------------------------
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    service_category TEXT NOT NULL,               -- 'hospitals','pharmacies', etc.
    service_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,                        -- 1-5
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Crowd-sourced community incident pins ----------------------------------
CREATE TABLE IF NOT EXISTS community_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    incident_type TEXT NOT NULL,                     -- accident / fire / flood / road_block / crime / other
    description TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    upvotes INTEGER DEFAULT 0,
    flags INTEGER DEFAULT 0,                          -- "inaccurate/resolved" reports from other users
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Vaccination / immunization records -------------------------------------
CREATE TABLE IF NOT EXISTS vaccinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    vaccine_name TEXT NOT NULL,
    date_administered TEXT,
    notes TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- SOS group chat - lightweight room shared via a token, no login required
-- for contacts who join to coordinate with the person in distress.
CREATE TABLE IF NOT EXISTS sos_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    report_id INTEGER,
    room_token TEXT UNIQUE NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sos_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    sender_name TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sos_sessions(id) ON DELETE CASCADE
);

-- Danger-zone check-in timers ---------------------------------------------
CREATE TABLE IF NOT EXISTS checkin_timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    label TEXT,
    deadline TEXT NOT NULL,
    status TEXT DEFAULT 'active',                     -- active / checked_in / alerted / cancelled
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Admin audit log -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Emergency kit inventory tracker -----------------------------------------
CREATE TABLE IF NOT EXISTS kit_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    expiry_date TEXT,
    notes TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Medication reminders -----------------------------------------------------
CREATE TABLE IF NOT EXISTS medication_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    medication_name TEXT NOT NULL,
    reminder_time TEXT NOT NULL,                     -- 'HH:MM' 24h, checked client-side
    notes TEXT,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Guardian / dependent relationships ---------------------------------------
-- A guardian manages a dependent's profile directly - the dependent does not
-- need their own login (useful for children, elderly relatives, etc).
CREATE TABLE IF NOT EXISTS dependents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guardian_user_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    relationship TEXT,
    date_of_birth TEXT,
    blood_group TEXT,
    allergies TEXT,
    medications TEXT,
    conditions TEXT,
    notes TEXT,
    card_token TEXT UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guardian_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Pets ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    species TEXT,                                     -- Dog / Cat / Bird / Other
    breed TEXT,
    date_of_birth TEXT,
    weight_kg REAL,
    microchip_number TEXT,
    allergies TEXT,
    medications TEXT,
    notes TEXT,
    card_token TEXT UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pet_vaccinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL,
    vaccine_name TEXT NOT NULL,
    date_administered TEXT,
    notes TEXT,
    FOREIGN KEY (pet_id) REFERENCES pets(id) ON DELETE CASCADE
);

-- Veterinary clinics (8th service-finder category) --------------------------
CREATE TABLE IF NOT EXISTS vet_clinics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    latitude REAL,
    longitude REAL,
    emergency_24h INTEGER DEFAULT 0
);

-- Human & financial support after an incident --------------------------------
-- We never process payments ourselves (no gateway wired up here) - this
-- creates a shareable page pointing people to a link the user controls
-- (their own GoFundMe/PayPal.me/etc), plus a plain-language incident summary.
CREATE TABLE IF NOT EXISTS support_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    report_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    external_link TEXT,
    share_token TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE SET NULL
);

-- Saved favorite map destinations (home, work, family, etc) ----------------
CREATE TABLE IF NOT EXISTS favorite_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

# Sample coordinates are centered around Coimbatore, Tamil Nadu so the
# "nearby services" demo produces realistic distances out of the box.
HOSPITALS = [
    ("Coimbatore General Hospital", "Trichy Rd, Coimbatore", "0422-2301393", 11.0018, 76.9866, 42, 1),
    ("KMCH Hospital", "Avinashi Rd, Coimbatore", "0422-4323800", 11.0345, 77.0201, 18, 1),
    ("PSG Hospitals", "Peelamedu, Coimbatore", "0422-2570170", 11.0234, 77.0089, 25, 1),
    ("Ganga Hospital", "Mettupalayam Rd, Coimbatore", "0422-2485000", 11.0198, 76.9598, 12, 1),
    ("Kovai Medical Center (KMC)", "Avinashi Rd, Coimbatore", "0422-4324324", 11.0410, 77.0312, 30, 1),
]

AMBULANCES = [
    ("TN-38-AB-1234", "Murugan S", "108", 11.0050, 76.9700, "available"),
    ("TN-38-CD-5678", "Karthik R", "108", 11.0300, 77.0100, "available"),
    ("TN-38-EF-9012", "Vignesh P", "108", 11.0150, 76.9950, "busy"),
    ("TN-38-GH-3456", "Suresh M", "108", 11.0400, 77.0250, "available"),
]

POLICE = [
    ("Coimbatore City Police HQ", "Race Course, Coimbatore", "0422-2301100", 11.0016, 76.9673),
    ("Peelamedu Police Station", "Peelamedu, Coimbatore", "0422-2572100", 11.0271, 77.0111),
    ("Singanallur Police Station", "Singanallur, Coimbatore", "0422-2591100", 11.0016, 77.0294),
    ("R.S. Puram Police Station", "R.S. Puram, Coimbatore", "0422-2547100", 11.0050, 76.9500),
]

FIRE = [
    ("Coimbatore Central Fire Station", "Town Hall, Coimbatore", "101", 11.0025, 76.9614),
    ("Peelamedu Fire Station", "Peelamedu, Coimbatore", "101", 11.0280, 77.0080),
    ("Singanallur Fire Station", "Singanallur, Coimbatore", "101", 11.0005, 77.0270),
]

PHARMACIES = [
    ("Apollo Pharmacy - RS Puram", "RS Puram, Coimbatore", "044-45334455", 11.0060, 76.9510, 1),
    ("MedPlus - Peelamedu", "Peelamedu, Coimbatore", "044-49001100", 11.0260, 77.0095, 1),
    ("Netmeds Store - Singanallur", "Singanallur, Coimbatore", "044-40058888", 11.0020, 77.0280, 0),
    ("Global Pharmacy - Avinashi Rd", "Avinashi Rd, Coimbatore", "0422-4356789", 11.0380, 77.0230, 1),
]

BLOOD_BANKS = [
    ("Coimbatore Red Cross Blood Bank", "Race Course, Coimbatore", "0422-2301700", 11.0010, 76.9680, "A+,A-,B+,B-,O+,O-,AB+,AB-"),
    ("KMCH Blood Bank", "Avinashi Rd, Coimbatore", "0422-4324500", 11.0420, 77.0300, "O+,O-,B+,AB+"),
    ("Government Blood Bank CBE", "Trichy Rd, Coimbatore", "0422-2301500", 11.0022, 76.9870, "A+,B+,O+,O-"),
]

VET_CLINICS = [
    ("Coimbatore Veterinary Hospital", "Peelamedu, Coimbatore", "0422-2572200", 11.0265, 77.0085, 1),
    ("PetCare Animal Clinic", "RS Puram, Coimbatore", "0422-4551200", 11.0055, 76.9505, 0),
    ("City Vet Emergency Centre", "Avinashi Rd, Coimbatore", "0422-4390100", 11.0395, 77.0240, 1),
    ("Government Veterinary Polyclinic", "Trichy Rd, Coimbatore", "0422-2301900", 11.0028, 76.9880, 0),
]


def get_db():
    """Return a SQLite connection with dict-like row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed=True):
    """Create tables if they don't exist, and seed sample data once."""
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()

    if seed:
        _seed_if_empty(cur, conn)

    conn.close()


def _seed_if_empty(cur, conn):
    # --- Reference/service tables -----------------------------------
    def seed_table(table, rows, columns):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        if cur.fetchone()[0] == 0:
            placeholders = ",".join(["?"] * len(columns))
            cur.executemany(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                rows,
            )

    seed_table("hospitals", HOSPITALS,
               ["name", "address", "phone", "latitude", "longitude", "beds_available", "has_emergency_ward"])
    seed_table("ambulances", AMBULANCES,
               ["vehicle_no", "driver_name", "phone", "latitude", "longitude", "status"])
    seed_table("police_stations", POLICE,
               ["name", "address", "phone", "latitude", "longitude"])
    seed_table("fire_stations", FIRE,
               ["name", "address", "phone", "latitude", "longitude"])
    seed_table("pharmacies", PHARMACIES,
               ["name", "address", "phone", "latitude", "longitude", "open_24_hours"])
    seed_table("blood_banks", BLOOD_BANKS,
               ["name", "address", "phone", "latitude", "longitude", "blood_types_available"])
    seed_table("vet_clinics", VET_CLINICS,
               ["name", "address", "phone", "latitude", "longitude", "emergency_24h"])

    # --- Demo users ----------------------------------------------------
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        import secrets
        demo_users = [
            ("Admin User", "admin@hyperaid.local", "9999999999",
             generate_password_hash("Admin@123"), "admin",
             "What is your favourite colour?", generate_password_hash("blue"),
             "O+", "None known", "None", "None", secrets.token_hex(6)),
            ("Demo User", "demo@hyperaid.local", "9876543210",
             generate_password_hash("Demo@123"), "user",
             "What is your favourite colour?", generate_password_hash("red"),
             "B+", "Penicillin", "Cetirizine (as needed)", "Mild asthma", secrets.token_hex(6)),
        ]
        cur.executemany(
            """INSERT INTO users (full_name, email, phone, password_hash, role,
                                   security_question, security_answer_hash,
                                   blood_group, allergies, medications, conditions, medical_card_token)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            demo_users,
        )

    # --- Demo feedback ---------------------------------------------
    cur.execute("SELECT COUNT(*) FROM feedback")
    if cur.fetchone()[0] == 0:
        demo_feedback = [
            (None, "Priya N.", "priya@example.com",
             "The SOS button reached my family within seconds during a real emergency. Incredible peace of mind.", 5),
            (None, "Arun K.", "arun@example.com",
             "Hospital finder showed live bed availability - saved us a wasted trip across town.", 5),
            (None, "Divya S.", "divya@example.com",
             "Voice assistant understood my Tamil-English mix perfectly while I was driving.", 4),
        ]
        cur.executemany(
            """INSERT INTO feedback (user_id, name, email, message, rating)
               VALUES (?,?,?,?,?)""",
            demo_feedback,
        )

    # --- Demo reviews ---------------------------------------------
    cur.execute("SELECT COUNT(*) FROM reviews")
    if cur.fetchone()[0] == 0:
        demo_reviews = [
            (None, "hospitals", 1, 5, "ER staff were fast and clear about next steps."),
            (None, "hospitals", 1, 4, "Short wait, clean facility."),
            (None, "hospitals", 2, 5, "Excellent cardiac care team."),
            (None, "pharmacies", 2, 4, "Had everything in stock at 2am."),
            (None, "bloodbanks", 1, 5, "Donation process was smooth and well organised."),
        ]
        cur.executemany(
            """INSERT INTO reviews (user_id, service_category, service_id, rating, comment)
               VALUES (?,?,?,?,?)""",
            demo_reviews,
        )

    # --- Demo community incident reports ----------------------------
    cur.execute("SELECT COUNT(*) FROM community_reports")
    if cur.fetchone()[0] == 0:
        demo_incidents = [
            (None, "accident", "Minor two-vehicle collision, traffic slow-moving.", 11.0110, 76.9650),
            (None, "road_block", "Tree down blocking one lane after last night's storm.", 11.0290, 77.0050),
            (None, "flood", "Waterlogging near the underpass, avoid if possible.", 11.0050, 76.9950),
        ]
        cur.executemany(
            """INSERT INTO community_reports (user_id, incident_type, description, latitude, longitude)
               VALUES (?,?,?,?,?)""",
            demo_incidents,
        )

    # --- Demo vaccinations ---------------------------------------------
    cur.execute("SELECT COUNT(*) FROM vaccinations")
    if cur.fetchone()[0] == 0:
        demo_user_row = cur.execute("SELECT id FROM users WHERE email = 'demo@hyperaid.local'").fetchone()
        if demo_user_row:
            uid = demo_user_row[0]
            cur.executemany(
                "INSERT INTO vaccinations (user_id, vaccine_name, date_administered, notes) VALUES (?,?,?,?)",
                [
                    (uid, "Tetanus (Td)", "2023-05-14", "Booster, valid ~10 years"),
                    (uid, "COVID-19 (booster)", "2024-11-02", ""),
                    (uid, "Influenza", "2025-10-20", "Annual"),
                ],
            )

    conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH}")
