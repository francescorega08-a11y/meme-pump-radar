
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests

DEX_API = "https://api.dexscreener.com"
TELEGRAM_API = "https://api.telegram.org"

# FILTRI PIU' PERMISSIVI
MIN_LIQUIDITY_USD = 25_000
MIN_VOLUME_5M_USD = 3_000
MIN_VOLUME_1H_USD = 20_000
MIN_CHANGE_5M_PCT = 1.0
MAX_CHANGE_5M_PCT = 10.0
MIN_CHANGE_1H_PCT = 2.0
MIN_BUY_SELL_RATIO = 1.2

# ACCELERAZIONE E CONTROLLI
MIN_VOLUME_ACCELERATION = 1.1
MIN_TXNS_5M = 5
MAX_CHANGE_1H_PCT = 100.0
MIN_TOKEN_AGE_MINUTES = 5

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
        if result != result or abs(result) == float("inf"):
            return default
        return result
    except (TypeError, ValueError, OverflowError):
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

        except (requests.RequestException, ValueError) as exc:
            logging.warning(
                "Ricerca '%s' fallita: %s", query, exc
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
    buys = int(number(txns_5m.get("buys")))
    sells = int(number(txns_5m.get("sells")))
    total_txns = buys + sells

    ratio = buys / sells if sells > 0 else 0.0

    # Stima dell'attivita' recente rispetto alla media oraria.
    # Non e' una misura storica esatta dell'accelerazione.
    average_5m = volume_1h / 12 if volume_1h > 0 else 0
    acceleration = (
        volume_5m / average_5m
        if average_5m > 0 else 0.0
    )

    base = pair.get("baseToken") or {}
    token_address = base.get("address") or ""
    pair_id = pair.get("pairAddress") or ""
    price_usd = number(pair.get("priceUsd"))

    # Eta' della coppia, se disponibile.
    created_at = pair.get("pairCreatedAt")
    age_minutes = None

    if created_at:
        try:
            created = datetime.fromtimestamp(
                float(created_at) / 1000,
                tz=timezone.utc
            )
            age_minutes = (
                datetime.now(timezone.utc) - created
            ).total_seconds() / 60
        except (
            ValueError, TypeError, OSError, OverflowError
        ):
            age_minutes = None

    checks = {
        "liquidita": liquidity >= MIN_LIQUIDITY_USD,
        "volume_5m": volume_5m >= MIN_VOLUME_5M_USD,
        "volume_1h": volume_1h >= MIN_VOLUME_1H_USD,
        "prezzo_disponibile": price_usd > 0,
        "indirizzo_disponibile": bool(token_address),
        "variazione_5m": (
            MIN_CHANGE_5M_PCT
            <= change_5m
            <= MAX_CHANGE_5M_PCT
        ),
        "variazione_1h": (
            MIN_CHANGE_1H_PCT
            <= change_1h
            <= MAX_CHANGE_1H_PCT
        ),
        "rapporto_acquisti": ratio >= MIN_BUY_SELL_RATIO,
        "transazioni_sufficienti": total_txns >= MIN_TXNS_5M,
        "accelerazione_volume": (
            acceleration >= MIN_VOLUME_ACCELERATION
        ),
        # Se l'eta' non e' disponibile, non la consideriamo
        # una verifica superata: segnaliamo il dato mancante.
        "eta_disponibile": age_minutes is not None,
        "eta_minima": (
            age_minutes is not None
            and age_minutes >= MIN_TOKEN_AGE_MINUTES
        ),
    }

    # Punteggio indicativo su 100, non probabilita' di rialzo.
    score = 0

    if liquidity >= MIN_LIQUIDITY_USD:
        score += 20
    if volume_5m >= MIN_VOLUME_5M_USD:
        score += 10
    if volume_1h >= MIN_VOLUME_1H_USD:
        score += 10
    if acceleration >= 1.1:
        score += 15
    if acceleration >= 2.0:
        score += 5

    if MIN_CHANGE_5M_PCT <= change_5m <= 5.0:
        score += 10
    elif 5.0 < change_5m <= MAX_CHANGE_5M_PCT:
        score += 5

    if MIN_CHANGE_1H_PCT <= change_1h <= 30.0:
        score += 10
    if ratio >= MIN_BUY_SELL_RATIO:
        score += 10
    if total_txns >= MIN_TXNS_5M:
        score += 5
    if age_minutes is not None and age_minutes >= MIN_TOKEN_AGE_MINUTES:
        score += 5

    # Un segnale viene inviato solo se supera tutti i controlli.
    qualified = all(checks.values())

    return {
        "pair_id": pair_id,
        "name": base.get("name") or "Sconosciuto",
        "symbol": base.get("symbol") or "?",
        "token_address": token_address,
        "dex": pair.get("dexId") or "",
        "url": pair.get("url") or "",
        "price_usd": price_usd,
        "liquidity_usd": liquidity,
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "volume_acceleration": round(acceleration, 2),
        "change_5m": change_5m,
        "change_1h": change_1h,
        "buys_5m": buys,
        "sells_5m": sells,
        "total_txns_5m": total_txns,
        "buy_sell_ratio": round(ratio, 2),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None else None
        ),
        "score": score,
        "qualified": qualified,
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
    age = (
        f"{item['age_minutes']:.0f} minuti"
        if item["age_minutes"] is not None
        else "non disponibile"
    )

    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {item['name']} ({item['symbol']})\n"
        f"Punteggio: {item['score']}/100\n"
        f"Prezzo: ${item['price_usd']:.10f}\n"
        f"Liquidita': ${item['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${item['volume_5m']:,.0f}\n"
        f"Volume 1h: ${item['volume_1h']:,.0f}\n"
        f"Accelerazione volume: "
        f"{item['volume_acceleration']:.2f}x\n"
        f"Variazione 5m: {item['change_5m']:.2f}%\n"
        f"Variazione 1h: {item['change_1h']:.2f}%\n"
        f"Acquisti/vendite: {item['buy_sell_ratio']:.2f}\n"
        f"Transazioni 5m: {item['total_txns_5m']}\n"
        f"Eta' coppia: {age}\n\n"
        f"DEX: {item['url']}\n"
        f"Token address: {item['token_address']}\n\n"
        "Filtri automatici superati. "
        "La sicurezza del contratto non e' verificata "
        "completamente e non e' garantito un rialzo."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI")

    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    already_alerted = set(state.get("alerted_pairs", []))

    pairs = get_solana_pairs()
    logging.info("Coppie Solana raccolte: %d", len(pairs))

    results = [evaluate_pair(pair) for pair in pairs]
    results = [item for item in results if item["pair_id"]]

    now = datetime.now(timezone.utc).isoformat()

    for item in results:
        item["updated_at"] = now

    qualified = [
        item for item in results if item["qualified"]
    ]

    qualified.sort(
        key=lambda item: (
            item["score"],
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
            f"Liquidita' minima: ${MIN_LIQUIDITY_USD:,.0f}\n"
            f"Volume minimo 5m: ${MIN_VOLUME_5M_USD:,.0f}\n"
            f"Volume minimo 1h: ${MIN_VOLUME_1H_USD:,.0f}\n"
            f"Accelerazione minima: {MIN_VOLUME_ACCELERATION:.1f}x\n"
            f"Transazioni minime 5m: {MIN_TXNS_5M}\n\n"
            "I segnali automatici non garantiscono "
            "la sicurezza o un futuro rialzo."
        )

        if send_telegram(summary):
            state["last_summary_at"] = now
            logging.info("Riepilogo Telegram inviato.")
        else:
            logging.warning(
                "Riepilogo non inviato; verra' ritentato."
            )

    save_json(STATE_FILE, state)

    logging.info(
        "Fine scansione: coppie=%d, segnali=%d, alert_inviati=%d",
        len(results), len(qualified), sent_count
    )


if __name__ == "__main__":
    main()
