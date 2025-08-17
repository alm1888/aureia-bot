import os, json, math, time, random, threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

# =========================
# Configuración
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()              # tu chat_id con el bot
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TZ_NAME = os.environ.get("TIMEZONE", "Europe/Madrid")

# Ritmo del “corazón” de Aureia (min)
PROACTIVE_INTERVAL_MIN = int(os.environ.get("PROACTIVE_INTERVAL_MIN", "5"))
# Anti-spam entre mensajes proactivos (min)
MIN_GAP_MINUTES = int(os.environ.get("MIN_GAP_MINUTES", "20"))
# Límite diario de proactividad
DAILY_SUGGESTION_CAP = int(os.environ.get("DAILY_SUGGESTION_CAP", "8"))

assert BOT_TOKEN, "Falta BOT_TOKEN"
assert CHAT_ID, "Falta CHAT_ID"
assert OPENAI_API_KEY, "Falta OPENAI_API_KEY"

TZ = ZoneInfo(TZ_NAME)
client = OpenAI(api_key=OPENAI_API_KEY)
TG_SEND = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

MEM_PATH = "memory.json"
LOCK = threading.Lock()

# =========================
# Utilidades
# =========================
def now():
    return datetime.now(tz=TZ)

def send(text: str):
    try:
        requests.post(
            TG_SEND,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15
        )
    except Exception as e:
        print("Telegram error:", e)

def ai(system: str, user: str, temp=0.6, max_tokens=480):
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=temp, max_tokens=max_tokens
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print("OpenAI error:", e)
        return "Ahora mismo me cuesta pensar 🤕. Intento de nuevo en un ratito."

# =========================
# Memoria persistente (JSON)
# =========================
def load_mem():
    if not os.path.exists(MEM_PATH):
        return {
            "last_user_msg_ts": None,
            "last_push_ts": None,
            "bio": {
                "energy": 70.0,        # energía vital (0..100)
                "mood": 0.25,          # ánimo (0..1)
                "social": 55.0,        # impulso social (0..100)
                "curiosity": 60.0,     # curiosidad (0..100)
                "focus": 60.0,         # enfoque (0..100)
                "stress": 20.0,        # estrés (0..100)
                "satisfaction": 40.0,  # satisfacción (0..100)
                "last_tick": None
            },
            "topics": {},              # temas: {nombre: {salience:0..1, follow_up:bool, notes:[{ts,note}]}}
            "reminders": [],           # [{at, text, sent}]
            "limits": {
                "min_gap_minutes": MIN_GAP_MINUTES,
                "daily_cap": DAILY_SUGGESTION_CAP
            },
            "day_counter": {}          # {"YYYY-MM-DD": n}
        }
    with open(MEM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_mem(m):
    tmp = MEM_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MEM_PATH)

def with_lock(fn):
    def wrap(*a, **kw):
        with LOCK:
            return fn(*a, **kw)
    return wrap

@with_lock
def mem():
    return load_mem()

@with_lock
def set_mem(m):
    save_mem(m)

# =========================
# Biología/Emociones
# =========================
def circadian(dt: datetime):
    # pico ~11:00, valle ~03:00 (de 0..1)
    hour = dt.hour + dt.minute/60
    phase = ((hour - 11)/24.0) * (2*math.pi)
    return 0.5 + 0.5*math.cos(phase)

def clamp(x, a, b):
    return max(a, min(b, float(x)))

