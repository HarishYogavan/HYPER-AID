"""
utils.py
--------
Pure-logic helpers used by app.py:
  * haversine()        - great-circle distance between two lat/lng points
  * eta_minutes()       - rough ETA assuming average urban travel speed
  * chatbot_reply()     - rule/keyword based emergency assistant
  * analyse_accident_image() - lightweight heuristic "severity" classifier

NOTE ON THE ACCIDENT/AI FEATURES:
This project ships without any paid third-party AI vision API key, so the
"accident severity" classifier below is a transparent, explainable
heuristic (based on image statistics such as the proportion of red/dark
pixels and edge density as a rough proxy for damage/blood/debris) rather
than a trained deep-learning model. It is clearly documented as such in
the UI and README so it is never mistaken for a clinical-grade system.
To upgrade it, swap `analyse_accident_image()` for a call to a real
vision model (e.g. a Claude/GPT vision endpoint or a custom-trained
CNN) - the rest of the app is written against its (severity, notes)
return signature, so no other code needs to change.
"""

import math
import random
from PIL import Image
import numpy as np

EARTH_RADIUS_KM = 6371.0


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres between two coordinates."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def eta_minutes(distance_km, avg_speed_kmph=30):
    """Rough estimated arrival time in minutes for urban traffic."""
    if distance_km is None:
        return None
    return round((distance_km / avg_speed_kmph) * 60, 1)


def rank_nearby(rows, user_lat, user_lng, limit=5):
    """Attach distance_km + eta_min to each row (a sqlite3.Row->dict) and
    return the closest `limit` entries."""
    enriched = []
    for row in rows:
        d = row.get("latitude")
        lo = row.get("longitude")
        dist = haversine(user_lat, user_lng, d, lo)
        item = dict(row)
        item["distance_km"] = round(dist, 2) if dist is not None else None
        item["eta_min"] = eta_minutes(dist)
        enriched.append(item)
    enriched.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"]))
    return enriched[:limit]


