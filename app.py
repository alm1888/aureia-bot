import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,  # ¡en v20 es en minúsculas!
)

# ---- Handlers de Telegram ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("¡Hola! Soy Aureia_bot y ya estoy viva aquí 💜")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Responde con lo mismo que escribas (para probar que está vivo)
    await update.message.reply_text(update.message.text)

def make_bot_app() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Falta la variable TELEGRAM_BOT_TOKEN en Railway → Variables.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    return app

# ---- Mini servidor web para que Railway vea un puerto abierto ----
web = Flask(__name__)

@web.get("/")
def home():
    return "Aureia_bot OK"

def run_polling():
    bot_app = make_bot_app()
    # Ejecutamos el bot en modo polling (simple y robusto en Railway)
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Lanzamos el bot en un hilo
    threading.Thread(target=run_polling, daemon=True).start()

    # Servidor web para el health check de Railway
    port = int(os.getenv("PORT", 8080))
    web.run(host="0.0.0.0", port=port)
