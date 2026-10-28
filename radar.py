
import os
import json
import time
from datetime import datetime, timezone

import requests

# ============================================================
# CONFIGURAZIONE
# ============================================================

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 4
MAX_SCORE = 10

MIN_TOKEN_AGE_MINUTES = 5

# Soglie indicative: contribuiscono al punteggio,
# ma non sono tutte condizioni obbligatorie.
MIN_VOLUME_5M_USD = 3_000
MIN_VOLUME_1H_USD = 20_000
MIN_TXNS_5M = 5
MIN_BUY_SELL_RATIO = 1.2
MIN_VOLUME_ACCELERATION = 1.1

# Evita alert troppo frequenti sulla stessa coppia.
SUMMARY_INTERVAL_MINUTES = 15
ALERT_COOLDOWN_MINUTES = 120

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
    "User-Agent": "MemePumpRadarAI/3.0"
}


# ============================================================
# UTILITÀ
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
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    temporary_path = path + ".tmp"

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temporary_path, path)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato: alert non inviato.")
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
            print("Errore Telegram:", result)
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

            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                address = pair.get("pairAddress")

                if address:
                    pairs_by_address[address] = pair

            print(
                f"Ricerca '{term}' completata."
            )

        except (requests.RequestException, ValueError) as error:
            print(f"Errore ricerca '{term}': {error}")

        time.sleep(0.25)

    return list(pairs_by_address.values())


# ============================================================
# ANALISI PRE-PUMP
# ============================================================