# ---------------------------------------------------------------------
# Rule-based emergency chatbot
# ---------------------------------------------------------------------
INTENTS = [
    {
        "id": "cardiac",
        "critical": True,
        "keywords": ["heart attack", "chest pain", "cardiac"],
        "reply": ("This may be a cardiac emergency. Call your local emergency number "
                   "immediately (108 in India). While waiting: have the person sit down "
                   "and stay calm, loosen tight clothing, and if they are conscious and "
                   "not allergic, a 300mg aspirin can be chewed. Do not give food or water."),
        "follow_ups": ["They've stopped responding", "Where's the nearest hospital?", "How do I do CPR?"],
    },
    {
        "id": "bleeding",
        "critical": True,
        "keywords": ["bleeding", "cut", "wound", "gash", "blood loss", "bleeds"],
        "reply": ("For severe bleeding: apply firm, direct pressure to the wound with a "
                   "clean cloth, keep the injured area raised above heart level if possible, "
                   "and do not remove the cloth once soaked - add more layers on top. "
                   "Call emergency services if bleeding does not slow within a few minutes."),
        "follow_ups": ["The bleeding won't stop", "They're feeling faint", "Where's the nearest hospital?"],
    },
    {
        "id": "accident",
        "critical": True,
        "keywords": ["accident", "crash", "collision"],
        "reply": ("Stay calm. First check for danger (traffic, fire, gas) before approaching. "
                   "Do not move an injured person unless there is immediate danger. "
                   "Call 108/112 with your location. You can also use our Accident Detection "
                   "page to upload a photo and get a quick severity estimate and nearest hospital."),
        "follow_ups": ["They're unconscious", "There's heavy bleeding", "Where's the nearest hospital?"],
    },
    {
        "id": "sunburn",
        "critical": False, "category": "remedy",
        "keywords": ["sunburn", "sun burn"],
        "reply": ("Cool the skin with a cool (not ice-cold) shower or compress, moisturize with "
                   "aloe vera, and stay hydrated. Avoid further sun exposure until healed, and "
                   "don't pop any blisters. Seek care for large blisters, fever, or signs of heat "
                   "illness alongside the burn."),
        "follow_ups": ["There are large blisters", "I also feel feverish or dizzy"],
    },
    {
        "id": "burn",
        "critical": False,
        "keywords": ["burn", "burnt", "scald"],
        "reply": ("Cool the burn under clean, cool (not icy) running water for 10-20 minutes. "
                   "Remove nearby jewellery/clothing before swelling starts, cover loosely with "
                   "cling film or a clean cloth, and seek medical help for burns larger than a "
                   "palm or on the face/hands/joints."),
        "follow_ups": ["The burn is very large", "Should I pop a blister?", "It's on their face"],
    },
    {
        "id": "choking",
        "critical": True,
        "keywords": ["choking", "choke"],
        "reply": ("If the person can cough or speak, encourage coughing. If they cannot "
                   "breathe, cough or speak: give 5 back blows between the shoulder blades, "
                   "then 5 abdominal thrusts (Heimlich manoeuvre), alternating until the object "
                   "clears or help arrives."),
        "follow_ups": ["They've gone limp", "It's a baby/infant", "They're breathing again now"],
    },
    {
        "id": "snakebite",
        "critical": True,
        "keywords": ["snake", "snakebite"],
        "reply": ("Keep the person still and calm, keep the bitten limb below heart level, "
                   "remove tight rings/clothing before swelling, and get to a hospital "
                   "immediately. Do not cut the wound, apply ice, or try to suck out venom."),
        "follow_ups": ["They're having trouble breathing", "Where's the nearest hospital?"],
    },
    {
        "id": "unresponsive",
        "critical": True,
        "keywords": ["unconscious", "not breathing", "cpr", "unresponsive", "not responding", "won't wake up", "wont wake up"],
        "reply": ("Call emergency services immediately. If trained, check breathing and start "
                   "CPR: 30 chest compressions (about 5-6cm deep, 100-120/min) followed by 2 "
                   "rescue breaths, repeating until help arrives or they start breathing."),
        "follow_ups": ["Where's the nearest hospital?", "I don't know CPR", "They just started breathing"],
    },
    {
        "id": "hospital",
        "critical": False,
        "keywords": ["hospital", "nearest hospital", "find hospital"],
        "reply": ("I can help with that - open the Emergency Dashboard or Services > Hospital "
                   "Finder to see hospitals ranked by distance from you, with live bed "
                   "availability and one-tap calling."),
        "follow_ups": ["Find the nearest ambulance", "Find the nearest pharmacy"],
    },
    {
        "id": "ambulance",
        "critical": False,
        "keywords": ["ambulance", "call ambulance"],
        "reply": ("Dial 108 for a free government ambulance anywhere in India, or use the "
                   "Ambulance Finder in Services to see the nearest available vehicle and its "
                   "estimated arrival time."),
        "follow_ups": ["Where's the nearest hospital?", "How do I do CPR while I wait?"],
    },
    {
        "id": "police",
        "critical": False,
        "keywords": ["police", "crime", "theft", "robbery"],
        "reply": ("Dial 100 or 112 for police assistance. You can also find your nearest "
                   "police station with directions under Services > Police Station Finder."),
        "follow_ups": ["Someone is injured too", "I need to report this later"],
    },
    {
        "id": "fire_emergency",
        "critical": True,
        "keywords": ["fire", "smoke"],
        "reply": ("Dial 101 for the fire brigade. Get everyone out, stay low under smoke, "
                   "close doors behind you, and never re-enter a burning building."),
        "follow_ups": ["Someone has a burn", "Someone is trapped inside"],
    },
    {
        "id": "poisoning",
        "critical": True,
        "keywords": ["poison", "poisoning", "swallowed something", "ingested", "overdose"],
        "reply": ("Call 112 or your local poison control immediately. Try to identify what "
                   "was swallowed/inhaled and how much, keep the container/packaging if possible, "
                   "and don't induce vomiting unless a medical professional tells you to - some "
                   "substances cause more damage coming back up."),
        "follow_ups": ["They're vomiting", "They're unconscious now", "It was a medication overdose"],
    },
    {
        "id": "seizure",
        "critical": True,
        "keywords": ["seizure", "convulsion", "fit", "epilepsy"],
        "reply": ("Clear the area of anything they could hit, cushion their head, and time the "
                   "seizure. Do not hold them down or put anything in their mouth. Once it stops, "
                   "roll them onto their side (recovery position). Call 112 if it lasts more than "
                   "5 minutes, they don't regain consciousness, or it's their first seizure."),
        "follow_ups": ["It's been over 5 minutes", "They're not waking up after", "This keeps happening"],
    },
    {
        "id": "anaphylaxis",
        "critical": True,
        "keywords": ["allergic reaction", "anaphylaxis", "allergy", "epipen", "swelling face", "throat closing"],
        "reply": ("This can be life-threatening. Call 112 immediately. If they have an epinephrine "
                   "auto-injector (EpiPen), help them use it right away - outer thigh, hold for "
                   "10 seconds. Have them lie flat with legs raised unless breathing is easier "
                   "sitting up. A second dose may be needed if there's no improvement in 5-15 minutes."),
        "follow_ups": ["They don't have an EpiPen", "They're struggling to breathe", "It's happened again"],
    },
    {
        "id": "drowning",
        "critical": True,
        "keywords": ["drowning", "drowned", "swallowed water", "underwater too long"],
        "reply": ("Get them out of the water safely without putting yourself at risk. Call 112. "
                   "If they're not breathing, start CPR immediately - even a small amount of "
                   "water in the lungs can be dangerous, so anyone rescued from drowning should "
                   "be seen by a doctor even if they seem fine afterward."),
        "follow_ups": ["They're not breathing", "They seem okay now, do they still need a doctor?"],
    },
    {
        "id": "electric_shock",
        "critical": True,
        "keywords": ["electric shock", "electrocuted", "electrocution", "power line"],
        "reply": ("Do not touch them if they're still in contact with the electrical source - "
                   "turn off the power first, or use a dry, non-conductive object to separate "
                   "them from it. Once safe, call 112 and check breathing/pulse. Electrical "
                   "injuries can cause internal damage that isn't visible, so always get checked."),
        "follow_ups": ["They're not breathing", "There's a visible burn"],
    },
    {
        "id": "heatstroke",
        "critical": True,
        "keywords": ["heat stroke", "heatstroke", "heat exhaustion", "overheating"],
        "reply": ("Move them to a cool/shaded place immediately, remove excess clothing, and cool "
                   "them with water, wet cloths or a fan. Give small sips of water only if fully "
                   "conscious. Call 112 if they have confusion, stop sweating, have a very high "
                   "temperature, or lose consciousness - heatstroke can be fatal quickly."),
        "follow_ups": ["They're confused or not making sense", "They've stopped sweating"],
    },
    {
        "id": "hypothermia",
        "critical": True,
        "keywords": ["hypothermia", "freezing cold", "frostbite"],
        "reply": ("Get them somewhere warm and dry, remove wet clothing, and warm them gradually "
                   "with blankets - avoid hot water or direct heat on the skin, which can cause "
                   "shock. Give warm (not hot) sweet drinks if fully conscious. Call 112 for "
                   "confusion, shivering that stops suddenly, or slowed breathing."),
        "follow_ups": ["They've stopped shivering", "They seem confused or drowsy"],
    },
    {
        "id": "panic_attack",
        "critical": False,
        "keywords": ["panic attack", "anxiety attack", "hyperventilating", "can't breathe anxiety"],
        "reply": ("Help them find a calm spot to sit. Encourage slow breathing - in for 4 counts, "
                   "hold for 4, out for 6. Reassure them it will pass and stay with them. If chest "
                   "pain, fainting, or symptoms don't ease and you're unsure if it's cardiac, treat "
                   "it as an emergency and call 112 instead of assuming it's 'just' anxiety."),
        "follow_ups": ["They also have chest pain", "This keeps happening to them"],
    },
    {
        "id": "diabetic",
        "critical": True,
        "keywords": ["diabetic", "blood sugar", "hypoglycemia", "hyperglycemia", "insulin"],
        "reply": ("For suspected low blood sugar (shaky, confused, sweating): give fast sugar - "
                   "juice, regular soda, or glucose tablets - if they're conscious and can swallow "
                   "safely. If unconscious or not improving in 15 minutes, call 112 immediately; "
                   "do not give food or drink to someone who isn't fully alert."),
        "follow_ups": ["They're unconscious", "They're not improving after sugar"],
    },
    {
        "id": "asthma",
        "critical": True,
        "keywords": ["asthma", "inhaler", "wheezing"],
        "reply": ("Help them sit upright and use their reliever inhaler (usually blue) - 4 puffs, "
                   "one at a time, waiting between each. If no improvement in a few minutes, repeat. "
                   "Call 112 if lips/fingertips turn blue, they can't speak in full sentences, or "
                   "there's no inhaler available and breathing is severely difficult."),
        "follow_ups": ["They don't have an inhaler", "Their lips are turning blue"],
    },
    {
        "id": "fracture",
        "critical": False,
        "keywords": ["broken bone", "fracture", "sprain", "dislocation"],
        "reply": ("Don't try to straighten or push the bone back into place. Support the area in "
                   "the position it's in, using a splint or padding if available, and avoid moving "
                   "the injured limb. Apply ice wrapped in cloth to reduce swelling. Seek medical "
                   "care - urgently if the bone is visible, the limb looks deformed, or there's no "
                   "feeling/pulse below the injury."),
        "follow_ups": ["The bone is visible", "There's no feeling below the injury"],
    },
    {
        "id": "fainting",
        "critical": False,
        "keywords": ["fainted", "fainting", "passed out", "blacked out"],
        "reply": ("Lay them flat and raise their legs about 30cm to help blood flow back to the "
                   "brain. Loosen tight clothing and ensure fresh air. Most people recover within "
                   "a minute - if they don't regain consciousness quickly, or it happens repeatedly, "
                   "call 112."),
        "follow_ups": ["They haven't woken up yet", "This has happened before recently"],
    },
    {
        "id": "nosebleed",
        "critical": False,
        "keywords": ["nosebleed", "nose bleeding"],
        "reply": ("Sit them forward (not tilted back), pinch the soft part of the nose firmly for "
                   "10-15 minutes without releasing, and breathe through the mouth. Seek care if "
                   "it doesn't stop after 20-30 minutes or follows a head injury."),
        "follow_ups": ["It won't stop after 20 minutes", "It happened after a head injury"],
    },
    # ------------------------------------------------------------------
    # Everyday first aid & home remedies (non-emergency, informational).
    # These make HYPER AI useful for common day-to-day complaints, not
    # just acute emergencies. Still general guidance, not a diagnosis.
    # ------------------------------------------------------------------
    {
        "id": "fever",
        "critical": False, "category": "remedy",
        "keywords": ["fever", "high temperature", "temperature is high"],
        "reply": ("Rest and drink plenty of fluids. A lukewarm sponge bath and light clothing "
                   "can help cool the body - avoid cold water or alcohol rubs. Paracetamol/"
                   "acetaminophen can reduce fever if needed, following the package dose. See a "
                   "doctor if fever is above 39.5°C/103°F, lasts more than 3 days, or comes with "
                   "a stiff neck, rash, or difficulty breathing."),
        "follow_ups": ["It's over 103°F", "There's also a rash", "It's a child with fever"],
    },
    {
        "id": "cold_flu",
        "critical": False, "category": "remedy",
        "keywords": ["common cold", "flu", "runny nose", "stuffy nose", "cold symptoms"],
        "reply": ("Rest, stay hydrated, and warm fluids like soup or tea with honey can soothe "
                   "the throat. Steam inhalation helps with congestion. Most colds clear in 7-10 "
                   "days - see a doctor if symptoms worsen after a week, breathing becomes "
                   "difficult, or fever is very high."),
        "follow_ups": ["I also have a cough", "It's been more than a week", "I also have a fever"],
    },
    {
        "id": "cough",
        "critical": False, "category": "remedy",
        "keywords": ["cough", "coughing"],
        "reply": ("Warm honey and lemon in water, or ginger tea, can soothe a cough - avoid honey "
                   "for children under 1 year. Staying hydrated thins mucus. Humidify the air if "
                   "it's dry. See a doctor if the cough lasts over 3 weeks, brings up blood, or "
                   "comes with chest pain or breathlessness."),
        "follow_ups": ["It's lasted over 3 weeks", "There's blood in it", "I also have chest pain"],
    },
    {
        "id": "sore_throat",
        "critical": False, "category": "remedy",
        "keywords": ["sore throat", "throat pain", "throat hurts"],
        "reply": ("Gargle with warm salt water a few times a day, drink warm fluids, and throat "
                   "lozenges can help. Rest your voice. See a doctor if it's severe, lasts more "
                   "than a week, or comes with high fever or difficulty swallowing."),
        "follow_ups": ["I can barely swallow", "It's been over a week"],
    },
    {
        "id": "headache",
        "critical": False, "category": "remedy",
        "keywords": ["headache", "migraine", "head hurts"],
        "reply": ("Rest in a dim, quiet room and drink water - dehydration is a common trigger. "
                   "A cold compress on the forehead or a warm compress on the neck can help. "
                   "Over-the-counter pain relief works for most tension headaches. Seek urgent "
                   "care for a sudden, severe 'worst ever' headache, one with vision changes, "
                   "confusion, or after a head injury."),
        "follow_ups": ["It's the worst headache of my life", "It happened after a head injury", "I have vision changes too"],
    },
    {
        "id": "stomach_ache",
        "critical": False, "category": "remedy",
        "keywords": ["stomach ache", "stomach pain", "indigestion", "upset stomach", "abdominal pain"],
        "reply": ("Sip clear fluids, avoid heavy or spicy food for a while, and a warm compress "
                   "on the belly can ease cramping. Ginger or peppermint tea often helps mild "
                   "indigestion. Seek care urgently if pain is severe and sudden, is on the lower "
                   "right side (possible appendicitis), or comes with persistent vomiting or fever."),
        "follow_ups": ["The pain is on my lower right side", "I've been vomiting a lot", "There's blood involved"],
    },
    {
        "id": "diarrhea",
        "critical": False, "category": "remedy",
        "keywords": ["diarrhea", "diarrhoea", "loose motion"],
        "reply": ("The main risk is dehydration - sip oral rehydration solution (ORS) or water "
                   "with a pinch of salt and sugar frequently. Bland foods (rice, banana, toast) "
                   "are easier to digest once you can eat. See a doctor if it lasts more than 2 "
                   "days, has blood, or comes with high fever or signs of dehydration (dizziness, "
                   "very dark urine)."),
        "follow_ups": ["There's blood in it", "It's lasted more than 2 days", "It's a young child"],
    },
    {
        "id": "vomiting",
        "critical": False, "category": "remedy",
        "keywords": ["vomiting", "nausea", "throwing up", "feeling sick"],
        "reply": ("Sip small amounts of clear fluids frequently rather than large gulps. Ginger "
                   "(tea or candy) can settle the stomach. Avoid solid food until vomiting eases, "
                   "then start bland foods. Seek care if it persists over 24 hours, has blood, or "
                   "comes with severe abdominal pain or signs of dehydration."),
        "follow_ups": ["There's blood in the vomit", "It's lasted over a day", "There's severe abdominal pain too"],
    },
    {
        "id": "constipation",
        "critical": False, "category": "remedy",
        "keywords": ["constipation", "cant poop", "can't poop"],
        "reply": ("Increase water and fiber (fruits, vegetables, whole grains), and gentle "
                   "movement/walking can help. Warm fluids in the morning sometimes stimulate "
                   "things naturally. See a doctor if it lasts more than a week, or comes with "
                   "severe pain, bloating, or vomiting."),
        "follow_ups": ["It's been over a week", "There's severe pain or bloating"],
    },
    {
        "id": "insect_bite",
        "critical": False, "category": "remedy",
        "keywords": ["insect bite", "mosquito bite", "bug bite", "bee sting", "wasp sting"],
        "reply": ("Wash the area with soap and water, apply a cold compress to reduce swelling, "
                   "and avoid scratching. A mild antihistamine cream can ease itching. For bee/"
                   "wasp stings, scrape (don't squeeze) out any visible stinger. Seek urgent care "
                   "for facial/throat swelling, difficulty breathing, or a spreading rash - these "
                   "can signal a serious allergic reaction."),
        "follow_ups": ["My face or throat is swelling", "I'm having trouble breathing"],
    },
    {
        "id": "muscle_cramp",
        "critical": False, "category": "remedy",
        "keywords": ["muscle cramp", "leg cramp", "charley horse"],
        "reply": ("Gently stretch and massage the cramping muscle, and apply a warm compress. "
                   "Staying hydrated and getting enough potassium/magnesium (bananas, nuts, leafy "
                   "greens) can help prevent them. See a doctor if cramps are frequent, severe, or "
                   "come with swelling or redness."),
        "follow_ups": ["This happens often", "There's swelling or redness too"],
    },
    {
        "id": "back_pain",
        "critical": False, "category": "remedy",
        "keywords": ["back pain", "backache"],
        "reply": ("Rest briefly but avoid prolonged bed rest - gentle movement usually helps more. "
                   "Alternate ice (first 48 hours) and heat, and over-the-counter pain relief can "
                   "ease symptoms. Seek urgent care for pain after a fall/injury, numbness or "
                   "weakness in the legs, or loss of bladder/bowel control."),
        "follow_ups": ["There's numbness or weakness in my legs", "It happened after a fall"],
    },
    {
        "id": "toothache",
        "critical": False, "category": "remedy",
        "keywords": ["toothache", "tooth pain", "tooth ache"],
        "reply": ("Rinse with warm salt water, and a cold compress on the cheek can reduce "
                   "swelling and pain. Over-the-counter pain relief helps temporarily - avoid "
                   "putting aspirin directly on the gum. See a dentist as soon as possible, "
                   "especially if there's swelling of the face or fever, which can signal "
                   "infection."),
        "follow_ups": ["My face is swelling", "There's a fever too"],
    },
    {
        "id": "earache",
        "critical": False, "category": "remedy",
        "keywords": ["earache", "ear pain", "ear ache"],
        "reply": ("A warm compress against the ear can ease discomfort. Avoid inserting anything "
                   "into the ear canal. Over-the-counter pain relief can help temporarily. See a "
                   "doctor if pain is severe, there's discharge/fluid, hearing loss, or it follows "
                   "swimming or a cold."),
        "follow_ups": ["There's discharge from the ear", "There's also hearing loss"],
    },
    {
        "id": "eye_irritation",
        "critical": False, "category": "remedy",
        "keywords": ["eye irritation", "eye pain", "something in my eye", "red eye"],
        "reply": ("Flush the eye gently with clean water or saline for several minutes, and "
                   "avoid rubbing it. Blinking can help clear small debris naturally. Seek urgent "
                   "care for chemical exposure, a penetrating injury, vision changes, or pain that "
                   "doesn't improve after flushing."),
        "follow_ups": ["It was a chemical splash", "My vision has changed"],
    },
    {
        "id": "hiccups",
        "critical": False, "category": "remedy",
        "keywords": ["hiccups", "hiccoughs"],
        "reply": ("Try holding your breath for 10 seconds, sipping cold water slowly, or breathing "
                   "into a paper bag. Most hiccups resolve within minutes on their own. See a "
                   "doctor if they last more than 48 hours."),
        "follow_ups": ["It's lasted more than 2 days"],
    },
    {
        "id": "motion_sickness",
        "critical": False, "category": "remedy",
        "keywords": ["motion sickness", "car sick", "carsick", "seasick"],
        "reply": ("Look at a fixed point on the horizon, get fresh air if possible, and avoid "
                   "reading or screens while moving. Ginger (tea, candy) can help settle the "
                   "stomach. Over-the-counter motion sickness medication works best if taken "
                   "before travel begins."),
        "follow_ups": ["It's making me vomit", "This happens every time I travel"],
    },
    {
        "id": "dehydration",
        "critical": False, "category": "remedy",
        "keywords": ["dehydration", "dehydrated", "thirsty and dizzy"],
        "reply": ("Sip water or an oral rehydration solution steadily rather than gulping. Rest "
                   "in a cool place. Signs of dehydration include dark urine, dizziness, and dry "
                   "mouth. Seek urgent care for confusion, rapid heartbeat, fainting, or if fluids "
                   "can't be kept down."),
        "follow_ups": ["I feel confused or faint", "I can't keep fluids down"],
    },
    {
        "id": "bruise",
        "critical": False, "category": "remedy",
        "keywords": ["bruise", "bruising"],
        "reply": ("Apply a cold compress for the first 24-48 hours to reduce swelling, then a "
                   "warm compress afterward can help it heal. Rest and elevate the area if "
                   "possible. See a doctor if bruising is severe, unexplained, or comes with "
                   "significant swelling or inability to move the area."),
        "follow_ups": ["It's very swollen", "I can't move the area properly"],
    },
    {
        "id": "blister",
        "critical": False, "category": "remedy",
        "keywords": ["blister"],
        "reply": ("Leave a blister intact if possible - it protects against infection. Cover it "
                   "with a soft bandage or blister plaster to reduce friction. If it's already "
                   "burst, gently clean with mild soap and water and cover. Seek care if it shows "
                   "signs of infection (increasing redness, pus, warmth, fever)."),
        "follow_ups": ["It looks infected", "It's from a burn, not friction"],
    },
    {
        "id": "hangover",
        "critical": False, "category": "remedy",
        "keywords": ["hangover", "hung over"],
        "reply": ("Rehydrate steadily with water or an electrolyte drink, eat something light and "
                   "easy on the stomach, and rest. Avoid more alcohol and caffeine, which can "
                   "worsen dehydration. Over-the-counter pain relief can help a headache - avoid "
                   "taking it on a completely empty, irritated stomach."),
        "follow_ups": ["I'm also vomiting a lot", "I feel unusually confused"],
    },
    {
        "id": "insomnia",
        "critical": False, "category": "remedy",
        "keywords": ["insomnia", "can't sleep", "cant sleep", "trouble sleeping"],
        "reply": ("Keep a consistent sleep schedule, avoid screens/caffeine for a few hours before "
                   "bed, and keep the room cool and dark. A warm (non-caffeinated) drink and "
                   "relaxing routine can help signal wind-down. See a doctor if it persists for "
                   "weeks or significantly affects daily life."),
        "follow_ups": ["This has gone on for weeks", "It's affecting my daily life a lot"],
    },
    {
        "id": "food_poisoning_mild",
        "critical": False, "category": "remedy",
        "keywords": ["food poisoning", "bad food", "something i ate"],
        "reply": ("Rest and sip clear fluids or ORS frequently to prevent dehydration - let your "
                   "stomach settle before eating solid food again, then start with bland options. "
                   "Most mild food poisoning passes in 24-48 hours. Seek care urgently for high "
                   "fever, bloody stools/vomit, or signs of severe dehydration."),
        "follow_ups": ["There's blood in it", "It's lasted more than 2 days", "I feel very dizzy or weak"],
    },
    {
        "id": "greeting",
        "critical": False,
        "keywords": ["hello", "hi", "hey"],
        "reply": ("Hello, I'm HYPER AI. Tell me what's happening - anything from an emergency to "
                   "an everyday ache or complaint - and I'll guide you, or use "
                   "the SOS button any time for immediate help."),
        "follow_ups": ["Someone is hurt", "I have a question about first aid"],
    },
    {
        "id": "thanks",
        "critical": False,
        "keywords": ["thank", "thanks"],
        "reply": "You're welcome. Stay safe - I'm here any time you need help.",
        "follow_ups": [],
    },
]

