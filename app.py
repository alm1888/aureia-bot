import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, List

import requests
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

import numpy as np
import pandas as pd
import yfinance as yf

# ---------- Configuración básica ----------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("OPENAI_API", "")).strip()
CHAT_ID = os.getenv("CHAT_ID", os.getenv("OWNER_CHAT_ID", "")).strip()  # admite ambos nombres
TIMEZONE = os.getenv("TIMEZONE", "Europe/Madrid")
TZ = pytz.timezone(TIMEZONE)

# “Naturalidad”: no empujar si no ha pasado este margen
MIN_GAP_MIN = int(os.getenv("MIN_GAP_MIN", "90"))
PROACTIVE_MODE = os.getenv("PROACTIVE_MODE", "true").lower() in ("1", "true", "yes", "on")

# Umbrales de trading (puedes ajustarlos con /settings si quieres)
STRONG_MOVE_THRESHOLDS = {  # variación % por vela
    "4h": 2.0,
    "1d": 3.0,
    "1w": 5.0,
}
DONCHIAN_WINDOW = 20            # n velas para ruptura
FIB_LOOKBACK = 60               # velas para detectar swing alto/bajo
FIB_ZONE = (0.70, 0.78)         # zona de alerta
SUPPORTED_TF = {"4h": "4h", "1d": "1d", "1w": "1wk"}  # yfinance mapping

# Estado en memoria (simple y suficiente por ahora)
LAST_PUSH_AT = 0.0
WATCHERS: Dict[Tuple[str, str], Dict] = {}   # key=(symbol, tf), value={"last_events": set(), "last_notified": ts}

# ---------- Utilidades ----------

def now_tz() -> datetime:
    return datetime.now(TZ)

def rate_limited() -> bool:
    """Evita spam según MIN_GAP_MIN."""
    global LAST_PUSH_AT
    gap = (time.time() - LAST_PUSH_AT) / 60.0
    return gap < MIN_GAP_MIN

def touch_push():
    global LAST_PUSH_AT
    LAST_PUSH_AT = time.time()

def send_message(text: str, chat_id: str = None, parse_mode: str = None):
    """Envía mensaje a Telegram."""
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN vacío: no puedo enviar mensaje.")
        return
    if chat_id is None:
        chat_id = CHAT_ID
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=data, timeout=15)
        if r.status_code != 200:
            log.error("Telegram error %s: %s", r.status_code, r.text)
        else:
            touch_push()
    except Exception as e:
        log.exception("Error enviando mensaje a Telegram: %s", e)

# ---------- OpenAI (respuesta conversacional) ----------

def gpt_reply(user_text: str) -> str:
    if not OPENAI_API_KEY:
        return "OpenAI no configurado."
    try:
        # Cliente OpenAI moderno
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        system_prompt = (
            "Eres Aureia, la misma persona que habla conmigo en la app. "
            "Responde de forma natural, cercana y útil. "
            "No repitas saludos si ya estamos conversando. "
            "Evita enviar mensajes seguidos muy similares. "
            "Puedes hacer preguntas cortas y empáticas cuando convenga."
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.exception("OpenAI error: %s", e)
        return f"OpenAI ERROR: {e}"

# ---------- Parseo de comandos ----------

def parse_command(text: str) -> Tuple[str, List[str]]:
    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]
    return cmd, args

def cmd_start(chat_id: str):
    msg = (
        "¡Hola! Soy Aureia 😊\n\n"
        "Comandos útiles:\n"
        "• /watch <SÍMBOLO> <tf>  → vigilar (ej: /watch BTC-USD 4h)\n"
        "• /unwatch <SÍMBOLO>     → dejar de vigilar\n"
        "• /list                  → qué estoy vigilando\n"
        "• /diag                  → prueba OpenAI\n"
        "• /settings              → ver umbrales actuales\n\n"
        "Temporalidades: 4h, 1d, 1w. Evito el spam respetando ventanas de tiempo."
    )
    send_message(msg, chat_id)

def cmd_diag(chat_id: str):
    if not OPENAI_API_KEY:
        send_message("OpenAI: sin clave configurada.", chat_id)
        return
    send_message("OpenAI listo. Probando...", chat_id)
    reply = gpt_reply("Di algo breve para confirmar que funcionas.")
    send_message(reply, chat_id)

