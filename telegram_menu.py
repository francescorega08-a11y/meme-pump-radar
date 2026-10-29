
"""Menu Telegram e allarmi percentuali per Meme Pump Radar AI.

Nessun acquisto o vendita automatica.
Le impostazioni vengono salvate in radar_state.json.
"""

import os
import re
import time

import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AUTHORIZED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/"
TIMEOUT = 20

http = requests.Session()
http.headers.update({"User-Agent": "MemePumpRadarAI/5.1"})


def _api(method, payload=None):
    if not API_BASE:
        return None

    try:
        response = http.post(
            f"{API_BASE}/{method}",
            json=payload or {},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) and data.get("ok") else None
    except (requests.RequestException, ValueError) as exc:
        print(f"[TELEGRAM MENU] {method} fallito: {exc}")
        return None


def _send(chat_id, text, keyboard=None):
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
    }

    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    return _api("sendMessage", payload) is not None


def _menu_keyboard(config, state):
    price_label = (
        "⏸️ Pausa allarme prezzo"
        if config.get("enabled", False)
        else "▶️ Attiva allarme prezzo"
    )

    new_alerts = state.get("new_coin_alerts_enabled", True)
    new_label = (
        "🔕 Disattiva alert nuove coin"
        if new_alerts
        else "🔔 Attiva alert nuove coin"
    )

    return [
        [
            {"text": "🪙 Cambia coin", "callback_data": "coin"},
            {"text": "📈 Cambia %", "callback_data": "percent"},
        ],
        [
            {"text": "💵 Prezzo ingresso", "callback_data": "entry"},
            {"text": "↕️ Direzione", "callback_data": "direction"},
        ],
        [{"text": price_label, "callback_data": "toggle_price"}],
        [{"text": new_label, "callback_data": "toggle_new_alerts"}],
        [{"text": "🔄 Aggiorna stato", "callback_data": "status"}],
    ]


def _config(state):
    config = state.get("price_alert_config")

    if not isinstance(config, dict):
        config = {
            "enabled": False,
            "token_name": "GTA 6",
            "token_address": (
                "4kjruqGHDsSqkMdFfCjV7Vb62HPFhtXyWc2t6JpQzDnz"
            ),
            "entry_price_usd": 0,
            "target_change_pct": 50,
            "direction": "up",
            "triggered": False,
        }
        state["price_alert_config"] = config

    return config


def _status_text(config, state):
    entry = float(config.get("entry_price_usd") or 0)
    pct = float(config.get("target_change_pct") or 50)
    direction = config.get("direction", "up")

    if direction == "up":
        target = entry * (1 + pct / 100)
        direction_text = "rialzo"
    else:
        target = entry * (1 - pct / 100)
        direction_text = "ribasso"

    entry_text = f"${entry:.12g}" if entry > 0 else "da impostare"
    target_text = (
        f"${target:.12g}"
        if entry > 0
        else "non calcolabile finché non imposti l'ingresso"
    )

    return (
        "🔔 MEME PUMP RADAR AI — ALLARME PREZZO\n\n"
        f"Coin: {config.get('token_name') or 'non impostata'}\n"
        f"Indirizzo: {config.get('token_address') or 'non impostato'}\n"
        f"Stato: {'🟢 attivo' if config.get('enabled') else '⏸️ in pausa'}\n"
        f"Prezzo d'ingresso: {entry_text}\n"
        f"Obiettivo: {direction_text} {pct:g}%\n"
        f"Prezzo obiettivo: {target_text}\n"
        f"Notifica già inviata: {'sì' if config.get('triggered') else 'no'}\n\n"
        "Alert nuove coin/pre-pump: "
        f"{'🟢 attivi' if state.get('new_coin_alerts_enabled', True) else '⏸️ in pausa'}\n\n"
        "Solo notifiche Telegram: nessun acquisto o vendita automatica."
    )


def _show_menu(chat_id, config, state):
    _send(chat_id, _status_text(config, state), _menu_keyboard(config, state))


def _valid_token_address(value):
    return bool(
        re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", value or "")
    )