def evaluate_pair(pair, previous):
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}

    name = base.get("name") or "Sconosciuto"
    symbol = base.get("symbol") or "???"
    token_address = base.get("address") or ""
    pair_address = pair.get("pairAddress") or ""

    url = pair.get("url") or (
        "https://dexscreener.com/solana/" + pair_address
    )

    liquidity_usd = safe_float(liquidity.get("usd"))
    volume_5m = safe_float(volume.get("m5"))
    volume_1h = safe_float(volume.get("h1"))

    change_5m = safe_float(changes.get("m5"))
    change_1h = safe_float(changes.get("h1"))

    txns_5m = txns.get("m5") or {}
    buys_5m = int(safe_float(txns_5m.get("buys")))
    sells_5m = int(safe_float(txns_5m.get("sells")))
    total_txns = buys_5m + sells_5m

    if sells_5m > 0:
        buy_sell_ratio = buys_5m / sells_5m
    elif buys_5m > 0:
        buy_sell_ratio = 999.0
    else:
        buy_sell_ratio = 0.0

    # Età della coppia, se il dato è disponibile.
    age_minutes = None
    created_at = pair.get("pairCreatedAt")

    if created_at:
        try:
            age_minutes = max(
                0,
                (
                    now_utc().timestamp()
                    - float(created_at) / 1000
                ) / 60,
            )
        except (TypeError, ValueError, OverflowError):
            age_minutes = None

    age_ok = (
        age_minutes is None
        or age_minutes >= MIN_TOKEN_AGE_MINUTES
    )

    # --------------------------------------------------------
    # CONFRONTO CON L'ESECUZIONE PRECEDENTE
    # --------------------------------------------------------

    has_previous = bool(previous)

    previous_volume_5m = safe_float(
        previous.get("volume_5m"), 0
    )
    previous_volume_1h = safe_float(
        previous.get("volume_1h"), 0
    )
    previous_liquidity = safe_float(
        previous.get("liquidity_usd"), 0
    )
    previous_txns = safe_float(
        previous.get("transactions_5m"), 0
    )

    # Crescita percentuale solo quando il dato precedente
    # è positivo e quindi il confronto è significativo.
    volume_growth_pct = None
    if has_previous and previous_volume_5m > 0:
        volume_growth_pct = (
            (volume_5m - previous_volume_5m)
            / previous_volume_5m
        ) * 100

    liquidity_growth_pct = None
    if has_previous and previous_liquidity > 0:
        liquidity_growth_pct = (
            (liquidity_usd - previous_liquidity)
            / previous_liquidity
        ) * 100

    txns_growth_pct = None
    if has_previous and previous_txns > 0:
        txns_growth_pct = (
            (total_txns - previous_txns)
            / previous_txns
        ) * 100

    # Rapporto tra volume corrente a 5 minuti e media
    # teorica a 5 minuti ricavata dal volume orario.
    average_5m_volume = volume_1h / 12 if volume_1h > 0 else 0

    volume_acceleration = (
        volume_5m / average_5m_volume
        if average_5m_volume > 0
        else 0
    )

    # --------------------------------------------------------
    # PUNTEGGIO: MASSIMO 10 PUNTI
    # --------------------------------------------------------

    points = {}

    # 1 punto: volume recente sufficiente.
    points["volume_5m"] = (
        1 if volume_5m >= MIN_VOLUME_5M_USD else 0
    )

    # 1 punto: volume orario sufficiente.
    points["volume_1h"] = (
        1 if volume_1h >= MIN_VOLUME_1H_USD else 0
    )

    # 2 punti: volume a 5 minuti in crescita rispetto
    # all'ultima osservazione disponibile.
    points["volume_in_crescita"] = (
        2
        if volume_growth_pct is not None
        and volume_growth_pct >= 15
        else 0
    )

    # 1 punto: più transazioni rispetto all'osservazione
    # precedente. Non equivale a più acquirenti unici.
    points["attivita_in_crescita"] = (
        1
        if txns_growth_pct is not None
        and txns_growth_pct >= 15
        else 0
    )

    # 1 punto: prevalenza di acquisti.
    points["pressione_acquisti"] = (
        1
        if buy_sell_ratio >= MIN_BUY_SELL_RATIO
        and buys_5m >= 3
        else 0
    )

    # 1 punto: accelerazione stimata del volume.
    points["accelerazione_volume"] = (
        1
        if volume_acceleration >= MIN_VOLUME_ACCELERATION
        else 0
    )

    # 1 punto: prezzo positivo ma non già esploso.
    points["movimento_iniziale"] = (
        1
        if 0 < change_5m <= 8
        and change_1h <= 30
        else 0
    )

    # 1 punto: liquidità in crescita, quando confrontabile.
    points["liquidita_in_crescita"] = (
        1
        if liquidity_growth_pct is not None
        and liquidity_growth_pct >= 2
        else 0
    )

    score = sum(points.values())

    liquidity_ok = liquidity_usd >= MIN_LIQUIDITY_USD

    qualified = (
        liquidity_ok
        and age_ok
        and score >= MIN_SCORE
    )

    # La nuova osservazione verrà salvata per la prossima
    # esecuzione. I volumi sono aggregati mobili, non volumi
    # scambiati esclusivamente tra due esecuzioni.
    snapshot = {
        "observed_at": iso_now(),
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "liquidity_usd": liquidity_usd,
        "transactions_5m": total_txns,
    }

    return {
        "name": name,
        "symbol": symbol,
        "token_address": token_address,
        "pair_address": pair_address,
        "quote_symbol": quote.get("symbol") or "",
        "dex": pair.get("dexId") or "DEX",
        "url": url,
        "price_usd": safe_float(pair.get("priceUsd")),
        "liquidity_usd": round(liquidity_usd, 2),
        "volume_5m_usd": round(volume_5m, 2),
        "volume_1h_usd": round(volume_1h, 2),
        "change_5m_pct": round(change_5m, 2),
        "change_1h_pct": round(change_1h, 2),
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "transactions_5m": total_txns,
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "volume_acceleration": round(volume_acceleration, 2),
        "volume_growth_since_previous_pct": (
            round(volume_growth_pct, 2)
            if volume_growth_pct is not None
            else None
        ),
        "transactions_growth_since_previous_pct": (
            round(txns_growth_pct, 2)
            if txns_growth_pct is not None
            else None
        ),
        "liquidity_growth_since_previous_pct": (
            round(liquidity_growth_pct, 2)
            if liquidity_growth_pct is not None
            else None
        ),
        "age_minutes": (
            round(age_minutes, 1)
            if age_minutes is not None
            else None
        ),
        "has_previous_observation": has_previous,
        "score": score,
        "max_score": MAX_SCORE,
        "points": points,
        "liquidity_ok": liquidity_ok,
        "age_ok": age_ok,
        "qualified": qualified,
        "snapshot": snapshot,
    }


# ============================================================
# TELEGRAM
# ============================================================

def format_alert(token):
    age = token["age_minutes"]
    age_text = (
        f"{age} minuti"
        if age is not None
        else "non disponibile"
    )

    growth = token["volume_growth_since_previous_pct"]
    growth_text = (
        f"{growth:+.1f}%"
        if growth is not None
        else "in raccolta dati"
    )

    return (
        "🚨 MEME PUMP RADAR AI\n"
        "Possibile attività anticipata\n\n"
        f"🪙 {token['name']} ({token['symbol']})\n"
        f"⭐ Punteggio: {token['score']}/{MAX_SCORE}\n"
        f"💧 Liquidità: ${token['liquidity_usd']:,.0f}\n"
        f"📊 Volume 5m: ${token['volume_5m_usd']:,.0f}\n"
        f"📈 Volume 1h: ${token['volume_1h_usd']:,.0f}\n"
        f"⚡ Variazione 5m: {token['change_5m_pct']}%\n"
        f"📈 Variazione 1h: {token['change_1h_pct']}%\n"
        f"📉 Volume vs osservazione precedente: {growth_text}\n"
        f"🟢 Acquisti 5m: {token['buys_5m']}\n"
        f"🔴 Vendite 5m: {token['sells_5m']}\n"
        f"⚖️ Rapporto acquisti/vendite: "
        f"{token['buy_sell_ratio']}\n"
        f"🔥 Accelerazione stimata: "
        f"{token['volume_acceleration']}x\n"
        f"⏱️ Età coppia: {age_text}\n"
        f"🏦 DEX: {token['dex']}\n\n"
        f"🔗 {token['url']}\n\n"
        "⚠️ Segnale statistico, non previsione né garanzia. "
        "Verifica sicurezza del token e rischio."
    )


