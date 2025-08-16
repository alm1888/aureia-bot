import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# --- Vars ---
TOKEN = os.environ["BOT_TOKEN"]

# --- Mini servidor keep-alive ---
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# --- Handlers de Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola 👋 Soy Aureia. Envíame un mensaje.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Por si acaso, ignora si empieza con "/"
    if update.message.text.startswith("/"):
        return
    await update.message.reply_text(f"Me dijiste: {update.message.text}")

def main():
    app = Application.builder().token(TOKEN).build()

    # 1) Registra primero los comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))

    # 2) Luego el eco, EXCLUYENDO comandos
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # 3) Arranca Flask en segundo plano y el polling
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
