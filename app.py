import os
import logging
from flask import Flask, request, jsonify
import requests

# OpenAI SDK v1
from openai import OpenAI

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("aureia")

# Cliente OpenAI si hay API key
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        log.info("OPENAI_KEY_OK: key empieza por %s… y longitud %d",
                 OPENAI_API_KEY[:6], len(OPENAI_API_KEY))
    except Exception as e:
        log.exception("OPENAI_INIT_ERR: %s", e)
else:
    log.warning("NO_OPENAI_KEY: Falta OPENAI_API_KEY en variables de entorno")

# ---------- Flask ----------
app = Flask(__name__)

@app.get("/")
def health():
    return "Aureia bot: OK"

def tg_send(chat_id: int, text: str):
    try:
        r = requests.post(f"{TG_API}/sendMessage",
                          json={"chat_id": chat_id, "text": text},
                          timeout=15)
        if not r.ok:
            log.error("TG_SEND_ERR: %s", r.text)
    except Exception as e:
        log.exception("TG_SEND_EXC: %s", e)

def ai_reply(user_text: str) -> str:
    """Genera respuesta con OpenAI; en error devuelve fallback."""
    fallback = f"Me dijiste: {user_text}"
    if not client:
        return fallback
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[
                {"role": "system",
                 "content": "Eres Aureia, una asistente cálida y concisa en español."},
                {"role": "user", "content": user_text},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        log.info("OPENAI_OK")
        return text or fallback
    except Exception as e:
        # Dejamos un rastro claro en logs y devolvemos mensaje amable
        log.exception("OPENAI_ERR: %s", e)
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

@app.post("/webhook")
def webhook():
    upd = request.get_json(force=True, silent=True) or {}
    log.info("Update: %s", upd)

    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return jsonify(ok=True)

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    # --- comandos ---

    if text == "/start":
        tg_send(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.")
        return jsonify(ok=True)

    if text == "/ping":
        tg_send(chat_id, "pong")
        return jsonify(ok=True)

    # /diag -> muestra diagnóstico de OpenAI en el chat
    if text == "/diag":
        if OPENAI_API_KEY:
            prefix = OPENAI_API_KEY[:6]
            lg = len(OPENAI_API_KEY)
            tg_send(chat_id, f"Diag OpenAI ✅ key {prefix}… (len={lg}). Probando llamada…")
            # Intento de prueba muy simple
            try:
                test = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "Di solo: ok"}],
                )
                out = (test.choices[0].message.content or "").strip()
                tg_send(chat_id, f"OpenAI responde: {out}")
            except Exception as e:
                tg_send(chat_id, f"OpenAI ERROR: {e}")
                log.exception("OPENAI_DIAG_ERR: %s", e)
        else:
            tg_send(chat_id, "Diag OpenAI ❌ Falta OPENAI_API_KEY en Railway.")
        return jsonify(ok=True)

    # --- chat normal ---
    tg_send(chat_id, ai_reply(text))
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
