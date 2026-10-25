
import os
import json
import requests
from pathlib import Path

MIN_LIQUIDITY_USD = 25_000
DEXSCREENER_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
STATE_FILE = Path("alerted_pairs.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_alerted():
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_alerted(alerted):
    STATE_FILE.write_text(json.dumps(sorted(alerted), indent=2))


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=15,
        )
        response.raise_for_status()
        print("Notifica Telegram inviata.")
        return True
    except requests.RequestException as error:
        print(f"Errore Telegram: {error}")
        return False


def main():
    print("Meme Pump Radar AI")
    alerted = load_alerted()
    found = 0
    new_alerts = 0

    try:
        response = requests.get(DEXSCREENER_URL, timeout=20)
        response.raise_for_status()
        tokens = response.json()

        for token in tokens:
            if token.get("chainId") != "solana":
                continue

            address = token.get("tokenAddress")
            if not address:
                continue

            pair_url = (
                "https://api.dexscreener.com/token-pairs/v1/solana/"
                + address
            )

            try:
                result = requests.get(pair_url, timeout=20)
                result.raise_for_status()
                pairs = result.json()
            except requests.RequestException as error:
                print(f"Errore coppie: {error}")
                continue

            if not isinstance(pairs, list):
                continue

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                liquidity = (pair.get("liquidity") or {}).get("usd") or 0
                if liquidity < MIN_LIQUIDITY_USD:
                    continue

                pair_id = pair.get("pairAddress")
                if not pair_id:
                    continue

                found += 1

                if pair_id in alerted:
                    continue

                volume = pair.get("volume") or {}
                change = pair.get("priceChange") or {}
                base = pair.get("baseToken") or {}

                volume_5m = float(volume.get("m5") or 0)
                volume_1h = float(volume.get("h1") or 0)
                change_5m = float(change.get("m5") or 0)
                change_1h = float(change.get("h1") or 0)

                volume_signal = volume_5m >= 5_000 or volume_1h >= 20_000
                price_signal = change_5m >= 2 or change_1h >= 3

                if not (volume_signal and price_signal):
                    continue

                name = base.get("name", "N/D")
                symbol = base.get("symbol", "N/D")
                price = pair.get("priceUsd", "N/D")
                url = pair.get("url", "")

                message = (
                    "Meme Pump Radar AI - Segnale preliminare\n"
                    f"Token: {name} ({symbol})\n"
                    f"Liquidità: ${liquidity:,.0f}\n"
                    f"Prezzo: ${price}\n"
                    f"Volume 5m: ${volume_5m:,.0f}\n"
                    f"Volume 1h: ${volume_1h:,.0f}\n"
                    f"Variazione 5m: {change_5m:.2f}%\n"
                    f"Variazione 1h: {change_1h:.2f}%\n"
                    f"Link: {url}"
                )

                if send_telegram(message):
                    alerted.add(pair_id)
                    save_alerted(alerted)
                    new_alerts += 1

        print(f"Coppie con liquidità >= $25.000: {found}")
        print(f"Nuovi alert inviati: {new_alerts}")
        print(f"Coppie già segnalate memorizzate: {len(alerted)}")

    except (requests.RequestException, ValueError, TypeError) as error:
        print(f"Errore durante la ricerca: {error}")


if __name__ == "__main__":
    main()
