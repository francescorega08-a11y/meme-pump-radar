
import json
import os
import time
from datetime import datetime, timezone

import requests

# ============================================================
# MEME PUMP RADAR AI - SOLANA
# ============================================================

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 4
MAX_SCORE = 9
ALERT_COOLDOWN_MINUTES = 3
MAX_PAIRS_TO_ANALYZE = 250
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 0.15
MAX_SNAPSHOTS = 5000
MAX_SNAPSHOT_AGE_MINUTES = 30

STATE_FILE = "radar_state.json"
DATA_FILE = "radar_data.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def number(value, default=0.0):
    try:
        result = float(value)
        if result != result or result in (
            float("inf"),
            float("-inf"),
        ):
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
    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temporary, path)


def get_json(url, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            return None, "Risposta API non valida"

        return data, None

    except (requests.RequestException, ValueError) as error:
        return None, str(error)


def discover_pairs():
    found = {}

    for query in SEARCH_QUERIES:
        data, error = get_json(
            DEX_SEARCH_URL,
            {"q": query},
        )

        if error:
            print(f"[RICERCA] {query}: {error}")
            continue

        for pair in data.get("pairs") or []:
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

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
        return pair, False

    data, error = get_json(DEX_PAIR_URL + address)

    if not error:
        for item in data.get("pairs") or []:
            if (
                isinstance(item, dict)
                and item.get("chainId") == "solana"
                and str(item.get("pairAddress") or "").lower()
                == address.lower()
            ):
                return item, True

    token_address = str(
        (pair.get("baseToken") or {}).get("address") or ""
    ).strip()

    if token_address:
        token_data, token_error = get_json(
            DEX_TOKEN_URL + token_address
        )

        if not token_error:
            for item in token_data.get("pairs") or []:
                if (
                    isinstance(item, dict)
                    and item.get("chainId") == "solana"
                    and str(item.get("pairAddress") or "").lower()
                    == address.lower()
                ):
                    return item, True

    print(f"[DATI] Uso dati iniziali per {address}")
    return pair, False


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

    name = str(base.get("name") or "").strip()
    symbol = str(base.get("symbol") or "").strip()

    # Nome di riserva se l'API non restituisce il nome.
    if not name:
        name = symbol if symbol else "Nome non disponibile"

    if not symbol:
        symbol = "?"

    pair_address = str(pair.get("pairAddress") or "").strip()
    token_address = str(base.get("address") or "").strip()

    created = number(pair.get("pairCreatedAt"), -1)
    age_minutes = None

    if created > 0:
        age_minutes = max(
            0,
            (time.time() * 1000 - created) / 60000,
        )

    vol5 = max(0, number(volume.get("m5")))
    vol1h = max(0, number(volume.get("h1")))
    vol24h = max(0, number(volume.get("h24")))
    liquidity_usd = max(0, number(liquidity.get("usd")))

    buy_sell_ratio = (
        round(buys5 / sells5, 2)
        if sells5 > 0
        else float(buys5)
    )

    url = pair.get("url") or (
        f"https://dexscreener.com/solana/{pair_address}"
        if pair_address else ""
    )

    # Mantiene sia i campi standard sia quelli della dashboard.
    return {
        "name": name,
        "symbol": symbol,
        "token_name": name,
        "token_symbol": symbol,
        "baseToken": {
            "name": name,
            "symbol": symbol,
            "address": token_address,
        },
        "pair_address": pair_address,
        "pairAddress": pair_address,
        "token_address": token_address,
        "dex": pair.get("dexId") or "sconosciuto",
        "quote_symbol": quote.get("symbol") or "",
        "url": url,
        "dex_url": url,
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": liquidity_usd,
        "volume_5m_usd": vol5,
        "volume_1h_usd": vol1h,
        "volume_24h_usd": vol24h,
        "volume_5m": vol5,
        "volume_1h": vol1h,
        "buys_5m": buys5,
        "sells_5m": sells5,
        "transactions_5m": buys5 + sells5,
        "buys_1h": buys1h,
        "sells_1h": sells1h,
        "transactions_1h": buys1h + sells1h,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_sell_ratio_5m": buy_sell_ratio,
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


def score_pair(metrics, previous, previous_recent):
    score = 0
    checks = {}

    vol5 = metrics["volume_5m_usd"]
    vol1h = metrics["volume_1h_usd"]
    tx5 = metrics["transactions_5m"]
    tx1h = metrics["transactions_1h"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    checks["volume_5m"] = vol5 >= 1000
    score += int(checks["volume_5m"])

    checks["volume_1h"] = vol1h >= 10000
    score += int(checks["volume_1h"])

    previous_vol = number(previous.get("volume_1h_usd"))
    volume_growth = (
        vol1h / previous_vol
        if previous_vol > 0 else None
    )

    checks["volume_growth"] = (
        previous_recent
        and volume_growth is not None
        and volume_growth >= 1.20
    )
    score += 2 * int(checks["volume_growth"])

    previous_tx = number(previous.get("transactions_5m"))
    tx_growth = (
        tx5 / previous_tx
        if previous_tx > 0 else None
    )

    checks["transaction_growth"] = (
        previous_recent
        and tx_growth is not None
        and tx_growth >= 1.20
    )
    score += int(checks["transaction_growth"])

    ratio = buys5 / sells5 if sells5 > 0 else float(buys5)

    checks["buy_pressure"] = tx5 >= 5 and ratio >= 1.20
    score += int(checks["buy_pressure"])

    average_5m = vol1h / 12 if vol1h > 0 else 0

    checks["volume_acceleration"] = (
        average_5m > 0 and vol5 >= average_5m * 1.50
    )
    score += int(checks["volume_acceleration"])

    change5 = metrics["price_change_5m_pct"]
    change1h = metrics["price_change_1h_pct"]

    checks["early_movement"] = 0 < change5 <= 8 and change1h <= 20
    score += int(checks["early_movement"])

    previous_liquidity = number(previous.get("liquidity_usd"))

    checks["liquidity_growth"] = (
        previous_recent
        and previous_liquidity > 0
        and metrics["liquidity_usd"] >= previous_liquidity * 1.05
    )
    score += int(checks["liquidity_growth"])

    checks["hourly_activity"] = tx1h >= 20
    score += int(checks["hourly_activity"])

    growth = {
        "volume_growth_ratio": (
            round(volume_growth, 3)
            if volume_growth is not None else None
        ),
        "transaction_growth_ratio": (
            round(tx_growth, 3) if tx_growth is not None else None
        ),
    }

    return min(score, MAX_SCORE), checks, growth


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token o chat ID mancante.")
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
        data = response.json()

        if data.get("ok") is True:
            return True

        print(f"[TELEGRAM] Errore API: {data}")

    except (requests.RequestException, ValueError) as error:
        print(f"[TELEGRAM] Invio fallito: {error}")

    return False


def build_alert(metrics, score):
    age = metrics["age_minutes"]
    age_text = f"{age:.0f} minuti" if age is not None else "N/D"

    return (
        "🚨 MEME PUMP RADAR AI — SOLANA\n\n"
        f"Token: {metrics['name']} ({metrics['symbol']})\n"
        f"Punteggio: {score}/{MAX_SCORE}\n"
        f"Liquidità: ${metrics['liquidity_usd']:,.0f}\n"
        f"Volume 5m: ${metrics['volume_5m_usd']:,.0f}\n"
        f"Volume 1h: ${metrics['volume_1h_usd']:,.0f}\n"
        f"Volume 24h: ${metrics['volume_24h_usd']:,.0f}\n"
        f"Transazioni 5m: {metrics['transactions_5m']}\n"
        f"Transazioni 1h: {metrics['transactions_1h']}\n"
        f"Acquisti/vendite: "
        f"{metrics['buys_5m']}/{metrics['sells_5m']}\n"
        f"Variazione 5m: {metrics['change_5m']:.2f}%\n"
        f"Variazione 1h: {metrics['change_1h']:.2f}%\n"
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

    if not alert_history and isinstance(state.get("alerted_pairs"), dict):
        alert_history = state["alerted_pairs"]

    pairs = discover_pairs()

    results = []
    qualified_pairs = []

    alerts_sent = 0
    verified_count = 0
    fallback_count = 0
    cooldown_skipped = 0
    incomplete_skipped = 0

    now = time.time()
    current_snapshots = dict(snapshots)

    print(f"Scansione iniziata: {now_iso()}")
    print(f"Coppie trovate: {len(pairs)}")

    for original in pairs:
        pair, verified = refresh_pair(original)

        if verified:
            verified_count += 1
        else:
            fallback_count += 1

        metrics = get_metrics(pair)
        address = metrics["pair_address"].lower()

        if not address:
            continue

        previous = snapshots.get(address, {})
        previous_time = number(previous.get("updated_at_epoch"))

        previous_recent = (
            previous_time > 0
            and 0 <= now - previous_time
            <= MAX_SNAPSHOT_AGE_MINUTES * 60
        )

        score, checks, growth = score_pair(
            metrics,
            previous,
            previous_recent,
        )

        metrics["score"] = score
        metrics["checks"] = checks
        metrics["growth"] = growth
        metrics["api_verified"] = verified
        metrics["scanned_at"] = now_iso()

        warning = (
            metrics["volume_5m_usd"] <= 0
            and metrics["volume_1h_usd"] <= 0
            and metrics["transactions_1h"] <= 0
        )

        metrics["market_data_warning"] = warning

        market_data_ok = (
            verified
            and metrics["price_usd"] > 0
            and metrics["liquidity_usd"] > 0
            and bool(metrics["token_address"])
            and not warning
            and (
                metrics["volume_5m_usd"] > 0
                or metrics["volume_1h_usd"] > 0
            )
            and metrics["transactions_1h"] > 0
        )

        metrics["liquidity_ok"] = (
            metrics["liquidity_usd"] >= MIN_LIQUIDITY_USD
        )
        metrics["score_ok"] = score >= MIN_SCORE

        metrics["qualified"] = (
            metrics["liquidity_ok"]
            and metrics["score_ok"]
            and market_data_ok
        )

        results.append(metrics)

        if metrics["qualified"]:
            qualified_pairs.append(metrics)

            last_alert = number(alert_history.get(address))
            elapsed = now - last_alert if last_alert > 0 else float("inf")
            cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

            if elapsed < 0:
                alert_history.pop(address, None)
                elapsed = float("inf")

            if elapsed >= cooldown_seconds:
                if send_telegram(build_alert(metrics, score)):
                    alert_history[address] = now
                    alerts_sent += 1
                    print(
                        f"[ALERT] {metrics['name']} "
                        f"({metrics['symbol']}) - {score}/{MAX_SCORE}"
                    )
                else:
                    print(
                        f"[ALERT] Invio non riuscito per "
                        f"{metrics['name']} ({metrics['symbol']})"
                    )
            else:
                cooldown_skipped += 1
                remaining = max(
                    1,
                    int((cooldown_seconds - elapsed) / 60) + 1,
                )
                print(
                    f"[COOLDOWN] {metrics['symbol']}: "
                    f"{remaining} minuti rimanenti"
                )

        elif (
            metrics["liquidity_ok"]
            and metrics["score_ok"]
            and not market_data_ok
        ):
            incomplete_skipped += 1
            print(
                f"[DATI INCOMPLETI] {metrics['name']} "
                f"({metrics['symbol']}) escluso dagli avvisi."
            )

        current_snapshots[address] = {
            "volume_1h_usd": metrics["volume_1h_usd"],
            "volume_5m_usd": metrics["volume_5m_usd"],
            "transactions_5m": metrics["transactions_5m"],
            "liquidity_usd": metrics["liquidity_usd"],
            "updated_at": now_iso(),
            "updated_at_epoch": now,
        }

        time.sleep(REQUEST_DELAY_SECONDS)

    if len(current_snapshots) > MAX_SNAPSHOTS:
        current_snapshots = dict(
            list(current_snapshots.items())[-MAX_SNAPSHOTS:]
        )

    state["pair_snapshots"] = current_snapshots
    state["alert_history"] = alert_history
    state["updated_at"] = now_iso()
    save_json(STATE_FILE, state)

    warning_count = sum(
        1 for item in results if item["market_data_warning"]
    )

    # pairs/results contengono tutti i token per la dashboard.
    # qualified_pairs contiene solo quelli idonei agli avvisi.
    output = {
        "updated_at": now_iso(),
        "network": "solana",
        "pairs_scanned": len(results),
        "api_verified_count": verified_count,
        "api_verification_failed_count": fallback_count,
        "api_fallback_count": fallback_count,
        "qualified_count": len(qualified_pairs),
        "alerts_sent": alerts_sent,
        "cooldown_skipped_count": cooldown_skipped,
        "incomplete_data_skipped_count": incomplete_skipped,
        "min_score": MIN_SCORE,
        "max_score": MAX_SCORE,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "age_filter_enabled": False,
        "market_data_warning_count": warning_count,
        "pairs": results,
        "results": results,
        "qualified_pairs": qualified_pairs,
    }

    save_json(DATA_FILE, output)

    print("\n========== RISULTATO RADAR ==========")
    print(f"Coppie analizzate: {len(results)}")
    print(f"Coppie verificate: {verified_count}")
    print(f"Coppie con fallback: {fallback_count}")
    print(f"Dati senza volume e transazioni: {warning_count}")
    print(f"Coppie idonee: {len(qualified_pairs)}")
    print(f"Escluse per dati incompleti: {incomplete_skipped}")
    print(f"Saltate per cooldown: {cooldown_skipped}")
    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Dati salvati in {DATA_FILE}")
    print(f"Storico salvato in {STATE_FILE}")


if __name__ == "__main__":
    main()
