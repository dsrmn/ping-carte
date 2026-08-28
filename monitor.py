"""
Esegue UNA scansione dei prezzi Cardtrader per la lista di carte, e notifica
su Telegram le occasioni trovate. Pensato per essere lanciato periodicamente
da GitHub Actions (vedi .github/workflows/monitor.yml) — non contiene loop
infiniti né sleep lunghi.

Un'inserzione è considerata un'occasione se:
  - la media delle altre inserzioni della stessa carta è <= alla soglia
    fissa (config: fixed_price_threshold_eur): allora basta che il prezzo
    sia <= quella soglia fissa
  - altrimenti (media sopra la soglia fissa): il prezzo deve essere
    <= media * (1 - sconto%) (config: discount_below_average_pct)

Legge i token da variabili d'ambiente:
  CARDTRADER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Prima di lanciare questo script esegui build_card_index.py una volta.
"""
import json
import os
import sys
import statistics
from datetime import datetime

import requests

from cardtrader_api import CardTraderClient

CONFIG_FILE = "config.json"
INDEX_FILE = "cards_index.json"
SENT_ALERTS_FILE = "sent_alerts.json"  # per non notificare due volte la stessa occasione


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def send_telegram(bot_token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    if resp.status_code != 200:
        print(f"  ! Errore invio Telegram: {resp.status_code} {resp.text}")


def check_card(client, card_name, blueprint_entries, criteria, min_listings, sent_alerts):
