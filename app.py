# app.py
import os
import json
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

# ========= Config =========
BOT_TOKEN = os.environ["BOT_TOKEN"]               # en Railway
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]     # en Railway

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
app = Flask(__name__)

# Cliente OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# ========= Utilidades Telegram =========
def send_message(chat_id: int, text: str, reply_to: int | None = None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
        if r.status_code != 200:
            print("TELEGRAM ERROR >>>", r.status_code, r.text)
    except Exception as e:
        print("TELEGRAM EXCEPTION >>>", repr(e))

# ========= OpenAI =========
def generar_respuesta(texto_usuario: str) -> str:
    """Llama al modelo y devuelve texto. Loguea cualquier error."""
    try:
        r = client.responses.create(
            model="gpt-4o-mini",
            input=f"Eres Aureia, una asistente amable. Responde en español. Usuario: {texto_usuario}"
        )
        return r.output_text.strip()
    except Exception as e:
        # ESTE LOG ES CLAVE: míralo en Railway -> Logs
        print("OpenAI ERROR >>>", repr(e))
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

# ========= Rutas =========
@app.get("/")
def root():
    return "OK", 200

@app.post("/webhook")
def webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        msg_id = message.get("message_id")
        text = message.get("text", "")

        if not chat_id or not text:
            return jsonify(ok=True)  # nada que hacer

        # Comandos simples
        if text.strip().lower() == "/ping":
            send_message(chat_id, "pong", reply_to=msg_id)
            return jsonify(ok=True)
        if text.strip().lower().startswith("/start"):
            send_message(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.", reply_to=msg_id)
            return jsonify(ok=True)

        # Respuesta con OpenAI
        respuesta = generar_respuesta(text)
        send_message(chat_id, respuesta, reply_to=msg_id)
        return jsonify(ok=True)

    except Exception as e:
        print("WEBHOOK ERROR >>>", repr(e))
        return jsonify(ok=True)

# ========= Main (para ejecución local) =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