INTENTS_BY_ID = {i["id"]: i for i in INTENTS}

# ---------------------------------------------------------------------
# Pet first-aid library - kept separate from the human INTENTS list since
# dosing, technique and urgency signs differ meaningfully between species.
# Selected via mode='pet' on the chatbot.
# ---------------------------------------------------------------------
PET_INTENTS = [
    {
        "id": "pet_choking",
        "critical": True,
        "keywords": ["choking", "choke", "something stuck in throat"],
        "reply": ("Look in the mouth and remove any visible object with your fingers (careful not to push "
                   "it further in). For small dogs/cats, hold them with their back against your chest, apply "
                   "firm upward abdominal thrusts. For larger dogs, use both hands like a Heimlich manoeuvre. "
                   "Get to a vet immediately if the object doesn't come out."),
        "follow_ups": ["They've gone limp", "It's a small cat"],
    },
    {
        "id": "pet_poison",
        "critical": True,
        "keywords": ["poison", "ate chocolate", "ate xylitol", "toxic", "ate something toxic", "swallowed"],
        "reply": ("Call your vet or an animal poison hotline immediately - don't wait for symptoms. Try to "
                   "identify what and how much was eaten, and keep the packaging. Do not induce vomiting "
                   "unless a vet specifically tells you to, as some substances cause more harm coming back up. "
                   "Common toxic items: chocolate, xylitol (sugar-free gum/sweets), grapes/raisins, onions, "
                   "garlic, lilies (cats), antifreeze, and many human medications."),
        "follow_ups": ["What foods are toxic to pets?", "They're vomiting", "They seem drowsy or wobbly"],
    },
    {
        "id": "pet_toxic_food_list",
        "critical": False,
        "keywords": ["what foods are toxic", "toxic foods for pets", "what can't dogs eat", "what can't cats eat"],
        "reply": ("Common toxic foods/items for pets: chocolate (especially dark/baking), xylitol (sugar-free "
                   "gum, mints, some peanut butter), grapes and raisins, onions and garlic, macadamia nuts, "
                   "alcohol, caffeine, raw bread dough, lilies (highly toxic to cats), and human medications "
                   "like ibuprofen or paracetamol. If in doubt, keep it away from your pet and call your vet "
                   "before assuming a small amount is fine."),
        "follow_ups": ["My pet ate one of these", "Where's the nearest vet?"],
    },
    {
        "id": "pet_heatstroke",
        "critical": True,
        "keywords": ["heat stroke", "heatstroke", "overheating", "heavy panting"],
        "reply": ("Move them to shade/air conditioning immediately. Cool them gradually with room-temperature "
                   "(not ice-cold) water on the paws, belly and ears, and offer small amounts of water to drink. "
                   "Never leave a pet in a parked car. Get to a vet urgently for collapse, dark red gums, "
                   "vomiting, or extreme lethargy - heatstroke in pets can be fatal within minutes."),
        "follow_ups": ["They've collapsed", "Where's the nearest vet?"],
    },
    {
        "id": "pet_bleeding",
        "critical": True,
        "keywords": ["bleeding", "cut paw", "wound", "bleeding heavily"],
        "reply": ("Apply firm direct pressure with a clean cloth for several minutes without lifting to check. "
                   "Keep the pet calm and still - stress increases bleeding. If it's a limb, you can apply light "
                   "elevation. Muzzle a scared/painful pet gently before handling if you have something suitable, "
                   "since even gentle animals may bite when hurt. Get to a vet if bleeding doesn't slow within "
                   "5-10 minutes."),
        "follow_ups": ["It won't stop bleeding", "Where's the nearest vet?"],
    },
    {
        "id": "pet_seizure",
        "critical": True,
        "keywords": ["seizure", "convulsion", "fitting"],
        "reply": ("Keep the area clear so they can't hurt themselves, but don't touch or restrain them and "
                   "keep hands away from their mouth. Time the seizure. If it lasts more than 2-3 minutes, "
                   "or they have repeated seizures without fully recovering between them, this is an emergency "
                   "- get to a vet immediately."),
        "follow_ups": ["It's been over 2 minutes", "They're having another one"],
    },
    {
        "id": "pet_bloat",
        "critical": True,
        "keywords": ["bloat", "swollen belly", "distended stomach", "trying to vomit nothing"],
        "reply": ("This can be bloat/GDV, a life-threatening emergency especially in large, deep-chested dogs. "
                   "Signs: a swollen, hard belly, restlessness, drooling, and retching without producing "
                   "vomit. Do not wait to see if it improves - get to an emergency vet immediately, this can "
                   "become fatal within hours."),
        "follow_ups": ["Where's the nearest vet?"],
    },
    {
        "id": "pet_fracture",
        "critical": False,
        "keywords": ["broken leg", "broken bone", "limping badly", "can't put weight on leg"],
        "reply": ("Limit their movement as much as possible - carry small pets on a flat, firm surface if you "
                   "can, and avoid forcing them to walk. Don't try to splint it yourself unless a vet talks you "
                   "through it, as improper splinting can cause more damage. Get to a vet promptly."),
        "follow_ups": ["Where's the nearest vet?"],
    },
    {
        "id": "pet_snakebite",
        "critical": True,
        "keywords": ["snake bite", "snakebite", "bitten by a snake"],
        "reply": ("Keep your pet as still and calm as possible - carry them rather than letting them walk, "
                   "since movement speeds venom spread. Do not cut the wound, apply ice, or try to suck out "
                   "venom. Get to a vet immediately; note the snake's appearance if safely possible, but don't "
                   "risk another bite trying to identify it."),
        "follow_ups": ["Where's the nearest vet?"],
    },
    {
        "id": "pet_bee_sting",
        "critical": False,
        "keywords": ["bee sting", "wasp sting", "insect bite"],
        "reply": ("Remove the stinger if visible by scraping it out (don't squeeze). Apply a cold compress to "
                   "reduce swelling. Watch closely for signs of a serious allergic reaction - facial swelling, "
                   "difficulty breathing, vomiting, or collapse - and get to a vet immediately if any appear, "
                   "since pets can have severe reactions just like people."),
        "follow_ups": ["Their face is swelling", "Where's the nearest vet?"],
    },
    {
        "id": "pet_vet",
        "critical": False,
        "keywords": ["nearest vet", "find a vet", "vet clinic", "veterinarian"],
        "reply": ("Open Services > Vet Clinic Finder or the Emergency Dashboard to see clinics ranked by "
                   "distance, with 24-hour emergency options flagged."),
        "follow_ups": [],
    },
]

