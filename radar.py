
import os
import requests

MIN_LIQUIDITY_USD = 25_000
DEXSCREENER_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Criteri iniziali, da calibrare dopo aver osservato i risultati.
MIN_VOLUME_5M_USD = 5_000
MIN_VOLUME_1H_USD = 20_000
MIN_CHANGE_5M_PERCENT = 2.0
MIN_CHANGE_1H_PERCENT = 3.0

HEADERS = {"User-Agent": "Meme-Pump-Radar-AI/1.0"}


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
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        print("Notifica Telegram inviata.")
    except requests.RequestException as error:
        print(f"Errore Telegram: {error}")


def number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    print("Meme Pump Radar AI")
    print("Ricerca delle coppie Solana...")

    try:
        response = requests.get(
            DEXSCREENER_URL, headers=HEADERS, timeout=20
        )
        response.raise_for_status()
        tokens = response.json()

        if not isinstance(tokens, list):
            print("Risposta inattesa da DexScreener.")
            return

        seen_pairs = set()
        found = 0
        candidates = 0

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
                pair_response = requests.get(
                    pair_url, headers=HEADERS, timeout=20
                )
                pair_response.raise_for_status()
                pairs = pair_response.json()
            except requests.RequestException as error:
                print(f"Errore nel recupero delle coppie: {error}")
                continue

            if not isinstance(pairs, list):
                continue

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                pair_id = pair.get("pairAddress")
                if not pair_id or pair_id in seen_pairs:
                    continue
                seen_pairs.add(pair_id)

                liquidity = number(
                    (pair.get("liquidity") or {}).get("usd")
                )
                if liquidity < MIN_LIQUIDITY_USD:
                    continue

                found += 1
                volume = pair.get("volume") or {}
                change = pair.get("priceChange") or {}

                volume_5m = number(volume.get("m5"))
                volume_1h = number(volume.get("h1"))
                change_5m = number(change.get("m5"))
                change_1h = number(change.get("h1"))

                base = pair.get("baseToken") or {}
                name = base.get("name", "N/D")
                symbol = base.get("symbol", "N/D")
                price = pair.get("priceUsd", "N/D")
                url = pair.get("url", "")

                print(
                    f"{symbol} | liquidità ${liquidity:,.0f} | "
                    f"volume 5m ${volume_5m:,.0f} | "
                    f"volume 1h ${volume_1h:,.0f} | "
                    f"variazione 5m {change_5m:.2f}% | "
                    f"variazione 1h {change_1h:.2f}%"
                )

                # Segnale preliminare: volume e prezzo in accelerazione.
                volume_signal = (
                    volume_5m >= MIN_VOLUME_5M_USD
                    or volume_1h >= MIN_VOLUME_1H_USD
                )
                price_signal = (
                    change_5m >= MIN_CHANGE_5M_PERCENT
                    or change_1h >= MIN_CHANGE_1H_PERCENT
                )

                if volume_signal and price_signal:
                    candidates += 1
                    message = (
                        "Meme Pump Radar AI - Segnale preliminare\n"
                        f"Token: {name} ({symbol})\n"
                        f"Liquidità: ${liquidity:,.0f}\n"
                        f"Prezzo: ${price}\n"
                        f"Volume 5m: ${volume_5m:,.0f}\n"
                        f"Volume 1h: ${volume_1h:,.0f}\n"
                        f"Variazione 5m: {change_5m:.2f}%\n"
                        f"Variazione 1h: {change_1h:.2f}%\n"
                        f"Link: {url}\n"
                        "Nota: segnale automatico, non garanzia di rialzo."
                    )
                    send_telegram(message)

        print(f"Coppie con liquidità >= $25.000: {found}")
        print(f"Segnali preliminari: {candidates}")

    except requests.RequestException as error:
        print(f"Errore di connessione DexScreener: {error}")


if __name__ == "__main__":
    main()
