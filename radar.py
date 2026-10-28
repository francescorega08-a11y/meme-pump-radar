
import os
import json
import time
from datetime import datetime, timezone

import requests

# ============================================================
# CONFIGURAZIONE
# ============================================================

MIN_LIQUIDITY_USD = 25_000

# Soglia del sistema a punteggio.
# Punteggio massimo: 8.
MIN_SCORE = 4

MIN_VOLUME_5M_USD = 3_000
MIN_VOLUME_1H_USD = 20_000

MIN_CHANGE_5M_PCT = 1.0
MAX_CHANGE_5M_PCT = 10.0
MIN_CHANGE_1H_PCT = 2.0
MAX_CHANGE_1H_PCT = 100.0

MIN_BUY_SELL_RATIO = 1.2
MIN_VOLUME_ACCELERATION = 1.1
MIN_TXNS_5M = 5
MIN_TOKEN_AGE_MINUTES = 5

SUMMARY_INTERVAL_MINUTES = 15

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "radar_state.json"
DATA_FILE = "radar_data.json"

DEX_SEARCH_URL = (
    "https://api.dexscreener.com/latest/dex/search"
)

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
    "User-Agent": "MemePumpRadarAI/2.0"
}


# ============================================================
# FUNZIONI DI SUPPORTO
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato.")
        print(message)
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    try:
        response = requests.post(
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
            print("Telegram ha restituito un errore:", result)
            return False

        return True

    except (requests.RequestException, ValueError) as error:
        print("Errore Telegram:", error)
        return False


# ============================================================
# RACCOLTA COPPIE SOLANA
# ============================================================

def get_solana_pairs():
    pairs_by_address = {}

    for term in SEARCH_TERMS:
        try:
            response = requests.get(
                DEX_SEARCH_URL,
                params={"q": term},
                headers=HEADERS,
                timeout=25,
            )
            response.raise_for_status()

            payload = response.json()
            pairs = payload.get("pairs") or []

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                address = pair.get("pairAddress")

                if not address:
                    continue

                pairs_by_address[address] = pair

            print(
                f"Ricerca '{term}': "
                f"{len(pairs)} risultati ricevuti"
            )

        except (requests.RequestException, ValueError) as error:
            print(f"Errore nella ricerca '{term}': {error}")

        time.sleep(0.25)

    return list(pairs_by_address.values())


# ============================================================
# ANALISI E PUNTEGGIO
# ============================================================

def evaluate_pair(pair):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    transactions = pair.get("txns") or {}

    name = base.get("name") or "Sconosciuto"
    symbol = base.get("symbol") or "???"
    token_address = base.get("address") or ""
    pair_address = pair.get("pairAddress") or ""

    dex_id = pair.get("dexId") or "DEX"
    pair_url = pair.get("url") or (
        "https://dexscreener.com/solana/" + pair_address
    )

    liquidity_usd = safe_float(liquidity.get("usd"))
    volume_5m = safe_float(volume.get("m5"))
    volume_1h = safe_float(volume.get("h1"))

    change_5m = safe_float(changes.get("m5"))
    change_1h = safe_float(changes.get("h1"))

    txns_5m = transactions.get("m5") or {}
    buys_5m = int(safe_float(txns_5m.get("buys")))
    sells_5m = int(safe_float(txns_5m.get("sells")))
    total_txns_5m = buys_5m + sells_5m

    if sells_5m > 0:
        buy_sell_ratio = buys_5m / sells_5m
    elif buys_5m > 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = 0.0

    # Stima indicativa: volume degli ultimi 5 minuti
    # confrontato con un dodicesimo del volume orario.
    average_5m_volume = (
        volume_1h / 12 if volume_1h > 0 else 0
    )

    if average_5m_volume > 0:
        volume_acceleration = volume_5m / average_5m_volume
    else:
        volume_acceleration = 0.0

    # Età della coppia: può non essere disponibile.
    age_minutes = None
    created_at = pair.get("pairCreatedAt")

    if created_at:
        try:
            created_timestamp = float(created_at) / 1000
            age_minutes = max(
                0,
                (now_utc().timestamp() - created_timestamp) / 60,
            )
        except (TypeError, ValueError, OverflowError):
            age_minutes = None

    age_ok = (
        age_minutes is None
        or age_minutes >= MIN_TOKEN_AGE_MINUTES
    )

    # --------------------------------------------------------
    # LIQUIDITÀ: REQUISITO OBBLIGATORIO
    # --------------------------------------------------------

    liquidity_ok = liquidity_usd >= MIN_LIQUIDITY_USD

    # --------------------------------------------------------
    # PUNTEGGIO
    #
    # Volume 5m                 1 punto
    # Volume 1h                 1 punto
    # Variazione positiva 5m    1 punto
    # Variazione positiva 1h    1 punto
    # Rapporto acquisti/vendite 1 punto
    # Accelerazione volume     2 punti
    # Transazioni 5m            1 punto
    #
    # Totale massimo: 8 punti.
    # --------------------------------------------------------

    checks = {
        "liquidita": liquidity_ok,
        "volume_5m": volume_5m >= MIN_VOLUME_5M_USD,
        "volume_1h": volume_1h >= MIN_VOLUME_1H_USD,
        "variazione_5m_min": change_5m >= MIN_CHANGE_5M_PCT,
        "variazione_5m_max": change_5m <= MAX_CHANGE_5M_PCT,
        "variazione_1h": change_1h >= MIN_CHANGE_1H_PCT,
        "variazione_1h_max": change_1h <= MAX_CHANGE_1H_PCT,
        "rapporto_acquisti_vendite": (
            buy_sell_ratio >= MIN_BUY_SELL_RATIO
        ),
        "accelerazione_volume": (
            volume_acceleration >= MIN_VOLUME_ACCELERATION
        ),
        "transazioni_5m": total_txns_5m >= MIN_TXNS_5M,
        "eta_minima": age_ok,
    }

    points = {
        "volume_5m": (
            1 if checks["volume_5m"] else 0
        ),
        "volume_1h": (
            1 if checks["volume_1h"] else 0
        ),
        "variazione_5m": (
            1
            if (
                checks["variazione_5m_min"]
                and checks["variazione_5m_max"]
            )
            else 0
        ),
        "variazione_1h": (
            1
            if (
                checks["variazione_1h"]
                and checks["variazione_1h_max"]
            )
            else 0
        ),
        "rapporto_acquisti_vendite": (
            1 if checks["rapporto_acquisti_vendite"] else 0
        ),
        "accelerazione_volume": (
            2 if checks["accelerazione_volume"] else 0
        ),
        "transazioni_5m": (
            1 if checks["transazioni_5m"] else 0
        ),
    }

    score = sum(points.values())

    # La liquidità resta obbligatoria.
    # L'età, se nota, deve essere almeno 5 minuti.
    qualified = (
        liquidity_ok
        and age_ok
        and score >= MIN_SCORE
    )

    return {
        "name": name,
        "symbol": symbol,
        "token_address": token_address,
        "pair_address": pair_address,
        "quote_symbol": quote.get("symbol") or "",
        "dex": dex_id,
        "url": pair_url,
        "price_usd": safe_float(pair.get("priceUsd")),
        "liquidity_usd": round(liquidity_usd, 2),
        "volume_5m_usd": round(volume_5m, 2),
        "volume_1h_usd": round(volume_1h, 2),
        "change_5m_pct": round(change_5m, 2),
        "change_1h_pct": round(change_1h, 2),
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "transactions_5m": total_txns_5m,
        "buy_sell_ratio": (
            round(buy_sell_ratio, 2)
            if buy_sell_ratio != float("inf")
            else "infinito"
        ),
        "volume_acceleration": round(volume_acceleration, 2),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None
            else None
        ),
        "score": score,
        "max_score": 8,
        "points": points,
        "checks": checks,
        "liquidity_ok": liquidity_ok,
        "qualified": qualified,
    }


