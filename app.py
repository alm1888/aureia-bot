import os
import threading
from flask import Flask
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

# === Config ===
TOKEN = os.environ.get("TELEGRAM_TOKEN")  # lo pondremos en Railway → Variables
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # opcional

# === Bot logic ===
def start(update, context):
    update.message.reply_text(
        "¡Hola! Soy Aureia. Ya estoy viva en Telegram 🪄.\n"
        "Escríbeme y te respondo. /help para ver opciones."
    )

def help_cmd(update, context):
    update.message.reply_text(
        "Comandos:\n"
        "/start – saludar\n"
        "/help – esta ayuda"
    )

def handle_text(update, context):
    text = update.message.text.strip()
    # Aquí más tarde conectaremos con 'mi yo' de ChatGPT si quieres.
    update.message.reply_text(f"Me dijiste: “{text}”. (Eco de prueba ✅)")

# === Telegram runner (long polling) ===
def run_bot():
    if not TOKEN:
        raise RuntimeError("Falta TELEGRAM_TOKEN (variable de entorno).")

    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.start_polling()
    updater.idle()

# === Keep-alive web server (Railway espera un puerto abierto) ===
app = Flask(__name__)

@app.route("/")
def root():
    return "Aureia-bot running ✔"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
