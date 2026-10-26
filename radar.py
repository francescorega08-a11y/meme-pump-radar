
import os
import json
import time
import logging
import math
from pathlib import Path
from datetime import datetime, timezone

import requests

# ==========================================
# MEME PUMP RADAR AI — SOLANA
# Dati dashboard + alert individuali Telegram
# Nessun riepilogo. Nessun trading automatico.
# ==========================================

DEX_API = "https://api.dexscreener.com"
TELEGRAM_API = "https://api.telegram.org"

# FILTRI ORIGINALI: NON MODIFICATI
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

STATE_FILE = Path("radar_state.json")
RADAR_DATA_FILE = Path("radar_data.json")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SOL_MINTS = {
    "So11111111111111111111111111111111111111112",
}

QUERIES = [
    "bonk", "dogwifhat", "popcat", "pepe",
    "pump", "meme", "frog", "cat", "inu",
    "ai meme", "solana meme", "fair launch",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/8.0"
})


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def load_state():
    if not STATE_FILE.exists():
        return {"active_pairs": [], "volume_history": {}}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            return None

        active = data.get("active_pairs", [])
        history = data.get("volume_history", {})

        if not isinstance(active, list) or not isinstance(history, dict):
            return None

        return {
            "active_pairs": [x for x in active if isinstance(x, str)],
            "volume_history": history,
        }

    except (OSError, ValueError):
        logging.exception("Impossibile leggere lo stato")
        return None


def save_state(active_pairs, history):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps({
            "active_pairs": sorted(active_pairs),
            "volume_history": history,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATE_FILE)


def get_solana_pairs():
    """Cerca coppie Solana su DexScreener."""

    pairs_by_id = {}
    successful = 0

    for query in QUERIES:
        try:
            response = session.get(
                f"{DEX_API}/latest/dex/search",
                params={"q": query},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            results = response.json().get("pairs")

            if not isinstance(results, list):
                continue

            successful += 1

            for pair in results:
                if not isinstance(pair, dict):
                    continue
                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}
                base_symbol = str(base.get("symbol") or "").upper()
                base_address = str(base.get("address") or "")

                if base_symbol in {"SOL", "WSOL"}:
                    continue
                if base_address in SOL_MINTS:
                    continue

                pair_id = pair.get("pairAddress")
                if pair_id:
                    pairs_by_id[pair_id] = pair

        except (requests.RequestException, ValueError) as exc:
            logging.warning("Ricerca '%s' fallita: %s", query, exc)

        time.sleep(REQUEST_DELAY_SECONDS)

    logging.info(
        "Query riuscite=%d/%d; coppie Solana=%d",
        successful, len(QUERIES), len(pairs_by_id),
    )

    if successful == 0:
        return None

    return list(pairs_by_id.values())


def save_dashboard_data(pairs):
    """Salva le coppie reali raccolte per il dashboard."""

    tokens = []

    for pair in pairs:
        base = pair.get("baseToken") or {}
        volume = pair.get("volume") or {}
        changes = pair.get("priceChange") or {}
        liquidity = pair.get("liquidity") or {}
        tx5 = (pair.get("txns") or {}).get("m5") or {}

        buys = int(number(tx5.get("buys")))
        sells = int(number(tx5.get("sells")))

        if sells:
            ratio = buys / sells
        elif buys:
            ratio = 999999
        else:
            ratio = 0

        created = number(pair.get("pairCreatedAt"))
        age_days = (
            max(0, (time.time() * 1000 - created) / 86400000)
            if created > 0 else None
        )

        tokens.append({
            "symbol": str(base.get("symbol") or "?"),
            "name": str(base.get("name") or "Sconosciuto"),
            "pairAddress": str(pair.get("pairAddress") or ""),
            "liquidity": number(liquidity.get("usd")),
            "volume5m": number(volume.get("m5")),
            "volume1h": number(volume.get("h1")),
            "change5m": number(changes.get("m5")),
            "change1h": number(changes.get("h1")),
            "buySellRatio": ratio,
            "buys": buys,
            "sells": sells,
            "ageDays": age_days,
            "price": number(pair.get("priceUsd")),
            "url": str(pair.get("url") or ""),
        })

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "tokenCount": len(tokens),
        "tokens": tokens,
    }

    temp = RADAR_DATA_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(RADAR_DATA_FILE)

    logging.info("Dashboard aggiornato: %d coppie", len(tokens))


def log_market_diagnostics(pairs):
    ranked = sorted(
        pairs,
        key=lambda p: number((p.get("liquidity") or {}).get("usd")),
        reverse=True,
    )

    logging.info(
        "DIAGNOSTICA MERCATO: prime %d coppie per liquidità",
        min(DIAGNOSTIC_PAIRS, len(ranked)),
    )

    for pair in ranked[:DIAGNOSTIC_PAIRS]:
        base = pair.get("baseToken") or {}
        volume = pair.get("volume") or {}
        changes = pair.get("priceChange") or {}
        tx5 = (pair.get("txns") or {}).get("m5") or {}

        logging.info(
            "MERCATO | %s (%s) | liquidita=$%.0f | "
            "volume5m=$%.0f | volume1h=$%.0f | "
            "var5m=%+.2f%% | var1h=%+.2f%% | "
            "acquisti=%d | vendite=%d",
            base.get("name", "?"),
            base.get("symbol", "?"),
            number((pair.get("liquidity") or {}).get("usd")),
            number(volume.get("m5")),
            number(volume.get("h1")),
            number(changes.get("m5")),
            number(changes.get("h1")),
            int(number(tx5.get("buys"))),
            int(number(tx5.get("sells"))),
        )


