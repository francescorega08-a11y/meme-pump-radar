
import json
import os
import time
from datetime import datetime, timezone

import requests

# ============================================================
# MEME PUMP RADAR AI - SOLANA
# Liquidita minima: $25.000
# Punteggio minimo: 4/9
# Cooldown notifiche: 120 minuti
# ============================================================

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 4
MAX_SCORE = 9
ALERT_COOLDOWN_MINUTES = 120
MAX_PAIRS_TO_ANALYZE = 250
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 0.15
MAX_SNAPSHOTS = 5000
MAX_SNAPSHOT_AGE_MINUTES = 30

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
session.headers.update({"User-Agent": "MemePumpRadarAI/5.0"})


def iso_now():
    return datetime.now(timezone.utc).isoformat()


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
            data = json.load(file)
            return data if isinstance(data, dict) else default
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
    payload, error = get_json(
        DEX_SEARCH_URL,
        {"q": query},
    )

    if error:
        print(f"[API] Ricerca '{query}' fallita: {error}")
        return []

    return [
        pair
        for pair in (payload.get("pairs") or [])
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
    address = str(pair.get("pairAddress") or "").strip()

    if not address:
        return pair, False, "Indirizzo coppia mancante"

    payload, error = get_json(DEX_PAIR_URL + address)

    if not error:
        for item in payload.get("pairs") or []:
            if (
                isinstance(item, dict)
                and item.get("chainId") == "solana"
                and str(item.get("pairAddress") or "").lower()
                == address.lower()
            ):
                return item, True, None

    token_address = str(
        (pair.get("baseToken") or {}).get("address") or ""
    ).strip()

    if token_address:
        token_payload, token_error = get_json(
            DEX_TOKEN_URL + token_address
        )

        if not token_error:
            for item in token_payload.get("pairs") or []:
                if (
                    isinstance(item, dict)
                    and item.get("chainId") == "solana"
                    and str(item.get("pairAddress") or "").lower()
                    == address.lower()
                ):
                    return item, True, None

    reason = error or "Verifica coppia non riuscita"
    print(f"[API] Dati iniziali usati per {address}: {reason}")

    return pair, False, reason


def get_metrics(pair):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    changes = pair.get("priceChange") or {}

    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys5 = max(0, int(number(tx5.get("buys"))))
    sells5 = max(0, int(number(tx5.get("sells"))))
    buys1h = max(0, int(number(tx1h.get("buys"))))
    sells1h = max(0, int(number(tx1h.get("sells"))))

    created_ms = number(pair.get("pairCreatedAt"), -1)
    age_minutes = None

    if created_ms > 0:
        age_minutes = max(
            0.0,
            (time.time() * 1000 - created_ms) / 60000,
        )

    volume5m = max(0.0, number(volume.get("m5")))
    volume1h = max(0.0, number(volume.get("h1")))
    volume24h = max(0.0, number(volume.get("h24")))

    address = str(pair.get("pairAddress") or "").strip()
    token_address = str(base.get("address") or "").strip()

    url = pair.get("url") or (
        f"https://dexscreener.com/solana/{address.lower()}"
        if address else ""
    )

    buy_sell_ratio = (
        round(buys5 / sells5, 2)
        if sells5 > 0
        else float(buys5)
    )

    return {
        "pair_address": address,
        "dex": pair.get("dexId") or "sconosciuto",
        "token_name": str(base.get("name") or "Sconosciuto").strip(),
        "token_symbol": str(base.get("symbol") or "?").strip(),
        "token_address": token_address,
        "quote_symbol": quote.get("symbol") or "",
        "url": url,
        "dex_url": url,
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": max(0.0, number(liquidity.get("usd"))),
        "volume_5m_usd": volume5m,
        "volume_1h_usd": volume1h,
        "volume_24h_usd": volume24h,
        "volume_5m": volume5m,
        "volume_1h": volume1h,
        "buys_5m": buys5,
        "sells_5m": sells5,
        "transactions_5m": buys5 + sells5,
        "buys_1h": buys1h,
        "sells_1h": sells1h,
        "transactions_1h": buys1h + sells1h,
        "buy_sell_ratio_5m": buy_sell_ratio,
        "buy_sell_ratio": buy_sell_ratio,
        "price_change_5m_pct": number(changes.get("m5")),
        "price_change_1h_pct": number(changes.get("h1")),
        "change_5m": number(changes.get("m5")),
        "change_1h": number(changes.get("h1")),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None else None
        ),
        "data_source": "dexscreener",
    }