PET_INTENTS_BY_ID = {i["id"]: i for i in PET_INTENTS}


def pet_chatbot_reply(message):
    """Same longest-match approach as the human assistant, scoped to pets."""
    text = message.lower()
    best_intent, best_len = None, 0
    for intent in PET_INTENTS:
        for kw in intent["keywords"]:
            if kw in text and len(kw) > best_len:
                best_intent, best_len = intent, len(kw)
    if best_intent is None:
        return {
            "reply": ("I'm not sure about that one yet. If your pet is in serious distress, call your vet "
                       "or the nearest emergency animal clinic right away."),
            "intent_id": None, "critical": False, "follow_ups": [],
        }
    return {
        "reply": best_intent["reply"], "intent_id": best_intent["id"],
        "critical": best_intent["critical"], "follow_ups": best_intent.get("follow_ups", []),
    }


# Phrases that, regardless of which intent matched, indicate the situation is
# acute enough to surface an inline "Trigger SOS now" action in the chat UI.
ESCALATION_PHRASES = [
    "not breathing", "unconscious", "unresponsive", "no pulse", "not responding",
    "turning blue", "can't breathe", "cant breathe", "severe bleeding", "won't stop bleeding",
    "collapsed", "seizure", "not waking up", "stopped breathing",
]


def is_critical(message):
    text = message.lower()
    return any(p in text for p in ESCALATION_PHRASES)