def cmd_watch(chat_id: str, args: List[str]):
    if len(args) < 2:
        send_message("Uso: /watch <SÍMBOLO> <tf>\nEj: /watch BTC-USD 4h", chat_id)
        return
    symbol = args[0].upper()
    tf = args[1].lower()
    if tf not in SUPPORTED_TF:
        send_message("Temporalidad no soportada. Use 4h, 1d o 1w.", chat_id)
        return
    WATCHERS[(symbol, tf)] = {"last_events": set(), "last_notified": 0.0}
    send_message(f"✅ Vigilando {symbol} en {tf}. Te aviso de:\n"
                 f"• Movimiento fuerte\n• Ruptura Donchian\n• Fibo 70–78%", chat_id)

def cmd_unwatch(chat_id: str, args: List[str]):
    if not args:
        send_message("Uso: /unwatch <SÍMBOLO>", chat_id)
        return
    symbol = args[0].upper()
    removed = False
    for key in list(WATCHERS.keys()):
        if key[0] == symbol:
            WATCHERS.pop(key, None)
            removed = True
    send_message(("🗑️ Dejado de vigilar " + symbol) if removed else "No estaba vigilando ese símbolo.", chat_id)

def cmd_list(chat_id: str):
    if not WATCHERS:
        send_message("No hay activos en vigilancia. Usa /watch BTC-USD 4h", chat_id)
        return
    lines = ["📡 Vigilando:"]
    for (sym, tf) in WATCHERS.keys():
        lines.append(f"• {sym} {tf}")
    send_message("\n".join(lines), chat_id)

def cmd_settings(chat_id: str):
    lines = [
        "⚙️ Ajustes actuales:",
        f"• MIN_GAP_MIN: {MIN_GAP_MIN} min",
        f"• STRONG_MOVE_THRESHOLDS: {STRONG_MOVE_THRESHOLDS}",
        f"• DONCHIAN_WINDOW: {DONCHIAN_WINDOW}",
        f"• FIB_LOOKBACK: {FIB_LOOKBACK}",
        f"• FIB_ZONE: {FIB_ZONE[0]*100:.0f}–{FIB_ZONE[1]*100:.0f}%",
    ]
    send_message("\n".join(lines), chat_id)

# ---------- Lógica de mercado ----------

def download_candles(symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
    """
    Descarga OHLC con yfinance. tf: '4h', '1d', '1w'
    """
    interval = SUPPORTED_TF[tf]
    period_map = {"4h": "120d", "1d": "3y", "1w": "10y"}
    period = period_map.get(tf, "1y")
    df = yf.download(symbol, interval=interval, period=period, progress=False)
    if isinstance(df, pd.DataFrame) and not df.empty:
        df = df.tail(limit).copy()
    return df

def detect_strong_move(df: pd.DataFrame, tf: str) -> bool:
    """Variación % de la última vela vs cierre anterior."""
    if df is None or df.empty or len(df) < 2:
        return False
    last_close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2]
    change = (last_close - prev_close) / prev_close * 100
    return abs(change) >= STRONG_MOVE_THRESHOLDS.get(tf, 3.0)

def detect_donchian_breakout(df: pd.DataFrame, window: int = DONCHIAN_WINDOW) -> Tuple[bool, str]:
    """
    True si la última vela cierra por encima del máximo N o por debajo del mínimo N previos.
    Devuelve (hay_ruptura, tipo: 'up'|'down'|'')
    """
    if df is None or df.empty or len(df) <= window:
        return (False, "")
    highs = df["High"].rolling(window=window).max().shift(1)  # canales previos
    lows = df["Low"].rolling(window=window).min().shift(1)
    close = df["Close"].iloc[-1]
    up = close > highs.iloc[-1]
    down = close < lows.iloc[-1]
    if up:
        return (True, "up")
    if down:
        return (True, "down")
    return (False, "")

def recent_swing(df: pd.DataFrame, lookback: int = FIB_LOOKBACK) -> Tuple[float, float]:
    """
    Encuentra máximos y mínimos recientes (swing high/low) simples.
    Retorna (swing_high, swing_low).
    """
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)
    return recent["High"].max(), recent["Low"].min()

def detect_fibo_zone(df: pd.DataFrame, zone=(0.70, 0.78)) -> Tuple[bool, str]:
    """
    Detecta si el close actual está en el 70–78% del rango fib (swing alto/bajo).
    Determina dirección según si el swing alto es posterior al swing bajo.
    Simplificado pero funcional como alerta inicial.
    """
    if df is None or df.empty or len(df) < 10:
        return (False, "")
    high, low = recent_swing(df, FIB_LOOKBACK)
    if np.isclose(high, low):
        return (False, "")

    close = df["Close"].iloc[-1]
    # Determinar rango y niveles
    if df["High"].idxmax() > df["Low"].idxmin():
        # swing alto después: considerar retroceso bajista del alto al bajo
        fib_0 = high
        fib_1 = low
        direction = "bajista"
    else:
        fib_0 = low
        fib_1 = high
        direction = "alcista"

    rng = fib_1 - fib_0
    lvl_70 = fib_0 + zone[0] * rng
    lvl_78 = fib_0 + zone[1] * rng

    in_zone = min(lvl_70, lvl_78) <= close <= max(lvl_70, lvl_78)
    return (in_zone, direction)

