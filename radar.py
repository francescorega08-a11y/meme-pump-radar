
import os
import time
import logging
import requests

# ============================================================
# MEME PUMP RADAR AI — SOLANA
# FILTRI ORIGINALI: NON MODIFICATI
# ============================================================

MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 5_000
MIN_VOLUME_1H_USD = 30_000
MIN_CHANGE_5M_PCT = 2.0
MAX_CHANGE_5M_PCT = 12.0
MIN_CHANGE_1H_PCT = 3.0
MIN_BUY_SELL_RATIO = 1.3

MAX_ALERTS_PER_RUN = 10
REQUEST_DELAY_SECONDS = 0.3
TIMEOUT = 20
DIAGNOSTIC_PAIRS = 15

DEX_API = "https://api.dexscreener.com"
TELEGRAM_API = "https://api.telegram.org"

# Query utilizzate per trovare nuove coppie.
SEARCH_TERMS = [
    "meme",
    "pump",
    "BONK",
    "WIF",
    "POPCAT",
    "BOME",
    "FARTCOIN",
    "PEPE",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "MemePumpRadarAI/1.0",
    "Accept": "application/json",
})


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logging.error(
            "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancante."
        )
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    try:
        response = session.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        if not response.json().get("ok"):
            logging.error("Telegram ha rifiutato il messaggio.")
            return False

        logging.info("Notifica Telegram inviata.")
        return True

    except requests.RequestException as exc:
        logging.error("Errore Telegram: %s", exc)
        return False


# ============================================================
# RICHIESTE API
# ============================================================