def process_telegram_updates(state):
    """Elabora i comandi e i pulsanti Telegram a ogni avvio del radar."""

    config = _config(state)

    if not BOT_TOKEN or not AUTHORIZED_CHAT_ID:
        print("[TELEGRAM MENU] Token o chat ID mancante.")
        return state

    offset = int(state.get("telegram_update_offset", 0) or 0)

    data = _api(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": ["message", "callback_query"],
        },
    )

    if not data:
        return state

    for update in data.get("result", []):
        update_id = int(update.get("update_id", 0))
        state["telegram_update_offset"] = max(
            int(state.get("telegram_update_offset", 0) or 0),
            update_id + 1,
        )

        callback = update.get("callback_query")

        if callback:
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            callback_id = callback.get("id")
            action = str(callback.get("data") or "")

            if chat_id != AUTHORIZED_CHAT_ID:
                _api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Chat non autorizzata.",
                    },
                )
                continue

            _api("answerCallbackQuery", {"callback_query_id": callback_id})

            if action == "coin":
                state["telegram_pending_input"] = {
                    "field": "coin",
                    "chat_id": chat_id,
                }
                _send(
                    chat_id,
                    "Invia nome e indirizzo del token Solana in un solo "
                    "messaggio, separati da |\n"
                    "Esempio: GTA 6 | "
                    "4kjruqGHDsSqkMdFfCjV7Vb62HPFhtXyWc2t6JpQzDnz",
                )

            elif action == "percent":
                state["telegram_pending_input"] = {
                    "field": "percent",
                    "chat_id": chat_id,
                }
                _send(
                    chat_id,
                    "Scrivi la soglia percentuale positiva, "
                    "ad esempio 50 per +50%.",
                )

            elif action == "entry":
                state["telegram_pending_input"] = {
                    "field": "entry",
                    "chat_id": chat_id,
                }
                _send(
                    chat_id,
                    "Scrivi il prezzo d'ingresso in USD per singolo token, "
                    "ad esempio 0.0002. Usa un punto per i decimali.",
                )

            elif action == "direction":
                state["telegram_pending_input"] = {
                    "field": "direction",
                    "chat_id": chat_id,
                }
                _send(
                    chat_id,
                    "Scrivi UP per avviso al rialzo oppure DOWN "
                    "per avviso al ribasso.",
                )

            elif action == "toggle_price":
                config["enabled"] = not bool(config.get("enabled", False))

                if config["enabled"]:
                    config["triggered"] = False

                _send(
                    chat_id,
                    "🟢 Allarme prezzo attivato."
                    if config["enabled"]
                    else "⏸️ Allarme prezzo messo in pausa.",
                )
                _show_menu(chat_id, config, state)

            elif action == "toggle_new_alerts":
                state["new_coin_alerts_enabled"] = not bool(
                    state.get("new_coin_alerts_enabled", True)
                )

                _send(
                    chat_id,
                    "🟢 Alert nuove coin/pre-pump attivati."
                    if state["new_coin_alerts_enabled"]
                    else "⏸️ Alert nuove coin/pre-pump messi in pausa.",
                )
                _show_menu(chat_id, config, state)

            elif action == "status":
                _show_menu(chat_id, config, state)

            continue

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = str(message.get("text") or "").strip()

        if not text:
            continue

        if chat_id != AUTHORIZED_CHAT_ID:
            print("[TELEGRAM MENU] Ignorato messaggio da chat non autorizzata.")
            continue

        # Accetta /allert, /allarme, /start e /help,
        # anche se Telegram aggiunge @nome_bot.
        command = text.split()[0].split("@")[0].lower()

        if command in ("/allert", "/allarme", "/start", "/help"):
            _show_menu(chat_id, config, state)
            continue

        pending = state.get("telegram_pending_input") or {}

        if str(pending.get("chat_id", "")) != chat_id:
            continue

        field = pending.get("field")

        if field == "coin":
            parts = [part.strip() for part in text.split("|", 1)]

            if (
                len(parts) != 2
                or not parts[0]
                or not _valid_token_address(parts[1])
            ):
                _send(
                    chat_id,
                    "Formato non valido. Invia: NOME | INDIRIZZO_SOLANA",
                )
                continue

            config["token_name"] = parts[0][:80]
            config["token_address"] = parts[1]
            config["entry_price_usd"] = 0
            config["triggered"] = False
            config["enabled"] = False
            state.pop("telegram_pending_input", None)

            _send(
                chat_id,
                "✅ Coin aggiornata. L'allarme prezzo è in pausa. "
                "Imposta il prezzo d'ingresso e attivalo dal menu.",
            )
            _show_menu(chat_id, config, state)

        elif field == "percent":
            try:
                pct = float(text.replace(",", ".").replace("%", "").strip())
            except ValueError:
                pct = 0

            if not 0 < pct <= 100000:
                _send(
                    chat_id,
                    "Percentuale non valida. Inserisci un numero maggiore "
                    "di 0 e non superiore a 100000.",
                )
                continue

            config["target_change_pct"] = pct
            config["triggered"] = False
            state.pop("telegram_pending_input", None)
            _send(chat_id, "✅ Percentuale aggiornata.")
            _show_menu(chat_id, config, state)

        elif field == "entry":
            try:
                entry = float(text.replace(",", ".").replace("$", "").strip())
            except ValueError:
                entry = 0

            if entry <= 0 or entry != entry or entry == float("inf"):
                _send(
                    chat_id,
                    "Prezzo non valido. Inserisci un prezzo in USD maggiore "
                    "di zero.",
                )
                continue

            config["entry_price_usd"] = entry
            config["triggered"] = False
            state.pop("telegram_pending_input", None)
            _send(chat_id, "✅ Prezzo d'ingresso salvato.")
            _show_menu(chat_id, config, state)

        elif field == "direction":
            value = text.strip().lower()

            if value not in ("up", "down"):
                _send(chat_id, "Rispondi UP per rialzo o DOWN per ribasso.")
                continue

            config["direction"] = value
            config["triggered"] = False
            state.pop("telegram_pending_input", None)
            _send(chat_id, "✅ Direzione aggiornata.")
            _show_menu(chat_id, config, state)

    return state