# ============================================================
# ALERT TELEGRAM
# ============================================================

def format_alert(token):
    ratio = token["buy_sell_ratio"]

    age = token["age_minutes"]
    age_text = (
        f"{age} minuti"
        if age is not None
        else "non disponibile"
    )

    return (
        "🚨 MEME PUMP RADAR AI\n"
        "📊 Segnale basato su punteggio\n\n"
        f"🪙 {token['name']} ({token['symbol']})\n"
        f"⭐ Punteggio: {token['score']}/8\n"
        f"💵 Prezzo: ${token['price_usd']:.10f}\n"
        f"💧 Liquidità: ${token['liquidity_usd']:,.0f}\n"
        f"📊 Volume 5m: ${token['volume_5m_usd']:,.0f}\n"
        f"📈 Volume 1h: ${token['volume_1h_usd']:,.0f}\n"
        f"⚡ Variazione 5m: {token['change_5m_pct']}%\n"
        f"📈 Variazione 1h: {token['change_1h_pct']}%\n"
        f"🟢 Acquisti 5m: {token['buys_5m']}\n"
        f"🔴 Vendite 5m: {token['sells_5m']}\n"
        f"⚖️ Rapporto buy/sell: {ratio}\n"
        f"🔥 Accelerazione volume: "
        f"{token['volume_acceleration']}x\n"
        f"⏱️ Età coppia: {age_text}\n"
        f"🏦 DEX: {token['dex']}\n\n"
        f"🔗 {token['url']}\n\n"
        "⚠️ Segnale automatico, non garanzia di rialzo. "
        "Verifica sicurezza del token, liquidità e rischio "
        "prima di prendere decisioni."
    )


