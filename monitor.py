"""
Esegue UNA scansione dei prezzi Cardtrader per la lista di carte Yu-Gi-Oh!
e notifica su Telegram le occasioni trovate. Pensato per essere lanciato
periodicamente da GitHub Actions (vedi .github/workflows/monitor.yml).

Le inserzioni vengono scaricate per espansione (una richiesta per set invece
di una per ogni versione di carta): la scansione è molto più veloce.

Un'inserzione è considerata un'occasione se:
  - il prezzo di riferimento (mediana calcolata dopo aver rimosso gli
    estremi più alti/bassi — config: trim_pct) è <= alla soglia fissa
    (config: fixed_price_threshold_eur): allora basta che il prezzo
    dell'inserzione sia <= quella soglia fissa
  - altrimenti (riferimento sopra la soglia fissa): il prezzo deve essere
    <= riferimento * (1 - sconto%) (config: discount_below_average_pct)

Legge i token da variabili d'ambiente:
  CARDTRADER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Opzioni:
  --dry-run   non manda niente su Telegram, stampa i messaggi nel log
"""
import html
import json
import os
import statistics
import sys
import time
from datetime import datetime

import requests

from cardtrader_api import CardTraderClient

CONFIG_FILE = "config.json"
INDEX_FILE = "cards_index.json"
INDEX_VERSION = 2
SENT_ALERTS_FILE = "sent_alerts.json"  # per non notificare due volte la stessa occasione

# Scala delle condizioni Cardtrader, dalla migliore alla peggiore.
CONDITION_ORDER = ["Mint", "Near Mint", "Slightly Played", "Moderately Played", "Played", "Poor"]
CONDITION_SHORT = {"Mint": "M", "Near Mint": "NM", "Slightly Played": "SP",
                   "Moderately Played": "MP", "Played": "PL", "Poor": "PO"}
MAX_LISTINGS_PER_MESSAGE = 5


def condition_rank(condition_name):
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


def trimmed_median(values, trim_pct):
    """Rimuove trim_pct dei valori più bassi e più alti, poi calcola la
    mediana dei rimanenti. Se il taglio lascerebbe troppo pochi valori,
    usa la mediana su tutti i valori senza tagliare."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    k = int(n * trim_pct)
    trimmed = sorted_vals[k:n - k] if n - 2 * k >= 1 else sorted_vals
    return statistics.median(trimmed)


def find_prop(product, *needles):
    """Cardtrader nomina le proprietà in modo diverso per ogni gioco
    (es. 'yugioh_rarity'): cerchiamo qualsiasi chiave che contenga la parola."""
    props = product.get("properties_hash", {}) or {}
    for key, value in props.items():
        if any(n in key.lower() for n in needles):
            return value
    return None


def get_rarity(product):
    value = find_prop(product, "rarity")
    return str(value).strip().lower() if value else None


def is_deal(price, ref, criteria):
    # Se la media delle inserzioni è già sotto la soglia fissa, basta
    # rispettare quella (le percentuali diventano poco significative su
    # prezzi già bassi). Se la media è sopra la soglia fissa, conta solo lo
    # sconto percentuale rispetto alla media.
    if ref > criteria["fixed_price_threshold_eur"]:
        return price <= ref * (1 - criteria["discount_below_average_pct"] / 100)
    return price <= criteria["fixed_price_threshold_eur"]


# ---------------------------------------------------------------------------
# Messaggi Telegram
# ---------------------------------------------------------------------------

def eur(value):
    return f"{value:.2f} €".replace(".", ",")


def flag(country_code):
    """'US' -> 🇺🇸"""
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def describe_listing(product):
    """Dettagli dell'inserzione: condizione, edizione, lingua."""
    parts = []
    cond = (product.get("properties_hash") or {}).get("condition")
    if cond:
        parts.append(f"{cond} ({CONDITION_SHORT.get(cond, '?')})")
    first = find_prop(product, "first_edition", "1st")
    if first is True or str(first).lower() in ("true", "1", "yes"):
        parts.append("1ª edizione")
    elif first is False or str(first).lower() in ("false", "0", "no"):
        parts.append("Unlimited")
    lang = find_prop(product, "language")
    if lang:
        parts.append(str(lang).upper())
    return " · ".join(parts)


