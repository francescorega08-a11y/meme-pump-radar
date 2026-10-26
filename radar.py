import os
import requests
import json
from datetime import datetime

# --- CONFIGURAZIONE FILTRI E PARAMETRI ---
MIN_LIQUIDITY = 15000       # Liquidità minima (USD)
MIN_VOLUME_5M = 12000       # Volume minimo 5m (USD)
MIN_CHANGE_5M = 10.0        # Variazione prezzo 5m (%)
MAX_CHANGE_1H = 300.0       # Blocco Anti-Pump (scarta se > +300%)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
JSON_FILE = "alerted_pairs.json"

def load_alerted_pairs():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_alerted_pairs(pairs):
    with open(JSON_FILE, "w") as f:
        json.dump(pairs, f, indent=4)

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token o chat ID non configurati.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("Notifica Telegram inviata.")
            return True
        else:
            print(f"Errore invio Telegram (status {res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"Errore invio Telegram: {e}")
        return False

def check_radar():
    print("Meme Pump Radar AI")
    
    url = "https://api.dexscreener.com/latest/dex/search?q=solana"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception as e:
        print(f"Errore richiesta API: {e}")
        return

    pairs = data.get("pairs", [])
    alerted = load_alerted_pairs()
    alerted_ids = {p.get("address") for p in alerted
