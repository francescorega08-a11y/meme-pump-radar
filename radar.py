
import os
import json
import time
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIGURAZIONE
# ============================================================

MIN_LIQUIDITY_USD = 25_000
MIN_SCORE = 6
MAX_SCORE = 9
ALERT_COOLDOWN_MINUTES = 120

# Evita di valutare come accelerazione significativa
# variazioni dovute a volumi troppo piccoli.
MIN_VOLUME_5M_USD = 1_000
MIN_VOLUME_1H_USD = 10_000
MIN_TXNS_5M = 5

# Evita di inseguire rialzi già eccessivi.
MAX_PRICE_CHANGE_5M_PCT = 8.0
MAX_PRICE_CHANGE_1H_PCT = 20.0

STATE_FILE = "radar_state.json"
DATA_FILE = "radar_data.json"

DEX_BASE_URL = "https://api.dexscreener.com"

SEARCH_TERMS = [
    "solana meme",
    "solana dog",
    "solana cat",
    "solana pepe",
    "solana pump",
    "solana bonk",
    "solana ai",
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Meme-Pump-Radar-AI/3.0"
})


# ============================================================
# UTILITÀ
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_epoch():
    return time.time()


def to_number(value, default=0.0):
    try:
        if value is None:
            return default
        result = float(value)
        if result != result or result in (
            float("inf"), float("-inf")
        ):
            return default
        return result
    except (TypeError, ValueError):
        return default


def optional_number(value):
    if value is None:
        return None
    try:
        result = float(value)
        if result != result or result in (
            float("inf"), float("-inf")
        ):
            return None
        return result
    except (TypeError, ValueError):
        return None


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, data):
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )
    os.replace(temporary_path, path)


def api_get(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def format_usd(value):
    if value is None:
        return "N/D"
    return f"${value:,.2f}"


def format_pct(value):
    if value is None:
        return "N/D"
    return f"{value:+.2f}%"


# ============================================================
# LIQUIDITÀ
# ============================================================

def get_liquidity(pair):
    liquidity = pair.get("liquidity")

    if not isinstance(liquidity, dict):
        return None

    value = optional_number(liquidity.get("usd"))

    if value is None or value <= 0:
        return None

    return value


# ============================================================
# RICERCA DELLE COPPIE SOLANA
# ============================================================

def search_pairs():
    found = {}

    for term in SEARCH_TERMS:
        try:
            data = api_get(
                f"{DEX_BASE_URL}/latest/dex/search",
                params={"q": term}
            )

            for pair in data.get("pairs") or []:
                if (
                    (pair.get("chainId") or "").lower()
                    != "solana"
                ):
                    continue

                address = (
                    pair.get("pairAddress") or ""
                ).strip()

                if not address:
                    continue

                found[address.lower()] = pair

        except requests.RequestException as error:
            print(f"Errore ricerca '{term}': {error}")
        except (ValueError, TypeError) as error:
            print(f"Risposta non valida '{term}': {error}")

        time.sleep(0.25)

    return list(found.values())


# ============================================================
# VERIFICA DELLA COPPIA
# ============================================================

def refresh_pair(pair):
    """
    Verifica l'indirizzo esatto della pool.
    Integra campi mancanti soltanto dalla ricerca originale
    della stessa coppia, senza inventare liquidità.
    """

    address = (pair.get("pairAddress") or "").strip()

    if (
        (pair.get("chainId") or "").lower() != "solana"
        or not address
    ):
        result = dict(pair)
        result["verified"] = False
        result["liquidity_source"] = "missing"
        return result

    url = (
        f"{DEX_BASE_URL}/latest/dex/pairs/"
        f"solana/{address}"
    )

    try:
        data = api_get(url)
        pairs = data.get("pairs") or []

        fresh = next(
            (
                item for item in pairs
                if (
                    (item.get("chainId") or "").lower()
                    == "solana"
                    and (
                        item.get("pairAddress") or ""
                    ).lower() == address.lower()
                )
            ),
            None
        )

        if fresh is None:
            result = dict(pair)
            result["verified"] = False
            result["liquidity_source"] = "missing"
            return result

        result = dict(pair)
        result.update(fresh)

        # Conserva dati della ricerca soltanto se quelli
        # della risposta verificata sono assenti.
        fields = (
            "liquidity",
            "volume",
            "txns",
            "priceUsd",
            "priceChange",
            "baseToken",
            "quoteToken",
            "pairCreatedAt",
        )

        for field in fields:
            fresh_value = fresh.get(field)
            original_value = pair.get(field)

            if (
                fresh_value is None
                and original_value is not None
            ):
                result[field] = original_value
            elif (
                isinstance(fresh_value, dict)
                and isinstance(original_value, dict)
            ):
                merged = dict(original_value)
                merged.update(fresh_value)

                for key, value in original_value.items():
                    if merged.get(key) is None and value is not None:
                        merged[key] = value

                result[field] = merged

        fresh_liquidity = get_liquidity(fresh)
        final_liquidity = get_liquidity(result)

        if fresh_liquidity is not None:
            result["liquidity_source"] = "pair_endpoint"
        elif final_liquidity is not None:
            result["liquidity_source"] = "search_fallback"
        else:
            result["liquidity_source"] = "missing"

        result["verified"] = True
        return result

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ) as error:
        print(f"Verifica fallita per {address}: {error}")

        result = dict(pair)
        result["verified"] = False
        result["liquidity_source"] = "missing"
        return result


