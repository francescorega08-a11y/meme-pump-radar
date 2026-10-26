
import os
import json
import time
import logging
import math
from pathlib import Path
from datetime import datetime, timezone

import requests

# ==================================================
# MEME PUMP RADAR AI — SOLANA
# DexScreener + Telegram + monitoraggio momentum
# Nessun acquisto automatico. Nessun riepilogo Telegram.
# ==================================================

DEX_API = "https://api.dexscreener.com"
TELEGRAM_API = "https://api.telegram.org"

# FILTRI DI MERCATO
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 8_000
MIN_VOLUME_1H_USD = 30_000

MIN_CHANGE_5M_PCT = 2.0
MAX_CHANGE_5M_PCT = 12.0
MIN_CHANGE_1H_PCT = 3.0

MIN_BUY_SELL_RATIO = 1.3

# L'accelerazione del volume aumenta il punteggio,
# ma non è un requisito obbligatorio.
VOLUME_ACCELERATION_BONUS_PCT = 20.0

# LIMITI OPERATIVI
MAX_ALERTS_PER_RUN = 10
REQUEST_DELAY_SECONDS = 0.3
TIMEOUT = 20

# Stato locale: deve essere persistito tra le esecuzioni
# di GitHub Actions per ricordare scansioni precedenti.
STATE_FILE = Path("radar_state.json")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/4.0"
})


def number(value, default=0.0):
    """Converte numeri, rifiutando valori non finiti."""
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def load_state():
    """Carica token già notificati e osservazioni precedenti."""
    if not STATE_FILE.exists():
        return {"active_pairs": [], "volume_history": {}}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            raise ValueError("Stato non valido")

        active = data.get("active_pairs", [])
        history = data.get("volume_history", {})

        if not isinstance(active, list):
            raise ValueError("active_pairs non valido")

        if not isinstance(history, dict):
            raise ValueError("volume_history non valido")

        return {
            "active_pairs": [
                item for item in active
                if isinstance(item, str)
            ],
            "volume_history": history,
        }

    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logging.error("Stato non leggibile: %s", exc)
        return None


def save_state(active_pairs, volume_history):
    """Salva lo stato in modo atomico."""
    temp_file = STATE_FILE.with_suffix(".tmp")

    payload = {
        "active_pairs": sorted(active_pairs),
        "volume_history": volume_history,
    }

    temp_file.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(STATE_FILE)


def get_solana_pairs():
    """Cerca coppie Solana con le ricerche DexScreener."""
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
                "Ricerca DexScreener %s fallita: %s",
                query,
                exc,
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    # None significa che tutte le richieste sono fallite.
    # Una lista vuota significa che le ricerche sono riuscite
    # ma non hanno restituito coppie utilizzabili.
    if successful_queries == 0:
        return None

    return list(pairs_by_id.values())


def volume_acceleration(pair_id, volume_5m, history):
    """
    Confronta il volume 5m con l'ultima osservazione salvata.

    È un indicatore approssimativo: le finestre mobili possono
    sovrapporsi e non rappresentano volumi indipendenti.
    """
    previous = history.get(pair_id, {})
    previous_volume = number(previous.get("volume_5m"), 0)

    if previous_volume <= 0:
        return None

    return (
        (volume_5m - previous_volume)
        / previous_volume
    ) * 100.0


def evaluate_pair(pair, history):
    """Applica i filtri e calcola un punteggio indicativo."""
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

    if liquidity < MIN_LIQUIDITY_USD:
        return None

    if vol_m5 < MIN_VOLUME_5M_USD:
        return None

    if vol_h1 < MIN_VOLUME_1H_USD:
        return None

    if not MIN_CHANGE_5M_PCT <= change_m5 <= MAX_CHANGE_5M_PCT:
        return None

    if change_h1 < MIN_CHANGE_1H_PCT:
        return None

    # Evita di considerare un rapporto favorevole quando
    # non sono disponibili transazioni sufficienti.
    if buys_m5 + sells_m5 == 0:
        return None

    ratio = buys_m5 / sells_m5 if sells_m5 else float("inf")

    if ratio < MIN_BUY_SELL_RATIO:
        return None

    pair_id = pair.get("pairAddress", "")
    acceleration = volume_acceleration(
        pair_id, vol_m5, history
    )

    # Punteggio su 5: non è una probabilità di pump.
    score = 0
    score += int(change_m5 >= 5)
    score += int(change_h1 >= 8)
    score += int(vol_m5 >= 15_000)
    score += int(buys_m5 > sells_m5)
    score += int(vol_h1 >= 50_000)

    if (
        acceleration is not None
        and acceleration >= VOLUME_ACCELERATION_BONUS_PCT
    ):
        score = min(score + 1, 5)

    base = pair.get("baseToken") or {}

    return {
        "pair_id": pair_id,
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
        "ratio": ratio,
        "acceleration": acceleration,
        "score": score,
        "url": pair.get("url") or "",
    }


