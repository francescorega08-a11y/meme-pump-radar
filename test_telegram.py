import os
import requests

# Inserisci i tuoi dati direttamente qui per il test (oppure usa le variabili d'ambiente)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "IL_TUO_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IL_TUO_CHAT_ID")

def send_test_message():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🚨 <b>Test di prova Meme Pump Radar AI</b>\n\nSe vedi questo messaggio, il bot funziona perfettamente!",
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Notifica Telegram inviata con successo!")
        else:
            print(f"Errore: {response.text}")
    except Exception as e:
        print(f"Errore di connessione: {e}")

if __name__ == "__main__":
    send_test_message()
