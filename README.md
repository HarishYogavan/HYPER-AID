# 🚨 HyperAid — Emergency Response Assistant

An AI-assisted emergency response web application built with **Flask**, **SQLite**, **Bootstrap 5**, and vanilla **JavaScript (ES6)**. It helps people find nearby hospitals, ambulances, police/fire stations, pharmacies and blood banks, offers first-aid guidance through a chat + voice assistant, gives a quick photo-based accident severity estimate, and can alert saved emergency contacts with one tap.

> **This is a demonstration / portfolio project.** In a real emergency, always call your local emergency number first (112 in India, or 911/999/etc. depending on your country). The AI features here (chatbot, accident-severity estimator) are simplified, explainable heuristics — not certified medical or dispatch systems — and the SOS/SMS notifications are **simulated**, not sent through a real telecom gateway.

---

## ✨ Features

> **The whole site now requires an account.** Every page redirects to `/login` unless you're signed in, *except*: the login/register/forgot-password pages themselves, static assets, and a small set of intentional exceptions — the **SOS trigger** and **`/watch`** button (so a person in a real emergency is never blocked by a login screen), and the **public share-link pages** (Emergency Card, Pet Card, Dependent Card, live Location Tracking link, SOS coordination chat, Support Request page) since those exist specifically so someone *without* an account can view something shared with them.