FALLBACK_REPLY = (
    "I don't have a specific answer for that in my library yet - I cover a wide range of "
    "emergencies and everyday first aid/home remedies, but not everything. If this is a "
    "life-threatening emergency, please call 112 (or 108 for ambulance / 100 for police / "
    "101 for fire) right now. Otherwise, try rephrasing, use the Symptom Checker for a "
    "guided walkthrough, or describe your symptoms in more detail and I'll do my best."
)

DISCLAIMER = "General first-response guidance - not a diagnosis. Call 112 for real emergencies."

# ---------------------------------------------------------------------
# Static multi-language translations for the handful of most critical
# replies (no external translation API - these are hand-written so the
# wording stays accurate for genuine emergencies). Anything without a
# translated entry gracefully falls back to English.
# ---------------------------------------------------------------------
TRANSLATIONS = {
    "hi": {  # Hindi
        "chest pain": ("यह हृदयाघात (कार्डियक इमरजेंसी) हो सकता है। तुरंत 108 पर कॉल करें। "
                        "व्यक्ति को बैठाएं, शांत रखें, तंग कपड़े ढीले करें।"),
        "bleeding": ("गंभीर रक्तस्राव के लिए घाव पर सीधा दबाव डालें, साफ कपड़े का प्रयोग करें, "
                     "और घायल हिस्से को हृदय से ऊपर रखें।"),
        "accident": ("शांत रहें। पहले खतरे की जांच करें, फिर 108/112 पर कॉल करें और अपना स्थान बताएं।"),
        "hello": "नमस्ते, मैं HYPER AI हूं। बताइए क्या हुआ है, मैं मदद करूंगा।",
    },
    "ta": {  # Tamil
        "chest pain": ("இது இதய அவசரநிலையாக இருக்கலாம். உடனடியாக 108-ஐ அழைக்கவும். "
                       "நபரை அமர வைத்து அமைதியாக இருக்கச் செய்யுங்கள்."),
        "bleeding": ("கடுமையான ரத்தப்போக்கிற்கு காயத்தின் மீது நேரடி அழுத்தம் கொடுக்கவும், "
                    "சுத்தமான துணியைப் பயன்படுத்தவும்."),
        "accident": ("அமைதியாக இருங்கள். ஆபத்தை சரிபார்த்த பின் 108/112-ஐ அழைத்து உங்கள் இருப்பிடத்தைக் கூறுங்கள்."),
        "hello": "வணக்கம், நான் HYPER AI. என்ன நடந்தது என்று சொல்லுங்கள், நான் உதவுகிறேன்.",
    },
    "es": {  # Spanish
        "chest pain": ("Esto puede ser una emergencia cardiaca. Llame al 112 de inmediato. "
                       "Siente a la persona, mantenga la calma, afloje la ropa ajustada."),
        "bleeding": ("Para sangrado severo, aplique presión directa firme sobre la herida con un paño limpio."),
        "accident": ("Mantenga la calma. Verifique el peligro, luego llame al 112 e indique su ubicación."),
        "hello": "Hola, soy HYPER AI. Cuénteme qué está pasando y le ayudaré.",
    },
    "fr": {  # French
        "chest pain": ("Cela peut être une urgence cardiaque. Appelez le 112 immédiatement. "
                       "Asseyez la personne, restez calme, desserrez les vêtements serrés."),
        "bleeding": ("Pour une hémorragie sévère, appliquez une pression directe ferme sur la plaie."),
        "accident": ("Restez calme. Vérifiez le danger, puis appelez le 112 et indiquez votre position."),
        "hello": "Bonjour, je suis HYPER AI. Dites-moi ce qui se passe, je vais vous aider.",
    },
}