def check_price_alert(state):
    """Controlla la soglia del prezzo e invia una sola notifica."""

    config = _config(state)

    if not config.get("enabled", False):
        return state

    token_address = str(config.get("token_address") or "").strip()

    try:
        entry = float(config.get("entry_price_usd"))
        pct = float(config.get("target_change_pct", 50))
    except (TypeError, ValueError):
        print("[PRICE ALERT] Prezzo d'ingresso o percentuale non validi.")
        return state

    direction = config.get("direction", "up")

    if (
        not _valid_token_address(token_address)
        or entry <= 0
        or pct <= 0
        or direction not in ("up", "down")
    ):
        print("[PRICE ALERT] Configurazione incompleta o non valida.")
        return state

    if config.get("triggered", False):
        return state

    try:
        response = http.get(DEX_TOKEN_URL + token_address, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[PRICE ALERT] Lettura prezzo fallita: {exc}")
        return state

    candidates = []

    for pair in data.get("pairs") or []:
        if not isinstance(pair, dict) or pair.get("chainId") != "solana":
            continue

        base = pair.get("baseToken") or {}

        if str(base.get("address") or "").lower() != token_address.lower():
            continue

        try:
            price = float(pair.get("priceUsd") or 0)
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            continue

        if price > 0:
            candidates.append((liquidity, price, pair))

    if not candidates:
        print("[PRICE ALERT] Nessuna coppia Solana con prezzo valido.")
        return state

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, current, pair = candidates[0]

    target = (
        entry * (1 + pct / 100)
        if direction == "up"
        else entry * (1 - pct / 100)
    )

    reached = current >= target if direction == "up" else current <= target
    change = (current / entry - 1) * 100

    print(
        f"[PRICE ALERT] {config.get('token_name')}: "
        f"${current:.12g}; variazione {change:+.2f}%; "
        f"obiettivo ${target:.12g}"
    )

    if not reached:
        return state

    url = pair.get("url") or (
        f"https://dexscreener.com/solana/{pair.get('pairAddress', '')}"
    )

    message = (
        "🔔 MEME PUMP RADAR AI — OBIETTIVO RAGGIUNTO\n\n"
        f"Coin: {config.get('token_name') or 'Token'}\n"
        f"Prezzo d'ingresso: ${entry:.12g}\n"
        f"Prezzo rilevato: ${current:.12g}\n"
        f"Variazione dall'ingresso: {change:+.2f}%\n"
        f"Obiettivo: {'+' if direction == 'up' else '-'}{pct:g}%\n"
        f"Link: {url}\n\n"
        "Questa è solo una notifica. Nessun acquisto o vendita automatica."
    )

    if _send(AUTHORIZED_CHAT_ID, message):
        config["triggered"] = True
        config["triggered_at"] = time.time()
        print("[PRICE ALERT] Notifica inviata.")
    else:
        print("[PRICE ALERT] Invio non riuscito; riproverà alla prossima scansione.")

    return state
