
import os
import json
import time
from datetime import datetime, timezone

import requests

# ==========================================
# MEME PUMP RADAR AI - SOLANA
# ==========================================

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 6
ALERT_COOLDOWN_MINUTES = 120
MAX_PAIRS_TO_ANALYZE = 250

STATE_FILE = "radar_state.json"
DATA_FILE = "radar_data.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SEARCH_QUERIES = [
    "solana meme",
    "solana dog",
    "solana cat",
    "solana pepe",
    "solana pump",
    "solana bonk",
    "solana ai",
]

session = requests.Session()
session.headers.update({"User-Agent": "MemePumpRadarAI/3.0"})


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temporary_path = path + ".tmp"

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temporary_path, path)


def number(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def get_pairs_from_search(query):
    try:
        response = session.get(
            DEX_SEARCH_URL,
            params={"q": query},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("pairs") or []
    except (requests.RequestException, ValueError) as error:
        print(f"Ricerca '{query}' fallita: {error}")
        return []


def discover_pairs():
    pairs_by_address = {}

    for query in SEARCH_QUERIES:
        pairs = get_pairs_from_search(query)

        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue

            address = pair.get("pairAddress")

            if address:
                pairs_by_address[address.lower()] = pair

        time.sleep(0.25)

    pairs = list(pairs_by_address.values())

    pairs.sort(
        key=lambda pair: number(
            (pair.get("liquidity") or {}).get("usd")
        ),
        reverse=True,
    )

    return pairs[:MAX_PAIRS_TO_ANALYZE]


def fetch_pair_details(pair):
    address = pair.get("pairAddress")

    if not address:
        return pair

    try:
        response = session.get(
            DEX_TOKEN_URL + address,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()

        detailed_pairs = payload.get("pairs") or []

        for detailed in detailed_pairs:
            if (
                detailed.get("chainId") == "solana"
                and detailed.get("pairAddress", "").lower()
                == address.lower()
            ):
                return detailed

    except (requests.RequestException, ValueError) as error:
        print(f"Dettagli non disponibili per {address}: {error}")

    return pair


def get_metrics(pair):
    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys5 = number(tx5.get("buys"))
    sells5 = number(tx5.get("sells"))

    buys1h = number(tx1h.get("buys"))
    sells1h = number(tx1h.get("sells"))

    price_usd = number(pair.get("priceUsd"))
    volume5m = number(volume.get("m5"))
    volume1h = number(volume.get("h1"))
    volume24h = number(volume.get("h24"))

    transactions5m = buys5 + sells5
    transactions1h = buys1h + sells1h

    pair_created = pair.get("pairCreatedAt")
    age_minutes = None

    if pair_created:
        try:
            age_minutes = max(
                0,
                (time.time() * 1000 - float(pair_created)) / 60000,
            )
        except (TypeError, ValueError):
            age_minutes = None

    base_token = pair.get("baseToken") or {}
    quote_token = pair.get("quoteToken") or {}

    return {
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "token_name": base_token.get("name", "Sconosciuto"),
        "token_symbol": base_token.get("symbol", "?"),
        "token_address": base_token.get("address"),
        "quote_symbol": quote_token.get("symbol", ""),
        "url": pair.get("url", ""),
        "price_usd": price_usd,
        "liquidity_usd": liquidity,
        "volume_5m_usd": volume5m,
        "volume_1h_usd": volume1h,
        "volume_24h_usd": volume24h,
        "buys_5m": int(buys5),
        "sells_5m": int(sells5),
        "transactions_5m": int(transactions5m),
        "buys_1h": int(buys1h),
        "sells_1h": int(sells1h),
        "transactions_1h": int(transactions1h),
        "buy_sell_ratio_5m": (
            round(buys5 / sells5, 2) if sells5 > 0
            else round(buys5, 2)
        ),
        "price_change_5m_pct": number(price_change.get("m5")),
        "price_change_1h_pct": number(price_change.get("h1")),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None else None
        ),
    }


def score_pair(metrics, previous):
    score = 0
    checks = {}

    liquidity = metrics["liquidity_usd"]
    volume5m = metrics["volume_5m_usd"]
    volume1h = metrics["volume_1h_usd"]
    transactions5m = metrics["transactions_5m"]
    buys5m = metrics["buys_5m"]
    sells5m = metrics["sells_5m"]

    # 1. Presenza di volume recente.
    checks["volume_5m"] = volume5m >= 1_000
    if checks["volume_5m"]:
        score += 1

    # 2. Attività di volume nell'ultima ora.
    checks["volume_1h"] = volume1h >= 10_000
    if checks["volume_1h"]:
        score += 1

    # 3. Crescita del volume rispetto alla scansione precedente.
    previous_volume = number(previous.get("volume_1h_usd"))
    volume_growth = (
        volume1h / previous_volume
        if previous_volume > 0 else None
    )

    checks["volume_in_crescita"] = (
        volume_growth is not None and volume_growth >= 1.20
    )
    if checks["volume_in_crescita"]:
        score += 2

    # 4. Crescita delle transazioni rispetto alla scansione precedente.
    previous_tx = number(previous.get("transactions_5m"))
    tx_growth = (
        transactions5m / previous_tx
        if previous_tx > 0 else None
    )

    checks["attivita_in_crescita"] = (
        tx_growth is not None and tx_growth >= 1.20
    )
    if checks["attivita_in_crescita"]:
        score += 1

    # 5. Pressione degli acquisti negli ultimi 5 minuti.
    buy_ratio = (
        buys5m / sells5m
        if sells5m > 0 else (buys5m if buys5m > 0 else 0)
    )

    checks["pressione_acquisti"] = (
        transactions5m >= 5 and buy_ratio >= 1.20
    )
    if checks["pressione_acquisti"]:
        score += 1

    # 6. Volume recente elevato rispetto alla media oraria.
    average_5m_volume = volume1h / 12 if volume1h > 0 else 0

    checks["accelerazione_volume"] = (
        average_5m_volume > 0
        and volume5m >= average_5m_volume * 1.50
    )
    if checks["accelerazione_volume"]:
        score += 1

    # 7. Movimento iniziale del prezzo, non un rialzo estremo.
    change5m = metrics["price_change_5m_pct"]
    change1h = metrics["price_change_1h_pct"]

    checks["movimento_iniziale"] = (
        0 < change5m <= 8 and change1h <= 20
    )
    if checks["movimento_iniziale"]:
        score += 1

    # 8. Crescita della liquidità rispetto alla scansione precedente.
    previous_liquidity = number(previous.get("liquidity_usd"))

    checks["liquidita_in_crescita"] = (
        previous_liquidity > 0
        and liquidity >= previous_liquidity * 1.05
    )
    if checks["liquidita_in_crescita"]:
        score += 1

    return score, checks, {
        "volume_growth_ratio": volume_growth,
        "transaction_growth_ratio": tx_growth,
        "buy_sell_ratio_5m": round(buy_ratio, 2),
    }


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERRORE: mancano i segreti Telegram.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    try:
        response = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print(f"Telegram ha rifiutato il messaggio: {result}")
            return False

        return True

    except (requests.RequestException, ValueError) as error:
        print(f"Invio Telegram fallito: {error}")
        return False


def build_alert(metrics, score, checks):
    age = metrics["age_minutes"]
    age_text = f"{age:.0f} min" if age is not None else "non disponibile"

    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {metrics['token_name']} "
        f"({metrics['token_symbol']})\n"
        f"Punteggio: {score}/9\n"
        f"Liquidità: ${metrics['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${metrics['volume_5m_usd']:,.0f}\n"
        f"Volume 1h: ${metrics['volume_1h_usd']:,.0f}\n"
        f"Transazioni 5m: {metrics['transactions_5m']}\n"
        f"Acquisti/vendite 5m: "
        f"{metrics['buys_5m']}/{metrics['sells_5m']}\n"
        f"Variazione prezzo 5m: "
        f"{metrics['price_change_5m_pct']:.2f}%\n"
        f"Variazione prezzo 1h: "
        f"{metrics['price_change_1h_pct']:.2f}%\n"
        f"Età coppia: {age_text}\n\n"
        f"DEX: {metrics['dex']}\n"
        f"Link: {metrics['url']}\n\n"
        "Segnale automatico, non garanzia di rialzo. "
        "Verifica sempre i rischi del token."
    )


def main():
    state = load_json(STATE_FILE, {})
    pair_snapshots = state.get("pair_snapshots", {})
    alert_history = state.get("alert_history", {})

    # Compatibilità con il vecchio formato dello storico.
    if not alert_history:
        alert_history = state.get("alerted_pairs", {})

    pairs = discover_pairs()

    all_results = []
    qualified_results = []
    alerts_sent = 0
    current_snapshots = dict(pair_snapshots)
    now_seconds = time.time()

    print(f"Scansione iniziata: {iso_now()}")
    print(f"Coppie Solana trovate: {len(pairs)}")

    for pair in pairs:
        metrics = get_metrics(pair)
        address = (metrics.get("pair_address") or "").lower()

        if not address:
            continue

        previous = pair_snapshots.get(address, {})

        score, checks, growth = score_pair(metrics, previous)

        metrics["score"] = score
        metrics["checks"] = checks
        metrics["growth"] = growth
        metrics["scanned_at"] = iso_now()

        # L'età sconosciuta non esclude automaticamente una coppia.
        age = metrics["age_minutes"]
        age_ok = age is None or age <= 120

        liquidity_ok = (
            metrics["liquidity_usd"] >= MIN_LIQUIDITY_USD
        )

        metrics["liquidity_ok"] = liquidity_ok
        metrics["age_ok"] = age_ok
        metrics["qualified"] = (
            liquidity_ok and age_ok and score >= MIN_SCORE
        )

        all_results.append(metrics)

        if metrics["qualified"]:
            qualified_results.append(metrics)

            last_alert = number(alert_history.get(address))
            cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

            if now_seconds - last_alert >= cooldown_seconds:
                message = build_alert(metrics, score, checks)

                if send_telegram(message):
                    alert_history[address] = now_seconds
                    alerts_sent += 1
                    print(
                        f"AVVISO INVIATO: {metrics['token_symbol']} "
                        f"(punteggio {score})"
                    )
                else:
                    print(
                        f"Avviso non inviato: "
                        f"{metrics['token_symbol']}"
                    )

        # Salva lo snapshot per la prossima scansione.
        current_snapshots[address] = {
            "volume_1h_usd": metrics["volume_1h_usd"],
            "volume_5m_usd": metrics["volume_5m_usd"],
            "transactions_5m": metrics["transactions_5m"],
            "liquidity_usd": metrics["liquidity_usd"],
            "updated_at": iso_now(),
        }

        time.sleep(0.10)

    # Limita la crescita dello storico a 5.000 coppie.
    if len(current_snapshots) > 5000:
        current_snapshots = dict(
            list(current_snapshots.items())[-5000:]
        )

    state["pair_snapshots"] = current_snapshots
    state["alert_history"] = alert_history
    state["updated_at"] = iso_now()

    save_json(STATE_FILE, state)

    output = {
        "updated_at": iso_now(),
        "pairs_scanned": len(all_results),
        "qualified_count": len(qualified_results),
        "alerts_sent": alerts_sent,
        "min_score": MIN_SCORE,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "pairs": all_results,
        "qualified_pairs": qualified_results,
    }

    save_json(DATA_FILE, output)

    print("\n========== RISULTATO RADAR ==========")
    print(f"Coppie analizzate: {len(all_results)}")
    print(f"Coppie con punteggio >= {MIN_SCORE} "
          f"e liquidità sufficiente: {len(qualified_results)}")
    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Soglia punteggio: {MIN_SCORE}/9")
    print(f"Liquidità minima: ${MIN_LIQUIDITY_USD:,.0f}")
    print("Dati salvati in radar_data.json")
    print("Storico salvato in radar_state.json")


if __name__ == "__main__":
    main()