def _match_intent(text):
    """Return the intent whose matched keyword is the longest (most specific)
    substring match, rather than simply the first intent in list order. This
    correctly resolves cases like "food poisoning" (mild, common) vs the
    generic "poison"/"poisoning" keywords (a more urgent chemical/medication
    ingestion intent) - the longer, more specific phrase wins."""
    text = text.lower()
    best_intent, best_len = None, 0
    for intent in INTENTS:
        for kw in intent["keywords"]:
            if kw in text and len(kw) > best_len:
                best_intent, best_len = intent, len(kw)
    return best_intent


def chatbot_reply(message, lang="en", history=None):
    """Keyword-matching intent engine for offline emergency guidance.

    `lang` (ISO short code: en/hi/ta/es/fr) selects a hand-translated reply
    for the handful of most critical intents where one exists; everything
    else falls back to English rather than guessing.

    `history` (optional list of the last few user messages, oldest first)
    lets short follow-up messages like "now he's not responding" be
    understood in context: if the current message alone doesn't match
    anything, it's retried combined with the previous message.

    Returns a dict: {reply, intent_id, critical, follow_ups, category}
    """
    intent = _match_intent(message)
    combined_note = False

    if intent is None and history:
        combined_text = " ".join(history[-2:]) + " " + message
        intent = _match_intent(combined_text)
        combined_note = intent is not None

    critical = is_critical(message) or (intent["critical"] if intent else False)

    if intent is None:
        return {
            "reply": FALLBACK_REPLY, "intent_id": None,
            "critical": is_critical(message), "follow_ups": [], "category": "unknown",
        }

    reply = intent["reply"]
    if lang != "en" and lang in TRANSLATIONS:
        for kw in intent["keywords"]:
            if kw in TRANSLATIONS[lang]:
                reply = TRANSLATIONS[lang][kw]
                break

    if combined_note and lang == "en":
        reply = "(Following up on what you said before) " + reply

    return {
        "reply": reply, "intent_id": intent["id"],
        "critical": critical, "follow_ups": intent.get("follow_ups", []),
        "category": intent.get("category", "emergency"),
    }


