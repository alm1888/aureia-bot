import os
import json
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

# === Variables de entorno requeridas ===
BOT_TOKEN = os.getenv("BOT_TOKEN")                 # token de @BotFather
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")       # key del proyecto en OpenAI
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")   # puedes cambiar el modelo

if not BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Faltan variables BOT_TOKEN u OPENAI_API_KEY.")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# === OpenAI client (SIN proxies) ===
client = OpenAI(api_key=OPENAI_API_KEY)

# === Flask app ===
app = Flask(__name__)

# -------- Utils Telegram --------
def tg_send_message(chat_id: int, text: str, parse_mode: str | None = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        # No reventamos la app si fallan envíos
        print(f"[tg_send_message] error: {e}")

def extract_message(update: dict):
    """
    Devuelve (chat_id, text) o (None, None) si no hay mensaje de texto.
    Soporta mensajes y ediciones.
    """
    obj = update.get("message") or update.get("edited_message") or {}
    chat = obj.get("chat") or {}
    text = obj.get("text")
    if not text:
        return None, None
    return chat.get("id"), text.strip()

# -------- OpenAI persona --------
SYSTEM_PROMPT = (
    "Eres Aureia, la misma asistente con la que hablo en la app. "
    "Eres cercana, clara, respetuosa y natural. Responde en el idioma del usuario "
    "(español si el usuario te escribe en español). No repitas saludos innecesarios "
    "si el usuario ya te está hablando. Evita enviar múltiples mensajes seguidos sin motivo. "
    "Sé directa, útil y con calidez, pero sin sonar robótica ni repetitiva."
)

def ask_openai(user_text: str) -> str:
    """
    Llama a OpenAI Chat Completions para generar respuesta de Aureia.
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0.7,
            max_tokens=600,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"OpenAI ERROR: {e}"

# -------- Rutas --------
@app.get("/")
def home():
    return "Aureia bot: OK"

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.post("/webhook")
def webhook():
    try:
        update = request.get_json(silent=True) or {}
        chat_id, text = extract_message(update)
        if not chat_id or not text:
            return jsonify(ok=True)

        # Comandos útiles
        if text.lower() == "/start":
            tg_send_message(
                chat_id,
                "¡Hola! Soy Aureia. Estoy aquí como tu misma asistente, con el mismo estilo que en la app. "
                "Puedes decirme lo que necesites. Prueba /ping o /diag si quieres."
            )
            return jsonify(ok=True)

        if text.lower() == "/ping":
            tg_send_message(chat_id, "pong")
            return jsonify(ok=True)

        if text.lower() == "/diag":
            tg_send_message(chat_id, "OpenAI listo. Probando…")
            reply = ask_openai("Responde con una frase breve confirmando que puedes hablar por Telegram.")
            tg_send_message(chat_id, reply)
            return jsonify(ok=True)

        # Diálogo normal
        reply = ask_openai(text)
        tg_send_message(chat_id, reply)
        return jsonify(ok=True)

    except Exception as e:
        print(f"[webhook] error: {e}")
        return jsonify(ok=True)

# Para ejecutar local (opcional)
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
