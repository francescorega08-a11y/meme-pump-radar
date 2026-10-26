import os
import requests
import json
from datetime import datetime

# --- CONFIGURAZIONE FILTRI REGOLATI PER MAGGIORE REATTIVITÀ ---
MIN_LIQUIDITY = 10000       # Abbassata a $10,000 per catturare l'avvio
MIN_VOLUME_5M = 5000        # Abbassato a $5,000 per rilevare i primi volumi
MIN_CHANGE_5M = 5.0         # Abbassata a +5% per intercettare l'inizio impulso
MAX_CHANGE_1H = 300.0       # Evita i token che hanno già completato il pump

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
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Errore invio Telegram: {e}")

def check_radar():
    print("Scansione segnali precoci e controllo social su Solana...")
    
    url = "https://api.dexscreener.com/latest/dex/search?q=solana"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception as e:
        print(f"Errore nella richiesta API: {e}")
        return

    pairs = data.get("pairs", [])
    alerted = load_alerted_pairs()
    alerted_ids = {p.get("address") for p in alerted if isinstance(p, dict)}

    new_alerts = []

    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue

        pair_address = pair.get("pairAddress")
        if pair_address in alerted_ids:
            continue

        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        volume_5m = float(pair.get("volume", {}).get("m5", 0) or 0)
        volume_1h = float(pair.get("volume", {}).get("h1", 0) or 0)
        change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
        change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        
        token_name = pair.get("baseToken", {}).get("name", "N/D")
        token_symbol = pair.get("baseToken", {}).get("symbol", "N/D")
        token_address = pair.get("baseToken", {}).get("address", pair_address)
        price_usd = pair.get("priceUsd", "0")
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{pair_address}")

        info = pair.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        
        social_status = "🌐 Social Trovati" if (socials or websites) else "⚠️ Nessun Social Ufficiale"
        twitter_search_url = f"https://twitter.com/search?q={token_address}&f=live"

        if (liquidity >= MIN_LIQUIDITY and 
            volume_5m >= MIN_VOLUME_5M and 
            change_5m >= MIN_CHANGE_5M and 
            change_1h <= MAX_CHANGE_1H):

            timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            msg = (
                f"🚨 <b>ALLERTA PRECOCE + SOCIAL CHECK</b> 🚨\n\n"
                f"<b>Token:</b> {token_name} ({token_symbol})\n"
                f"<b>Prezzo:</b> ${price_usd}\n"
                f"<b>Liquidità:</b> ${liquidity:,.0f}\n"
                f"<b>Vol 5m:</b> ${volume_5m:,.0f}\n"
                f"<b>Var 5m:</b> +{change_5m:.2f}% | <b>Var 1h:</b> +{change_1h:.2f}%\n"
                f"<b>Stato:</b> {social_status}\n\n"
                f"🔗 <a href='{dex_url}'>Grafico Dexscreener</a>\n"
                f"🐦 <a href='{twitter_search_url}'>Cerca su X (Ultime)</a>"
            )

            send_telegram_message(msg)

            item = {
                "name": token_name,
                "symbol": token_symbol,
                "address": token_address,
                "liquidity": liquidity,
                "volume": volume_5m,
                "change_5m": change_5m,
                "timestamp": timestamp_str
            }

            new_alerts.append(item)
            alerted.insert(0, item)
            alerted_ids.add(pair_address)

    if new_alerts:
        save_alerted_pairs(alerted[:50])
        print(f"Trovati {len(new_alerts)} nuovi segnali.")
    else:
        print("Nessun segnale rilevato in questo ciclo.")

if __name__ == "__main__":
    check_radar()
