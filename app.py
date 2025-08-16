import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MODEL = "gpt-4o-mini"

def send_telegram_message(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception:
        pass

def ask_openai(prompt):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
    r = requests.post(url, headers=headers, json=data, timeout=30)
    if r.status_code == 401:
        # Clave inválida: deja un mensaje claro en logs y en chat
        raise RuntimeError("OPENAI 401 - API key inválida (revisa OPENAI_API_KEY)")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify(ok=True)
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text == "/start":
        send_telegram_message(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.")
        return jsonify(ok=True)
    if text == "/ping":
        send_telegram_message(chat_id, "pong")
        return jsonify(ok=True)

    try:
        answer = ask_openai(text if text else "Saluda en español de forma breve.")
    except Exception as e:
        send_telegram_message(chat_id, "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato.")
        # Log útil
        print("ERROR ask_openai:", repr(e))
        return jsonify(ok=True)

    send_telegram_message(chat_id, answer)
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