def format_summary(scanned, eligible, qualified, sent):
    return (
        "📡 MEME PUMP RADAR AI — RIEPILOGO\n\n"
        f"🔎 Coppie analizzate: {scanned}\n"
        f"💧 Coppie con liquidità >= $25.000: {eligible}\n"
        f"⭐ Coppie qualificate: {qualified}\n"
        f"📨 Nuovi alert inviati: {sent}\n"
        f"🕒 Ora UTC: {iso_now()}\n\n"
        "Le variazioni tra esecuzioni sono indicative: "
        "i volumi di Dexscreener sono finestre mobili."
    )


# ============================================================
# ESECUZIONE PRINCIPALE
# ============================================================

def main():
    print("=" * 55)
    print("MEME PUMP RADAR AI - PRE-PUMP SCANNER")
    print("Avvio:", iso_now())
    print("=" * 55)

    state = load_json(
        STATE_FILE,
        {
            "alert_history": {},
            "pair_snapshots": {},
            "last_summary_timestamp": 0,
        },
    )

    # Compatibilità con lo storico della versione precedente.
    alert_history = state.get("alert_history", {})

    if not isinstance(alert_history, dict):
        alert_history = {}

    pair_snapshots = state.get("pair_snapshots", {})

    if not isinstance(pair_snapshots, dict):
        pair_snapshots = {}

    # Migra gli indirizzi già segnalati dalla versione vecchia.
    old_alerted = state.get("alerted_pairs", [])

    for address in old_alerted:
        alert_history.setdefault(
            address,
            {"last_alert_timestamp": 0},
        )

    last_summary_timestamp = safe_float(
        state.get("last_summary_timestamp"), 0
    )

    pairs = get_solana_pairs()

    analyzed = []
    qualified = []
    new_snapshots = dict(pair_snapshots)

    for pair in pairs:
        address = pair.get("pairAddress")

        if not address:
            continue

        try:
            previous = pair_snapshots.get(address, {})
            token = evaluate_pair(pair, previous)

            analyzed.append(token)
            new_snapshots[address] = token["snapshot"]

            if token["qualified"]:
                qualified.append(token)

        except Exception as error:
            print("Errore analisi coppia:", error)

    qualified.sort(
        key=lambda token: token["score"],
        reverse=True,
    )

    alerts_sent = 0
    current_timestamp = time.time()

    for token in qualified:
        address = token["pair_address"]

        previous_alert = alert_history.get(address, {})
        last_alert = safe_float(
            previous_alert.get("last_alert_timestamp"), 0
        )

        cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

        if current_timestamp - last_alert < cooldown_seconds:
            continue

        if send_telegram(format_alert(token)):
            alerts_sent += 1
            alert_history[address] = {
                "last_alert_timestamp": current_timestamp,
                "last_score": token["score"],
                "symbol": token["symbol"],
            }

    # Riepilogo periodico.
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
                qualified=len(qualified),
                sent=alerts_sent,
            )
        ):
            last_summary_timestamp = current_timestamp

    # Limita lo storico per contenere la dimensione del file.
    if len(new_snapshots) > 5000:
        new_snapshots = dict(
            list(new_snapshots.items())[-5000:]
        )

    if len(alert_history) > 3000:
        alert_history = dict(
            list(alert_history.items())[-3000:]
        )

    save_json(
        STATE_FILE,
        {
            "alert_history": alert_history,
            "pair_snapshots": new_snapshots,
            "last_summary_timestamp": last_summary_timestamp,
            "updated_at": iso_now(),
        },
    )

    # Salva risultati e diagnostica.
    save_json(
        DATA_FILE,
        {
            "updated_at": iso_now(),
            "pairs_scanned": len(analyzed),
            "qualified_count": len(qualified),
            "alerts_sent": alerts_sent,
            "min_score": MIN_SCORE,
            "max_score": MAX_SCORE,
            "min_liquidity_usd": MIN_LIQUIDITY_USD,
            "alert_cooldown_minutes": ALERT_COOLDOWN_MINUTES,
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
    print("Alert inviati:", alerts_sent)
    print("Fine:", iso_now())


if __name__ == "__main__":
    main()