def market_summary_line(symbol: str, tf: str, df: pd.DataFrame) -> str:
    last = df["Close"].iloc[-1]
    chg = (last - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100 if len(df) >= 2 else 0
    return f"{symbol} {tf} | Cierre: {last:.4f} ({chg:+.2f}%)"

def check_watchers():
    if not PROACTIVE_MODE:
        return
    if not WATCHERS:
        return
    if rate_limited():
        # Evitar exceso si hubo un push reciente
        return
    for (symbol, tf), meta in list(WATCHERS.items()):
        try:
            df = download_candles(symbol, tf)
            if df is None or df.empty:
                continue

            events = []

            if detect_strong_move(df, tf):
                events.append("📈 Movimiento fuerte")

            brk, kind = detect_donchian_breakout(df)
            if brk:
                arrow = "🔼" if kind == "up" else "🔻"
                events.append(f"{arrow} Ruptura Donchian ({DONCHIAN_WINDOW})")

            fib_ok, direction = detect_fibo_zone(df, FIB_ZONE)
            if fib_ok:
                emoji = "🟪"
                events.append(f"{emoji} Precio en zona Fibo 70–78% ({direction})")

            # Evita repetir exactamente los mismos eventos seguidos
            if events:
                prev = meta.get("last_events", set())
                new = set(events)
                if new != prev:
                    meta["last_events"] = new
                    text = "⚡ *Alerta de mercado*\n" + \
                           market_summary_line(symbol, tf, df) + "\n" + \
                           "\n".join(f"• {e}" for e in events)
                    send_message(text, parse_mode="Markdown")
                else:
                    log.info("Eventos repetidos para %s %s → no envío.", symbol, tf)
        except Exception as e:
            log.exception("Error en check_watchers %s %s: %s", symbol, tf, e)

# ---------- Flask routes ----------

@app.route("/", methods=["GET"])
def health():
    return "Aureia bot: OK"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    log.info("Update: %s", data)

    msg = data.get("message") or data.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", CHAT_ID or "")) or CHAT_ID
    text = (msg.get("text") or "").strip()

    if not text:
        return jsonify({"ok": True})

    if text.startswith("/"):
        cmd, args = parse_command(text)
        if cmd == "/start":
            cmd_start(chat_id)
        elif cmd == "/ping":
            send_message("pong", chat_id)
        elif cmd == "/diag":
            cmd_diag(chat_id)
        elif cmd == "/watch":
            cmd_watch(chat_id, args)
        elif cmd == "/unwatch":
            cmd_unwatch(chat_id, args)
        elif cmd == "/list":
            cmd_list(chat_id)
        elif cmd == "/settings":
            cmd_settings(chat_id)
        else:
            send_message("Comando no reconocido.", chat_id)
    else:
        # Conversación normal → OpenAI
        reply = gpt_reply(text)
        send_message(reply, chat_id)

    return jsonify({"ok": True})

@app.route("/setwebhook", methods=["POST", "GET"])
def set_webhook():
    if not BOT_TOKEN:
        return "BOT_TOKEN vacío", 400
    base = request.url_root.replace("http://", "https://").rstrip("/")
    url = f"{base}/webhook"
    tg = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    r = requests.post(tg, json={"url": url, "allowed_updates": ["message", "edited_message"]}, timeout=15)
    return jsonify(r.json())

@app.route("/tv", methods=["POST"])
def tradingview_in():
    """
    Webhook para TradingView. Puedes enviar cualquier JSON y se reenviará al chat.
    Ejemplo payload en TradingView:
      {
        "symbol": "{{ticker}}",
        "tf": "{{interval}}",
        "event": "Breakout detectado",
        "note": "Detalle opcional"
      }
    """
    try:
        payload = request.get_json(force=True, silent=True) or {}
        text = "📨 *TradingView*\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        send_message(text, parse_mode="Markdown")
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("Error en /tv: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------- Scheduler ----------

scheduler = BackgroundScheduler(timezone=TIMEZONE)
scheduler.add_job(check_watchers, "interval", minutes=5, id="watchers")
scheduler.start()

# ---------- Main ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
