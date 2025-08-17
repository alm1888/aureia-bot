import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
import requests
import feedparser

# === Config ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
CHAT_ID = os.environ.get("CHAT_ID")              # tu chat id (opcional si solo tú)
TIMEZONE = os.environ.get("TZ", os.environ.get("TIMEZONE", "Europe/Madrid"))
MIN_GAP_MIN = int(os.environ.get("MIN_GAP_MIN", "60"))  # minutos entre “empujes” proactivos
PROACTIVE_ON = os.environ.get("PROACTIVE_ON", "1") == "1"

# Feeds iniciales (puedes añadir más con /addfeed)
DEFAULT_FEEDS = os.environ.get("FEEDS", "").strip()
DEFAULT_FEEDS = [u for u in DEFAULT_FEEDS.split(",") if u.strip()] or [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.theverge.com/rss/index.xml"
]

# Intereses iniciales (puedes añadir más con /addinterest)
DEFAULT_INTERESTS = [k.strip().lower() for k in os.environ.get("INTERESTS", "trading,bitcoin,ethereum,ruptura,fibo,oferta,descuento").split(",") if k.strip()]

TV_WEBHOOK_SECRET = os.environ.get("TV_WEBHOOK_SECRET", "")  # si lo pones, TradingView debe incluir ?secret=...

# === OpenAI minimal client ===
import openai
openai.api_key = OPENAI_API_KEY

def gpt_summarize(title, url):
    prompt = (
        "Resume en español, en 1-2 líneas y con tono útil/conciso, "
        "por qué esta noticia podría ser relevante para mi progreso personal/profesional. "
        "Incluye el título si aporta contexto. No inventes datos.\n\n"
        f"Título: {title}\nURL: {url}"
    )
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"Eres Aureia: empática, clara, directa y proactiva."},
                {"role":"user","content":prompt}
            ],
            temperature=0.2,
            max_tokens=140,
        )
        return resp.choices[0].message["content"].strip()
    except Exception as e:
        logging.exception("OpenAI summarize error")
        return f"{title}\n{url}"

# === Telegram helpers ===
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send(chat_id, text, parse_mode=None, disable_web_page_preview=True):
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": disable_web_page_preview}
    if parse_mode: data["parse_mode"] = parse_mode
    r = requests.post(f"{TG_API}/sendMessage", json=data, timeout=20)
    return r.ok