# ============================================================
# METRICHE DI MERCATO
# ============================================================

def get_metrics(pair):
    base = pair.get("baseToken") or {}
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}

    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    price = optional_number(pair.get("priceUsd"))
    liquidity = get_liquidity(pair)

    volume_5m = optional_number(volume.get("m5"))
    volume_1h = optional_number(volume.get("h1"))

    buys_5m = optional_number(tx5.get("buys"))
    sells_5m = optional_number(tx5.get("sells"))
    buys_1h = optional_number(tx1h.get("buys"))
    sells_1h = optional_number(tx1h.get("sells"))

    change_5m = optional_number(changes.get("m5"))
    change_1h = optional_number(changes.get("h1"))

    total_tx_5m = (
        (buys_5m or 0) + (sells_5m or 0)
        if buys_5m is not None or sells_5m is not None
        else None
    )

    buy_ratio = None
    if buys_5m is not None and sells_5m is not None:
        if sells_5m > 0:
            buy_ratio = buys_5m / sells_5m
        elif buys_5m > 0:
            buy_ratio = float("inf")

    created_at = pair.get("pairCreatedAt")

    return {
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "token_address": base.get("address"),
        "symbol": base.get("symbol") or "",
        "name": base.get("name") or "",
        "price_usd": price,
        "liquidity_usd": liquidity,
        "liquidity_source": pair.get(
            "liquidity_source", "missing"
        ),
        "volume_5m_usd": volume_5m,
        "volume_1h_usd": volume_1h,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "buys_1h": buys_1h,
        "sells_1h": sells_1h,
        "transactions_5m": total_tx_5m,
        "buy_sell_ratio_5m": (
            buy_ratio if buy_ratio != float("inf")
            else None
        ),
        "buy_sell_ratio_infinite": (
            buy_ratio == float("inf")
        ),
        "price_change_5m_pct": change_5m,
        "price_change_1h_pct": change_1h,
        "pair_created_at": created_at,
        "verified": bool(pair.get("verified")),
        "dex_url": pair.get("url"),
    }


# ============================================================
# PUNTEGGIO PRE-PUMP: MASSIMO 9 PUNTI
# ============================================================

