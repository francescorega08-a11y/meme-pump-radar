
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests

DEX_API = "https://api.dexscreener.com"
TELEGRAM_API = "https://api.telegram.org"

# FILTRI DEL RADAR — NON MODIFICARE LE SOGLIE
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 8_000
MIN_VOLUME_1H_USD = 30_000
MIN_CHANGE_5M_PCT = 2.0
MAX_CHANGE_5M_PCT = 12.0
MIN_CHANGE_1H_PCT = 3.0
MIN_BUY_SELL_RATIO = 1.3

# RIEPILOGO TELEGRAM OGNI 15 MINUTI
SUMMARY_INTERVAL_SECONDS = 900

MAX_ALERTS_PER_RUN = 10
TIMEOUT = 20

STATE_FILE = Path("radar_state.json")
DATA_FILE = Path("radar_data.json")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/1.0"
})


def number(value, default=0.0):
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def get_solana_pairs():
    queries = [
        "solana meme",
        "solana dog",
        "solana cat",
        "solana pepe",
        "solana pump",
        "solana bonk",
        "solana ai",
    ]

    pairs_by_id = {}

    for query in queries:
        try:
            response = session.get(
                f"{DEX_API}/latest/dex/search",
                params={"q": query},
                timeout=TIMEOUT
            )
            response.raise_for_status()
            pairs = response.json().get("pairs") or []

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                pair_id = pair.get("pairAddress")
                if pair_id:
                    pairs_by_id[pair_id] = pair

        except requests.RequestException as exc:
            logging.warning(
                "Ricerca '%s' fallita: %s",
                query,
                exc
            )

    return list(pairs_by_id.values())


def evaluate_pair(pair):
    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}

    volume_5m = number(volume.get("m5"))
    volume_1h = number(volume.get("h1"))
    change_5m = number(change.get("m5"))
    change_1h = number(change.get("h1"))

    txns_5m = txns.get("m5") or {}
    buys = number(txns_5m.get("buys"))
    sells = number(txns_5m.get("sells"))

    if sells <= 0:
        ratio = 0.0
    else:
        ratio = buys / sells

    checks = {
        "liquidita": liquidity >= MIN_LIQUIDITY_USD,
        "volume_5m": volume_5m >= MIN_VOLUME_5M_USD,
        "volume_1h": volume_1h >= MIN_VOLUME_1H_USD,
        "variazione_5m": (
            MIN_CHANGE_5M_PCT
            <= change_5m
            <= MAX_CHANGE_5M_PCT
        ),
        "variazione_1h": change_1h >= MIN_CHANGE_1H_PCT,
        "rapporto_acquisti": ratio >= MIN_BUY_SELL_RATIO,
    }

    base = pair.get("baseToken") or {}

    return {
        "pair_id": pair.get("pairAddress"),
        "name": base.get("name") or "Sconosciuto",
        "symbol": base.get("symbol") or "?",
        "token_address": base.get("address") or "",
        "dex": pair.get("dexId") or "",
        "url": pair.get("url") or "",
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": liquidity,
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "change_5m": change_5m,
        "change_1h": change_1h,
        "buys_5m": int(buys),
        "sells_5m": int(sells),
        "buy_sell_ratio": round(ratio, 2),
        "qualified": all(checks.values()),
        "checks": checks,
    }


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning(
            "Credenziali Telegram non configurate."
        )
        return False

    try:
        response = session.post(
            f"{TELEGRAM_API}/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )

        response.raise_for_status()
        return response.json().get("ok") is True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Invio Telegram fallito: %s", exc)
        return False


def format_alert(item):
    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {item['name']} ({item['symbol']})\n"
        f"Prezzo: ${item['price_usd']:.10f}\n"
        f"Liquidità: ${item['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${item['volume_5m']:,.0f}\n"
        f"Volume 1h: ${item['volume_1h']:,.0f}\n"
        f"Variazione 5m: {item['change_5m']:.2f}%\n"
        f"Variazione 1h: {item['change_1h']:.2f}%\n"
        f"Acquisti/vendite 5m: "
        f"{item['buy_sell_ratio']:.2f}\n\n"
        f"DEX: {item['url']}\n"
        f"Token address: {item['token_address']}\n\n"
        "Segnale automatico da verificare: "
        "non è una garanzia di rialzo."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI")

    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    already_alerted = set(
        state.get("alerted_pairs", [])
    )

    pairs = get_solana_pairs()

    logging.info(
        "Coppie Solana raccolte: %d",
        len(pairs)
    )

    results = [
        evaluate_pair(pair)
        for pair in pairs
    ]

    results = [
        item for item in results
        if item["pair_id"]
    ]

    now = datetime.now(timezone.utc).isoformat()

    for item in results:
        item["updated_at"] = now

    qualified = [
        item for item in results
        if item["qualified"]
    ]

    qualified.sort(
        key=lambda item: (
            item["volume_5m"],
            item["liquidity_usd"]
        ),
        reverse=True
    )

    save_json(DATA_FILE, {
        "updated_at": now,
        "pairs_scanned": len(results),
        "qualified_count": len(qualified),
        "pairs": results,
    })

    sent_count = 0

    for item in qualified:
        if item["pair_id"] in already_alerted:
            continue

        if sent_count >= MAX_ALERTS_PER_RUN:
            break

        if send_telegram(format_alert(item)):
            already_alerted.add(item["pair_id"])
            sent_count += 1

    state["alerted_pairs"] = list(already_alerted)[-1000:]
    state["last_run"] = now
    state["last_pairs_scanned"] = len(results)
    state["last_qualified_count"] = len(qualified)
    state["last_alerts_sent"] = sent_count

    save_json(STATE_FILE, state)

    # Controlla se è ora di inviare il riepilogo.
    last_summary = state.get("last_summary_at")
    send_summary = not last_summary

    if last_summary:
        try:
            previous = datetime.fromisoformat(last_summary)
            current = datetime.fromisoformat(now)

            send_summary = (
                current - previous
            ).total_seconds() >= SUMMARY_INTERVAL_SECONDS

        except (TypeError, ValueError):
            send_summary = True

    if send_summary:
        summary = (
            "🟢 MEME PUMP RADAR AI — RIEPILOGO\n\n"
            "Stato: scansione completata\n"
            f"Ultima scansione UTC: {now}\n"
            f"Coppie analizzate: {len(results)}\n"
            f"Segnali validi: {len(qualified)}\n"
            f"Avvisi inviati in questa scansione: {sent_count}\n\n"
            "Scansione prevista ogni 5 minuti.\n"
            "Riepilogo previsto ogni 15 minuti.\n"
            "Liquidità minima: $25.000\n"
            "Volume minimo 5m: $8.000\n\n"
            "Il riepilogo conferma questa scansione, "
            "non garantisce le successive."
        )

        if send_telegram(summary):
            state["last_summary_at"] = now
            save_json(STATE_FILE, state)
            logging.info("Riepilogo Telegram inviato.")
        else:
            logging.warning(
                "Riepilogo non inviato; verrà ritentato."
            )

    logging.info(
        "Fine scansione: coppie=%d, segnali=%d, alert_inviati=%d",
        len(results),
        len(qualified),
        sent_count
    )


if __name__ == "__main__":
    main()