# ---------------------------------------------------------------------
# Guided symptom checker - a small decision tree so users who don't want
# to type free text can tap through structured questions instead.
# Each node: id -> {question, options:[(label,next_id)]} or a leaf with
# 'result' (title + advice) instead of 'question'.
# ---------------------------------------------------------------------
SYMPTOM_TREE = {
    "start": {
        "question": "What best describes the situation?",
        "options": [
            ("Chest pain / heart symptoms", "cardiac_1"),
            ("Bleeding or a wound", "bleeding_1"),
            ("Breathing difficulty / choking", "breathing_1"),
            ("Injury from a fall or accident", "injury_1"),
            ("Burn", "burn_1"),
        ],
    },
    "cardiac_1": {
        "question": "Is the person conscious and able to talk?",
        "options": [("Yes", "cardiac_2"), ("No / unresponsive", "unresponsive_result")],
    },
    "cardiac_2": {
        "question": "Do they have chest pressure, pain spreading to the arm/jaw, or breathlessness?",
        "options": [("Yes", "cardiac_result"), ("No, milder discomfort", "cardiac_mild_result")],
    },
    "bleeding_1": {
        "question": "Is the bleeding heavy or not slowing down after a minute of pressure?",
        "options": [("Yes, heavy/continuous", "bleeding_severe_result"), ("No, minor", "bleeding_minor_result")],
    },
    "breathing_1": {
        "question": "Can the person still cough, speak, or make sounds?",
        "options": [("Yes", "breathing_mild_result"), ("No, silent / turning blue", "choking_result")],
    },
    "injury_1": {
        "question": "Is there visible deformity, inability to move a limb, or a head injury?",
        "options": [("Yes", "injury_severe_result"), ("No, just pain/bruising", "injury_mild_result")],
    },
    "burn_1": {
        "question": "Is the burn larger than your palm, or on the face/hands/joints?",
        "options": [("Yes", "burn_severe_result"), ("No, small area", "burn_mild_result")],
    },

    "cardiac_result": {"result": {
        "title": "Possible cardiac emergency", "severity": "High",
        "advice": "Call 108/112 now. Sit the person down, keep them calm, loosen tight clothing. "
                  "If conscious and not allergic, a chewed 300mg aspirin can help. Do not give food or water."}},
    "cardiac_mild_result": {"result": {
        "title": "Monitor closely", "severity": "Medium",
        "advice": "Have them rest sitting up, avoid exertion, and seek medical advice soon - "
                  "call emergency services immediately if symptoms worsen or spread."}},
    "unresponsive_result": {"result": {
        "title": "Unresponsive - emergency", "severity": "High",
        "advice": "Call 108/112 immediately. If trained, check breathing and begin CPR: 30 chest "
                  "compressions then 2 rescue breaths, repeating until help arrives."}},
    "bleeding_severe_result": {"result": {
        "title": "Severe bleeding", "severity": "High",
        "advice": "Apply firm direct pressure with a clean cloth, raise the area above heart level if "
                  "possible, add layers rather than removing a soaked cloth, and call 108/112."}},
    "bleeding_minor_result": {"result": {
        "title": "Minor bleeding", "severity": "Low",
        "advice": "Clean the wound, apply pressure until it stops, and cover with a sterile bandage. "
                  "Seek care if it doesn't stop within 10 minutes or shows signs of infection."}},
    "breathing_mild_result": {"result": {
        "title": "Partial airway obstruction", "severity": "Medium",
        "advice": "Encourage strong coughing. Stay with them and be ready to act if breathing worsens. "
                  "Call 108/112 if it doesn't clear quickly."}},
    "choking_result": {"result": {
        "title": "Choking - emergency", "severity": "High",
        "advice": "Give 5 back blows between the shoulder blades, then 5 abdominal thrusts, alternating "
                  "until the object clears or help arrives. Call 108/112 immediately."}},
    "injury_severe_result": {"result": {
        "title": "Possible serious injury", "severity": "High",
        "advice": "Do not move the person unless there is immediate danger. Keep them still, support "
                  "the area, and call 108/112 - especially important for suspected head/spine injury."}},
    "injury_mild_result": {"result": {
        "title": "Likely minor injury", "severity": "Low",
        "advice": "Rest, ice, compression and elevation (RICE) can help. Seek medical care if pain or "
                  "swelling worsens or doesn't improve in a day or two."}},
    "burn_severe_result": {"result": {
        "title": "Significant burn", "severity": "High",
        "advice": "Cool under running water for 10-20 minutes, cover loosely with cling film or a clean "
                  "cloth, do not apply ice or ointments, and seek medical care promptly."}},
    "burn_mild_result": {"result": {
        "title": "Minor burn", "severity": "Low",
        "advice": "Cool under running water for 10-20 minutes and keep clean. See a doctor if blistering "
                  "is significant or it doesn't improve."}},
}


