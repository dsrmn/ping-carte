"""
Esegue UNA scansione dei prezzi Cardtrader per la lista di carte, e notifica
su Telegram le occasioni trovate. Pensato per essere lanciato periodicamente
da GitHub Actions (vedi .github/workflows/monitor.yml) — non contiene loop
infiniti né sleep lunghi.

Un'inserzione è considerata un'occasione se soddisfa ENTRAMBI i criteri:
  1. prezzo <= soglia fissa (config: fixed_price_threshold_eur)
  2. prezzo <= media delle altre inserzioni della stessa carta * (1 - sconto%)
     (config: discount_below_average_pct)

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


def check_card(client, card_name, blueprint_entries, criteria, min_listings, sent_alerts, allowed_seller_countries):
    """Controlla tutte le blueprint (edizioni) di una carta e ritorna eventuali occasioni."""
    deals = []
    for entry in blueprint_entries:
        bp_id = entry["blueprint_id"]
        try:
            # language="en" filtra lato API solo le inserzioni in inglese
            products = client.get_marketplace_products(bp_id, language="en")
        except Exception as e:
            print(f"  ! Errore recuperando listings per {card_name} ({bp_id}): {e}")
            continue

        prices = []
        for p in products:
            price_info = p.get("price", {})
            cents = price_info.get("cents")
            currency = price_info.get("currency", "EUR")
            if cents is None or currency != "EUR":
                continue
            # Filtra per Paese di spedizione del venditore (es. solo US/CA)
            if allowed_seller_countries:
                seller_country = p.get("user", {}).get("country_code")
                if seller_country not in allowed_seller_countries:
                    continue
            prices.append((cents / 100.0, p))

        if len(prices) < min_listings:
            continue

        values = [pr for pr, _ in prices]
        avg_price = statistics.mean(values)

        for price, product in prices:
            # Se la media delle inserzioni è già sotto la soglia fissa,
            # basta rispettare quella (le percentuali diventano poco
            # significative su prezzi già bassi). Se la media è sopra la
            # soglia fissa, conta solo lo sconto percentuale rispetto alla
            # media: altrimenti le carte con media alta non scatterebbero
            # mai, perché lo sconto le porterebbe comunque sopra la soglia.
            if avg_price > criteria["fixed_price_threshold_eur"]:
                is_deal = price <= avg_price * (1 - criteria["discount_below_average_pct"] / 100)
            else:
                is_deal = price <= criteria["fixed_price_threshold_eur"]

            if is_deal:
                product_id = product.get("id")
                alert_key = f"{bp_id}:{product_id}"
                if alert_key in sent_alerts:
                    continue
                deals.append({
                    "card_name": card_name,
                    "expansion": entry["expansion"],
                    "price": price,
                    "avg_price": round(avg_price, 2),
                    "seller": product.get("user", {}).get("username", "?"),
                    "alert_key": alert_key,
                })
    return deals


def main():
    cardtrader_token = os.environ.get("CARDTRADER_TOKEN")
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing_env = [name for name, val in [
        ("CARDTRADER_TOKEN", cardtrader_token),
        ("TELEGRAM_BOT_TOKEN", telegram_bot_token),
        ("TELEGRAM_CHAT_ID", telegram_chat_id),
    ] if not val]
    if missing_env:
        sys.exit(f"Errore: variabili d'ambiente mancanti: {', '.join(missing_env)}")

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit("config.json non trovato!")

    cards_index = load_json(INDEX_FILE, None)
    if cards_index is None:
        sys.exit("cards_index.json non trovato: esegui prima build_card_index.py")

    sent_alerts = set(load_json(SENT_ALERTS_FILE, []))
    client = CardTraderClient(cardtrader_token)
    criteria = config["deal_criteria"]
    min_listings = config.get("min_listings_for_average", 3)
    allowed_seller_countries = set(config.get("allowed_seller_countries", []))

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Inizio scansione di {len(cards_index)} carte...")
    total_deals = 0

    for card_name, blueprint_entries in cards_index.items():
        if not blueprint_entries:
            continue
        deals = check_card(client, card_name, blueprint_entries, criteria, min_listings, sent_alerts, allowed_seller_countries)

        for deal in deals:
            msg = (
                f"🃏 <b>Occasione trovata!</b>\n"
                f"<b>{deal['card_name']}</b> ({deal['expansion']})\n"
                f"Prezzo: <b>{deal['price']:.2f}€</b> (media: {deal['avg_price']:.2f}€)\n"
                f"Venditore: {deal['seller']}"
            )
            send_telegram(telegram_bot_token, telegram_chat_id, msg)
            sent_alerts.add(deal["alert_key"])
            total_deals += 1
            print(f"  -> Notificata occasione: {deal['card_name']} a {deal['price']:.2f}€")

    save_json(SENT_ALERTS_FILE, list(sent_alerts))
    print(f"Scansione completata. Occasioni trovate: {total_deals}.")


if __name__ == "__main__":
    main()