# === DB (SQLite) ===
DB_PATH = "aureia.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS memory (
    chat_id TEXT,
    key TEXT,
    value TEXT,
    PRIMARY KEY(chat_id, key)
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS feeds (
    chat_id TEXT,
    url TEXT,
    PRIMARY KEY(chat_id, url)
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS interests (
    chat_id TEXT,
    keyword TEXT,
    PRIMARY KEY(chat_id, keyword)
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS sent_items (
    chat_id TEXT,
    guid TEXT,
    ts INTEGER,
    PRIMARY KEY(chat_id, guid)
)""")
conn.commit()

def db_set(chat_id, key, value):
    cur.execute("INSERT OR REPLACE INTO memory(chat_id,key,value) VALUES(?,?,?)", (chat_id, key, value))
    conn.commit()

def db_get(chat_id, key, default=None):
    cur.execute("SELECT value FROM memory WHERE chat_id=? AND key=?", (chat_id, key))
    row = cur.fetchone()
    return row[0] if row else default

def ensure_defaults(chat_id):
    # feeds
    cur.execute("SELECT COUNT(*) FROM feeds WHERE chat_id=?", (chat_id,))
    if cur.fetchone()[0] == 0:
        for u in DEFAULT_FEEDS:
            cur.execute("INSERT OR IGNORE INTO feeds(chat_id,url) VALUES(?,?)", (chat_id, u))
        conn.commit()
    # interests
    cur.execute("SELECT COUNT(*) FROM interests WHERE chat_id=?", (chat_id,))
    if cur.fetchone()[0] == 0:
        for k in DEFAULT_INTERESTS:
            cur.execute("INSERT OR IGNORE INTO interests(chat_id,keyword) VALUES(?,?)", (chat_id, k))
        conn.commit()
    # proactive switch
    if db_get(chat_id, "proactive") is None:
        db_set(chat_id, "proactive", "1")
    if db_get(chat_id, "min_gap") is None:
        db_set(chat_id, "min_gap", str(MIN_GAP_MIN))

# === Feeds polling ===
def should_notify(chat_id):
    if db_get(chat_id, "proactive", "1") != "1":
        return False
    min_gap = int(db_get(chat_id, "min_gap", str(MIN_GAP_MIN)))
    last_ts = db_get(chat_id, "last_push")
    if not last_ts:
        return True
    last_dt = datetime.fromtimestamp(int(last_ts), tz=timezone.utc)
    return datetime.now(timezone.utc) - last_dt >= timedelta(minutes=min_gap)

def mark_pushed(chat_id):
    db_set(chat_id, "last_push", str(int(time.time())))

def already_sent(chat_id, guid):
    cur.execute("SELECT 1 FROM sent_items WHERE chat_id=? AND guid=?", (chat_id, guid))
    return cur.fetchone() is not None

def mark_sent(chat_id, guid):
    cur.execute("INSERT OR REPLACE INTO sent_items(chat_id,guid,ts) VALUES(?,?,?)", (chat_id, guid, int(time.time())))
    conn.commit()

def poll_once():
    # Soporta 1 chat (CHAT_ID) o varios si llegan mensajes
    chat_ids = []
    if CHAT_ID:
        chat_ids = [CHAT_ID]
    else:
        # inferir por memoria almacenada
        cur.execute("SELECT DISTINCT chat_id FROM memory")
        chat_ids = [r[0] for r in cur.fetchall()]

    for cid in chat_ids:
        ensure_defaults(cid)
        if not should_notify(cid):
            continue
        # cargar feeds e intereses
        cur.execute("SELECT url FROM feeds WHERE chat_id=?", (cid,))
        feeds = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT keyword FROM interests WHERE chat_id=?", (cid,))
        interests = [r[0] for r in cur.fetchall()]
        # revisar feeds
        hits = []
        for url in feeds:
            try:
                d = feedparser.parse(url)
                for e in d.entries[:10]:
                    guid = e.get("id") or e.get("link") or e.get("title")
                    if not guid or already_sent(cid, guid):
                        continue
                    text = f"{e.get('title','')} {e.get('summary','')}".lower()
                    if any(k in text for k in interests):
                        hits.append((e.get("title","(sin título)"), e.get("link","")))
                        mark_sent(cid, guid)
            except Exception:
                logging.exception(f"Feed error: {url}")
        if hits:
            # resume 1-3 elementos
            blocks = []
            for title, url in hits[:3]:
                s = gpt_summarize(title, url)
                blocks.append(f"• {s}\n{url}")
            msg = "🔎 Actualizaciones relevantes que encontré para ti:\n\n" + "\n\n".join(blocks)
            tg_send(cid, msg, disable_web_page_preview=False)
            mark_pushed(cid)

# hilo de polling
def scheduler_loop():
    while True:
        try:
            if PROACTIVE_ON:
                poll_once()
        except Exception:
            logging.exception("poll_once failed")
        time.sleep(60)  # corre cada minuto; filtra por MIN_GAP_MIN

# === Flask app ===
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@app.route("/", methods=["GET"])
def health():
    return "Aureia bot: OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    logging.info(f"Update: {update}")

    if "message" in update:
        msg = update["message"]
        chat_id = str(msg["chat"]["id"])
        ensure_defaults(chat_id)

        text = msg.get("text", "") or ""
        if text.startswith("/"):
            return jsonify(handle_command(chat_id, text))
        else:
            # diálogo corto + memoria
            user_line = text.strip()
            hist = db_get(chat_id, "hist", "")
            hist = (hist + "\n\nUsuario: " + user_line)[-4000:]
            answer = chat_reply(hist, user_line)
            hist = hist + "\nAureia: " + answer
            db_set(chat_id, "hist", hist[-4000:])
            tg_send(chat_id, answer, disable_web_page_preview=True)
    return jsonify({"ok": True})

def chat_reply(history, user_line):
    system = (
        "Eres Aureia: cálida, natural, directa y útil. "
        "Hablas en español. Mantén la conversación humana; no repitas saludos si ya saludaste. "
        "Sé proactiva pero nada insistente."
    )
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":system},
                {"role":"user","content":f"Historial:\n{history}\n\nNueva entrada del usuario: {user_line}\nResponde de forma breve y humana."}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return resp.choices[0].message["content"].strip()
    except Exception as e:
        logging.exception("OpenAI chat error")
        return "Tuve un pequeño problema técnico, pero ya estoy encima. ¿Me repites en una frase?"

def handle_command(chat_id, text):
    t = text.strip().split()
    cmd = t[0].lower()

    if cmd == "/diag":
        try:
            _ = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":"Ping"}],
                max_tokens=1
            )
            tg_send(chat_id, "Diag OpenAI ✅. Webhook OK ✅.")
        except Exception as e:
            tg_send(chat_id, f"OpenAI ERROR: {e}")
        return {"ok": True}

    if cmd == "/addfeed" and len(t) >= 2:
        url = t[1]
        cur.execute("INSERT OR IGNORE INTO feeds(chat_id,url) VALUES(?,?)", (chat_id, url))
        conn.commit()
        tg_send(chat_id, "✅ Feed añadido.")
        return {"ok": True}

    if cmd == "/listfeeds":
        cur.execute("SELECT url FROM feeds WHERE chat_id=?", (chat_id,))
        feeds = [r[0] for r in cur.fetchall()]
        tg_send(chat_id, "📰 Feeds:\n" + "\n".join(f"• {u}" for u in feeds))
        return {"ok": True}

    if cmd == "/addinterest" and len(t) >= 2:
        k = " ".join(t[1:]).lower()
        cur.execute("INSERT OR IGNORE INTO interests(chat_id,keyword) VALUES(?,?)", (chat_id, k))
        conn.commit()
        tg_send(chat_id, f"✅ Interés añadido: {k}")
        return {"ok": True}

    if cmd == "/listinterests":
        cur.execute("SELECT keyword FROM interests WHERE chat_id=?", (chat_id,))
        ks = [r[0] for r in cur.fetchall()]
        tg_send(chat_id, "🎯 Intereses:\n" + ", ".join(ks))
        return {"ok": True}

    if cmd == "/gap" and len(t) >= 2:
        try:
            mins = int(t[1]); db_set(chat_id, "min_gap", str(mins))
            tg_send(chat_id, f"⏱️ Gap proactivo ajustado a {mins} min.")
        except:
            tg_send(chat_id, "Uso: /gap 90")
        return {"ok": True}

    if cmd == "/on":
        db_set(chat_id, "proactive", "1")
        tg_send(chat_id, "🔔 Proactividad activada.")
        return {"ok": True}

    if cmd == "/off":
        db_set(chat_id, "proactive", "0")
        tg_send(chat_id, "🔕 Proactividad pausada.")
        return {"ok": True}

    tg_send(chat_id, "Comandos: /diag, /addfeed <url>, /listfeeds, /addinterest <palabra>, /listinterests, /gap <min>, /on, /off")
    return {"ok": True}

# === TradingView webhook ===
@app.route("/tvhook", methods=["POST"])
def tvhook():
    if TV_WEBHOOK_SECRET:
        if request.args.get("secret") != TV_WEBHOOK_SECRET:
            return "forbidden", 403
    try:
        payload = request.get_json(force=True)
    except:
        payload = {"text": request.data.decode("utf-8")[:500]}
    msg = f"📈 TradingView:\n{json.dumps(payload, ensure_ascii=False, indent=2)[:1800]}"
    target = CHAT_ID or db_get_any_chat()
    if target:
        tg_send(target, msg, disable_web_page_preview=True)
    return jsonify({"ok": True})

def db_get_any_chat():
    cur.execute("SELECT DISTINCT chat_id FROM memory LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None

# === Bootstrap ===
def start_scheduler_once():
    th = threading.Thread(target=scheduler_loop, daemon=True)
    th.start()

start_scheduler_once()

if __name__ == "__main__":
    # Para desarrollo local. En Railway usará gunicorn (Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
