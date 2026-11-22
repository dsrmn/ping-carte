"""
Aggiorna la lista "cards" di config.json con le carte più giocate in uno o
più formati, contate sulle decklist dei tornei pubblicate su Format Library
(formatlibrary.com).

Come conta: per ogni carta, in quanti mazzi compare (main, extra o side).
A parità, vince chi ha più copie in totale. La lista finale è l'unione delle
prime top_n carte di ogni formato.

Impostazioni in config.json -> "format_library":
  formats        elenco di formati, es. [{"format": "Edison", "top_n": 200},
                 {"format": "Goat", "top_n": 100}]
  max_decks      quanti mazzi da torneo leggere per formato, dai più recenti
  extra_cards    carte da monitorare sempre, anche se non sono nella top
  excluded_cards carte da non monitorare mai

Salva anche format_library_stats.json con la classifica completa.

Uso:
    python update_card_list.py
"""
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

CONFIG_FILE = "config.json"
STATS_FILE = "format_library_stats.json"
API = "https://formatlibrary.com/api"
PAGE_SIZE = 100          # massimo consentito dall'API
REQUEST_DELAY = 0.5      # per non sovraccaricare il sito
MIN_DECKS_REQUIRED = 20  # sotto questa soglia qualcosa è andato storto: non tocchiamo la lista


def get_json(session, url, params=None):
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=60)
            time.sleep(REQUEST_DELAY)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  ! {url}: {e}")
            time.sleep(3)
    return None


def list_event_decks(session, fmt, max_decks):
    """Mazzi dei tornei del formato, dai più recenti."""
    decks = []
    page = 1
    while len(decks) < max_decks:
        data = get_json(session, f"{API}/decks", params={
            "limit": PAGE_SIZE,
            "page": page,
            "filter": f"format:eq:{fmt},origin:eq:event",
            "sort": "publishDate:desc",
        })
        if not isinstance(data, list) or not data:
            break
        decks.extend(d for d in data if isinstance(d, dict) and d.get("id"))
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return decks[:max_decks]


def card_names(deck_detail):
    """Lista dei nomi (con ripetizioni) di main + extra + side."""
    names = []
    for part in ("main", "extra", "side"):
        for card in deck_detail.get(part) or []:
            if isinstance(card, dict) and card.get("name"):
                names.append(card["name"])
    return names


def rank_cards(details):
    in_decks = Counter()   # in quanti mazzi compare
    copies = Counter()     # copie totali
    for names in details:
        copies.update(names)
        in_decks.update(set(names))
    ranking = sorted(in_decks, key=lambda n: (-in_decks[n], -copies[n], n))
    return [{"name": n, "decks": in_decks[n], "copies": copies[n]} for n in ranking]


def read_format(session, fmt, max_decks):
    """Classifica delle carte di un formato, o None se i mazzi letti sono troppo pochi."""
    print(f"\n=== {fmt} ===")
    print(f"Leggo i mazzi da torneo {fmt} su Format Library (max {max_decks})...")
    decks = list_event_decks(session, fmt, max_decks)
    print(f"  -> {len(decks)} mazzi trovati")
    details = []
    for i, d in enumerate(decks, 1):
        if i % 50 == 0 or i == len(decks):
            print(f"  ...letti {i}/{len(decks)} mazzi")
        detail = get_json(session, f"{API}/decks/{d['id']}")
        if isinstance(detail, dict):
            names = card_names(detail)
            if names:
                details.append(names)
    if len(details) < MIN_DECKS_REQUIRED:
        print(f"  ! Letti solo {len(details)} mazzi validi (minimo {MIN_DECKS_REQUIRED}).")
        return None
    return {"decks_read": len(details), "ranking": rank_cards(details)}


def old_rankings():
    """Classifiche dell'ultimo aggiornamento, per formato (anche dal vecchio formato del file)."""
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if "formats" in old:
        return old["formats"]
    if "format" in old:
        return {old["format"]: {"decks_read": old.get("decks_read"), "ranking": old.get("ranking", [])}}
    return {}


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)
    fl = config.get("format_library", {})
    formats = fl.get("formats") or [{"format": fl.get("format", "Edison"), "top_n": fl.get("top_n", 200)}]
    max_decks = fl.get("max_decks", 400)
    extra = fl.get("extra_cards", [])
    excluded = {c.lower() for c in fl.get("excluded_cards", [])}

    session = requests.Session()
    session.headers.update({"User-Agent": "ping-carte (monitor prezzi personale)", "Accept": "application/json"})

    previous = old_rankings()
    stats = {}
    for f in formats:
        name, top_n = f["format"], f.get("top_n", 100)
        result = read_format(session, name, max_decks)
        if result is None:
            if name in previous:
                print(f"  Uso la classifica {name} dell'aggiornamento precedente.")
                result = previous[name]
            else:
                print(f"  Salto {name}.")
                continue
        result["top_n"] = top_n
        stats[name] = result

    if not stats:
        sys.exit("Nessun formato letto: lascio invariata la lista carte.")

    top = []
    for name, st in stats.items():
        kept = [r["name"] for r in st["ranking"] if r["name"].lower() not in excluded][:st["top_n"]]
        new_here = [n for n in kept if n not in top]
        print(f"{name}: prime {len(kept)} carte, {len(new_here)} non già presenti nei formati precedenti")
        top.extend(new_here)
    for name in extra:
        if name not in top:
            top.append(name)

    old = set(config.get("cards", []))
    added, removed = sorted(set(top) - old), sorted(old - set(top))
    print(f"\nLista aggiornata: {len(top)} carte ({len(added)} nuove, {len(removed)} tolte)")
    for n in added:
        print(f"  + {n}")
    for n in removed:
        print(f"  - {n}")

    config["cards"] = top
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "formats": stats,
        }, f, indent=1, ensure_ascii=False)
    print(f"Classifiche complete salvate in {STATS_FILE}")


if __name__ == "__main__":
    main()
