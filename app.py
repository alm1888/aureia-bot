import os
from flask import Flask, request
import requests
from openai import OpenAI

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

def tg(method: str, data: dict):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    return requests.post(url, json=data, timeout=30).json()

@app.route("/", methods=["GET"])
def home():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True, force=True) or {}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return "no message"

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text == "/start":
        tg("sendMessage", {"chat_id": chat_id,
                           "text": "Hola 👋 Soy Aureia. Envíame un mensaje."})
        return "ok"

    if text == "/ping":
        tg("sendMessage", {"chat_id": chat_id, "text": "pong"})
        return "ok"

    if text:
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system",
                     "content": "Eres Aureia, una IA simpática y breve."},
                    {"role": "user", "content": text},
                ],
                max_tokens=200,
            )
            reply = resp.choices[0].message.content.strip()
        except Exception:
            reply = "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

        tg("sendMessage", {"chat_id": chat_id, "text": reply})
    return "ok"

if __name__ == "__main__":
    # IMPORTANTE: solo para desarrollo local.
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
