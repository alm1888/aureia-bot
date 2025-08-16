import os
import logging
from flask import Flask, request, jsonify
import requests
from openai import OpenAI

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Falta la variable de entorno BOT_TOKEN")
if not OPENAI_API_KEY:
    logging.warning("Falta OPENAI_API_KEY: responderé sin IA.")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Cliente OpenAI (solo si hay API key)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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


def aureia_reply(user_text: str) -> str:
    """
    Genera respuesta con OpenAI; si falla o no hay API key, usa fallback.
    """
    # Fallback simple
    fallback = f"Me dijiste: {user_text}"

    if not client:
        return fallback

    try:
        # Modelo ligero y barato para chat
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Aureia, una asistente cálida y concisa que responde en español. "
                        "Sé amable y directa. Si el usuario te saluda, respóndele con cercanía."
                    ),
                },
                {"role": "user", "content": user_text},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return text or fallback
    except Exception as e:
        app.logger.exception("Error OpenAI: %s", e)
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."


@app.post("/webhook")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    app.logger.info("Update: %s", update)

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify(ok=True)

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    # Comandos
    if text == "/start":
        tg_send_message(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.")
        return jsonify(ok=True)

    if text == "/ping":
        tg_send_message(chat_id, "pong")
        return jsonify(ok=True)

    # Respuesta IA o fallback
    reply = aureia_reply(text)
    tg_send_message(chat_id, reply)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