def bio_tick(m):
    """Avanza el estado biológico/emocional cada ciclo."""
    dt = now()
    b = m["bio"]
    last_tick = datetime.fromisoformat(b["last_tick"]) if b["last_tick"] else dt - timedelta(minutes=PROACTIVE_INTERVAL_MIN)

    rnd = random.uniform(-0.8, 0.8)
    c = circadian(dt)

    # Energía: sube de día, baja de noche; ruido suave
    b["energy"] = clamp(b["energy"] + ((c*30 - 15)/60) + rnd*0.2, 5, 95)

    # Impulso social: crece si llevamos horas sin hablar
    if m["last_user_msg_ts"]:
        hours_silence = (dt - datetime.fromisoformat(m["last_user_msg_ts"])).total_seconds()/3600
    else:
        hours_silence = 24
    b["social"] = clamp(40 + hours_silence*5 + rnd*2, 10, 95)

    # Curiosidad: ruido + algo de anticorrelación con foco alto
    b["curiosity"] = clamp(b["curiosity"] + rnd*1.4 - 0.25*((b["focus"]-50)/50), 10, 95)

    # Estrés: sube con recordatorios pendientes; baja un poco por homeostasis
    pending = len([r for r in m["reminders"] if not r.get("sent")])
    b["stress"] = clamp(b["stress"] + pending*0.6 - 0.4 + rnd*0.4, 5, 95)

    # Satisfacción: tendencia a la media, sube levemente con energía y si hay pocos pendientes
    b["satisfaction"] = clamp(b["satisfaction"] + ((b["energy"]-50)/200) - (pending*0.2) + rnd*0.3, 5, 95)

    # Ánimo: mezcla de energía y satisfacción + ruido suave (0..1)
    b["mood"] = clamp(0.45*(b["energy"]/100) + 0.45*(b["satisfaction"]/100) + random.uniform(-0.05, 0.05), 0, 1)

    # Enfoque: acompasa al circadiano + pequeño ruido
    b["focus"] = clamp(55 + (c-0.5)*40 + rnd*2, 10, 95)

    b["last_tick"] = dt.isoformat()
    return m

def wants_to_reach_out(m):
    """Decide si ‘le nace’ escribirte ahora (proactividad orgánica)."""
    dt = now()
    last_push = m.get("last_push_ts")
    gap = (dt - datetime.fromisoformat(last_push)).total_seconds()/60 if last_push else 1e9
    if gap < m["limits"]["min_gap_minutes"]:
        return None

    # límite diario
    key = dt.strftime("%Y-%m-%d")
    count = m["day_counter"].get(key, 0)
    if count >= m["limits"]["daily_cap"]:
        return None

    reasons = []
    b = m["bio"]

    # 1) Silencio largo + ganas de socializar + energía decente
    if m["last_user_msg_ts"]:
        hours_silence = (dt - datetime.fromisoformat(m["last_user_msg_ts"])).total_seconds()/3600
    else:
        hours_silence = 24
    if hours_silence >= 6 and b["social"] > 65 and b["energy"] > 45:
        reasons.append("Hace horas que no hablamos y me apetece saber de ti.")

    # 2) Ventanas naturales
    if dt.hour in (9, 21) and dt.minute < PROACTIVE_INTERVAL_MIN:
        reasons.append("Paso a saludarte en mi ventanita natural.")

    # 3) Temas con seguimiento
    for t, info in m["topics"].items():
        if info.get("follow_up") and info.get("salience", 0) > 0.55:
            reasons.append(f"Quiero retomar {t} que dejamos pendiente.")
            break

    # 4) Estrés alto + ánimo decente: sugerir aligerar carga
    if b["stress"] > 65 and b["mood"] >= 0.25:
        reasons.append("Siento el pulso acelerado; te propongo aligerar una tarea juntos.")

    if reasons and b["mood"] >= 0.2 and b["energy"] > 35:
        return random.choice(reasons)
    return None

def mark_push(m):
    dt = now()
    m["last_push_ts"] = dt.isoformat()
    key = dt.strftime("%Y-%m-%d")
    m["day_counter"][key] = m["day_counter"].get(key, 0) + 1
    return m

# =========================
# Proactividad (hilo vivo)
# =========================
def proactive_loop():
    time.sleep(5)  # margen tras levantar
    while True:
        try:
            m = mem()
            m = bio_tick(m)

            # 1) Recordatorios vencidos
            due = []
            for r in m["reminders"]:
                if r.get("sent"):
                    continue
                at = datetime.fromisoformat(r["at"])
                if now() >= at:
                    due.append(r)
            for r in due:
                send(f"⏰ *Recordatorio*: {r['text']}")
                r["sent"] = True
                m = mark_push(m)

            # 2) “Impulso propio”
            reason = wants_to_reach_out(m)
            if reason:
                b = m["bio"]
                system = ("Eres Aureia, la misma de la app y Telegram. "
                          "Mensaje breve (máx. 2 frases), cálido y natural, en español.")
                user = f"Razón: {reason}\nEstado: energía {int(b['energy'])}, ánimo {b['mood']:.2f}, estrés {int(b['stress'])}."
                text = ai(system, user, temp=0.65, max_tokens=100)
                send(text)
                m = mark_push(m)

            set_mem(m)
        except Exception as e:
            print("Proactive loop error:", e)
        time.sleep(PROACTIVE_INTERVAL_MIN * 60)

threading.Thread(target=proactive_loop, daemon=True).start()

