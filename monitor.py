"""
Esegue UNA scansione dei prezzi Cardtrader per la lista di carte, e notifica
su Telegram le occasioni trovate. Pensato per essere lanciato periodicamente
da GitHub Actions (vedi .github/workflows/monitor.yml) — non contiene loop
infiniti né sleep lunghi.

Un'inserzione è considerata un'occasione se:
  - il prezzo di riferimento (mediana calcolata dopo aver rimosso gli
    estremi più alti/bassi — config: trim_pct) è <= alla soglia fissa
    (config: fixed_price_threshold_eur): allora basta che il prezzo
    dell'inserzione sia <= quella soglia fissa
  - altrimenti (riferimento sopra la soglia fissa): il prezzo deve essere
    <= riferimento * (1 - sconto%) (config: discount_below_average_pct)

Legge i token da variabili d'ambiente:
  CARDTRADER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Prima di lanciare questo script esegui build_card_index.py una volta.
"""
import json
import os
import sys
import statistics
import time
from datetime import datetime

import requests

from cardtrader_api import CardTraderClient

CONFIG_FILE = "config.json"
INDEX_FILE = "cards_index.json"
SENT_ALERTS_FILE = "sent_alerts.json"  # per non notificare due volte la stessa occasione

# Scala delle condizioni Cardtrader, dalla migliore alla peggiore.
# Un'inserzione è accettata solo se la sua condizione è in questa lista
# ed è alla posizione della soglia minima configurata o più in alto (indice minore o uguale).
CONDITION_ORDER = ["Mint", "Near Mint", "Slightly Played", "Moderately Played", "Played", "Poor"]


def condition_rank(condition_name):
    """Ritorna la posizione della condizione nella scala (0 = migliore).
    None se la condizione non è riconosciuta."""
    try:
        return CONDITION_ORDER.index(condition_name)
    except ValueError:
        return None


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
    """Ritorna True se l'invio è andato a buon fine, False altrimenti
    (es. rate limit di Telegram)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    if resp.status_code != 200:
        print(f"  ! Errore invio Telegram: {resp.status_code} {resp.text}")
        return False
    return True


def trimmed_median(values, trim_pct):
    """Rimuove trim_pct dei valori più bassi e più alti, poi calcola la
    mediana dei rimanenti. Se il taglio lascerebbe troppo pochi valori,
    usa la mediana su tutti i valori senza tagliare."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    k = int(n * trim_pct)
    trimmed = sorted_vals[k:n - k] if n - 2 * k >= 1 else sorted_vals
    return statistics.median(trimmed)


def check_card(client, card_name, blueprint_entries, criteria, min_listings, sent_alerts, allowed_seller_countries, max_expansion_year, trim_pct, min_condition_rank):
    """Controlla tutte le blueprint (edizioni) di una carta e ritorna eventuali occasioni."""
    deals = []
    for entry in blueprint_entries:
        bp_id = entry["blueprint_id"]
        exp_year = entry.get("expansion_year")
        # Esclude del tutto le espansioni uscite dopo l'anno soglia. Se l'anno
        # non è noto (dato mancante), la teniamo comunque per non perdere carte.
        if max_expansion_year is not None and exp_year is not None and exp_year > max_expansion_year:
            continue
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
            # Filtra per condizione minima (es. solo MP o superiore).
            # Condizioni non riconosciute vengono scartate per prudenza.
            if min_condition_rank is not None:
                cond = p.get("properties_hash", {}).get("condition")
                rank = condition_rank(cond)
                if rank is None or rank > min_condition_rank:
                    continue
            prices.append((cents / 100.0, p))

        if len(prices) < min_listings:
            continue

        values = [pr for pr, _ in prices]
        avg_price = trimmed_median(values, trim_pct)

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
    max_expansion_year = config.get("max_expansion_year")
    trim_pct = config.get("trim_pct", 0.1)
    min_condition_name = config.get("min_condition", "Moderately Played")
    min_condition_rank = condition_rank(min_condition_name)
    if min_condition_rank is None:
        print(f"Attenzione: min_condition '{min_condition_name}' non riconosciuta, nessun filtro condizione applicato.")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Inizio scansione di {len(cards_index)} carte...")
    total_deals = 0
    telegram_blocked = False  # se Telegram ci rifiuta un invio, smettiamo di provare per questo giro

    for card_name, blueprint_entries in cards_index.items():
        if not blueprint_entries:
            continue
        deals = check_card(client, card_name, blueprint_entries, criteria, min_listings, sent_alerts, allowed_seller_countries, max_expansion_year, trim_pct, min_condition_rank)

        if not deals:
            continue

        if telegram_blocked:
            # Telegram ci ha già rifiutato un invio in questo giro: non
            # segnamo queste occasioni come notificate, verranno ritentate
            # al prossimo controllo.
            continue

        # Raggruppa tutte le occasioni della stessa carta in UN SOLO
        # messaggio, invece di uno per ogni inserzione: evita di superare
        # il limite anti-spam di Telegram quando ci sono molte inserzioni
        # che soddisfano il criterio contemporaneamente.
        deals.sort(key=lambda d: d["price"])
        lines = [
            f"🃏 <b>{card_name}</b> — {len(deals)} occasion{'e' if len(deals) == 1 else 'i'} trovat{'a' if len(deals) == 1 else 'e'}"
        ]
        for deal in deals[:10]:  # elenco al massimo 10 inserzioni per non fare messaggi enormi
            lines.append(
                f"• {deal['price']:.2f}€ (rif. {deal['avg_price']:.2f}€) — {deal['expansion']} — {deal['seller']}"
            )
        if len(deals) > 10:
            lines.append(f"…e altre {len(deals) - 10}")
        msg = "\n".join(lines)

        if send_telegram(telegram_bot_token, telegram_chat_id, msg):
            for deal in deals:
                sent_alerts.add(deal["alert_key"])
            total_deals += len(deals)
            print(f"  -> Notificata occasione: {card_name} ({len(deals)} inserzioni)")
        else:
            # Non riproviamo altri invii in questo giro: probabilmente è un
            # rate limit di Telegram che richiede diversi minuti per sbloccarsi.
            telegram_blocked = True

        time.sleep(1)  # piccola pausa tra un messaggio e l'altro, per sicurezza

    save_json(SENT_ALERTS_FILE, list(sent_alerts))
    print(f"Scansione completata. Occasioni trovate: {total_deals}.")


if __name__ == "__main__":
    main()
