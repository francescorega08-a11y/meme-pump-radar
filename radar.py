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
    alerted_ids = {p.get("address") for p in alerted if isinstance(p, dict)}

    high_liquidity_count = 0
    new_alerts_count = 0

    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue

        pair_address = pair.get("pairAddress")
        if not pair_address:
            continue

        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        
        # Conteggio coppie con liquidità >= $25.000 per i log
        if liquidity >= 25000:
            high_liquidity_count += 1

        if pair_address in alerted_ids:
            continue

        volume_5m = float(pair.get("volume", {}).get("m5", 0) or 0)
        change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
        change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        
        token_name = pair.get("baseToken", {}).get("name", "N/D")
        token_symbol = pair.get("baseToken", {}).get("symbol", "N/D")
        token_address = pair.get("baseToken", {}).get("address", pair_address)
        price_usd = pair.get("priceUsd", "0")
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{pair_address}")

        # Controllo social ufficiali
        info = pair.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        social_status = "🌐 Social Trovati" if (socials or websites) else "⚠️ Nessun Social Ufficiale"
        twitter_search_url = f"https://twitter.com/search?q={token_address}&f=live"

        # Filtri di momentum + Blocco Anti-Pump
        if (liquidity >= MIN_LIQUIDITY and 
            volume_5m >= MIN_VOLUME_5M and 
            change_5m >= MIN_CHANGE_5M and 
            change_1h <= MAX_CHANGE_1H):

            timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            msg = (
                f"🚨 <b>ALLERTA MEME COIN (Solana)</b> 🚨\n\n"
                f"<b>Token:</b> {token_name} ({token_symbol})\n"
                f"<b>Prezzo:</b> ${price_usd}\n"
                f"<b>Liquidità:</b> ${liquidity:,.0f}\n"
                f"<b>Vol 5m:</b> ${volume_5m:,.0f}\n"
                f"<b>Var 5m:</b> +{change_5m:.2f}% | <b>Var 1h:</b> +{change_1h:.2f}%\n"
                f"<b>Stato:</b> {social_status}\n\n"
                f"🔗 <a href='{dex_url}'>Grafico Dexscreener</a>\n"
                f"🐦 <a href='{twitter_search_url}'>Cerca su X (Ultime)</a>"
            )

            if send_telegram_message(msg):
                new_alerts_count += 1

            item = {
                "name": token_name,
                "symbol": token_symbol,
                "address": token_address,
                "liquidity": liquidity,
                "volume": volume_5m,
                "change_5m": change_5m,
                "change_1h": change_1h,
                "price": price_usd,
                "dex_url": dex_url,
                "twitter_url": twitter_search_url,
                "social_status": social_status,
                "timestamp": timestamp_str
            }

            alerted.insert(0, item)
            alerted_ids.add(pair_address)

    # Salvataggio e stampa del riepilogo nei log di GitHub
    save_alerted_pairs(alerted[:50])

    print(f"Coppie con liquidità >= $25.000: {high_liquidity_count}")
    print(f"Nuovi alert inviati: {new_alerts_count}")
    print(f"Coppie già segnalate memorizzate: {len(alerted)}")

if __name__ == "__main__":
    check_radar()
