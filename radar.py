
import json
import os
import time
from datetime import datetime, timezone

import requests

from telegram_menu import process_telegram_updates, check_price_alert

# ============================================================
# MEME PUMP RADAR AI - SOLANA
# Liquidita minima: $25.000 | Punteggio minimo notifiche: 4/9
# ============================================================

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 4
ALERT_COOLDOWN_MINUTES = 3
MAX_PAIRS_TO_ANALYZE = 250
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 0.15
MAX_SNAPSHOTS = 5000

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
session.headers.update({"User-Agent": "MemePumpRadarAI/4.0"})


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def number(value, default=0.0):
    try:
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return default
        return result
    except (TypeError, ValueError):
        return default


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def get_json(url, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            return None, "Risposta API non valida"

        return payload, None

    except (requests.RequestException, ValueError) as error:
        return None, str(error)


def get_pairs_from_search(query):
    payload, error = get_json(DEX_SEARCH_URL, {"q": query})

    if error:
        print(f"[API] Ricerca '{query}' fallita: {error}")
        return []

    pairs = payload.get("pairs") or []

    return [
        pair
        for pair in pairs
        if isinstance(pair, dict)
        and pair.get("chainId") == "solana"
    ]


def discover_pairs():
    found = {}

    for query in SEARCH_QUERIES:
        for pair in get_pairs_from_search(query):
            address = str(pair.get("pairAddress") or "").strip()

            if address:
                found[address.lower()] = pair

        time.sleep(REQUEST_DELAY_SECONDS)

    pairs = list(found.values())

    pairs.sort(
        key=lambda pair: number(
            (pair.get("liquidity") or {}).get("usd")
        ),
        reverse=True,
    )

    return pairs[:MAX_PAIRS_TO_ANALYZE]


def refresh_pair(pair):
    """Verifica la coppia con l'endpoint dedicato di Dexscreener."""

    address = str(pair.get("pairAddress") or "").strip()

    if not address:
        return pair, False, "Indirizzo coppia mancante"

    payload, error = get_json(DEX_PAIR_URL + address)

    if error:
        print(
            f"[API] Verifica coppia {address}: {error}; "
            "uso ricerca iniziale."
        )
        return pair, False, error

    matches = payload.get("pairs") or []

    for item in matches:
        if (
            item.get("chainId") == "solana"
            and str(item.get("pairAddress") or "").lower()
            == address.lower()
        ):
            return item, True, None

    # Fallback: recupera le coppie tramite l'indirizzo del token.
    token_address = (
        (pair.get("baseToken") or {}).get("address") or ""
    ).strip()

    if token_address:
        token_payload, token_error = get_json(
            DEX_TOKEN_URL + token_address
        )

        if not token_error:
            for item in token_payload.get("pairs") or []:
                if (
                    item.get("chainId") == "solana"
                    and str(item.get("pairAddress") or "").lower()
                    == address.lower()
                ):
                    return item, True, None

    return pair, False, "Coppia non trovata nell'endpoint di verifica"


def get_metrics(pair):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    changes = pair.get("priceChange") or {}

    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys5 = int(number(tx5.get("buys")))
    sells5 = int(number(tx5.get("sells")))
    buys1h = int(number(tx1h.get("buys")))
    sells1h = int(number(tx1h.get("sells")))

    created_ms = number(pair.get("pairCreatedAt"), -1)
    age_minutes = None

    if created_ms > 0:
        age_minutes = max(
            0.0,
            (time.time() * 1000 - created_ms) / 60000,
        )

    volume5m = number(volume.get("m5"))
    volume1h = number(volume.get("h1"))
    volume24h = number(volume.get("h24"))

    address = str(pair.get("pairAddress") or "")
    token_address = str(base.get("address") or "")

    return {
        "pair_address": address,
        "dex": pair.get("dexId") or "sconosciuto",
        "token_name": base.get("name") or "Sconosciuto",
        "token_symbol": base.get("symbol") or "?",
        "token_address": token_address,
        "quote_symbol": quote.get("symbol") or "",
        "url": pair.get("url") or (
            f"https://dexscreener.com/solana/{address.lower()}"
            if address
            else ""
        ),
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": number(liquidity.get("usd")),
        "volume_5m_usd": volume5m,
        "volume_1h_usd": volume1h,
        "volume_24h_usd": volume24h,
        "buys_5m": buys5,
        "sells_5m": sells5,
        "transactions_5m": buys5 + sells5,
        "buys_1h": buys1h,
        "sells_1h": sells1h,
        "transactions_1h": buys1h + sells1h,
        "buy_sell_ratio_5m": (
            round(buys5 / sells5, 2)
            if sells5
            else (float(buys5) if buys5 else 0.0)
        ),
        "price_change_5m_pct": number(changes.get("m5")),
        "price_change_1h_pct": number(changes.get("h1")),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None
            else None
        ),
        "data_source": "dexscreener_pair_endpoint",
    }


