import os
import logging
from flask import Flask, request, abort

from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

# --- OpenAI (SDK nuevo) ---
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    openai_client = None

# --- Configuración básica ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Falta la variable de entorno BOT_TOKEN")

bot = Bot(BOT_TOKEN)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# --- Handlers de Telegram ---
def start(update: Update, context):
    update.message.reply_text("Hola 👋 Soy Aureia. Envíame un mensaje.")

def ping(update: Update, context):
    update.message.reply_text("pong")

def chat_handler(update: Update, context):
    """Responde usando OpenAI si está disponible; si no, mensaje de cortesía."""
    text = (update.message.text or "").strip()
    if not text:
        return

    # Evita bucle con comandos
    if text.startswith("/"):
        return

    try:
        if openai_client is None:
            raise RuntimeError("OpenAI client no inicializado")

        completion = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres Aureia, un bot amable y conciso."},
                {"role": "user", "content": text},
            ],
            temperature=0.7,
        )
        reply = completion.choices[0].message.content.strip()
        if not reply:
            reply = "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."
        update.message.reply_text(reply)
    except Exception as e:
        logger.exception("Error llamando a OpenAI: %s", e)
        update.message.reply_text("Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato.")

# Dispatcher global (modo webhook)
dispatcher = Dispatcher(bot=bot, update_queue=None, workers=0, use_context=True)
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("ping", ping))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, chat_handler))

# --- Rutas Flask ---
@app.route("/", methods=["GET"])
def index():
    return "Aureia bot: OK"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        logger.info("Update: %s", request.get_data(as_text=True))
        dispatcher.process_update(update)
    except Exception as e:
        logger.exception("Error procesando update: %s", e)
        abort(400)
    return "OK", 200

# Opción: auto-configurar webhook si se define APP_BASE_URL
# p.ej. APP_BASE_URL=https://web-production-xxxx.up.railway.app
@app.before_first_request
def maybe_set_webhook():
    base_url = os.environ.get("APP_BASE_URL")
    if not base_url:
        return
    try:
        target = f"{base_url.rstrip('/')}/webhook"
        bot.set_webhook(url=target, allowed_updates=["message"])
        logger.info("Webhook configurado en: %s", target)
    except Exception as e:
        logger.exception("No se pudo configurar el webhook automáticamente: %s", e)

if __name__ == "__main__":
    # Solo para pruebas locales. En Railway se usa gunicorn (Procfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
