import os
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# --- Tokens de entorno ---
TOKEN = os.environ["BOT_TOKEN"]  # ya lo tienes en Railway
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]  # lo añadirás ahora

# --- Mini servidor keep-alive ---
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- OpenAI (SDK v1) ---
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola 👋 Soy Aureia. Envíame un mensaje.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

async def chat_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde con IA a cualquier texto que no sea comando."""
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    def _ask():
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system",
                 "content": "Eres Aureia, una asistente breve, amable y útil. Responde en español de forma natural."},
                {"role": "user", "content": user_text}
            ],
            temperature=0.7,
            max_tokens=300
        )

    try:
        resp = await asyncio.to_thread(_ask)  # ejecuta la llamada bloqueante en un hilo
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        answer = "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

    await update.message.reply_text(answer)

def main():
    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))

    # Mensajes normales -> IA (excluye comandos)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_ai))

    # Keep alive + polling
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