def price_line(d):
    median = f"mediana {eur(d['ref'])} su {d['n_listings']} inserzioni"
    if d.get("rule") == "soglia":
        if d["discount"] > 0:
            return f"💰 <b>{eur(d['price'])}</b> (−{d['discount']}%) · sotto la soglia di {eur(d['threshold'])} · {median}"
        return f"💰 <b>{eur(d['price'])}</b> · sotto la soglia di {eur(d['threshold'])} · {median}"
    return f"💰 <b>{eur(d['price'])}</b> invece di {eur(d['ref'])} (−{d['discount']}%) · mediana su {d['n_listings']} inserzioni"


def format_message(card_name, deals):
    deals = sorted(deals, key=lambda d: d["price"])
    n = len(deals)
    best = deals[0]
    best_discount = f" (−{best['discount']}%)" if best["discount"] > 0 else ""
    lines = [
        f"🃏 <b>{html.escape(card_name)}</b>",
        f"{n} occasion{'e' if n == 1 else 'i'} · la migliore a <b>{eur(best['price'])}</b>{best_discount}",
    ]
    for d in deals[:MAX_LISTINGS_PER_MESSAGE]:
        edition = " · ".join(html.escape(x) for x in (d["expansion"], d["number"], d["rarity"]) if x)
        seller = html.escape(str(d["seller"]))
        country = d.get("country") or ""
        lines += [
            "",
            price_line(d),
            f"📦 {edition}",
        ]
        if d["details"]:
            lines.append(f"✅ {html.escape(d['details'])}")
        lines.append(f"👤 {seller} {flag(country)} {html.escape(country)}".rstrip())
        lines.append(f"🔗 <a href=\"{html.escape(d['url'], quote=True)}\">Apri su Cardtrader</a>")
    if n > MAX_LISTINGS_PER_MESSAGE:
        lines += ["", f"…e altre {n - MAX_LISTINGS_PER_MESSAGE} inserzioni"]
    return "\n".join(lines)


def send_telegram(bot_token, chat_id, text, dry_run=False):
    """Ritorna True se l'invio è andato a buon fine, False altrimenti
    (es. rate limit di Telegram)."""
    if dry_run:
        print("----- [dry-run] messaggio Telegram -----\n" + text + "\n")
        return True
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
    except requests.RequestException as e:
        print(f"  ! Errore invio Telegram: {e}")
        return False
    if resp.status_code != 200:
        print(f"  ! Errore invio Telegram: {resp.status_code} {resp.text}")
        return False
    return True


# ---------------------------------------------------------------------------
# Scansione
# ---------------------------------------------------------------------------

def check_expansion(exp, products_by_bp, settings, sent_alerts):
    """Ritorna {nome carta: [occasioni]} per una espansione."""
    found = {}
    # Le espansioni nella lista old_expansion_codes (fino ad Abyss Rising
    # incluso) vanno bene con qualsiasi rarità. Qualunque altra espansione
    # (comprese quelle senza codice riconosciuto, per prudenza) richiede una
    # delle rarità in allowed_rarities.
    require_rarity_filter = exp.get("code", "") not in settings["old_expansion_codes"]

    for bp_id, bp in exp["blueprints"].items():
        prices = []
        for p in products_by_bp.get(int(bp_id), []):
            price_info = p.get("price", {}) or {}
            cents = price_info.get("cents")
            if cents is None or price_info.get("currency", "EUR") != "EUR":
                continue
            # Filtra per Paese di spedizione del venditore (es. solo US/CA)
            if settings["countries"]:
                if (p.get("user") or {}).get("country_code") not in settings["countries"]:
                    continue
            # Filtra per condizione minima. Condizioni non riconosciute
            # vengono scartate per prudenza.
            if settings["min_condition_rank"] is not None:
                rank = condition_rank((p.get("properties_hash") or {}).get("condition"))
                if rank is None or rank > settings["min_condition_rank"]:
                    continue
            if require_rarity_filter:
                rarity = get_rarity(p) or (bp.get("rarity") or "").lower() or None
                if rarity is None or rarity not in settings["allowed_rarities"]:
                    continue
            prices.append((cents / 100.0, p))

        if len(prices) < settings["min_listings"]:
            continue
        ref = trimmed_median([x for x, _ in prices], settings["trim_pct"])

        for price, p in prices:
            if not is_deal(price, ref, settings["criteria"]):
                continue
            alert_key = f"{bp_id}:{p.get('id')}"
            if alert_key in sent_alerts:
                continue
            user = p.get("user") or {}
            rarity = find_prop(p, "rarity") or bp.get("rarity")
            threshold = settings["criteria"]["fixed_price_threshold_eur"]
            found.setdefault(bp["name"], []).append({
                "price": price,
                # "soglia" = scattata perché sotto la soglia fissa (carte economiche)
                "rule": "sconto" if ref > threshold else "soglia",
                "threshold": threshold,
                "ref": round(ref, 2),
                "discount": max(0, round((1 - price / ref) * 100)) if ref else 0,
                "n_listings": len(prices),
                "expansion": exp["name"],
                "number": bp.get("number"),
                "rarity": str(rarity) if rarity else None,
                "details": describe_listing(p),
                "seller": user.get("username", "?"),
                "country": user.get("country_code"),
                "alert_key": alert_key,
                "url": f"https://www.cardtrader.com/en/cards/{bp_id}",
            })
    return found