def score_pair(metrics, previous, previous_is_recent):
    score = 0
    checks = {}

    vol5 = metrics["volume_5m_usd"]
    vol1h = metrics["volume_1h_usd"]
    liquidity = metrics["liquidity_usd"]
    tx5 = metrics["transactions_5m"]
    tx1h = metrics["transactions_1h"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    checks["volume_5m"] = vol5 >= 1_000
    score += int(checks["volume_5m"])

    checks["volume_1h"] = vol1h >= 10_000
    score += int(checks["volume_1h"])

    previous_vol1h = number(previous.get("volume_1h_usd"))
    volume_growth = (
        vol1h / previous_vol1h
        if previous_vol1h > 0 else None
    )

    checks["volume_in_crescita"] = (
        previous_is_recent
        and volume_growth is not None
        and volume_growth >= 1.20
    )
    score += 2 * int(checks["volume_in_crescita"])

    previous_tx5 = number(previous.get("transactions_5m"))
    transaction_growth = (
        tx5 / previous_tx5
        if previous_tx5 > 0 else None
    )

    checks["attivita_in_crescita"] = (
        previous_is_recent
        and transaction_growth is not None
        and transaction_growth >= 1.20
    )
    score += int(checks["attivita_in_crescita"])

    buy_ratio = (
        buys5 / sells5
        if sells5 > 0
        else (float(buys5) if buys5 else 0.0)
    )

    checks["pressione_acquisti"] = (
        tx5 >= 5 and buy_ratio >= 1.20
    )
    score += int(checks["pressione_acquisti"])

    average_5m_volume = vol1h / 12 if vol1h > 0 else 0.0

    checks["accelerazione_volume"] = (
        average_5m_volume > 0
        and vol5 >= average_5m_volume * 1.50
    )
    score += int(checks["accelerazione_volume"])

    change5 = metrics["price_change_5m_pct"]
    change1h = metrics["price_change_1h_pct"]

    checks["movimento_iniziale"] = (
        0 < change5 <= 8 and change1h <= 20
    )
    score += int(checks["movimento_iniziale"])

    previous_liquidity = number(previous.get("liquidity_usd"))

    checks["liquidita_in_crescita"] = (
        previous_is_recent
        and previous_liquidity > 0
        and liquidity >= previous_liquidity * 1.05
    )
    score += int(checks["liquidita_in_crescita"])

    # Nono punto: attivita assoluta nell'ultima ora.
    checks["attivita_1h"] = tx1h >= 20
    score += int(checks["attivita_1h"])

    growth = {
        "volume_growth_ratio": (
            round(volume_growth, 3)
            if volume_growth is not None else None
        ),
        "transaction_growth_ratio": (
            round(transaction_growth, 3)
            if transaction_growth is not None else None
        ),
        "buy_sell_ratio_5m": round(buy_ratio, 2),
    }

    return min(score, MAX_SCORE), checks, growth


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] Invio impossibile: "
            "token o chat ID mancante."
        )
        return False

    url = (
        f"https://api.telegram.org/bot"
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

        print(
            "[TELEGRAM] API ha rifiutato il messaggio: "
            f"{payload.get('description', payload)}"
        )

    except (requests.RequestException, ValueError) as error:
        print(f"[TELEGRAM] Invio fallito: {error}")

    return False


def build_alert(metrics, score):
    age = metrics["age_minutes"]

    age_text = (
        f"{age:.0f} minuti"
        if age is not None else "non disponibile"
    )

    return (
        "🚨 MEME PUMP RADAR AI — SOLANA\n\n"
        f"Token: {metrics['token_name']} "
        f"({metrics['token_symbol']})\n"
        f"Punteggio: {score}/{MAX_SCORE}\n"
        f"Liquidità: ${metrics['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${metrics['volume_5m_usd']:,.0f}\n"
        f"Volume 1h: ${metrics['volume_1h_usd']:,.0f}\n"
        f"Volume 24h: ${metrics['volume_24h_usd']:,.0f}\n"
        f"Transazioni 5m: {metrics['transactions_5m']}\n"
        f"Transazioni 1h: {metrics['transactions_1h']}\n"
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

    snapshots = state.get("pair_snapshots", {})
    alert_history = state.get("alert_history", {})

    if not isinstance(snapshots, dict):
        snapshots = {}

    if not isinstance(alert_history, dict):
        alert_history = {}

    # Compatibilita con il vecchio formato dello storico.
    if not alert_history and isinstance(state.get("alerted_pairs"), dict):
        alert_history = state["alerted_pairs"]

    pairs = discover_pairs()

    all_results = []
    qualified_results = []
    alerts_sent = 0
    api_verified = 0
    api_fallback = 0
    skipped_incomplete = 0
    cooldown_skipped = 0

    current_snapshots = dict(snapshots)
    now_seconds = time.time()

    print(f"Scansione iniziata: {iso_now()}")
    print(f"Coppie Solana scoperte: {len(pairs)}")

    for original_pair in pairs:
        pair, verified, verification_error = refresh_pair(original_pair)

        if verified:
            api_verified += 1
        else:
            api_fallback += 1

        metrics = get_metrics(pair)
        address = metrics["pair_address"].lower()

        if not address:
            continue

        previous = snapshots.get(address, {})
        previous_time = number(previous.get("updated_at_epoch"))

        previous_is_recent = (
            previous_time > 0
            and 0 <= now_seconds - previous_time
            <= MAX_SNAPSHOT_AGE_MINUTES * 60
        )

        score, checks, growth = score_pair(
            metrics,
            previous,
            previous_is_recent,
        )

        metrics["score"] = score
        metrics["checks"] = checks
        metrics["growth"] = growth
        metrics["scanned_at"] = iso_now()
        metrics["api_verified"] = verified
        metrics["api_verification_error"] = verification_error

        metrics["liquidity_ok"] = (
            metrics["liquidity_usd"] >= MIN_LIQUIDITY_USD
        )
        metrics["score_ok"] = score >= MIN_SCORE

        market_data_warning = (
            metrics["volume_5m_usd"] <= 0
            and metrics["volume_1h_usd"] <= 0
            and metrics["transactions_1h"] <= 0
        )

        metrics["market_data_warning"] = market_data_warning

        metrics["market_data_ok"] = (
            verified
            and metrics["price_usd"] > 0
            and metrics["liquidity_usd"] > 0
            and bool(metrics["token_address"])
            and bool(metrics["pair_address"])
            and not market_data_warning
            and (
                metrics["volume_5m_usd"] > 0
                or metrics["volume_1h_usd"] > 0
            )
            and metrics["transactions_1h"] > 0
        )

        metrics["age_ok"] = True

        metrics["qualified"] = (
            metrics["liquidity_ok"]
            and metrics["score_ok"]
            and metrics["market_data_ok"]
        )

        all_results.append(metrics)

        if metrics["qualified"]:
            qualified_results.append(metrics)

            # Cooldown basato sulla coppia DEX.
            last_alert = number(alert_history.get(address))
            elapsed = (
                now_seconds - last_alert
                if last_alert > 0
                else float("inf")
            )

            cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

            if elapsed < 0:
                print(
                    f"[COOLDOWN] Timestamp futuro per "
                    f"{metrics['token_symbol']}; reset."
                )
                alert_history.pop(address, None)
                elapsed = float("inf")

            if elapsed >= cooldown_seconds:
                sent = send_telegram(build_alert(metrics, score))

                if sent:
                    alert_history[address] = now_seconds
                    alerts_sent += 1
                    print(
                        f"[ALERT] Inviato "
                        f"{metrics['token_symbol']} "
                        f"score={score}/{MAX_SCORE}"
                    )
                else:
                    print(
                        f"[ALERT] Invio fallito per "
                        f"{metrics['token_symbol']}; "
                        "nuovo tentativo alla prossima scansione."
                    )
            else:
                cooldown_skipped += 1
                remaining = max(
                    1,
                    int((cooldown_seconds - elapsed) / 60) + 1,
                )

                print(
                    f"[COOLDOWN] {metrics['token_symbol']}: "
                    f"riprovare tra circa {remaining} minuti."
                )

        elif (
            metrics["liquidity_ok"]
            and metrics["score_ok"]
            and not metrics["market_data_ok"]
        ):
            skipped_incomplete += 1

            print(
                f"[FILTRO DATI] {metrics['token_symbol']} "
                "escluso dalle notifiche: dati non verificati, "
                "incompleti o senza attivita recente."
            )

        current_snapshots[address] = {
            "volume_1h_usd": metrics["volume_1h_usd"],
            "volume_5m_usd": metrics["volume_5m_usd"],
            "transactions_5m": metrics["transactions_5m"],
            "liquidity_usd": metrics["liquidity_usd"],
            "updated_at": iso_now(),
            "updated_at_epoch": now_seconds,
        }

        time.sleep(REQUEST_DELAY_SECONDS)

    if len(current_snapshots) > MAX_SNAPSHOTS:
        current_snapshots = dict(
            list(current_snapshots.items())[-MAX_SNAPSHOTS:]
        )

    state["pair_snapshots"] = current_snapshots
    state["alert_history"] = alert_history
    state["updated_at"] = iso_now()

    save_json(STATE_FILE, state)

    warning_count = sum(
        1 for item in all_results
        if item["market_data_warning"]
    )

    output = {
        "updated_at": iso_now(),
        "network": "solana",
        "pairs_scanned": len(all_results),
        "api_verified_count": api_verified,
        "api_verification_failed_count": api_fallback,
        "api_fallback_count": api_fallback,
        "qualified_count": len(qualified_results),
        "alerts_sent": alerts_sent,
        "cooldown_skipped_count": cooldown_skipped,
        "incomplete_data_skipped_count": skipped_incomplete,
        "min_score": MIN_SCORE,
        "max_score": MAX_SCORE,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "age_filter_enabled": False,
        "market_data_warning_count": warning_count,
        "pairs": all_results,
        "results": all_results,
        "qualified_pairs": qualified_results,
    }

    save_json(DATA_FILE, output)

    print("\n========== RISULTATO RADAR ==========")
    print(f"Coppie analizzate: {len(all_results)}")
    print(f"Coppie verificate: {api_verified}")
    print(f"Coppie con dati di fallback: {api_fallback}")
    print(
        "Coppie con volume e transazioni tutti a zero: "
        f"{warning_count}"
    )
    print(
        f"Coppie idonee (dati validi, liquidita >= "
        f"${MIN_LIQUIDITY_USD:,.0f}, punteggio >= "
        f"{MIN_SCORE}/{MAX_SCORE}): "
        f"{len(qualified_results)}"
    )
    print(f"Coppie escluse per dati incompleti: {skipped_incomplete}")
    print(f"Notifiche saltate per cooldown: {cooldown_skipped}")
    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Punteggio minimo: {MIN_SCORE}/{MAX_SCORE}")
    print("Filtro eta: disattivato")
    print("Dati salvati in radar_data.json")
    print("Storico salvato in radar_state.json")


if __name__ == "__main__":
    main()