def api_get(url, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    except (requests.RequestException, ValueError) as exc:
        logging.warning("Errore API %s: %s", url, exc)
        return None


# ============================================================
# RACCOLTA COPPIE SOLANA
# ============================================================

def discover_pairs():
    pairs_by_address = {}

    for term in SEARCH_TERMS:
        data = api_get(
            f"{DEX_API}/latest/dex/search",
            params={"q": term},
        )

        if not isinstance(data, dict):
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        for pair in data.get("pairs") or []:
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            address = pair.get("pairAddress")
            if address:
                pairs_by_address[address] = pair

        time.sleep(REQUEST_DELAY_SECONDS)

    logging.info(
        "Coppie Solana uniche individuate: %s",
        len(pairs_by_address),
    )

    return list(pairs_by_address.values())


# ============================================================
# ESTRAZIONE DATI E FILTRI
# ============================================================

def safe_number(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze_pair(pair):
    liquidity = safe_number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    volume_5m = safe_number(volume.get("m5"))
    volume_1h = safe_number(volume.get("h1"))

    changes = pair.get("priceChange") or {}
    change_5m = safe_number(changes.get("m5"))
    change_1h = safe_number(changes.get("h1"))

    transactions = pair.get("txns") or {}
    txns_5m = transactions.get("m5") or {}

    buys = safe_number(txns_5m.get("buys"))
    sells = safe_number(txns_5m.get("sells"))

    # Se non ci sono acquisti, il rapporto non supera il filtro.
    # Con zero vendite e almeno un acquisto, il rapporto è infinito.
    if buys <= 0:
        buy_sell_ratio = 0.0
    elif sells <= 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = buys / sells

    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    return {
        "name": base.get("name") or "Sconosciuto",
        "symbol": base.get("symbol") or "N/A",
        "address": base.get("address") or "",
        "pair_address": pair.get("pairAddress") or "",
        "dex": pair.get("dexId") or "N/A",
        "url": pair.get("url") or "",
        "quote": quote.get("symbol") or "",
        "price": safe_number(pair.get("priceUsd")),
        "liquidity": liquidity,
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "change_5m": change_5m,
        "change_1h": change_1h,
        "buys": int(buys),
        "sells": int(sells),
        "ratio": buy_sell_ratio,
    }


def passes_filters(coin):
    return (
        coin["liquidity"] >= MIN_LIQUIDITY_USD
        and coin["volume_5m"] >= MIN_VOLUME_5M_USD
        and coin["volume_1h"] >= MIN_VOLUME_1H_USD
        and MIN_CHANGE_5M_PCT <= coin["change_5m"]
        <= MAX_CHANGE_5M_PCT
        and coin["change_1h"] >= MIN_CHANGE_1H_PCT
        and coin["ratio"] >= MIN_BUY_SELL_RATIO
    )


# ============================================================
# DIAGNOSTICA
# ============================================================

def print_diagnostics(coins):
    logging.info(
        "========== DIAGNOSTICA: PRIME %s COPPIE ==========",
        DIAGNOSTIC_PAIRS,
    )

    for coin in coins[:DIAGNOSTIC_PAIRS]:
        logging.info(
            "%s (%s) | Liquidità $%.0f | Vol 5m $%.0f "
            "| Vol 1h $%.0f | Cambio 5m %.2f%% "
            "| Cambio 1h %.2f%% | Buy %d / Sell %d",
            coin["name"],
            coin["symbol"],
            coin["liquidity"],
            coin["volume_5m"],
            coin["volume_1h"],
            coin["change_5m"],
            coin["change_1h"],
            coin["buys"],
            coin["sells"],
        )


# ============================================================
# ALERT TELEGRAM
# ============================================================

def format_alert(coin):
    ratio_text = (
        "∞"
        if coin["ratio"] == float("inf")
        else f'{coin["ratio"]:.2f}'
    )

    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f'🪙 {coin["name"]} ({coin["symbol"]})\n'
        f'💵 Prezzo: ${coin["price"]:.10f}\n'
        f'💧 Liquidità: ${coin["liquidity"]:,.0f}\n'
        f'📊 Volume 5m: ${coin["volume_5m"]:,.0f}\n'
        f'📊 Volume 1h: ${coin["volume_1h"]:,.0f}\n'
        f'📈 Variazione 5m: +{coin["change_5m"]:.2f}%\n'
        f'📈 Variazione 1h: +{coin["change_1h"]:.2f}%\n'
        f'🟢 Acquisti 5m: {coin["buys"]}\n'
        f'🔴 Vendite 5m: {coin["sells"]}\n'
        f'⚖️ Rapporto buy/sell: {ratio_text}\n'
        f'🏦 DEX: {coin["dex"]}\n\n'
        f'🔗 Coppia: {coin["url"]}\n'
        f'📋 Token: {coin["address"]}\n\n'
        "⚠️ Segnale automatico, non garanzia di rialzo."
    )


# ============================================================
# SCANSIONE PRINCIPALE
# ============================================================

def main():
    logging.info("Avvio Meme Pump Radar AI.")

    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        logging.error("Secret TELEGRAM_BOT_TOKEN mancante.")
        return

    if not os.getenv("TELEGRAM_CHAT_ID"):
        logging.error("Secret TELEGRAM_CHAT_ID mancante.")
        return

    pairs = discover_pairs()

    if not pairs:
        logging.warning(
            "Nessuna coppia trovata. Controllare la risposta API."
        )
        return

    coins = [analyze_pair(pair) for pair in pairs]

    # Diagnostica delle prime 15 coppie individuate.
    print_diagnostics(coins)

    # Ordina per liquidità, poi per volume a 5 minuti.
    eligible = [
        coin for coin in coins
        if passes_filters(coin)
    ]

    eligible.sort(
        key=lambda coin: (
            coin["volume_5m"],
            coin["liquidity"],
        ),
        reverse=True,
    )

    selected = eligible[:MAX_ALERTS_PER_RUN]

    logging.info("Coppie analizzate: %d", len(coins))
    logging.info("Coppie che superano i filtri: %d", len(eligible))
    logging.info("Alert selezionati: %d", len(selected))

    if not selected:
        logging.info("Nessuna coin supera tutti i filtri.")
        return

    sent = 0

    for coin in selected:
        if telegram_send(format_alert(coin)):
            sent += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    logging.info(
        "Scansione terminata. Alert Telegram inviati: %d/%d",
        sent,
        len(selected),
    )


if __name__ == "__main__":
    main()