def score_pair(metrics, previous):
    """
    Punteggio massimo 9:
    volume 5m (1), volume 1h (1), crescita volume (2),
    crescita transazioni (1), pressione acquisti (1),
    accelerazione volume (1), movimento iniziale (1),
    liquidita in crescita (1).

    L'eta NON esclude la coppia.
    """

    score = 0
    checks = {}

    vol5 = metrics["volume_5m_usd"]
    vol1h = metrics["volume_1h_usd"]
    liq = metrics["liquidity_usd"]
    tx5 = metrics["transactions_5m"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    checks["volume_5m"] = vol5 >= 1_000
    if checks["volume_5m"]:
        score += 1

    checks["volume_1h"] = vol1h >= 10_000
    if checks["volume_1h"]:
        score += 1

    prev_vol1h = number(previous.get("volume_1h_usd"))
    vol_growth = (
        vol1h / prev_vol1h
        if prev_vol1h > 0
        else None
    )

    checks["volume_in_crescita"] = (
        vol_growth is not None and vol_growth >= 1.20
    )
    if checks["volume_in_crescita"]:
        score += 2

    prev_tx5 = number(previous.get("transactions_5m"))
    tx_growth = (
        tx5 / prev_tx5
        if prev_tx5 > 0
        else None
    )

    checks["attivita_in_crescita"] = (
        tx_growth is not None and tx_growth >= 1.20
    )
    if checks["attivita_in_crescita"]:
        score += 1

    buy_ratio = (
        buys5 / sells5
        if sells5 > 0
        else (float(buys5) if buys5 else 0.0)
    )

    checks["pressione_acquisti"] = (
        tx5 >= 5 and buy_ratio >= 1.20
    )
    if checks["pressione_acquisti"]:
        score += 1

    average_5m_volume = vol1h / 12 if vol1h > 0 else 0

    checks["accelerazione_volume"] = (
        average_5m_volume > 0
        and vol5 >= average_5m_volume * 1.50
    )
    if checks["accelerazione_volume"]:
        score += 1

    change5 = metrics["price_change_5m_pct"]
    change1h = metrics["price_change_1h_pct"]

    checks["movimento_iniziale"] = (
        0 < change5 <= 8 and change1h <= 20
    )
    if checks["movimento_iniziale"]:
        score += 1

    prev_liq = number(previous.get("liquidity_usd"))

    checks["liquidita_in_crescita"] = (
        prev_liq > 0 and liq >= prev_liq * 1.05
    )
    if checks["liquidita_in_crescita"]:
        score += 1

    growth = {
        "volume_growth_ratio": (
            round(vol_growth, 3)
            if vol_growth is not None
            else None
        ),
        "transaction_growth_ratio": (
            round(tx_growth, 3)
            if tx_growth is not None
            else None
        ),
        "buy_sell_ratio_5m": round(buy_ratio, 2),
    }

    return score, checks, growth


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] Mancano TELEGRAM_BOT_TOKEN o "
            "TELEGRAM_CHAT_ID nei GitHub Secrets."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        payload = response.json()

        if payload.get("ok") is True:
            return True

        print(f"[TELEGRAM] Risposta non valida: {payload}")

    except (requests.RequestException, ValueError) as error:
        print(f"[TELEGRAM] Invio fallito: {error}")

    return False


def build_alert(metrics, score):
    age = metrics["age_minutes"]

    age_text = (
        f"{age:.0f} minuti"
        if age is not None
        else "non disponibile"
    )

    return (
        "🚨 MEME PUMP RADAR AI — SOLANA\n\n"
        f"Token: {metrics['token_name']} "
        f"({metrics['token_symbol']})\n"
        f"Punteggio: {score}/9\n"
        f"Liquidità: ${metrics['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${metrics['volume_5m_usd']:,.0f}\n"
        f"Volume 1h: ${metrics['volume_1h_usd']:,.0f}\n"
        f"Volume 24h: ${metrics['volume_24h_usd']:,.0f}\n"
        f"Transazioni 5m: {metrics['transactions_5m']}\n"
        f"Acquisti/vendite 5m: "
        f"{metrics['buys_5m']}/{metrics['sells_5m']}\n"
        f"Variazione prezzo 5m: "
        f"{metrics['price_change_5m_pct']:.2f}%\n"
        f"Variazione prezzo 1h: "
        f"{metrics['price_change_1h_pct']:.2f}%\n"
        f"Età coppia: {age_text}\n"
        f"DEX: {metrics['dex']}\n"
        f"Link: {metrics['url']}\n\n"
        "Segnale automatico: non garantisce un rialzo. "
        "Verifica sempre i rischi."
    )


