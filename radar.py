
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================
# MEME PUMP RADAR AI - SOLANA
# ============================================================

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 6
ALERT_COOLDOWN_MINUTES = 120

STATE_FILE = Path("radar_state.json")
DATA_FILE = Path("radar_data.json")

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{}"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{}"

SEARCH_TERMS = [
    "solana meme",
    "solana dog",
    "solana cat",
    "solana pepe",
    "solana pump",
    "solana bonk",
    "solana ai",
]

HEADERS = {
    "User-Agent": "MemePumpRadarAI/1.0",
    "Accept": "application/json",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# UTILITIES
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    """Converte numeri validi; i dati mancanti restano None."""
    try:
        if value is None:
            return None
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (TypeError, ValueError):
        return None


def integer(value):
    value = number(value)
    return int(value) if value is not None else None


def read_json(path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, type(default)):
                    return data
    except (OSError, json.JSONDecodeError):
        print(f"Avviso: impossibile leggere {path}")
    return default


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    temporary.replace(path)


def get_json(url, params=None):
    try:
        response = session.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except (requests.RequestException, ValueError) as exc:
        print(f"Errore API {url}: {exc}")
        return {}


def usd(value):
    value = number(value)
    return "N/D" if value is None else f"${value:,.2f}"


def pct(value):
    value = number(value)
    return "N/D" if value is None else f"{value:+.2f}%"


# ============================================================
# TOKEN METADATA
# ============================================================

def token_identity(pair):
    base = pair.get("baseToken") or {}
    address = base.get("address") or ""

    symbol = str(base.get("symbol") or "").strip()
    name = str(base.get("name") or "").strip()

    # Se i metadati sono incompleti, prova a recuperarli
    # dall'endpoint token di Dexscreener.
    if address and (not symbol or not name):
        payload = get_json(DEX_TOKEN_URL.format(address))
        candidates = payload.get("pairs") or []

        for candidate in candidates:
            if (candidate.get("chainId") or "").lower() != "solana":
                continue

            candidate_base = candidate.get("baseToken") or {}
            candidate_address = candidate_base.get("address") or ""

            if candidate_address.lower() == address.lower():
                symbol = symbol or str(
                    candidate_base.get("symbol") or ""
                ).strip()
                name = name or str(
                    candidate_base.get("name") or ""
                ).strip()

                if symbol and name:
                    break

    # Mai usare "Sconosciuto" come se fosse un dato reale.
    if not symbol:
        symbol = f"TOKEN-{address[:6]}" if address else "TOKEN-N/D"

    if not name:
        name = symbol

    return {
        "token_address": address or None,
        "symbol": symbol,
        "name": name,
    }


# ============================================================
# SEARCH AND PAIR VERIFICATION
# ============================================================

def search_pairs():
    found = {}

    for term in SEARCH_TERMS:
        payload = get_json(DEX_SEARCH_URL, params={"q": term})

        for pair in payload.get("pairs") or []:
            if (pair.get("chainId") or "").lower() != "solana":
                continue

            pair_address = pair.get("pairAddress")
            if not pair_address:
                continue

            found[pair_address.lower()] = pair

        # Riduce la frequenza delle richieste API.
        time.sleep(0.25)

    return list(found.values())


def refresh_pair(original):
    pair_address = original.get("pairAddress")
    if not pair_address:
        return original, False, "pair_address_missing"

    payload = get_json(DEX_PAIR_URL.format(pair_address))
    candidates = payload.get("pair")

    if isinstance(candidates, dict):
        candidates = [candidates]
    elif not isinstance(candidates, list):
        candidates = []

    original_base = original.get("baseToken") or {}
    original_token_address = str(
        original_base.get("address") or ""
    ).lower()

    # Accetta soltanto la coppia esatta restituita dall'API.
    for pair in candidates:
        if (pair.get("chainId") or "").lower() != "solana":
            continue

        if str(pair.get("pairAddress") or "").lower() != (
            pair_address.lower()
        ):
            continue

        verified_base = pair.get("baseToken") or {}
        verified_token_address = str(
            verified_base.get("address") or ""
        ).lower()

        if (
            original_token_address
            and verified_token_address
            and original_token_address != verified_token_address
        ):
            continue

        return pair, True, None

    # Il dato iniziale può essere mostrato a fini diagnostici,
    # ma non è considerato verificato e non può generare allerta.
    return original, False, "pair_verification_failed"


# ============================================================
# MARKET METRICS
# ============================================================

def get_metrics(pair, verified, verification_error=None):
    identity = token_identity(pair)

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    m5 = txns.get("m5") or {}
    h1 = txns.get("h1") or {}

    buys_5m = integer(m5.get("buys"))
    sells_5m = integer(m5.get("sells"))

    total_tx_5m = (
        buys_5m + sells_5m
        if buys_5m is not None and sells_5m is not None
        else None
    )

    volume_5m = number(volume.get("m5"))
    volume_1h = number(volume.get("h1"))

    # Non tutti i dati sono sempre presenti nell'API.
    # None significa "non disponibile", non zero.
    price_usd = number(pair.get("priceUsd"))
    change_5m = number(price_change.get("m5"))
    change_1h = number(price_change.get("h1"))

    liquidity_value = number(liquidity)

    return {
        **identity,
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "url": pair.get("url"),
        "price_usd": price_usd,
        "liquidity_usd": liquidity_value,
        "volume_5m_usd": volume_5m,
        "volume_1h_usd": volume_1h,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "txns_5m": total_tx_5m,
        "txns_1h": integer(h1.get("buys")) + integer(h1.get("sells"))
        if integer(h1.get("buys")) is not None
        and integer(h1.get("sells")) is not None
        else None,
        "price_change_5m_pct": change_5m,
        "price_change_1h_pct": change_1h,
        "pair_created_at": pair.get("pairCreatedAt"),
        "verified": verified,
        "verification_error": verification_error,
        "qualified": False,
        "score": 0,
        "score_details": [],
        "updated_at": now_iso(),
    }


# ============================================================
# SCORING
# Maximum score: 9 points
# ============================================================

def calculate_score(metrics, previous):
    score = 0
    details = []

    def award(points, message):
        nonlocal score
        score += points
        details.append({
            "points": points,
            "reason": message,
        })

    volume_5m = metrics["volume_5m_usd"]
    volume_1h = metrics["volume_1h_usd"]
    txns_5m = metrics["txns_5m"]
    buys = metrics["buys_5m"]
    sells = metrics["sells_5m"]
    change_5m = metrics["price_change_5m_pct"]
    change_1h = metrics["price_change_1h_pct"]
    liquidity = metrics["liquidity_usd"]

    if volume_5m is not None and volume_5m >= 1_000:
        award(1, "Volume 5m almeno $1.000")

    if volume_1h is not None and volume_1h >= 10_000:
        award(1, "Volume 1h almeno $10.000")

    old_volume = number(previous.get("volume_1h_usd"))
    if (
        volume_1h is not None
        and old_volume is not None
        and old_volume > 0
        and volume_1h >= old_volume * 1.2
    ):
        award(2, "Volume 1h in aumento di almeno il 20%")

    old_txns = number(previous.get("txns_5m"))
    if (
        txns_5m is not None
        and old_txns is not None
        and old_txns > 0
        and txns_5m >= old_txns * 1.2
    ):
        award(1, "Transazioni 5m in aumento di almeno il 20%")

    if (
        buys is not None
        and sells is not None
        and buys + sells >= 5
        and buys >= sells * 1.2
    ):
        award(1, "Pressione acquisti superiore alle vendite")

    # Volume 5m confrontato con una stima media derivata dal volume 1h.
    if (
        volume_5m is not None
        and volume_1h is not None
        and volume_1h > 0
        and volume_5m >= (volume_1h / 12) * 1.5
    ):
        award(1, "Volume 5m superiore alla media oraria stimata")

    if (
        change_5m is not None
        and change_1h is not None
        and 0 < change_5m <= 8
        and change_1h <= 20
    ):
        award(1, "Movimento prezzo positivo senza forte estensione")

    old_liquidity = number(previous.get("liquidity_usd"))
    if (
        liquidity is not None
        and old_liquidity is not None
        and old_liquidity > 0
        and liquidity >= old_liquidity * 1.05
    ):
        award(1, "Liquidità in aumento di almeno il 5%")

    return score, details


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram non configurato: controlla i GitHub Secrets.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = session.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            print("Telegram ha rifiutato il messaggio.")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        print(f"Errore invio Telegram: {exc}")
        return False


def alert_message(metrics):
    return (
        "🚨 MEME PUMP RADAR AI\n\n"
        f"Token: {metrics['name']} ({metrics['symbol']})\n"
        f"Score: {metrics['score']}/9\n"
        f"Liquidità: {usd(metrics['liquidity_usd'])}\n"
        f"Prezzo: {usd(metrics['price_usd'])}\n"
        f"Volume 5m: {usd(metrics['volume_5m_usd'])}\n"
        f"Volume 1h: {usd(metrics['volume_1h_usd'])}\n"
        f"Variazione 5m: {pct(metrics['price_change_5m_pct'])}\n"
        f"Variazione 1h: {pct(metrics['price_change_1h_pct'])}\n"
        f"Transazioni 5m: {metrics['txns_5m'] if metrics['txns_5m'] is not None else 'N/D'}\n"
        f"Verificato: {'Sì' if metrics['verified'] else 'No'}\n\n"
        f"Token address: {metrics['token_address'] or 'N/D'}\n"
        f"Coppia: {metrics['pair_address'] or 'N/D'}\n"
        f"{metrics['url'] or ''}\n\n"
        "⚠️ Segnale automatico, non garanzia di rialzo. "
        "Verifica liquidità, slippage e rischio rug pull."
    )


# ============================================================
# MAIN RADAR
# ============================================================

def main():
    print("=" * 55)
    print("MEME PUMP RADAR AI - START")
    print(now_iso())
    print("=" * 55)

    state = read_json(STATE_FILE, {})
    if not isinstance(state.get("pairs"), dict):
        state["pairs"] = {}

    previous_pairs = state["pairs"]
    new_pairs = {}
    results = []

    raw_pairs = search_pairs()
    print(f"Coppie uniche trovate: {len(raw_pairs)}")

    verified_count = 0
    failed_verifications = 0
    qualified_count = 0
    alerts_sent = 0

    for original in raw_pairs:
        pair, verified, error = refresh_pair(original)
        metrics = get_metrics(pair, verified, error)

        pair_address = metrics.get("pair_address")
        if not pair_address:
            continue

        key = pair_address.lower()
        previous = previous_pairs.get(key, {})

        score, details = calculate_score(metrics, previous)
        metrics["score"] = score
        metrics["score_details"] = details

        liquidity = metrics["liquidity_usd"]
        metrics["qualified"] = (
            verified
            and liquidity is not None
            and liquidity >= MIN_LIQUIDITY_USD
            and score >= MIN_SCORE
        )

        if verified:
            verified_count += 1
        else:
            failed_verifications += 1

        if metrics["qualified"]:
            qualified_count += 1

            last_alert = number(previous.get("last_alert_epoch")) or 0
            cooldown = ALERT_COOLDOWN_MINUTES * 60
            current_epoch = time.time()

            if current_epoch - last_alert >= cooldown:
                sent = send_telegram(alert_message(metrics))
                if sent:
                    metrics["last_alert_epoch"] = current_epoch
                    alerts_sent += 1

        # Conserva l'ultimo invio anche se l'API non ha verificato
        # la coppia in questa esecuzione.
        if "last_alert_epoch" not in metrics:
            metrics["last_alert_epoch"] = previous.get(
                "last_alert_epoch", 0
            )

        new_pairs[key] = metrics
        results.append(metrics)

        # Evita raffiche eccessive di richieste.
        time.sleep(0.15)

    # Aggiorna anche i dati di coppie non più presenti nella ricerca
    # senza sovrascrivere lo storico appena calcolato.
    state["pairs"] = new_pairs
    state["updated_at"] = now_iso()

    results.sort(
        key=lambda item: (
            item["qualified"],
            item["score"],
            item["liquidity_usd"] or 0,
        ),
        reverse=True,
    )

    output = {
        "updated_at": now_iso(),
        "network": "solana",
        "pairs_scanned": len(results),
        "api_verified_count": verified_count,
        "api_verification_failed_count": failed_verifications,
        "qualified_count": qualified_count,
        "alerts_sent": alerts_sent,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "min_score": MIN_SCORE,
        "pairs": results,
    }

    write_json(STATE_FILE, state)
    write_json(DATA_FILE, output)

    print(f"Coppie verificate: {verified_count}")
    print(f"Verifiche fallite: {failed_verifications}")
    print(f"Segnali qualificati: {qualified_count}")
    print(f"Allerte Telegram inviate: {alerts_sent}")
    print(f"Dati salvati in {DATA_FILE}")
    print(f"Stato salvato in {STATE_FILE}")
    print("MEME PUMP RADAR AI - END")


if __name__ == "__main__":
    main()