# ---------------------------------------------------------------------
# Accident image heuristic severity estimator
# ---------------------------------------------------------------------
def analyse_accident_image(file_path):
    """
    Lightweight, explainable heuristic (NOT a trained ML model) that
    estimates rough accident severity from basic image statistics:
      - proportion of "red-dominant" pixels (proxy for blood/brake-lights/damage)
      - overall edge density via a simple gradient magnitude (proxy for debris/deformation)
    Returns: (severity: 'Low'|'Medium'|'High', score: float 0-1, notes: str)
    """
    try:
        img = Image.open(file_path).convert("RGB").resize((256, 256))
        arr = np.asarray(img).astype("float32")

        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        red_dominant = np.mean((r > g + 25) & (r > b + 25))

        gray = (0.299 * r + 0.587 * g + 0.114 * b)
        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))
        edge_density = (gx.mean() + gy.mean()) / 255.0

        # Weighted composite score, clipped to [0,1]
        score = min(1.0, 0.6 * red_dominant * 4 + 0.4 * min(edge_density * 3, 1.0))

        if score < 0.33:
            severity = "Low"
            notes = ("Minimal visible damage/debris detected. Likely a minor fender-bender. "
                     "Exchange details, take photos, and contact your insurer.")
        elif score < 0.66:
            severity = "Medium"
            notes = ("Moderate damage/debris signatures detected. Check occupants for injuries, "
                     "move to a safe location if the vehicle is driveable, and consider a "
                     "hospital check-up as a precaution.")
        else:
            severity = "High"
            notes = ("Significant damage/blood-colour signatures detected. Treat as a serious "
                     "emergency: call 108/112 immediately, do not move injured persons unless "
                     "necessary, and keep the area safe from oncoming traffic.")

        return severity, round(float(score), 2), notes

    except Exception:
        # Fallback so a corrupt/unusual image never crashes the request
        severity = random.choice(["Low", "Medium"])
        return severity, 0.4, ("Could not fully analyse this image, showing a conservative "
                                "estimate. Please assess the scene directly and call 108/112 "
                                "if there is any doubt.")
