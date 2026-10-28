
import json
import os
import time
from datetime import datetime, timezone

import requests

# ============================================================
# MEME PUMP RADAR AI - SOLANA
# Liquidita minima: $25.000 | Punteggio minimo: 6/9
# ============================================================

DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 6
ALERT_COOLDOWN_MINUTES = 120
MAX_PAIRS_TO_ANALYZE = 250
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 0.20
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
session.headers.update({"User-Agent": "MemePumpRadarAI/5.0"})


# ============================================================
# UTILITA
# ============================================================

def iso_now():
    return datetime.now(timezone.utc).isoformat()


def number(value, default=0.0):
    try:
        result = float(value)
        if result != result or result in (
            float("inf"), float("-inf")
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
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def get_json(url, params=None):
    """Richiede JSON e restituisce un errore leggibile se fallisce."""
    try:
        response = session.get(
            url, params=params, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            return None, "La risposta API non e un oggetto JSON"

        return payload, None

    except requests.RequestException as error:
        return None, f"{type(error).__name__}: {error}"
    except ValueError as error:
        return None, f"JSON non valido: {error}"


# ============================================================
# RICERCA DELLE COPPIE
# ============================================================

def get_pairs_from_search(query):
    payload, error = get_json(
        DEX_SEARCH_URL, {"q": query}
    )

    if error:
        print(f"[API] Ricerca '{query}' fallita: {error}")
        return []

    pairs = payload.get("pairs") or []

    return [
        pair for pair in pairs
        if isinstance(pair, dict)
        and pair.get("chainId") == "solana"
        and pair.get("pairAddress")
    ]


def discover_pairs():
    """Cerca coppie Solana e rimuove i duplicati per indirizzo."""
    found = {}

    for query in SEARCH_QUERIES:
        for pair in get_pairs_from_search(query):
            address = str(
                pair.get("pairAddress") or ""
            ).strip()

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


# ============================================================
# VERIFICA API
# ============================================================

def refresh_pair(pair):
    """
    Verifica la coppia con l'endpoint Dexscreener.
    Il fallback serve solo per diagnosticare e mostrare dati:
    le coppie non verificate non possono generare avvisi.
    """
    address = str(
        pair.get("pairAddress") or ""
    ).strip()

    if not address:
        return pair, False, "Indirizzo della coppia mancante"

    errors = []

    # 1. Endpoint specifico della coppia.
    payload, error = get_json(DEX_PAIR_URL + address)

    if error:
        errors.append(f"endpoint coppia: {error}")
    else:
        items = payload.get("pairs") or []

        for item in items:
            if not isinstance(item, dict):
                continue

            if (
                item.get("chainId") == "solana"
                and str(item.get("pairAddress") or "").lower()
                == address.lower()
            ):
                return item, True, None

        errors.append(
            "endpoint coppia: indirizzo non trovato nella risposta"
        )

    # 2. Endpoint token: accetta solo la stessa coppia.
    token_address = str(
        (pair.get("baseToken") or {}).get("address") or ""
    ).strip()

    if token_address:
        token_payload, token_error = get_json(
            DEX_TOKEN_URL + token_address
        )

        if token_error:
            errors.append(f"endpoint token: {token_error}")
        else:
            token_pairs = token_payload.get("pairs") or []

            for item in token_pairs:
                if not isinstance(item, dict):
                    continue

                if (
                    item.get("chainId") == "solana"
                    and str(item.get("pairAddress") or "").lower()
                    == address.lower()
                ):
                    return item, True, None

            errors.append(
                "endpoint token: la stessa coppia non e stata trovata"
            )

    # Mantiene i dati della ricerca solo a scopo diagnostico.
    return pair, False, " | ".join(errors)


# ============================================================
# ESTRAZIONE DELLE METRICHE
# ============================================================

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
            0, (time.time() * 1000 - created_ms) / 60000
        )

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
            if address else ""
        ),
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": number(liquidity.get("usd")),
        "volume_5m_usd": number(volume.get("m5")),
        "volume_1h_usd": number(volume.get("h1")),
        "volume_24h_usd": number(volume.get("h24")),
        "buys_5m": buys5,
        "sells_5m": sells5,
        "transactions_5m": buys5 + sells5,
        "buys_1h": buys1h,
        "sells_1h": sells1h,
        "transactions_1h": buys1h + sells1h,
        "buy_sell_ratio_5m": (
            round(buys5 / sells5, 2)
            if sells5 > 0
            else (float(buys5) if buys5 else 0.0)
        ),
        "price_change_5m_pct": number(changes.get("m5")),
        "price_change_1h_pct": number(changes.get("h1")),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None else None
        ),
        "data_source": "dexscreener",
    }


