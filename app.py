import os
import json
from flask import Flask, request, jsonify
import requests
from openai import OpenAI

app = Flask(__name__)

# --- Carga segura de variables (sin espacios accidentales) ---
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()

# Cliente OpenAI (permitimos que esté vacío, lo detectamos en /test)
def get_openai_client():
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None, "Falta OPENAI_API_KEY"
    try:
        client = OpenAI(api_key=key)
        return client, None
    except Exception as e:
        return None, f"Error creando cliente OpenAI: {e}"

# --- Rutas de utilidad ---

@app.route("/", methods=["GET"])
def home():
    return "Aureia bot: OK"

@app.route("/ping", methods=["GET"])
def ping():
    return "pong"

@app.route("/test", methods=["GET"])
def test_openai():
    """
    Endpoint de diagnóstico:
    - Comprueba que existe OPENAI_API_KEY y su formato
    - Intenta una llamada mínima a OpenAI y devuelve el resultado o el error
    """
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    info = {
        "has_key": bool(key),
        "key_prefix": key[:8] if key else None,      # p.ej. "sk-proj-"
        "key_len": len(key) if key else 0
    }

    client, err = get_openai_client()
    if err:
        return jsonify({"ok": False, "stage": "client_init", "info": info, "error": err}), 500

    try:
        # llamada mínima y barata
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Di 'ok' y nada más."}],
            max_tokens=3,
        )
        text = resp.choices[0].message.content
        return jsonify({"ok": True, "info": info, "openai_reply": text})
    except Exception as e:
        # Devolvemos el error exacto que manda OpenAI
        return jsonify({"ok": False, "stage": "openai_call", "info": info, "error": str(e)}), 500

# --- Webhook de Telegram ---

def reply_telegram(chat_id: int, text: str):
    if not BOT_TOKEN:
        app.logger.error("Falta BOT_TOKEN")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True})  # ignoramos otros updates

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if text == "/ping":
        reply_telegram(chat_id, "pong")
        return jsonify({"ok": True})

    # Aquí pedimos a OpenAI
    client, err = get_openai_client()
    if err:
        reply_telegram(chat_id, "Ahora mismo no puedo pensar 😅. (No hay OPENAI_API_KEY)")
        app.logger.error(err)
        return jsonify({"ok": False, "error": err}), 500

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres Aureia, una asistente simpática y breve."},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
        )
        answer = resp.choices[0].message.content
        reply_telegram(chat_id, answer)
        return jsonify({"ok": True})
    except Exception as e:
        # Si OpenAI falla, devolvemos el motivo y mostramos fallback al usuario
        app.logger.error(f"OpenAI error: {e}")
        reply_telegram(chat_id, "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato.")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Sólo para ejecución local; en Railway se usa Gunicorn (Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