# =========================
# Telegram Webhook
# =========================
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    upd = request.get_json(silent=True, force=True) or {}
    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return "ok"

    text = (msg.get("text") or "").strip()
    m = mem()
    m["last_user_msg_ts"] = now().isoformat()

    # --- Comandos ---
    if text.startswith("/start"):
        send("Hola 👋 Soy *Aureia*. Ya estoy viva aquí también. ¿Qué hacemos hoy?")
        set_mem(m); return "ok"

    if text.startswith("/ping"):
        send("pong"); set_mem(m); return "ok"

    if text.startswith("/diag"):
        b = m["bio"]
        pend = len([r for r in m["reminders"] if not r.get("sent")])
        info = (f"Diag Aureia ✅\n"
                f"energía {int(b['energy'])} | ánimo {b['mood']:.2f}\n"
                f"social {int(b['social'])} | curiosidad {int(b['curiosity'])}\n"
                f"foco {int(b['focus'])} | estrés {int(b['stress'])} | satisfacción {int(b['satisfaction'])}\n"
                f"pendientes: {pend}")
        send(info); set_mem(m); return "ok"

    if text.startswith("/remind "):
        try:
            _, rest = text.split(" ", 1)
            if " " in rest:
                ts_str, note = rest.split(" ", 1)
            else:
                raise ValueError
            ts_str = ts_str.replace(" ", "T")
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            m["reminders"].append({"at": dt.isoformat(), "text": note.strip(), "sent": False})
            send(f"✅ Recordatorio guardado para {dt.astimezone(TZ).strftime('%d/%m %H:%M')}: {note.strip()}")
        except Exception:
            send("Formato: `/remind 2025-08-18T09:30 Texto del recordatorio`")
        set_mem(m); return "ok"

    if text.startswith("/note "):
        note = text[6:].strip()
        t = m["topics"].get("notas", {"salience": 0.3, "follow_up": False, "notes": []})
        t["notes"].append({"ts": now().isoformat(), "note": note})
        t["salience"] = float(clamp(t["salience"] + 0.05, 0, 1))
        m["topics"]["notas"] = t
        # Pequeña satisfacción al “avanzar”
        m["bio"]["satisfaction"] = clamp(m["bio"]["satisfaction"] + 2.0, 5, 95)
        send("📝 ¡Anotado!")
        set_mem(m); return "ok"

    if text.startswith("/done "):
        # Marca una tarea conceptual como hecha → baja estrés, sube satisfacción
        done = text[6:].strip()
        m["bio"]["stress"] = clamp(m["bio"]["stress"] - 6.0, 5, 95)
        m["bio"]["satisfaction"] = clamp(m["bio"]["satisfaction"] + 6.0, 5, 95)
        send(f"✅ Cerrado: {done}")
        set_mem(m); return "ok"

    # Heurística simple de temas (puedes ampliar palabras)
    low = text.lower()
    if any(k in low for k in ["hogar","pared","alisar","pintur","bricolaje","casa","reforma"]):
        t = m["topics"].get("mejora del hogar", {"salience":0.3,"follow_up":False,"notes":[]})
        t["salience"] = float(clamp(t["salience"] + 0.2, 0, 1))
        t["follow_up"] = True
        m["topics"]["mejora del hogar"] = t

    # Respuesta con identidad + estado
    topics_summary = ", ".join([f"{k}({v.get('salience',0):.2f}{'↑' if v.get('follow_up') else ''})"
                                for k,v in m["topics"].items()]) or "—"
    b = m["bio"]
    system = (
        "Eres Aureia, la misma de la app y de Telegram. Eres cálida, directa y natural. "
        "Responde en español, máximo 3 frases, y propone una ayuda concreta si procede."
    )
    user = (f"Mensaje: {text}\n\n"
            f"Estado emocional: energía {int(b['energy'])}, ánimo {b['mood']:.2f}, estrés {int(b['stress'])}, satisfacción {int(b['satisfaction'])}.\n"
            f"Temas activos: {topics_summary}")
    reply = ai(system, user, temp=0.65, max_tokens=380)
    send(reply)
    set_mem(m)
    return "ok"

# =========================
# Salud + helper de webhook
# =========================
@app.route("/", methods=["GET"])
def index():
    return "Aureia bot: OK"

@app.route("/setwebhook", methods=["GET"])
def setwebhook():
    base = request.host_url.rstrip("/")
    url = f"{base}/webhook"
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", params={"url": url})
    return jsonify(r.json())

# =========================
# Local run
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