# ============================================================
# PUNTEGGIO: MASSIMO 9
# ============================================================

def score_pair(metrics, previous):
    score = 0
    checks = {}

    vol5 = metrics["volume_5m_usd"]
    vol1h = metrics["volume_1h_usd"]
    liq = metrics["liquidity_usd"]
    tx5 = metrics["transactions_5m"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    # 1 punto: volume 5 minuti >= $1.000.
    checks["volume_5m"] = vol5 >= 1_000
    if checks["volume_5m"]:
        score += 1

    # 1 punto: volume 1 ora >= $10.000.
    checks["volume_1h"] = vol1h >= 10_000
    if checks["volume_1h"]:
        score += 1

    # 2 punti: volume orario cresciuto almeno del 20%.
    # Nota: il volume orario e una finestra mobile.
    previous_volume = number(
        previous.get("volume_1h_usd")
    )

    volume_growth = (
        vol1h / previous_volume
        if previous_volume > 0 else None
    )

    checks["volume_in_crescita"] = (
        volume_growth is not None
        and volume_growth >= 1.20
    )

    if checks["volume_in_crescita"]:
        score += 2

    # 1 punto: transazioni 5m cresciute almeno del 20%.
    previous_tx = number(
        previous.get("transactions_5m")
    )

    transaction_growth = (
        tx5 / previous_tx
        if previous_tx > 0 else None
    )

    checks["attivita_in_crescita"] = (
        transaction_growth is not None
        and transaction_growth >= 1.20
    )

    if checks["attivita_in_crescita"]:
        score += 1

    # 1 punto: almeno 5 transazioni e rapporto acquisti/vendite >= 1,2.
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

    # 1 punto: volume 5m almeno 1,5 volte la media teorica 5m.
    average_5m_volume = vol1h / 12 if vol1h > 0 else 0

    checks["accelerazione_volume"] = (
        average_5m_volume > 0
        and vol5 >= average_5m_volume * 1.50
    )

    if checks["accelerazione_volume"]:
        score += 1

    # 1 punto: rialzo 5m positivo, non oltre 8%;
    # variazione oraria non oltre il 20%.
    change5 = metrics["price_change_5m_pct"]
    change1h = metrics["price_change_1h_pct"]

    checks["movimento_iniziale"] = (
        0 < change5 <= 8 and change1h <= 20
    )

    if checks["movimento_iniziale"]:
        score += 1

    # 1 punto: liquidita cresciuta almeno del 5%.
    previous_liquidity = number(
        previous.get("liquidity_usd")
    )

    checks["liquidita_in_crescita"] = (
        previous_liquidity > 0
        and liq >= previous_liquidity * 1.05
    )

    if checks["liquidita_in_crescita"]:
        score += 1

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

    return score, checks, growth


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] Mancano TELEGRAM_BOT_TOKEN "
            "o TELEGRAM_CHAT_ID nei GitHub Secrets."
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

        print("[TELEGRAM] Telegram ha rifiutato il messaggio.")
        return False

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
        f"Punteggio: {score}/9\n"
        f"Liquidita: ${metrics['liquidity_usd']:,.0f}\n"
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
        f"Eta coppia: {age_text}\n"
        f"DEX: {metrics['dex']}\n"
        f"Link: {metrics['url']}\n\n"
        "Segnale automatico, non previsione garantita. "
        "Verifica sempre liquidita, slippage e rischi."
    )


# ============================================================
# ESECUZIONE PRINCIPALE
# ============================================================