| Area | What it does |
|---|---|
| **Home** | Hero, live stats, feature grid, testimonials |
| **About** | Mission, vision, benefits, tech stack |
| **Services** | Hospital / Ambulance / Police / Fire / Pharmacy / Blood Bank / **Vet Clinic** finders, each searchable and distance-ranked |
| **HYPER AI Assistant** | Rule-based assistant covering **50+ situations** — both critical emergencies (cardiac, poisoning, seizures, anaphylaxis, drowning, electric shock, heatstroke, hypothermia, diabetic emergencies, asthma, fractures) and everyday first-aid/home-remedy questions (fever, cold, headache, sore throat, stomach ache, insect bites, sunburn, hangovers, and more). Features **voice input & output**, a voice **wake word** ("Hey HYPER"), multi-turn context (understands short follow-ups), location-aware "nearest hospital" answers, an inline **Trigger SOS** button on critical language, and tappable follow-up questions. |
| **Emergency Dashboard** | Live tiles + ranked list of every nearby service with distance & ETA, based on your shared location |
| **Accident Detection** | Upload a photo → heuristic Low/Medium/High severity read, recommended actions, and nearest hospital |
| **Emergency Contacts** | Save contacts, ranked by **alert priority**; **SOS button** simulates SMS + email alerts with your live location and opens a group coordination chat |
| **Live Location Tracker** | Start real-time GPS sharing and get a link that works for a chosen duration (30m–12h). Anyone with the link watches your live position + trail on a map — **no login required** on their end. |
| **Symptom Checker** | Tap-through decision tree (no typing) that narrows down to specific first-aid guidance with a severity tag. |
| **Community Alerts** | Crowd-sourced incident pins (accidents, floods, road blocks, etc.) from the last 48h, with upvotes and a "flag as resolved/inaccurate" system. |
| **Emergency Medical Card** | Printable, QR-shareable card with blood group, allergies, medications, conditions, insurance and vaccination history. |
| **Vaccination Records** | Personal immunization history, shown on the Emergency Card. |
| **Service Reviews** | Star ratings + comments on hospitals/pharmacies/etc., averaged and shown in the finder lists. |
| **"I'm Safe" Broadcast** | One tap to tell every saved contact you're okay, instead of individual calls. |
| **SOS Group Chat** | Each SOS opens a lightweight, no-login coordination room. |
| **Danger-Zone Check-in Timer** | Set a timer before going somewhere isolated; if you don't check in, contacts are alerted automatically (tab must stay open — no server-side scheduler in this build). |
| **Shake-to-SOS** | Opt-in: shake your phone firmly 3 times to trigger SOS hands-free. |
| **Decoy "Fake Call" Screen** | One tap simulates a full-screen incoming call. |
| **Weather Safety Alerts** | Free, no-key Open-Meteo integration surfaces storm/heat/wind warnings on the dashboard. |
| **Multi-language Chatbot** | Hand-translated replies for the most critical intents in Hindi, Tamil, Spanish and French. |
| **Pregnancy / Pediatric Mode** | Optional chatbot context that appends tailored safety notes. |
| **High-Contrast Mode** | One-click accessibility theme, plus dark/light mode that respects your OS preference on first visit. |
| **Export My Data** | Download a JSON file of everything tied to your account. |
| **Watch-Sized View** | A stripped-down `/watch` page with just a big SOS button. |
| **Installable PWA** | Add-to-home-screen support with offline caching of key pages. |
| **Live Map** | 🔒 Requires login. Leaflet/OpenStreetMap **street or satellite view** (free Esri imagery, no key) by default; switches to the real **Google Maps API** once you add a key. A classic pin marks "you are here", and every nearby hospital/ambulance/pharmacy/etc. shown automatically with **clustered markers** and a **symbol legend**. Search or tap to mark a destination for a drawn route with **turn-by-turn directions**, a **driving/walking/cycling** toggle, and a live **ETA countdown**. **Save favorite places**, see **recently viewed** destinations, a **2km radius ring**, **Community Alerts pins merged in**, click-to-call straight from a marker, and a drift-detection prompt to recalculate if you wander off the route. All routing/search/clustering uses free, no-key services (OSRM + Nominatim + Leaflet + Esri). |
| **Kit Inventory** | Track first-aid/go-bag contents with expiry-date warnings. |
| **Medication Reminders** | Set daily reminder times with best-effort browser notifications (tab must be open). |
| **Blood Donation Tracker** | Log your last donation date and see when you're next eligible (90-day rule). |
| **Break-glass Medical Card** | Blood group/phone are always visible; allergies/medications/conditions require one tap to reveal (always shown when printed). |
| **Import Contacts** | Pull a contact straight from your phone (Contact Picker API, Android Chrome) instead of typing it in. |
| **First-time Walkthrough** | A short guided tour on first visit, skippable, never shown again after. |
| **Big-Icon Simplified Mode** | One-click larger text/buttons for elderly or low-literacy users, alongside high-contrast mode. |
| **Admin CSV Export** | Download users/reports/feedback as CSV, on top of the personal JSON data export. |
| **Pet First Aid & Vet Finder** | A dedicated "Pet" mode in HYPER AI covering choking, poisoning/toxic foods, heatstroke, bloat, seizures and more for dogs/cats, plus a Vet Clinic Finder (8th service category) with 24-hour emergency filtering. |
| **Pet Profiles & Records** | Multiple pets per account, vaccination history, and a printable/QR pet emergency card for vets or pet-sitters. |
| **Guardian / Dependent Mode** | Manage a full medical profile for someone who doesn't need their own login (a child, an elderly relative) — printable card, and the guardian can trigger SOS on their behalf. |
| **Support Requests** | A shareable post-incident page with a plain-language summary and a link to *your own* fundraiser — HyperAid never processes payments itself. |
| **Disaster Guides** | Dedicated before/during/after checklists for earthquake, flood, fire/wildfire, and storm/cyclone — not generic tips. |
| **Anomaly Check-in** | Opt-in: if you haven't opened HyperAid in a while, a gentle "everything okay?" banner appears on your next visit. |
| **NFC & Barcode Bridges** | Write your emergency card link to an NFC tag (Chrome/Android with NFC hardware), and scan a medication's barcode to prefill the name field (reads the raw code — not a real drug database lookup). |
| **Fridge Magnet Card** | A large-print, high-contrast printable layout distinct from the wallet-sized emergency card, designed for a fridge or door. |
| **Contact** | Validated contact form, embedded map, social links |
| **Auth** | Register, login, forgot-password (security question flow), profile editing, logout |
| **Admin Panel** | User management, capacity/ambulance-status editing, report/feedback review, SLA + duplicate-report detection, activity heat-calendar, audit log, Chart.js analytics |
| **Extras** | Dark/light mode, glassmorphism cards, loading animation, scroll-to-top, floating SOS button, FAQ, Blog |

---

## 🧱 Tech Stack

- **Backend:** Python 3, Flask, `sqlite3` (no ORM — schema is transparent in `database.py`)
- **Frontend:** HTML5, CSS3 (custom design system), Bootstrap 5, JavaScript ES6, Chart.js, Font Awesome
- **Maps:** Google Maps API (optional) with a Leaflet/OpenStreetMap fallback that works with zero configuration
- **Voice:** Browser-native Web Speech API (no external key needed)
- **Image handling:** Pillow + NumPy for the accident-photo heuristic

---

## 📂 Folder Structure

