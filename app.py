import os
import logging
import requests
from flask import Flask, request

# --- Config básica ---
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Falta la variable BOT_TOKEN en Railway")

SEND_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

@app.get("/")
def health():
    return "Aureia bot OK"

# ⚠️ Esta ruta DEBE coincidir con tu webhook de Telegram
@app.post("/webhook")
def telegram_webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        app.logger.info(f"Update: {data}")

        # Soportar message o edited_message
        msg = data.get("message") or data.get("edited_message") or {}
        chat = (msg.get("chat") or {})
        chat_id = chat.get("id")
        text = msg.get("text", "")

        if not chat_id:
            return "no chat", 200

        reply = f"Estoy viva 💫. Me dijiste: {text or '(sin texto)'}"
        requests.post(SEND_URL, json={"chat_id": chat_id, "text": reply})
        return "ok", 200
    except Exception as e:
        app.logger.exception("Error en webhook")
        return "err", 200  # Telegram solo necesita 200 para no reintentar en bucle

if __name__ == "__main__":
    # Para correr local si quieres, Railway usará Gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
