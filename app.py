import os
import logging
from flask import Flask, request, jsonify
import requests

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  # opcional si luego llamas a OpenAI

if not BOT_TOKEN:
    raise RuntimeError("Falta la variable de entorno BOT_TOKEN")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------- Flask ----------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


@app.get("/")
def health():
    return "Aureia bot: OK"


def tg_send_message(chat_id: int, text: str):
    try:
        resp = requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        if not resp.ok:
            app.logger.error("Telegram sendMessage error: %s", resp.text)
    except Exception as e:
        app.logger.exception("Error enviando mensaje a Telegram: %s", e)


@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    app.logger.info("Update: %s", update)

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify(ok=True)  # no hay mensaje que atender

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    # Comandos básicos
    if text == "/start":
        tg_send_message(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.")
        return jsonify(ok=True)

    if text == "/ping":
        tg_send_message(chat_id, "pong")
        return jsonify(ok=True)

    # Aquí podrías integrar OpenAI si quieres (captura errores para no romper el bot)
    try:
        # -- ejemplo placeholder sin OpenAI --
        reply = f"Me dijiste: {text}"
        tg_send_message(chat_id, reply)
    except Exception as e:
        app.logger.exception("Error generando respuesta: %s", e)
        tg_send_message(chat_id, "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato.")

    return jsonify(ok=True)


if __name__ == "__main__":
    # Para desarrollo local; en Railway arrancará con Gunicorn (Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
