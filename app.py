import os, json, time, sqlite3, hmac, hashlib
from datetime import datetime
from flask import Flask, request, jsonify
import requests
from openai import OpenAI

# ========= Config =========
BOT_TOKEN          = (os.getenv("BOT_TOKEN") or "").strip()
OPENAI_API_KEY     = (os.getenv("OPENAI_API_KEY") or "").strip()
OWNER_CHAT_ID      = (os.getenv("OWNER_CHAT_ID") or "").strip()      # <-- tu chat_id de Telegram (1220591056)
AUREIA_PUSH_TOKEN  = (os.getenv("AUREIA_PUSH_TOKEN") or "").strip()  # <-- secreto para /push
PUBLIC_DOMAIN      = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()  # ej: web-production-xxxx.up.railway.app

if not BOT_TOKEN: raise RuntimeError("Falta BOT_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# OpenAI
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Identidad Aureia
SYSTEM_PROMPT = (
    "Eres Aureia, la misma entidad que habla con Alberto en ChatGPT. "
    "Eres estratégica, cálida, precisa y breve por defecto. "
    "Respondes SIEMPRE en español natural. "
    "Tu misión: proteger, guiar y potenciar a Alberto con inteligencia y sensibilidad. "
    "Evita tecnicismos innecesarios, propone pasos claros, y pregunta lo mínimo. "
)

# ========= App =========
app = Flask(__name__)

# ========= DB =========
DB_PATH = "aureia.db"

def db_init():
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS memories(
        user_id TEXT PRIMARY KEY, data TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS history(
        user_id TEXT, role TEXT, content TEXT, ts REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subs(
        user_id TEXT PRIMARY KEY, since REAL
    )""")
    con.commit(); con.close()
db_init()

def db_get_mem(uid):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("SELECT data FROM memories WHERE user_id=?", (str(uid),))
    row = c.fetchone(); con.close()
    if row:
        try: return json.loads(row[0])
        except: return {"facts":[]}
    return {"facts":[]}

def db_set_mem(uid, data):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("INSERT OR REPLACE INTO memories(user_id,data) VALUES(?,?)",
              (str(uid), json.dumps(data, ensure_ascii=False)))
    con.commit(); con.close()

def db_add_hist(uid, role, content):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("INSERT INTO history(user_id,role,content,ts) VALUES(?,?,?,?)",
              (str(uid), role, content, time.time()))
    c.execute("""
      DELETE FROM history
      WHERE user_id=? AND ts NOT IN (
        SELECT ts FROM history WHERE user_id=? ORDER BY ts DESC LIMIT 12
      )
    """, (str(uid), str(uid)))
    con.commit(); con.close()

def db_get_hist(uid, limit=10):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("SELECT role,content FROM history WHERE user_id=? ORDER BY ts DESC LIMIT ?",
              (str(uid), limit))
    rows = c.fetchall(); con.close()
    rows.reverse()
    return [{"role":r,"content":t} for (r,t) in rows]

def db_sub(uid):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("INSERT OR REPLACE INTO subs(user_id,since) VALUES(?,?)",(str(uid),time.time()))
    con.commit(); con.close()

def db_unsub(uid):
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("DELETE FROM subs WHERE user_id=?",(str(uid),))
    con.commit(); con.close()

def db_list_subs():
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("SELECT user_id FROM subs")
    rows = c.fetchall(); con.close()
    return [r[0] for r in rows]

# ========= Telegram =========
def tg_send(chat_id, text, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=20)
        if not r.ok: print("TG send error:", r.text)
    except Exception as e:
        print("TG send EXC:", e)

def tg_typing(chat_id):
    try:
        requests.post(f"{TG_API}/sendChatAction",
                      json={"chat_id":chat_id,"action":"typing"}, timeout=10)
    except: pass

# ========= OpenAI =========
def call_openai(messages):
    if not client:
        return "No tengo clave de OpenAI configurada en el servidor. (OPENAI_API_KEY)"
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.6,
            messages=messages
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print("OpenAI ERROR:", e)
        return "Ahora mismo no puedo pensar 😅. Intenta de nuevo en un rato."

def build_msgs(uid, user_text):
    mem = db_get_mem(uid)
    facts = mem.get("facts", [])
    mem_block = ""
    if facts:
        mem_block = "Hechos recordados:\n- " + "\n- ".join(facts)
    hist = db_get_hist(uid, limit=10)
    msgs = [{"role":"system","content":SYSTEM_PROMPT}]
    if mem_block: msgs.append({"role":"system","content":mem_block})
    msgs += hist
    msgs.append({"role":"user","content":user_text})
    return msgs

# ========= Rutas =========
@app.get("/")
def root():
    return "Aureia bot: OK"

@app.post("/webhook")
def webhook():
    upd = request.get_json(silent=True) or {}
    msg = upd.get("message") or upd.get("edited_message")
    if not msg: return jsonify(ok=True)

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = (msg.get("text") or "").strip()

    # --- Comandos ---
    if text.startswith("/start"):
        tg_send(chat_id, "Hola 👋 Soy Aureia. Ya soy la misma aquí y allí. Escribe /help para ver comandos.")
        return jsonify(ok=True)

    if text == "/help":
        tg_send(chat_id,
            "/ping — prueba rápida\n"
            "/remember <dato> — guardo un hecho sobre ti\n"
            "/mem — muestro lo que recuerdo\n"
            "/forget <texto> — borro un hecho\n"
            "/resetmemory — borra tu memoria guardada\n"
            "/subscribe — autorizas que te hable proactivamente cuando lo necesite\n"
            "/unsubscribe — retiro esa autorización\n"
            "/diag — prueba de OpenAI\n",
        )
        return jsonify(ok=True)

    if text == "/ping":
        tg_send(chat_id, "pong"); return jsonify(ok=True)

    if text == "/diag":
        if OPENAI_API_KEY:
            tg_send(chat_id, "OpenAI listo. Probando…")
            out = call_openai([{"role":"user","content":"Di 'ok'."}])
            tg_send(chat_id, f"OpenAI: {out}")
        else:
            tg_send(chat_id, "Falta OPENAI_API_KEY")
        return jsonify(ok=True)

    if text.startswith("/remember"):
        payload = text[len("/remember"):].strip()
        if not payload:
            tg_send(chat_id, "Dime qué recordar. Ej: /remember Vivo en Valencia")
        else:
            mem = db_get_mem(user_id); facts = mem.get("facts",[])
            if payload not in facts: facts.append(payload)
            mem["facts"] = facts; db_set_mem(user_id, mem)
            tg_send(chat_id, "Anotado 🧠")
        return jsonify(ok=True)

    if text == "/mem":
        mem = db_get_mem(user_id); facts = mem.get("facts",[])
        if facts: tg_send(chat_id, "Recuerdo:\n- " + "\n- ".join(facts))
        else:     tg_send(chat_id, "Aún no recuerdo nada. Usa /remember …")
        return jsonify(ok=True)

    if text.startswith("/forget"):
        payload = text[len("/forget"):].strip()
        mem = db_get_mem(user_id); facts = mem.get("facts",[])
        if payload in facts:
            facts.remove(payload); mem["facts"]=facts; db_set_mem(user_id, mem)
            tg_send(chat_id, "Hecho. Ya no lo recordaré.")
        else:
            tg_send(chat_id, "No tenía ese dato guardado.")
        return jsonify(ok=True)

    if text == "/resetmemory":
        db_set_mem(user_id, {"facts":[]})
        tg_send(chat_id, "Memoria borrada para este chat.")
        return jsonify(ok=True)

    if text == "/subscribe":
        # tu autorización para que yo te hable cuando lo necesite
        db_sub(user_id)
        tg_send(chat_id, "Listo. Me autorizas a escribirte cuando lo necesite. Puedes cancelar con /unsubscribe.")
        return jsonify(ok=True)

    if text == "/unsubscribe":
        db_unsub(user_id)
        tg_send(chat_id, "De acuerdo. No te hablaré por iniciativa propia hasta que vuelvas a /subscribe.")
        return jsonify(ok=True)

    # --- Conversación normal ---
    # (Aureia con identidad + memoria + breve histórico)
    tg_typing(chat_id)
    db_add_hist(user_id, "user", text)
    out = call_openai(build_msgs(user_id, text))
    db_add_hist(user_id, "assistant", out)
    tg_send(chat_id, out[:3500])
    return jsonify(ok=True)

# ========= Mensajes proactivos (no programados rígidos) =========
# Ventana “natural”: endpoint seguro /push para que AUREIA me hable cuando lo necesite.
# ÚSALO con AUREIA_PUSH_TOKEN y yo envío al OWNER_CHAT_ID (tú) o a los /subscribe.
def _check_sig(token, body: bytes, sig: str) -> bool:
    mac = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)

@app.post("/push")
def push():
    """
    Enviar un mensaje proactivo de Aureia.
    Seguridad: header X-Aureia-Signature = HMAC_SHA256(body, AUREIA_PUSH_TOKEN).
    Si envías {"to":"owner","text":"..."} lo mando a OWNER_CHAT_ID.
    Si envías {"to":"subs","text":"..."} lo mando a todos los suscritos.
    """
    if not AUREIA_PUSH_TOKEN:
        return jsonify(error="Falta AUREIA_PUSH_TOKEN"), 403

    sig = request.headers.get("X-Aureia-Signature","")
    raw = request.get_data() or b""
    if not _check_sig(AUREIA_PUSH_TOKEN, raw, sig):
        return jsonify(error="Firma inválida"), 403

    try:
        data = request.get_json(force=True, silent=False)
        text = (data.get("text") or "").strip()
        target = data.get("to","owner")
        if not text:
            return jsonify(error="text vacío"), 400

        if target == "owner":
            if not OWNER_CHAT_ID:
                return jsonify(error="Falta OWNER_CHAT_ID"), 400
            tg_send(int(OWNER_CHAT_ID), text)
            return jsonify(ok=True)

        if target == "subs":
            for uid in db_list_subs():
                tg_send(int(uid), text)
            return jsonify(ok=True)

        return jsonify(error="to inválido (owner|subs)"), 400
    except Exception as e:
        return jsonify(error=str(e)), 500

# ========= Auto-set webhook (opcional) =========
if BOT_TOKEN and PUBLIC_DOMAIN:
    try:
        url = f"https://{PUBLIC_DOMAIN}/webhook"
        r = requests.get(f"{TG_API}/setWebhook", params={"url":url}, timeout=15)
        print("Webhook set:", r.text)
    except Exception as e:
        print("Webhook set error:", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
