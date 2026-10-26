
import os
import json
import time
import logging
from pathlib import Path

import requests

# ==========================================
# MEME PUMP RADAR AI — SOLANA
# Monitoraggio e notifiche. Nessun trading.
# ==========================================

DEX_API = "https://api.dexscreener.com"
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 5_000
MIN_VOLUME_1H_USD = 20_000
MIN_CHANGE_5M_PCT = 3.0
MIN_CHANGE_1H_PCT = 5.0
MAX_ALERTS_PER_RUN = 10

STATE_FILE = Path("radar_state.json")
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


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def load_state():
    """Carica lo stato persistente dei token."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("active_pairs", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_state(active_pairs):
    """Salva i token che attualmente soddisfano i filtri."""
    STATE_FILE.write_text(
        json.dumps(
            {"active_pairs": sorted(active_pairs)},
            indent=2,
        ),
        encoding="utf-8",
    )


def get_solana_pairs():
    """Cerca coppie Solana attraverso DexScreener."""
    queries = [
        "solana meme",
        "solana pump",
        "solana dog",
        "solana cat",
    ]
    pairs_by_id = {}

    for query in queries:
        try:
            response = session.get(
                f"{DEX_API}/latest/dex/search",
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
            logging.warning(
                "Errore ricerca %s: %s", query, exc
            )

    return list(pairs_by_id.values())


def evaluate_pair(pair):
    """Applica i filtri di liquidità, volume e momentum."""
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}

    vol_m5 = number(volume.get("m5"))
    vol_h1 = number(volume.get("h1"))
    change_m5 = number(changes.get("m5"))
    change_h1 = number(changes.get("h1"))

    tx_m5 = txns.get("m5") or {}
    buys_m5 = int(number(tx_m5.get("buys")))
    sells_m5 = int(number(tx_m5.get("sells")))

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

    score = 0
    score += int(change_m5 >= 5)
    score += int(change_h1 >= 10)
    score += int(vol_m5 >= 15_000)
    score += int(buys_m5 > sells_m5)
    score += int(vol_h1 >= 50_000)

    base = pair.get("baseToken") or {}

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
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "score": score,
        "url": pair.get("url", ""),
    }


def send_telegram(message):
    """Invia un messaggio Telegram e verifica la risposta."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Segreti Telegram mancanti.")
        return False

    try:
        response = session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
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
            logging.error("Telegram non ha accettato il messaggio.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Errore Telegram: %s", exc)
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

    # Non considerare una ricerca fallita come fine del pump.
    pairs = get_solana_pairs()
    if not pairs:
        logging.error(
            "Nessuna coppia recuperata. Stato conservato; "
            "riepilogo di errore in corso."
        )
        send_telegram(
            "⚠️ MEME PUMP RADAR AI\n"
            "Ricerca DexScreener senza risultati utilizzabili.\n"
            "Lo stato precedente non è stato modificato."
        )
        return

    previous_active = load_state()
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

    # Un nuovo ingresso nei filtri può generare un alert.
    new_pumps = [
        item for item in candidates
        if item["pair_id"] not in previous_active
    ]

    alerts_sent = 0
    failed_alerts = 0
    alert_names = []

    for item in new_pumps[:MAX_ALERTS_PER_RUN]:
        if send_telegram(format_alert(item)):
            alerts_sent += 1
            alert_names.append(item["symbol"])
            logging.info("Alert inviato: %s", item["symbol"])
        else:
            failed_alerts += 1

        time.sleep(0.3)

    # Salva lo stato solo dopo una scansione utilizzabile.
    try:
        save_state(current_active)
    except OSError as exc:
        logging.error("Impossibile salvare lo stato: %s", exc)

    summary = (
        "📊 RIEPILOGO MEME PUMP RADAR AI\n\n"
        f"Coppie Solana analizzate: {len(pairs)}\n"
        f"Token sopra i filtri: {len(candidates)}\n"
        f"Nuovi segnali rilevati: {len(new_pumps)}\n"
        f"Alert inviati: {alerts_sent}\n"
        f"Invii falliti: {failed_alerts}\n"
        f"Liquidità minima: ${MIN_LIQUIDITY_USD:,.0f}\n"
    )

    if alert_names:
        summary += "\nToken segnalati: " + ", ".join(alert_names)
    else:
        summary += "\nNessun nuovo pump rilevato."

    if not send_telegram(summary):
        logging.error("Riepilogo Telegram non inviato.")

    logging.info(summary.replace("\n", " | "))
    logging.info("Radar terminato.")


if __name__ == "__main__":
    main()
