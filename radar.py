
import os
import json
import time
import logging
from pathlib import Path

import requests

# Meme Pump Radar AI — Solana
# Solo monitoraggio e notifiche; nessun trading automatico.

DEX_API = "https://api.dexscreener.com"
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 5_000
MIN_VOLUME_1H_USD = 20_000
MIN_CHANGE_5M_PCT = 3.0
MIN_CHANGE_1H_PCT = 5.0
MAX_ALERTS_PER_RUN = 10

ALERTS_FILE = Path("alerted_pairs.json")
TIMEOUT = 20

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/1.0"
})


def load_alerted():
    """Carica gli ID delle coppie già segnalate."""
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_alerted(alerted):
    """Salva gli ID delle coppie già segnalate."""
    ALERTS_FILE.write_text(
        json.dumps(sorted(alerted), indent=2),
        encoding="utf-8",
    )


def get_solana_pairs():
    """Recupera coppie Solana tramite la ricerca pubblica DexScreener."""
    queries = ["solana meme", "solana pump", "solana dog", "solana cat"]
    pairs_by_id = {}

    for query in queries:
        url = f"{DEX_API}/latest/dex/search"
        try:
            response = session.get(
                url,
                params={"q": query},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            for pair in data.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                pair_id = pair.get("pairAddress")
                if pair_id:
                    pairs_by_id[pair_id] = pair

            time.sleep(0.3)

        except (requests.RequestException, ValueError) as exc:
            logging.warning("Ricerca %r non riuscita: %s", query, exc)

    return list(pairs_by_id.values())


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def evaluate_pair(pair):
    """Valuta liquidità, volumi e variazioni di prezzo."""
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}

    vol_m5 = number(volume.get("m5"))
    vol_h1 = number(volume.get("h1"))
    change_m5 = number(changes.get("m5"))
    change_h1 = number(changes.get("h1"))

    buys_m5 = number((txns.get("m5") or {}).get("buys"))
    sells_m5 = number((txns.get("m5") or {}).get("sells"))

    if liquidity < MIN_LIQUIDITY_USD:
        return None

    # Esclude coppie con attività insufficiente o senza momentum.
    if vol_m5 < MIN_VOLUME_5M_USD:
        return None

    if vol_h1 < MIN_VOLUME_1H_USD:
        return None

    if change_m5 < MIN_CHANGE_5M_PCT:
        return None

    if change_h1 < MIN_CHANGE_1H_PCT:
        return None

    # Punteggio euristico: non è una probabilità di rialzo.
    score = 0
    score += 1 if change_m5 >= 5 else 0
    score += 1 if change_h1 >= 10 else 0
    score += 1 if vol_m5 >= 15_000 else 0
    score += 1 if buys_m5 > sells_m5 else 0
    score += 1 if vol_h1 >= 50_000 else 0

    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    return {
        "pair_id": pair.get("pairAddress", ""),
        "name": base.get("name", "Sconosciuto"),
        "symbol": base.get("symbol", "?"),
        "price": number(pair.get("priceUsd")),
        "liquidity": liquidity,
        "vol_m5": vol_m5,
        "vol_h1": vol_h1,
        "change_m5": change_m5,
        "change_h1": change_h1,
        "buys_m5": int(buys_m5),
        "sells_m5": int(sells_m5),
        "score": score,
        "url": pair.get("url", ""),
        "quote": quote.get("symbol", ""),
    }


def send_telegram(message):
    """Invia una notifica usando i segreti dell'ambiente."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning(
            "Credenziali Telegram mancanti: alert non inviato."
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    try:
        response = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        if not response.json().get("ok"):
            logging.error("Telegram ha rifiutato il messaggio.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Invio Telegram fallito: %s", exc)
        return False


def format_alert(item):
    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {item['name']} ({item['symbol']})\n"
        f"Prezzo: ${item['price']:.10f}\n"
        f"Liquidità: ${item['liquidity']:,.0f}\n"
        f"Volume 5m: ${item['vol_m5']:,.0f}\n"
        f"Volume 1h: ${item['vol_h1']:,.0f}\n"
        f"Variazione 5m: {item['change_m5']:+.2f}%\n"
        f"Variazione 1h: {item['change_h1']:+.2f}%\n"
        f"Acquisti/vendite 5m: "
        f"{item['buys_m5']}/{item['sells_m5']}\n"
        f"Radar score: {item['score']}/5\n\n"
        f"Grafico: {item['url']}\n\n"
        "⚠️ Segnale sperimentale, non consiglio finanziario."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI")

    alerted = load_alerted()
    pairs = get_solana_pairs()
    logging.info("Coppie Solana recuperate: %d", len(pairs))

    candidates = []

    for pair in pairs:
        item = evaluate_pair(pair)
        if item:
            candidates.append(item)

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["vol_m5"],
            item["liquidity"],
        ),
        reverse=True,
    )

    logging.info("Candidati dopo i filtri: %d", len(candidates))
    newly_alerted = 0

    for item in candidates:
        pair_id = item["pair_id"]

        if not pair_id or pair_id in alerted:
            continue

        if newly_alerted >= MAX_ALERTS_PER_RUN:
            break

        if send_telegram(format_alert(item)):
            alerted.add(pair_id)
            newly_alerted += 1
            logging.info("Alert inviato: %s", item["symbol"])

        time.sleep(0.3)

    save_alerted(alerted)
    logging.info("Esecuzione terminata. Nuovi alert: %d", newly_alerted)


if __name__ == "__main__":
    main()