def main():
    state = load_json(STATE_FILE, {})
    state = process_telegram_updates(state)

    snapshots = state.get("pair_snapshots", {})
    alert_history = state.get("alert_history", {})

    if not alert_history:
        alert_history = state.get("alerted_pairs", {})

    pairs = discover_pairs()

    all_results = []
    qualified_results = []
    alerts_sent = 0
    api_verified = 0
    api_fallback = 0
    current_snapshots = dict(snapshots)
    now_seconds = time.time()

    print(f"Scansione iniziata: {iso_now()}")
    print(f"Coppie Solana scoperte: {len(pairs)}")

    for original_pair in pairs:
        pair, verified, verification_error = refresh_pair(
            original_pair
        )

        if verified:
            api_verified += 1
        else:
            api_fallback += 1

        metrics = get_metrics(pair)
        address = metrics["pair_address"].lower()

        if not address:
            continue

        previous = snapshots.get(address, {})
        score, checks, growth = score_pair(metrics, previous)

        metrics["score"] = score
        metrics["checks"] = checks
        metrics["growth"] = growth
        metrics["scanned_at"] = iso_now()
        metrics["api_verified"] = verified
        metrics["api_verification_error"] = verification_error

        # L'eta e' informativa: non esclude le coppie.
        metrics["age_ok"] = True

        metrics["liquidity_ok"] = (
            metrics["liquidity_usd"] >= MIN_LIQUIDITY_USD
        )

        # Servono sia liquidita minima sia punteggio minimo.
        metrics["score_ok"] = score >= MIN_SCORE

        metrics["qualified"] = (
            metrics["liquidity_ok"]
            and metrics["score_ok"]
        )

        # Diagnostica dei dati di mercato mancanti o a zero.
        metrics["market_data_warning"] = (
            metrics["volume_5m_usd"] == 0
            and metrics["volume_1h_usd"] == 0
            and metrics["transactions_1h"] == 0
        )

        all_results.append(metrics)

        if metrics["qualified"]:
            qualified_results.append(metrics)

            last_alert = number(alert_history.get(address))
            cooldown = ALERT_COOLDOWN_MINUTES * 60

            if now_seconds - last_alert >= cooldown:
                if not state.get("new_coin_alerts_enabled", True):
                    print(
                        "[ALERT] Alert nuove coin/pre-pump "
                        "in pausa dal menu Telegram."
                    )

                elif send_telegram(build_alert(metrics, score)):
                    alert_history[address] = now_seconds
                    alerts_sent += 1

                    print(
                        f"[ALERT] Inviato "
                        f"{metrics['token_symbol']} score={score}/9"
                    )

                else:
                    print(
                        f"[ALERT] Invio non riuscito "
                        f"per {metrics['token_symbol']}"
                    )

        current_snapshots[address] = {
            "volume_1h_usd": metrics["volume_1h_usd"],
            "volume_5m_usd": metrics["volume_5m_usd"],
            "transactions_5m": metrics["transactions_5m"],
            "liquidity_usd": metrics["liquidity_usd"],
            "updated_at": iso_now(),
        }

        time.sleep(REQUEST_DELAY_SECONDS)

    if len(current_snapshots) > MAX_SNAPSHOTS:
        current_snapshots = dict(
            list(current_snapshots.items())[-MAX_SNAPSHOTS:]
        )

    state["pair_snapshots"] = current_snapshots
    state["alert_history"] = alert_history
    state["updated_at"] = iso_now()

    # Controlla l'allarme prezzo indipendente delle nuove coin.
    state = check_price_alert(state)

    save_json(STATE_FILE, state)

    output = {
        "updated_at": iso_now(),
        "pairs_scanned": len(all_results),
        "qualified_count": len(qualified_results),
        "alerts_sent": alerts_sent,
        "min_score": MIN_SCORE,
        "max_score": 9,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "age_filter_enabled": False,
        "api_verified_count": api_verified,
        "api_fallback_count": api_fallback,
        "market_data_warning_count": sum(
            1
            for item in all_results
            if item["market_data_warning"]
        ),
        "pairs": all_results,
        "qualified_pairs": qualified_results,
    }

    save_json(DATA_FILE, output)

    print("\n========== RISULTATO RADAR ==========")
    print(f"Coppie analizzate: {len(all_results)}")
    print(f"Coppie verificate con endpoint coppia: {api_verified}")
    print(f"Coppie con dati di fallback: {api_fallback}")

    print(
        "Coppie con volume e transazioni tutti a zero: "
        f"{output['market_data_warning_count']}"
    )

    print(
        f"Coppie idonee (liquidita >= "
        f"${MIN_LIQUIDITY_USD:,.0f} e punteggio >= "
        f"{MIN_SCORE}/9): {len(qualified_results)}"
    )

    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Punteggio minimo per le notifiche: {MIN_SCORE}/9")
    print("Filtro eta: disattivato (eta solo informativa)")
    print("Dati salvati in radar_data.json")
    print("Storico salvato in radar_state.json")


if __name__ == "__main__":
    main()
