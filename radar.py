
import requests
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato.")
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=15,
        )
        response.raise_for_status()
        print("Notifica Telegram inviata.")
    except requests.RequestException as error:
        print(f"Errore Telegram: {error}")
MIN_LIQUIDITY_USD = 25000
DEXSCREENER_URL = "https://api.dexscreener.com/token-profiles/latest/v1"


def main():
    print("Meme Pump Radar AI")
    print("Ricerca delle meme coin Solana...")

    try:
        response = requests.get(DEXSCREENER_URL, timeout=20)
        response.raise_for_status()
        tokens = response.json()

        found = 0

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

            pair_response = requests.get(pair_url, timeout=20)
            pair_response.raise_for_status()
            pairs = pair_response.json()

            for pair in pairs:
                liquidity = pair.get("liquidity") or {}
                liquidity_usd = liquidity.get("usd") or 0

                if liquidity_usd < MIN_LIQUIDITY_USD:
                    continue

                print(
                    f"Token: {pair.get('baseToken', {}).get('name', 'N/D')} | "
                    f"Simbolo: {pair.get('baseToken', {}).get('symbol', 'N/D')} | "
                    f"Liquidità: ${liquidity_usd:,.0f} | "
                    f"Prezzo: ${pair.get('priceUsd', 'N/D')} | "
                    f"URL: {pair.get('url', '')}"
                )
                found += 1

        print(f"\nCoppie trovate: {found}")

    except requests.RequestException as error:
        print(f"Errore di connessione: {error}")


if __name__ == "__main__":
    main()
