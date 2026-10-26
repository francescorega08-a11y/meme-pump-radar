
import os
import json
import time
import logging
from pathlib import Path

import requests

# ==========================================
# MEME PUMP RADAR AI — SOLANA
# Alert individuali Telegram.
# Nessun trading automatico.
# ==========================================

DEX_API = "https://api.dexscreener.com"

# FILTRI PRINCIPALI
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 8_000
MIN_VOLUME_1H_USD = 30_000

MIN_CHANGE_5M_PCT = 2.0
MAX_CHANGE_5M_PCT = 12.0
MIN_CHANGE_1H_PCT = 3.0

MIN_BUY_SELL_RATIO = 1.3

# LIMITI OPERATIVI
MAX_ALERTS_PER_RUN = 10
REQUEST_DELAY_SECONDS = 0.3
TIMEOUT = 20

STATE_FILE = Path("radar_state.json")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/3.0"
})


def number(value, default=0.0):
    try:
        result = float(value)
        if not (result == result):
            return default
        if result in (float("inf"), float("-inf")):
            return default
        return result
    except (TypeError, ValueError, OverflowError):
        return default


def load_state():
    """Carica le coppie già notificate."""
    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        active = data.get("active_pairs", [])

        if not isinstance(active, list):
            return None

        return {
            item for item in active
            if isinstance(item, str)
        }

    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, OSError) as exc:
        logging.error("Errore lettura stato: %s", exc)
        return None


def save_state(active_pairs):
    """Salva lo stato in modo atomico."""
    temp_file = STATE_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            {"active_pairs": sorted(active_pairs)},
            indent=2,
        ),
        encoding="utf-8",
    )
    temp_file.replace(STATE_FILE)