def main():
    dry_run = "--dry-run" in sys.argv
    cardtrader_token = os.environ.get("CARDTRADER_TOKEN")
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    required = [("CARDTRADER_TOKEN", cardtrader_token)]
    if not dry_run:
        required += [("TELEGRAM_BOT_TOKEN", telegram_bot_token), ("TELEGRAM_CHAT_ID", telegram_chat_id)]
    missing_env = [name for name, val in required if not val]
    if missing_env:
        sys.exit(f"Errore: variabili d'ambiente mancanti: {', '.join(missing_env)}")

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit("config.json non trovato!")

    index = load_json(INDEX_FILE, None)
    if not isinstance(index, dict) or index.get("version") != INDEX_VERSION:
        sys.exit("cards_index.json mancante o nel formato vecchio: esegui build_card_index.py")

    min_condition_name = config.get("min_condition", "Moderately Played")
    settings = {
        "criteria": config["deal_criteria"],
        "min_listings": config.get("min_listings_for_average", 3),
        "countries": set(config.get("allowed_seller_countries", [])),
        "old_expansion_codes": {c.strip().lower() for c in config.get("old_expansion_codes", [])},
        "allowed_rarities": {r.strip().lower() for r in config.get("allowed_rarities", [])},
        "trim_pct": config.get("trim_pct", 0.1),
        "min_condition_rank": condition_rank(min_condition_name),
    }
    if settings["min_condition_rank"] is None:
        print(f"Attenzione: min_condition '{min_condition_name}' non riconosciuta, nessun filtro condizione applicato.")

    sent_alerts = set(load_json(SENT_ALERTS_FILE, []))
    client = CardTraderClient(cardtrader_token)
    expansions = index["expansions"]
    n_bp = sum(len(e["blueprints"]) for e in expansions.values())

    start = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scansione di {n_bp} versioni di carte in {len(expansions)} espansioni...")

    # 1) raccogli tutte le occasioni, raggruppate per carta
    deals_by_card = {}
    for i, (exp_id, exp) in enumerate(expansions.items(), 1):
        if i % 50 == 0 or i == len(expansions):
            print(f"  ...controllate {i}/{len(expansions)} espansioni")
        try:
            products = client.get_marketplace_products_by_expansion(int(exp_id), language="en")
        except Exception as e:
            print(f"  ! Errore recuperando le inserzioni di {exp['name']}: {e}")
            continue
        for card_name, deals in check_expansion(exp, products, settings, sent_alerts).items():
            deals_by_card.setdefault(card_name, []).extend(deals)

    # 2) un messaggio per carta, con tutte le sue versioni in offerta
    total_deals = 0
    for card_name, deals in sorted(deals_by_card.items()):
        if send_telegram(telegram_bot_token, telegram_chat_id, format_message(card_name, deals), dry_run):
            for d in deals:
                sent_alerts.add(d["alert_key"])
            total_deals += len(deals)
            print(f"  -> Notificata: {card_name} ({len(deals)} {'inserzione' if len(deals) == 1 else 'inserzioni'})")
            if not dry_run:
                time.sleep(1)  # piccola pausa tra un messaggio e l'altro
        else:
            # Probabilmente un rate limit di Telegram: non segniamo queste
            # occasioni come notificate, verranno ritentate al prossimo giro.
            print("  Telegram ha rifiutato l'invio: le altre occasioni verranno ritentate al prossimo giro.")
            break

    if not dry_run:  # in prova non segniamo nulla come già notificato
        save_json(SENT_ALERTS_FILE, list(sent_alerts))
    print(f"Scansione completata in {round(time.time() - start)}s. Occasioni notificate: {total_deals}.")


if __name__ == "__main__":
    main()