def send_telegram(message):
    """Invia un alert individuale e verifica la risposta."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Segreti Telegram mancanti.")
        return False

    endpoint = (
        f"{TELEGRAM_API}/bot{TELEGRAM_TOKEN}/sendMessage"
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
        data = response.json()

        if not data.get("ok"):
            logging.error("Telegram non ha accettato l'alert.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Errore invio Telegram: %s", exc)
        return False


def format_alert(item):
    """Formatta un alert per un singolo token."""
    price = item["price"]
    price_text = (
        f"${price:.10f}" if price > 0
        else "Non disponibile"
    )

    ratio = item["ratio"]
    ratio_text = "N/D" if math.isinf(ratio) else f"{ratio:.2f}:1"

    acceleration = item["acceleration"]
    acceleration_text = (
        "In attesa di storico"
        if acceleration is None
        else f"{acceleration:+.1f}% rispetto all'ultima osservazione"
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
        f"Acquisti/vendite: "
        f"{item['buys_m5']}/{item['sells_m5']}\n"
        f"Rapporto acquisti/vendite: {ratio_text}\n"
        f"Volume 5m vs osservazione precedente: "
        f"{acceleration_text}\n"
        f"Radar score: {item['score']}/5\n\n"
        f"Grafico: {item['url']}\n\n"
        "⚠️ Segnale sperimentale, non consiglio finanziario.\n"
        "Nessun acquisto automatico."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI.")

    state = load_state()
    if state is None:
        logging.error(
            "Stato non valido: scansione interrotta "
            "per evitare notifiche duplicate."
        )
        return

    pairs = get_solana_pairs()

    if pairs is None:
        logging.error(
            "Ricerche DexScreener fallite. Stato conservato."
        )
        return

    if not pairs:
        logging.warning(
            "Nessuna coppia trovata. Stato conservato."
        )
        return

    previous_active = set(state["active_pairs"])
    history = state["volume_history"]

    candidates = []

    for pair in pairs:
        item = evaluate_pair(pair, history)
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

    sent_ids = set()
    failed_ids = set()

    for item in new_pumps[:MAX_ALERTS_PER_RUN]:
        if send_telegram(format_alert(item)):
            sent_ids.add(item["pair_id"])
            logging.info("Alert inviato: %s", item["symbol"])
        else:
            failed_ids.add(item["pair_id"])

        time.sleep(REQUEST_DELAY_SECONDS)

    deferred_ids = {
        item["pair_id"]
        for item in new_pumps[MAX_ALERTS_PER_RUN:]
        if item["pair_id"]
    }

    # I token già attivi restano attivi; i nuovi entrano
    # nello stato solo se il loro alert è stato inviato.
    next_active = (
        previous_active.intersection(current_active)
        | sent_ids
    )

    # I segnali falliti e quelli oltre il limite restano
    # non notificati e possono essere ritentati.
    next_active -= failed_ids
    next_active -= deferred_ids

    # Aggiorna le osservazioni per le coppie viste.
    now = datetime.now(timezone.utc).isoformat()

    for pair in pairs:
        pair_id = pair.get("pairAddress")
        if not pair_id:
            continue

        vol = number((pair.get("volume") or {}).get("m5"))

        history[pair_id] = {
            "volume_5m": vol,
            "observed_at": now,
        }

    # Limita la crescita dello storico: conserva le
    # osservazioni più recenti, fino a 5.000 coppie.
    if len(history) > 5000:
        history = dict(
            sorted(
                history.items(),
                key=lambda entry: entry[1].get("observed_at", ""),
                reverse=True,
            )[:5000]
        )

    try:
        save_state(next_active, history)
    except OSError as exc:
        logging.error("Errore salvataggio stato: %s", exc)

    # Solo log interni a GitHub Actions. Nessun riepilogo Telegram.
    logging.info(
        "Scansione completata: coppie=%d, candidati=%d, "
        "nuovi_segnali=%d, alert_inviati=%d, falliti=%d",
        len(pairs),
        len(candidates),
        len(new_pumps),
        len(sent_ids),
        len(failed_ids),
    )

    logging.info("Radar terminato.")


if __name__ == "__main__":
    main()
