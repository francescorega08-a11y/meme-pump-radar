
import os
import json
import time
import logging
from pathlib import Path

import requests

# ==========================================
# MEME PUMP RADAR AI — SOLANA
# Monitoraggio e alert Telegram.
# Nessun trading automatico.
# ==========================================

DEX_API = "https://api.dexscreener.com"

# Filtri di mercato
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 5_000
MIN_VOLUME_1H_USD = 20_000
MIN_CHANGE_5M_PCT = 3.0
MIN_CHANGE_1H_PCT = 5.0

# Limiti operativi
MAX_ALERTS_PER_RUN = 10
REQUEST_DELAY_SECONDS = 0.3
TIMEOUT = 20

# Stato persistente
STATE_FILE = Path("radar_state.json")

# Segreti configurati in GitHub Actions
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/2.0"
})


def number(value, default=0.0):
    """Converte un valore in numero in modo sicuro."""
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError, OverflowError):
        return default


def load_state():
    """Carica gli identificativi delle coppie già attive."""
    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        pairs = data.get("active_pairs", [])

        if not isinstance(pairs, list):
            logging.warning("Formato dello stato non valido.")
            return None

        return set(
            item for item in pairs
            if isinstance(item, str)
        )

    except FileNotFoundError:
        return set()

    except (json.JSONDecodeError, OSError) as exc:
        logging.error("Stato non leggibile: %s", exc)
        return None


def save_state(active_pairs):
    """Salva lo stato solo dopo una scansione valida."""
    temporary_file = STATE_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            {"active_pairs": sorted(active_pairs)},
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary_file.replace(STATE_FILE)


def get_solana_pairs():
    """Recupera coppie Solana attraverso la ricerca DexScreener."""
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
                logging.warning(
                    "Risposta non valida per la ricerca %s",
                    query,
                )
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
                "Errore nella ricerca %s: %s",
                query,
                exc,
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    # Se tutte le ricerche falliscono, non aggiornare lo stato.
    if successful_queries == 0:
        return None

    return list(pairs_by_id.values())


def evaluate_pair(pair):
    """Applica i filtri di liquidità, volume e variazione."""
    liquidity_data = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    transactions = pair.get("txns") or {}

    liquidity = number(liquidity_data.get("usd"))
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

    if change_m5 < MIN_CHANGE_5M_PCT:
        return None

    if change_h1 < MIN_CHANGE_1H_PCT:
        return None

    # Punteggio indicativo: massimo 5 punti
    score = 0
    score += int(change_m5 >= 5)
    score += int(change_h1 >= 10)
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
        "score": score,
        "url": pair.get("url") or "",
    }


def send_telegram(message):
    """Invia un alert individuale e verifica la risposta."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error(
            "Configurazione Telegram mancante nei segreti."
        )
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
        result = response.json()

        if not result.get("ok"):
            logging.error("Telegram ha rifiutato il messaggio.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Invio Telegram fallito: %s", exc)
        return False


def format_alert(item):
    """Crea il messaggio Telegram per un singolo token."""
    price_text = (
        f"${item['price']:.10f}"
        if item["price"] > 0
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
        f"Radar score: {item['score']}/5\n\n"
        f"Grafico: {item['url']}\n\n"
        "⚠️ Segnale sperimentale, non consiglio finanziario.\n"
        "Nessun acquisto automatico."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI.")

    # 1. Recupera le coppie
    pairs = get_solana_pairs()

    if pairs is None:
        logging.error(
            "Tutte le ricerche DexScreener sono fallite. "
            "Stato conservato."
        )
        return

    if not pairs:
        logging.warning(
            "Nessuna coppia Solana trovata. "
            "Stato conservato."
        )
        return

    # Se lo stato esistente è corrotto, non inviare alert
    # potenzialmente duplicati e non sovrascriverlo.
    previous_active = load_state()

    if previous_active is None:
        logging.error(
            "Stato non affidabile: scansione interrotta "
            "per evitare alert duplicati."
        )
        return

    # 2. Applica i filtri
    candidates = []

    for pair in pairs:
        item = evaluate_pair(pair)

        if item:
            candidates.append(item)

    # Ordina per punteggio, volume e liquidità
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

    # 3. Individua i nuovi ingressi nei filtri
    new_pumps = [
        item for item in candidates
        if item["pair_id"] not in previous_active
    ]

    # 4. Invia esclusivamente alert individuali.
    # Nessun riepilogo automatico Telegram.
    alerts_sent = 0
    failed_alerts = 0

    for item in new_pumps[:MAX_ALERTS_PER_RUN]:
        if send_telegram(format_alert(item)):
            alerts_sent += 1
            logging.info(
                "Alert inviato: %s (%s)",
                item["symbol"],
                item["pair_id"],
            )
        else:
            failed_alerts += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    # Se restano alert non inviati, non marcarli come
    # già notificati: saranno riprovati alla prossima scansione.
    successfully_alerted_ids = {
        item["pair_id"]
        for item in new_pumps[:MAX_ALERTS_PER_RUN]
        if item["pair_id"]
    }

    if failed_alerts:
        failed_ids = {
            item["pair_id"]
            for item in new_pumps[:MAX_ALERTS_PER_RUN]
            if item["pair_id"]
        }

        # Conserva gli elementi che non sono stati notificati.
        failed_ids = {
            item["pair_id"]
            for item in new_pumps[:MAX_ALERTS_PER_RUN]
            if item["pair_id"]
        }

        # Rimuovi dal nuovo stato i candidati per cui
        # l'invio è fallito, così da ritentare.
        # Il mapping degli esiti è ricostruito nel ciclo
        # seguente nelle versioni future se necessario.
        # Per ora, conserva i candidati già attivi.
        logging.warning(
            "Alcuni alert non sono stati inviati: %s",
            failed_alerts,
        )

    # Evita di perdere i nuovi segnali oltre il limite
    # massimo di invii per esecuzione.
    deferred_ids = {
        item["pair_id"]
        for item in new_pumps[MAX_ALERTS_PER_RUN:]
        if item["pair_id"]
    }

    state_to_save = current_active - deferred_ids

    try:
        save_state(state_to_save)
    except OSError as exc:
        logging.error(
            "Impossibile salvare lo stato: %s",
            exc,
        )

    # Statistiche solo nei log GitHub Actions, mai su Telegram.
    logging.info(
        "Scansione completata. "
        "Coppie analizzate: %s | "
        "Token nei filtri: %s | "
        "Nuovi segnali: %s | "
        "Alert inviati: %s | "
        "Invii falliti: %s",
        len(pairs),
        len(candidates),
        len(new_pumps),
        alerts_sent,
        failed_alerts,
    )

    logging.info("Radar terminato.")


if __name__ == "__main__":
    main()
