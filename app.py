import os
import json
import requests
from flask import Flask, request, jsonify

# ====== Config ======
BOT_TOKEN = os.environ["BOT_TOKEN"]                 # En Railway: BOT_TOKEN
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]       # En Railway: OPENAI_API_KEY
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPENAI_URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-4o-mini"   # puedes usar gpt-4o-mini o el que prefieras

app = Flask(__name__)

# ====== Utilidades ======
def tg_send_text(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        app.logger.exception(f"Error enviando a Telegram: {e}")

def ask_openai(prompt: str) -> str:
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "input": [
                    {"role": "system", "content": "Eres Aureia, amable y breve."},
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        # La API de Responses devuelve el texto en output_text
        return data.get("output_text") or "No pude generar respuesta."
    except requests.HTTPError as e:
        app.logger.error(f"OpenAI HTTP {e.response.status_code}: {e.response.text}")
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."
    except Exception as e:
        app.logger.exception(f"Error llamando a OpenAI: {e}")
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

# ====== Rutas ======
@app.get("/")
def root():
    return "OK", 200

@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}
    app.logger.debug(f"Update: {json.dumps(update, ensure_ascii=False)}")

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return jsonify(ok=True)

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    # Comandos
    if text.lower() in ("/start", "start/"):
        tg_send_text(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.")
        return jsonify(ok=True)

    if text.lower() == "/ping":
        tg_send_text(chat_id, "pong")
        return jsonify(ok=True)

    # Conversación con OpenAI
    if text:
        reply = ask_openai(text)
        tg_send_text(chat_id, reply)

    return jsonify(ok=True)

if __name__ == "__main__":
    # Para entornos locales; en Railway arranca Gunicorn vía Procfile
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