```
Emergency_Response_Assistant/
│── app.py                 # Flask app: routes, auth, APIs
│── database.py             # Schema + sample-data seeding
│── utils.py                 # Distance/ETA, chatbot logic, accident heuristic
│── requirements.txt
│── database.db              # Created automatically on first run
│
├── static/
│   ├── css/style.css         # Design system (light + dark themes)
│   ├── js/main.js            # Theme toggle, SOS, loader, scroll-to-top
│   ├── uploads/               # Accident photos uploaded by users
│   └── images/ icons/
│
└── templates/
    ├── base.html, index.html, about.html, services.html, service_list.html
    ├── chatbot.html, dashboard.html, accident.html, contacts.html, map.html
    ├── contact.html, faq.html, blog.html
    ├── register.html, login.html, forgot_password.html, profile.html
    ├── admin.html, error.html
```

---

## 🚀 Getting Started

### 1. Install dependencies
```bash
cd Emergency_Response_Assistant
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

### 2. Run the app
```bash
python app.py
```
The first run automatically creates `database.db` and seeds it with sample hospitals, ambulances, police/fire stations, pharmacies, blood banks, two demo users and some feedback.

Visit **http://127.0.0.1:5000**

### 3. Demo accounts
| Role | Email | Password |
|---|---|---|
| Admin | `admin@hyperaid.local` | `Admin@123` |
| User | `demo@hyperaid.local` | `Demo@123` |

### 4. (Optional) Enable real Google Maps
```bash
export GOOGLE_MAPS_API_KEY="your-key-here"
python app.py
```
Without a key, the **Live Map** page automatically falls back to a fully working OpenStreetMap/Leaflet view — no placeholder, no broken page.

### 5. (Optional) Set a real secret key for production
```bash
export SECRET_KEY="something-long-and-random"
```

---

## 🗄️ Database Tables

`users`, `hospitals`, `ambulances`, `police_stations`, `fire_stations`, `pharmacies`, `blood_banks`, `vet_clinics`, `emergency_contacts`, `feedback`, `reports`, `chat_logs`, `location_shares`, `location_pings`, `reviews`, `community_reports`, `vaccinations`, `sos_sessions`, `sos_chat_messages`, `checkin_timers`, `admin_audit_log`, `kit_items`, `medication_reminders`, `dependents`, `pets`, `pet_vaccinations`, `support_requests`, `favorite_places`

All sample location data is centered around Coimbatore, Tamil Nadu so the "nearby services" demo produces realistic distances out of the box — browser geolocation will naturally recentre it wherever you actually are.

---

## 🔒 Security Notes

- Passwords are hashed with Werkzeug's `generate_password_hash` / `check_password_hash` (PBKDF2) — never stored in plain text.
- Session-based auth via Flask's signed cookies (`SECRET_KEY`).
- Parameterised SQL everywhere — no string-built queries.
- File uploads are restricted by extension and a 6MB size cap, and saved with a sanitised filename (`secure_filename`).
- `@login_required` / `@admin_required` decorators guard private and admin-only routes.

For a production deployment you would additionally want: CSRF tokens on forms, rate limiting on auth/SOS endpoints, HTTPS, a persistent server (gunicorn/uwsgi + nginx) instead of the Flask dev server, and a real SMS/email provider (e.g. Twilio + SMTP) wired into `api_sos()` in `app.py`.

---

## 🧠 About the "AI" Features

- **Chatbot** (`utils.chatbot_reply`): a small, explainable keyword-matching intent engine covering common emergencies (cardiac, bleeding, burns, choking, snake bite, CPR, etc.), with sensible fallback guidance to call 112.
- **Accident severity** (`utils.analyse_accident_image`): a transparent heuristic based on red-pixel dominance and edge density in the uploaded photo — not a trained computer-vision model. It's built so you can drop in a real vision model later without touching the rest of the app (see the docstring in `utils.py`).
- **Voice input/output**: uses the browser's built-in `SpeechRecognition` / `SpeechSynthesis` APIs — works offline of any paid API, but browser support varies (best in Chrome/Edge).

---

## 🛠️ Extending This Project

- Swap the accident heuristic for a real vision model API call.
- Wire `api_sos()` up to Twilio (SMS) and Flask-Mail (email) for real notifications.
- Add CSRF protection (`Flask-WTF`) and rate limiting (`Flask-Limiter`).
- Move from SQLite to PostgreSQL for multi-user production traffic.
- Add push notifications for admins when a new "High" severity report comes in.

---

Built as a complete, runnable reference implementation — every page listed in the spec is functional, not a placeholder.