def evaluate_pair(pair, history, stats):
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    tx5 = (pair.get("txns") or {}).get("m5") or {}

    vol_m5 = number(volume.get("m5"))
    vol_h1 = number(volume.get("h1"))
    change_m5 = number(changes.get("m5"))
    change_h1 = number(changes.get("h1"))

    buys = int(number(tx5.get("buys")))
    sells = int(number(tx5.get("sells")))

    if liquidity < MIN_LIQUIDITY_USD:
        stats["liquidita"] += 1
        return None
    if vol_m5 < MIN_VOLUME_5M_USD:
        stats["volume_5m"] += 1
        return None
    if vol_h1 < MIN_VOLUME_1H_USD:
        stats["volume_1h"] += 1
        return None
    if not MIN_CHANGE_5M_PCT <= change_m5 <= MAX_CHANGE_5M_PCT:
        stats["variazione_5m"] += 1
        return None
    if change_h1 < MIN_CHANGE_1H_PCT:
        stats["variazione_1h"] += 1
        return None
    if buys + sells == 0:
        stats["transazioni_assenti"] += 1
        return None

    ratio = buys / sells if sells else float("inf")
    if ratio < MIN_BUY_SELL_RATIO:
        stats["rapporto_acquisti_vendite"] += 1
        return None

    stats["candidati"] += 1

    pair_id = pair.get("pairAddress", "")
    previous = history.get(pair_id, {})
    previous_volume = number(previous.get("volume_5m"))

    acceleration = None
    if previous_volume > 0:
        acceleration = (
            (vol_m5 - previous_volume) / previous_volume * 100
        )

    score = (
        int(change_m5 >= 5)
        + int(change_h1 >= 8)
        + int(vol_m5 >= 15_000)
        + int(buys > sells)
        + int(vol_h1 >= 50_000)
        + int(acceleration is not None and acceleration >= 20)
    )

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
        "buys": buys,
        "sells": sells,
        "ratio": ratio,
        "acceleration": acceleration,
        "score": min(score, 5),
        "url": pair.get("url") or "",
    }


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Segreti Telegram mancanti")
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

        if response.json().get("ok") is not True:
            logging.error("Telegram ha rifiutato il messaggio")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        logging.error("Invio Telegram fallito: %s", exc)
        return False


def format_alert(item):
    price_text = (
        f"${item['price']:.10f}" if item["price"] > 0 else "N/D"
    )
    ratio_text = (
        "N/D" if math.isinf(item["ratio"])
        else f"{item['ratio']:.2f}:1"
    )
    acceleration = item["acceleration"]
    acceleration_text = (
        "Storico insufficiente" if acceleration is None
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
        f"Acquisti/vendite: {item['buys']}/{item['sells']}\n"
        f"Rapporto acquisti/vendite: {ratio_text}\n"
        f"Volume rispetto alla scansione precedente: {acceleration_text}\n"
        f"Radar score: {item['score']}/5\n\n"
        f"Grafico: {item['url']}\n\n"
        "⚠️ Segnale sperimentale, non consiglio finanziario.\n"
        "Nessun acquisto automatico."
    )


def main():
    logging.info("Avvio Meme Pump Radar AI")

    state = load_state()
    if state is None:
        logging.error("Stato non valido: scansione interrotta")
        return

    pairs = get_solana_pairs()
    if pairs is None:
        logging.error("Ricerca fallita: stato conservato")
        return

    if not pairs:
        logging.warning("Nessuna coppia trovata: stato conservato")
        return

    log_market_diagnostics(pairs)

    # Pubblica i dati per il dashboard a ogni scansione riuscita.
    try:
        save_dashboard_data(pairs)
    except (OSError, TypeError, ValueError) as exc:
        logging.exception("Salvataggio dashboard fallito: %s", exc)

    history = state["volume_history"]
    previous_active = set(state["active_pairs"])

    stats = {
        "liquidita": 0,
        "volume_5m": 0,
        "volume_1h": 0,
        "variazione_5m": 0,
        "variazione_1h": 0,
        "transazioni_assenti": 0,
        "rapporto_acquisti_vendite": 0,
        "candidati": 0,
    }

    candidates = []
    for pair in pairs:
        item = evaluate_pair(pair, history, stats)
        if item:
            candidates.append(item)

    candidates.sort(
        key=lambda x: (x["score"], x["vol_m5"], x["liquidity"]),
        reverse=True,
    )

    logging.info("DIAGNOSTICA FILTRI: %s", stats)

    current_active = {
        item["pair_id"] for item in candidates if item["pair_id"]
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
        item["pair_id"] for item in new_pumps[MAX_ALERTS_PER_RUN:]
        if item["pair_id"]
    }

    next_active = (
        previous_active.intersection(current_active) | sent_ids
    )
    next_active -= failed_ids
    next_active -= deferred_ids

    now = datetime.now(timezone.utc).isoformat()

    for pair in pairs:
        pair_id = pair.get("pairAddress")
        if pair_id:
            history[pair_id] = {
                "volume_5m": number(
                    (pair.get("volume") or {}).get("m5")
                ),
                "observed_at": now,
            }

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
        logging.error("Salvataggio stato fallito: %s", exc)

    logging.info(
        "Scansione completata: coppie=%d, candidati=%d, "
        "nuovi_segnali=%d, alert_inviati=%d, falliti=%d",
        len(pairs), len(candidates), len(new_pumps),
        len(sent_ids), len(failed_ids),
    )
    logging.info("Radar terminato")


if __name__ == "__main__":
    main()