def get_solana_pairs():
    """Cerca coppie Solana su DexScreener."""
    queries = [
        "solana meme",
        "solana pump",
        "solana dog",
        "solana cat",
    ]

    pairs_by_id = {}
    successful_queries = 0

    for query in queries:
        try:
            response = session.get(
                f"{DEX_API}/latest/dex/search",
                params={"q": query},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("pairs")
            if not isinstance(results, list):
                continue

            successful_queries += 1

            for pair in results:
                if not isinstance(pair, dict):
                    continue
                if pair.get("chainId") != "solana":
                    continue

                pair_id = pair.get("pairAddress")
                if pair_id:
                    pairs_by_id[pair_id] = pair

        except (requests.RequestException, ValueError) as exc:
            logging.warning(
                "Ricerca %s fallita: %s", query, exc
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    if successful_queries == 0:
        return None

    return list(pairs_by_id.values())


def evaluate_pair(pair):
    """Applica filtri di mercato e calcola un punteggio."""
    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    transactions = pair.get("txns") or {}

    vol_m5 = number(volume.get("m5"))
    vol_h1 = number(volume.get("h1"))

    change_m5 = number(changes.get("m5"))
    change_h1 = number(changes.get("h1"))

    tx_m5 = transactions.get("m5") or {}
    buys_m5 = int(number(tx_m5.get("buys")))
    sells_m5 = int(number(tx_m5.get("sells")))

    # Filtri obbligatori
    if liquidity < MIN_LIQUIDITY_USD:
        return None

    if vol_m5 < MIN_VOLUME_5M_USD:
        return None

    if vol_h1 < MIN_VOLUME_1H_USD:
        return None

    if not (
        MIN_CHANGE_5M_PCT
        <= change_m5
        <= MAX_CHANGE_5M_PCT
    ):
        return None

    if change_h1 < MIN_CHANGE_1H_PCT:
        return None

    # Rapporto tra conteggio acquisti e vendite.
    # Se non ci sono vendite, il rapporto è considerato
    # favorevole solo se sono presenti acquisti.
    if sells_m5 > 0:
        buy_sell_ratio = buys_m5 / sells_m5
    elif buys_m5 > 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = 0.0

    if buy_sell_ratio < MIN_BUY_SELL_RATIO:
        return None

    # Punteggio indicativo su 5 punti.
    score = 0
    score += int(change_m5 >= 5)
    score += int(change_h1 >= 8)
    score += int(vol_m5 >= 15_000)
    score += int(buys_m5 > sells_m5)
    score += int(vol_h1 >= 50_000)

    base = pair.get("baseToken") or {}

    return {
        "pair_id": pair.get("pairAddress", ""),
        "name": base.get("name") or "Sconosciuto",
        "symbol": base.get("symbol") or "?",
        "price": number(pair.get("priceUsd")),
        "liquidity": liquidity,
        "vol_m5": vol_m5,
        "vol_h1": vol_h1,
        "change_m5": change_m5,
        "change_h1": change_h1,
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "buy_sell_ratio": buy_sell_ratio,
        "score": score,
        "url": pair.get("url") or "",
    }


def send_telegram(message):
    """Invia un singolo alert Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Segreti Telegram mancanti.")
        return False

    endpoint = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    try:
        response = session.post(
            endpoint,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        if not response.json().get("ok"):
            logging.error("Telegram ha rifiutato l'alert.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Invio Telegram fallito: %s", exc)
        return False


def format_alert(item):
    """Formatta l'alert di un singolo token."""
    ratio = item["buy_sell_ratio"]
    ratio_text = (
        "N/A" if ratio == float("inf")
        else f"{ratio:.2f}:1"
    )

    price = item["price"]
    price_text = (
        f"${price:.10f}" if price > 0
        else "Non disponibile"
    )

    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {item['name']} ({item['symbol']})\n"
        f"Prezzo: {price_text}\n"
        f"Liquidità: ${item['liquidity']:,.0f}\n"
        f"Volume 5m: ${item['vol_m5']:,.0f}\n"
        f"Volume 1h: ${item['vol_h1']:,.0f}\n"
        f"Variazione 5m: {item['change_m5']:+.2f}%\n"
        f"Variazione 1h: {item['change_h1']:+.2f}%\n"
        f"Acquisti/vendite 5m: "
        f"{item['buys_m5']}/{item['sells_m5']}\n"
        f"Rapporto acquisti/vendite: {ratio_text}\n"
        f"Radar score: {item['score']}/5\n\n"
        f"Grafico: {item['url']}\n\n"
        "⚠️ Segnale sperimentale, non consiglio finanziario.\n"
        "Nessun acquisto automatico."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI.")

    pairs = get_solana_pairs()

    if pairs is None:
        logging.error(
            "Ricerca DexScreener fallita. Stato conservato."
        )
        return

    if not pairs:
        logging.warning(
            "Nessuna coppia trovata. Stato conservato."
        )
        return

    previous_active = load_state()

    if previous_active is None:
        logging.error(
            "Stato non valido. Scansione interrotta."
        )
        return

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

    current_active = {
        item["pair_id"]
        for item in candidates
        if item["pair_id"]
    }

    new_pumps = [
        item for item in candidates
        if item["pair_id"] not in previous_active
    ]

    alerts_sent = 0
    failed_ids = set()
    attempted_ids = set()

    for item in new_pumps[:MAX_ALERTS_PER_RUN]:
        pair_id = item["pair_id"]
        attempted_ids.add(pair_id)

        if send_telegram(format_alert(item)):
            alerts_sent += 1
            logging.info(
                "Alert inviato: %s", item["symbol"]
            )
        else:
            failed_ids.add(pair_id)

        time.sleep(REQUEST_DELAY_SECONDS)

    # Non segnare come notificati gli alert falliti
    # né quelli rimasti oltre il limite per esecuzione.
    deferred_ids = {
        item["pair_id"]
        for item in new_pumps[MAX_ALERTS_PER_RUN:]
        if item["pair_id"]
    }

    state_to_save = current_active - failed_ids - deferred_ids

    try:
        save_state(state_to_save)
    except OSError as exc:
        logging.error("Salvataggio stato fallito: %s", exc)

    # Nessun riepilogo Telegram: log solo in GitHub Actions.
    logging.info(
        "Scansione completata: coppie=%s, candidati=%s, "
        "nuovi_segnali=%s, alert_inviati=%s, falliti=%s",
        len(pairs),
        len(candidates),
        len(new_pumps),
        alerts_sent,
        len(failed_ids),
    )

    logging.info("Radar terminato.")


if __name__ == "__main__":
    main()
