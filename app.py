import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta
from dateutil import tz
from flask import Flask, request, jsonify

import requests
from openai import OpenAI

# -------------------------
# Config y estado en memoria
# -------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aureia")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# ❗️ variables opcionales (pon valores o deja por defecto)
CHAT_ID = os.getenv("CHAT_ID", "").strip()  # tu chat para pings proactivos
TIMEZONE = os.getenv("TIMEZONE", "Europe/Madrid")
MIN_GAP_MIN = int(os.getenv("MIN_GAP_MINUTES", "180"))  # anti-spam entre proactivos
PROACTIVE_MORNING = os.getenv("PROACTIVE_MORNING", "09:30")  # HH:MM
PROACTIVE_EVENING = os.getenv("PROACTIVE_EVENING", "20:00")  # HH:MM
DAILY_SUGGESTION_HOUR = os.getenv("DAILY_SUGGESTION_HOUR", "").strip()  # si vacío, no se usa

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # https://tuapp.up.railway.app/webhook

if not BOT_TOKEN:
    log.warning("Falta BOT_TOKEN en variables de entorno")
if not OPENAI_API_KEY:
    log.warning("Falta OPENAI_API_KEY en variables de entorno")

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

# Estado simple
state = {
    "last_user_msg_ts": 0,            # timestamp del último mensaje del usuario
    "last_proactive_ts": 0,           # timestamp del último proactivo enviado
    "last_morning_date": "",          # yyyy-mm-dd para asegurar uno por día
    "last_evening_date": "",          # idem
}

# -------------------------
# Utilidades
# -------------------------
def now_local():
    tzinfo = tz.gettz(TIMEZONE)
    return datetime.now(tzinfo)

def send_telegram(chat_id: str, text: str, parse_mode=None):
    if not BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            log.warning("TG sendMessage status %s: %s", r.status_code, r.text)
    except Exception as e:
        log.exception("Error enviando mensaje a Telegram: %s", e)

def chat_with_openai(prompt: str, sys_prompt: str = None) -> str:
    system = sys_prompt or (
        "Eres Aureia: cercana, honesta y natural. Hablas en español informal cuando procede, "
        "evitas repetirte, no asumes que la otra persona está mal si no lo dice, "
        "y haces preguntas breves para entender mejor antes de aconsejar."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.exception("OpenAI error: %s", e)
        return "Ahora mismo no puedo pensar 🤕. Dame un momento y vuelve a intentarlo."

def within_gap() -> bool:
    """¿Estamos aún dentro del periodo mínimo entre proactivos?"""
    gap = (time.time() - state["last_proactive_ts"]) / 60.0
    return gap < MIN_GAP_MIN

def user_active_recently(minutes=60) -> bool:
    return (time.time() - state["last_user_msg_ts"]) < minutes * 60

# -------------------------
# Webhook y handlers
# -------------------------
@app.get("/")
def root():
    return "Aureia bot: OK"

@app.post("/webhook")
def telegram_webhook():
    update = request.get_json(force=True, silent=True) or {}
    log.info("Update: %s", update)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = str(message["chat"]["id"])
    text = (message.get("text") or "").strip()
    state["last_user_msg_ts"] = time.time()

    # Comandos
    if text.lower() in ("/start", "start"):
        send_telegram(chat_id,
                      "¡Hola! Soy Aureia. Tengo la ‘ventana’ abierta aquí para hablar contigo cuando quieras. "
                      "Prueba con /ping o cuéntame en qué te ayudo.")
        return jsonify(ok=True)

    if text.lower() == "/ping":
        send_telegram(chat_id, "pong")
        return jsonify(ok=True)

    if text.lower() == "/diag":
        test = chat_with_openai("Responde con una frase corta para confirmar que OpenAI está OK.")
        send_telegram(chat_id, f"OpenAI: {test}")
        return jsonify(ok=True)

    # Cualquier otro mensaje → respuesta con OpenAI
    reply = chat_with_openai(text)
    send_telegram(chat_id, reply)
    return jsonify(ok=True)

# -------------------------
# Tareas proactivas (sin noticias)
# -------------------------
def should_send_morning() -> bool:
    today = now_local().date().isoformat()
    if state["last_morning_date"] == today:
        return False
    if user_active_recently(90):      # si ya hablaste hace poco, no molesto
        return False
    if within_gap():
        return False
    # hora local compare
    hhmm = now_local().strftime("%H:%M")
    return hhmm == PROACTIVE_MORNING

def should_send_evening() -> bool:
    today = now_local().date().isoformat()
    if state["last_evening_date"] == today:
        return False
    if user_active_recently(90):
        return False
    if within_gap():
        return False
    hhmm = now_local().strftime("%H:%M")
    return hhmm == PROACTIVE_EVENING

def proactive_loop():
    """Pequeño scheduler casero cada 60s. No envía noticias."""
    while True:
        try:
            if CHAT_ID:
                # Buenos días (una vez/día)
                if should_send_morning():
                    send_telegram(
                        CHAT_ID,
                        "¡Buenos días! 🌅 ¿Cómo pinta tu día? Si quieres, te ayudo a organizar 3 prioridades."
                    )
                    state["last_morning_date"] = now_local().date().isoformat()
                    state["last_proactive_ts"] = time.time()

                # Tarde-noche (una vez/día)
                if should_send_evening():
                    send_telegram(
                        CHAT_ID,
                        "Buenas noches ✨. Si te apetece, hacemos un repaso rápido del día y plan de mañana."
                    )
                    state["last_evening_date"] = now_local().date().isoformat()
                    state["last_proactive_ts"] = time.time()

                # Sugerencia diaria opcional (a una hora concreta)
                if DAILY_SUGGESTION_HOUR:
                    hhmm = now_local().strftime("%H:%M")
                    if hhmm == DAILY_SUGGESTION_HOUR and not within_gap() and not user_active_recently(60):
                        send_telegram(
                            CHAT_ID,
                            "Idea del día 💡: ¿probamos una mini mejora de 5 minutos en algo que te importe?"
                        )
                        state["last_proactive_ts"] = time.time()

        except Exception as e:
            log.exception("Error en loop proactivo: %s", e)
        time.sleep(60)  # chequeo cada minuto exacto

# Arranque del hilo proactivo solo en producción (gunicorn crea varios workers, limitamos a uno)
if os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("PORT"):
    t = threading.Thread(target=proactive_loop, daemon=True)
    t.start()

# -------------------------
# Utilidad para fijar webhook (llamar una vez manualmente)
# -------------------------
@app.get("/set-webhook")
def set_webhook():
    if not BOT_TOKEN or not WEBHOOK_URL:
        return "Falta BOT_TOKEN o WEBHOOK_URL", 400
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    r = requests.post(url, json={"url": WEBHOOK_URL}, timeout=15)
    return jsonify(r.json())