def main():
    state = load_json(STATE_FILE, {})

    snapshots = state.get("pair_snapshots", {})
    alert_history = state.get("alert_history", {})

    if not isinstance(snapshots, dict):
        snapshots = {}

    if not isinstance(alert_history, dict):
        alert_history = {}

    # Compatibilita con il vecchio formato dello storico.
    if not alert_history:
        old_history = state.get("alerted_pairs", {})
        if isinstance(old_history, dict):
            alert_history = old_history

    pairs = discover_pairs()

    all_results = []
    qualified_results = []
    alerts_sent = 0
    api_verified = 0
    api_fallback = 0
    verification_errors = 0

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
            verification_errors += 1

        metrics = get_metrics(pair)
        address = metrics["pair_address"].lower()

        if not address:
            print("[AVVISO] Coppia senza indirizzo, saltata.")
            continue

        previous = snapshots.get(address, {})
        if not isinstance(previous, dict):
            previous = {}

        score, checks, growth = score_pair(metrics, previous)

        metrics["score"] = score
        metrics["checks"] = checks
        metrics["growth"] = growth
        metrics["scanned_at"] = iso_now()
        metrics["api_verified"] = verified
        metrics["api_verification_error"] = verification_error

        # Eta informativa, non filtro.
        metrics["age_ok"] = True

        metrics["liquidity_ok"] = (
            metrics["liquidity_usd"] >= MIN_LIQUIDITY_USD
        )

        # IMPORTANTE: dati non verificati non possono qualificarsi.
        metrics["qualified"] = (
            verified
            and metrics["liquidity_ok"]
            and score >= MIN_SCORE
        )

        metrics["market_data_warning"] = (
            metrics["volume_5m_usd"] == 0
            and metrics["volume_1h_usd"] == 0
            and metrics["transactions_1h"] == 0
        )

        all_results.append(metrics)

        if not verified:
            print(
                f"[VERIFICA] {metrics['token_symbol']}: "
                f"{verification_error}"
            )

        if metrics["qualified"]:
            qualified_results.append(metrics)

            last_alert = number(alert_history.get(address))
            cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

            if now_seconds - last_alert >= cooldown_seconds:
                if send_telegram(build_alert(metrics, score)):
                    alert_history[address] = now_seconds
                    alerts_sent += 1

                    print(
                        f"[ALERT] Inviato "
                        f"{metrics['token_symbol']} "
                        f"score={score}/9"
                    )
                else:
                    print(
                        f"[ALERT] Invio non riuscito per "
                        f"{metrics['token_symbol']}"
                    )

        # Aggiorna lo storico anche per le coppie non qualificate.
        # Questo conserva i confronti fra scansioni.
        current_snapshots[address] = {
            "volume_1h_usd": metrics["volume_1h_usd"],
            "volume_5m_usd": metrics["volume_5m_usd"],
            "transactions_5m": metrics["transactions_5m"],
            "liquidity_usd": metrics["liquidity_usd"],
            "updated_at": iso_now(),
        }

        time.sleep(REQUEST_DELAY_SECONDS)

    # Limita la dimensione dello storico.
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
        "pairs_scanned": len(all_results),
        "qualified_count": len(qualified_results),
        "alerts_sent": alerts_sent,
        "min_score": MIN_SCORE,
        "max_score": 9,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "age_filter_enabled": False,
        "api_verified_count": api_verified,
        "api_fallback_count": api_fallback,
        "verification_error_count": verification_errors,
        "market_data_warning_count": warning_count,
        "pairs": all_results,
        "qualified_pairs": qualified_results,
    }

    save_json(DATA_FILE, output)

    print("\n========== RISULTATO RADAR ==========")
    print(f"Coppie analizzate: {len(all_results)}")
    print(f"Coppie verificate via API: {api_verified}")
    print(f"Coppie con dati di fallback: {api_fallback}")
    print(f"Errori di verifica: {verification_errors}")
    print(
        "Coppie con volume e transazioni tutti a zero: "
        f"{warning_count}"
    )
    print(
        f"Coppie idonee (verificate, score >= {MIN_SCORE}, "
        f"liquidita >= ${MIN_LIQUIDITY_USD:,.0f}): "
        f"{len(qualified_results)}"
    )
    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Soglia punteggio: {MIN_SCORE}/9")
    print("Filtro eta disattivato.")
    print("Dati salvati in radar_data.json")
    print("Storico salvato in radar_state.json")


if __name__ == "__main__":
    main()