def format_summary(scanned, eligible, alerts_sent):
    return (
        "📡 MEME PUMP RADAR AI — RIEPILOGO\n\n"
        f"🔎 Coppie analizzate: {scanned}\n"
        f"💧 Coppie con liquidità sufficiente: {eligible}\n"
        f"⭐ Soglia punteggio: {MIN_SCORE}/8\n"
        f"📨 Nuovi alert inviati: {alerts_sent}\n"
        f"🕒 Ora UTC: {iso_now()}\n\n"
        "Il punteggio è un filtro automatico, "
        "non una previsione certa del prezzo."
    )


# ============================================================
# ESECUZIONE PRINCIPALE
# ============================================================

def main():
    print("=" * 55)
    print("MEME PUMP RADAR AI - SISTEMA A PUNTEGGIO")
    print("Avvio:", iso_now())
    print("=" * 55)

    state = load_json(
        STATE_FILE,
        {
            "alerted_pairs": [],
            "last_summary_timestamp": 0,
        },
    )

    alerted_pairs = set(state.get("alerted_pairs", []))
    last_summary_timestamp = safe_float(
        state.get("last_summary_timestamp"), 0
    )

    pairs = get_solana_pairs()

    analyzed = []
    qualified = []

    for pair in pairs:
        try:
            token = evaluate_pair(pair)
            analyzed.append(token)

            if token["qualified"]:
                qualified.append(token)

        except Exception as error:
            print("Errore analisi coppia:", error)

    # Prima i punteggi più alti.
    qualified.sort(
        key=lambda token: token["score"],
        reverse=True,
    )

    alerts_sent = 0
    new_alerted_pairs = set(alerted_pairs)

    for token in qualified:
        pair_address = token["pair_address"]

        if not pair_address:
            continue

        if pair_address in alerted_pairs:
            continue

        if send_telegram(format_alert(token)):
            alerts_sent += 1
            new_alerted_pairs.add(pair_address)

    current_timestamp = time.time()
    summary_interval = SUMMARY_INTERVAL_MINUTES * 60

    if (
        current_timestamp - last_summary_timestamp
        >= summary_interval
    ):
        eligible_count = sum(
            1 for token in analyzed
            if token["liquidity_ok"]
        )

        if send_telegram(
            format_summary(
                scanned=len(analyzed),
                eligible=eligible_count,
                alerts_sent=alerts_sent,
            )
        ):
            last_summary_timestamp = current_timestamp

    # Conserva solo gli ultimi 2.000 indirizzi segnalati.
    saved_alerts = list(new_alerted_pairs)[-2000:]

    save_json(
        STATE_FILE,
        {
            "alerted_pairs": saved_alerts,
            "last_summary_timestamp": last_summary_timestamp,
            "updated_at": iso_now(),
        },
    )

    # Tutte le coppie, punteggi, controlli e risultati.
    save_json(
        DATA_FILE,
        {
            "updated_at": iso_now(),
            "pairs_scanned": len(analyzed),
            "qualified_count": len(qualified),
            "alerts_sent": alerts_sent,
            "min_score": MIN_SCORE,
            "min_liquidity_usd": MIN_LIQUIDITY_USD,
            "pairs": analyzed,
            "qualified_pairs": qualified,
        },
    )

    print("Coppie analizzate:", len(analyzed))
    print(
        "Coppie con liquidità sufficiente:",
        sum(1 for token in analyzed if token["liquidity_ok"]),
    )
    print("Coppie qualificate:", len(qualified))
    print("Nuovi alert inviati:", alerts_sent)
    print("Fine esecuzione:", iso_now())


if __name__ == "__main__":
    main()