def calculate_score(metrics, previous):
    score = 0
    reasons = []
    warnings = []

    v5 = metrics.get("volume_5m_usd")
    v1h = metrics.get("volume_1h_usd")
    tx5 = metrics.get("transactions_5m")
    buys = metrics.get("buys_5m")
    sells = metrics.get("sells_5m")
    change5 = metrics.get("price_change_5m_pct")
    change1h = metrics.get("price_change_1h_pct")
    liquidity = metrics.get("liquidity_usd")

    prev_v5 = optional_number(
        previous.get("volume_5m_usd")
    )
    prev_tx = optional_number(
        previous.get("transactions_5m")
    )
    prev_liquidity = optional_number(
        previous.get("liquidity_usd")
    )

    # 1 punto: volume recente sufficientemente significativo.
    if v5 is not None and v5 >= MIN_VOLUME_5M_USD:
        score += 1
        reasons.append("volume_5m_significativo")

    # 1 punto: attività complessiva nell'ultima ora.
    if v1h is not None and v1h >= MIN_VOLUME_1H_USD:
        score += 1
        reasons.append("volume_1h_significativo")

    # 2 punti: volume recente in accelerazione rispetto
    # alla precedente scansione disponibile.
    if (
        v5 is not None
        and prev_v5 is not None
        and prev_v5 >= 100
        and v5 >= prev_v5 * 1.5
    ):
        score += 2
        reasons.append("accelerazione_volume_5m")
    elif (
        v5 is not None
        and prev_v5 is not None
        and prev_v5 >= 100
        and v5 >= prev_v5 * 1.25
    ):
        score += 1
        reasons.append("crescita_volume_5m")

    # 1 punto: crescita delle transazioni recenti.
    if (
        tx5 is not None
        and prev_tx is not None
        and prev_tx >= 3
        and tx5 >= prev_tx * 1.5
    ):
        score += 1
        reasons.append("accelerazione_transazioni")

    # 1 punto: prevalenza degli acquisti con campione minimo.
    if (
        buys is not None
        and sells is not None
        and tx5 is not None
        and tx5 >= MIN_TXNS_5M
        and buys > sells
        and (
            sells == 0
            or buys / max(sells, 1) >= 1.2
        )
    ):
        score += 1
        reasons.append("pressione_acquisti")

    # 1 punto: volume recente elevato rispetto alla media
    # oraria stimata. È un indicatore grezzo, non una previsione.
    if (
        v5 is not None
        and v1h is not None
        and v1h > 0
        and v5 >= (v1h / 12) * 1.5
    ):
        score += 1
        reasons.append("volume_recente_sopra_media_oraria")

    # 1 punto: movimento del prezzo ancora contenuto.
    if (
        change5 is not None
        and change1h is not None
        and 0 < change5 <= MAX_PRICE_CHANGE_5M_PCT
        and change1h <= MAX_PRICE_CHANGE_1H_PCT
    ):
        score += 1
        reasons.append("prezzo_non_eccessivamente_esteso")

    # 1 punto: crescita della liquidità fra le scansioni.
    if (
        liquidity is not None
        and prev_liquidity is not None
        and prev_liquidity > 0
        and liquidity >= prev_liquidity * 1.05
    ):
        score += 1
        reasons.append("liquidita_in_crescita")

    # Avvisi diagnostici: non aggiungono punti.
    if liquidity is None:
        warnings.append("liquidita_non_disponibile")

    if change5 is not None and change5 > MAX_PRICE_CHANGE_5M_PCT:
        warnings.append("rialzo_5m_gia_elevato")

    if change1h is not None and change1h > MAX_PRICE_CHANGE_1H_PCT:
        warnings.append("rialzo_1h_gia_elevato")

    if (
        v5 is not None
        and prev_v5 is not None
        and prev_v5 > 0
        and v5 < prev_v5
    ):
        warnings.append("volume_5m_in_cal o")

    return min(score, MAX_SCORE), reasons, warnings


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram non configurato: avviso non inviato.")
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = SESSION.post(
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
            print("Telegram ha rifiutato il messaggio.")
            return False

        return True

    except requests.RequestException as error:
        print(f"Errore Telegram: {error}")
        return False


def build_alert(metrics, score, reasons, warnings):
    symbol = metrics.get("symbol") or "TOKEN"
    name = metrics.get("name") or symbol
    address = metrics.get("token_address") or "N/D"
    url = metrics.get("dex_url") or (
        "https://dexscreener.com/solana/"
        + str(metrics.get("pair_address") or "")
    )

    lines = [
        "🚨 POSSIBILE SEGNALE PRE-PUMP",
        "",
        f"Token: {name} ({symbol})",
        f"Punteggio: {score}/{MAX_SCORE}",
        f"Liquidità: {format_usd(metrics.get('liquidity_usd'))}",
        f"Prezzo: {format_usd(metrics.get('price_usd'))}",
        f"Volume 5m: {format_usd(metrics.get('volume_5m_usd'))}",
        f"Volume 1h: {format_usd(metrics.get('volume_1h_usd'))}",
        f"Variazione 5m: {format_pct(metrics.get('price_change_5m_pct'))}",
        f"Variazione 1h: {format_pct(metrics.get('price_change_1h_pct'))}",
        f"Acquisti 5m: {metrics.get('buys_5m')}",
        f"Vendite 5m: {metrics.get('sells_5m')}",
        "",
        "Segnali:",
    ]

    lines.extend(f"• {reason}" for reason in reasons)

    if warnings:
        lines.append("")
        lines.append("Attenzioni:")
        lines.extend(f"• {warning}" for warning in warnings)

    lines.extend([
        "",
        f"Indirizzo token: {address}",
        f"Pool: {metrics.get('pair_address') or 'N/D'}",
        f"Liquidità fonte: {metrics.get('liquidity_source')}",
        f"Dexscreener: {url}",
        "",
        "⚠️ Segnale automatico, non garanzia di rialzo. Verifica il token prima di agire.",
    ])

    return "\n".join(lines)


# ============================================================
# CICLO PRINCIPALE
# ============================================================

def main():
    started_at = now_iso()
    started_epoch = now_epoch()

    old_state = read_json(STATE_FILE, {})
    old_pairs = old_state.get("pairs", {})
    last_alerts = old_state.get("last_alerts", {})

    if not isinstance(old_pairs, dict):
        old_pairs = {}

    if not isinstance(last_alerts, dict):
        last_alerts = {}

    raw_pairs = search_pairs()

    results = []
    new_state_pairs = {}
    alerts_sent = 0
    qualified_count = 0
    verification_failed_count = 0
    verified_count = 0

    for raw_pair in raw_pairs:
        address = (
            raw_pair.get("pairAddress") or ""
        ).strip()

        if not address:
            continue

        key = address.lower()
        previous = old_pairs.get(key, {})

        pair = refresh_pair(raw_pair)

        if not pair.get("verified"):
            verification_failed_count += 1
            continue

        verified_count += 1
        metrics = get_metrics(pair)

        score, reasons, warnings = calculate_score(
            metrics,
            previous
        )

        liquidity = metrics.get("liquidity_usd")

        # Condizioni di qualificazione non negoziabili.
        liquidity_ok = (
            liquidity is not None
            and liquidity >= MIN_LIQUIDITY_USD
        )

        qualified = (
            metrics.get("verified")
            and liquidity_ok
            and score >= MIN_SCORE
        )

        metrics["score"] = score
        metrics["score_reasons"] = reasons
        metrics["warnings"] = warnings
        metrics["qualified"] = qualified
        metrics["scanned_at"] = now_iso()

        # Mantiene il dato dell'ultima scansione per il confronto
        # con la prossima. Non sovrascrive lo storico degli alert.
        new_state_pairs[key] = {
            "symbol": metrics.get("symbol"),
            "name": metrics.get("name"),
            "price_usd": metrics.get("price_usd"),
            "liquidity_usd": liquidity,
            "volume_5m_usd": metrics.get("volume_5m_usd"),
            "volume_1h_usd": metrics.get("volume_1h_usd"),
            "transactions_5m": metrics.get("transactions_5m"),
            "buys_5m": metrics.get("buys_5m"),
            "sells_5m": metrics.get("sells_5m"),
            "price_change_5m_pct": metrics.get("price_change_5m_pct"),
            "price_change_1h_pct": metrics.get("price_change_1h_pct"),
            "scanned_at": metrics["scanned_at"],
        }

        if qualified:
            qualified_count += 1
            previous_alert = to_number(
                last_alerts.get(key), 0
            )
            cooldown_seconds = ALERT_COOLDOWN_MINUTES * 60

            if started_epoch - previous_alert >= cooldown_seconds:
                message = build_alert(
                    metrics, score, reasons, warnings
                )

                if send_telegram(message):
                    alerts_sent += 1
                    last_alerts[key] = started_epoch

        results.append(metrics)

        print(
            f"{metrics.get('symbol') or 'TOKEN'} | "
            f"score={score}/{MAX_SCORE} | "
            f"liquidita={liquidity} | "
            f"qualificata={qualified}"
        )

        time.sleep(0.15)

    # Conserva anche gli orari degli alert per coppie che
    # non sono apparse in questa scansione.
    state = {
        "updated_at": now_iso(),
        "pairs": new_state_pairs,
        "last_alerts": last_alerts,
    }

    data = {
        "updated_at": now_iso(),
        "network": "solana",
        "pairs_scanned": len(raw_pairs),
        "api_verified_count": verified_count,
        "api_verification_failed_count": verification_failed_count,
        "qualified_count": qualified_count,
        "alerts_sent": alerts_sent,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "min_score": MIN_SCORE,
        "max_score": MAX_SCORE,
        "results": sorted(
            results,
            key=lambda item: (
                item.get("qualified", False),
                item.get("score", 0),
                item.get("volume_5m_usd") or 0,
            ),
            reverse=True,
        ),
    }

    write_json(STATE_FILE, state)
    write_json(DATA_FILE, data)

    print("")
    print("========== RISULTATO RADAR ==========")
    print(f"Coppie trovate: {len(raw_pairs)}")
    print(f"Coppie verificate: {verified_count}")
    print(f"Verifiche fallite: {verification_failed_count}")
    print(f"Segnali qualificati: {qualified_count}")
    print(f"Avvisi Telegram inviati: {alerts_sent}")
    print(f"Liquidità minima: ${MIN_LIQUIDITY_USD:,.0f}")
    print(f"Punteggio minimo: {MIN_SCORE}/{MAX_SCORE}")
    print(f"Completato: {now_iso()}")
    print("=====================================")


if __name__ == "__main__":
    main()
