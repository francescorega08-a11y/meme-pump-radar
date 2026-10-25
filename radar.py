
import os
import requests

MIN_LIQUIDITY_USD = 25000
DEXSCREENER_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato.")
        return

    url = (
        "https://api.telegram.org/bot"
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


def main():
    print("Meme Pump Radar AI")
    print("Ricerca delle meme coin Solana...")

    try:
        response = requests.get(DEXSCREENER_URL, timeout=20)
        response.raise_for_status()
        tokens = response.json()

        found = 0
        alerted = set()

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
                pair_response = requests.get(pair_url, timeout=20)
                pair_response.raise_for_status()
                pairs = pair_response.json()

            except requests.RequestException as error:
                print(f"Errore nel recupero delle coppie: {error}")
                continue

            if not isinstance(pairs, list):
                continue

            for pair in pairs:
                liquidity = pair.get("liquidity") or {}
                liquidity_usd = liquidity.get("usd") or 0

                if liquidity_usd < MIN_LIQUIDITY_USD:
                    continue

                base_token = pair.get("baseToken") or {}
                name = base_token.get("name", "N/D")
                symbol = base_token.get("symbol", "N/D")
                price = pair.get("priceUsd", "N/D")
                pair_url = pair.get("url", "")

                print(
                    f"Token: {name} | "
                    f"Simbolo: {symbol} | "
                    f"Liquidità: ${liquidity_usd:,.0f} | "
                    f"Prezzo: ${price} | "
                    f"URL: {pair_url}"
                )

                found += 1

                pair_id = pair.get("pairAddress") or pair_url

                if pair_id and pair_id not in alerted:
                    message = (
                        "Meme Pump Radar AI\n"
                        f"Token: {name}\n"
                        f"Simbolo: {symbol}\n"
                        f"Liquidità: ${liquidity_usd:,.0f}\n"
                        f"Prezzo: ${price}\n"
                        f"Link: {pair_url}"
                    )

                    send_telegram(message)
                    alerted.add(pair_id)

        print(f"\nCoppie trovate: {found}")

        if found == 0:
            print(
                "Nessuna coppia trovata con liquidità "
                "minima di 25.000 USD."
            )

    except requests.RequestException as error:
        print(f"Errore di connessione DexScreener: {error}")


if __name__ == "__main__":
    main()
