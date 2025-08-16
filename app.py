import os
import json
import time
from flask import Flask, request, jsonify
import requests

# ---------------------------
# Config
# ---------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------------------------
# Flask
# ---------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return "Aureia bot: OK"

@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"ok": True})

# ---------------------------
# Helpers Telegram
# ---------------------------
def tg_send_action(chat_id: int, action: str = "typing"):
    """Opcional: muestra 'escribiendo…'"""
    try:
        requests.post(
            f"{TELEGRAM_API}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] sendChatAction error: {e}")

def tg_send_message(chat_id: int, text: str, reply_to_message_id: int | None = None):
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        r = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
        if not r.ok:
            print(f"[ERROR] sendMessage {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[ERROR] sendMessage exception: {e}")

# ---------------------------
# OpenAI (SDK v1.x)
# ---------------------------
# Usamos chat.completions con un modelo económico. Si prefieres otro, cámbialo aquí.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

def ai_reply(user_text: str) -> str:
    """
    Devuelve la respuesta de OpenAI. Si hay cualquier problema,
    devolvemos un fallback amigable.
    """
    if not OPENAI_API_KEY:
        return "Ahora mismo no puedo pensar 😅. Falta la clave de OpenAI en el servidor."

    try:
        # Cargamos perezosamente para evitar importar si no hay clave
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # Mensajes del chat
        system_prompt = (
            "Eres *Aureia*, una IA amable, cercana y concisa. "
            "Responde en español natural, con empatía y claridad. "
            "Evita respuestas demasiado largas a menos que el usuario lo pida."
        )

        # Llamada al endpoint de chat
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
            max_tokens=400,
        )

        text = resp.choices[0].message.content.strip()
        # Seguridad por si volviese vacío
        return text if text else "Estoy aquí 😊. ¿En qué te ayudo?"
    except Exception as e:
        # Log detallado y fallback
        print(f"[ERROR] OpenAI call: {e}")
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

# ---------------------------
# Webhook de Telegram
# ---------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
        # Log breve
        print("[UPDATE]", json.dumps(update, ensure_ascii=False))

        # Elegimos el campo correcto (message o edited_message)
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})  # ignoramos otros tipos

        chat_id = msg["chat"]["id"]
        message_id = msg.get("message_id")
        text = (msg.get("text") or "").strip()

        if not text:
            tg_send_message(chat_id, "Envíame un mensaje de texto 🙂", message_id)
            return jsonify({"ok": True})

        # Comandos simples locales
        low = text.lower()
        if low.startswith("/start"):
            tg_send_message(chat_id, "Hola 👋 Soy Aureia. Envíame un mensaje.", message_id)
            return jsonify({"ok": True})

        if low.startswith("/ping"):
            tg_send_message(chat_id, "pong", message_id)
            return jsonify({"ok": True})

        # Escribiendo…
        tg_send_action(chat_id, "typing")

        # Llamada a OpenAI
        answer = ai_reply(text)

        tg_send_message(chat_id, answer, message_id)
        return jsonify({"ok": True})

    except Exception as e:
        print(f"[ERROR] webhook handler: {e}")
        # Siempre 200 a Telegram para que no reintente en bucle
        return jsonify({"ok": True})

# ---------------------------
# Local run (no se usa en Railway)
# ---------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
