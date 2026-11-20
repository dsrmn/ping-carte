"""
Costruisce cards_index.json: per ogni carta della lista in config.json,
tutte le sue versioni (blueprint) su Cardtrader, raggruppate per espansione.

Il raggruppamento per espansione serve a monitor.py, che così scarica le
inserzioni di un'intera espansione con UNA richiesta invece di una per carta.

Viene lanciato:
  - a mano dal workflow "Build card index";
  - da solo ogni lunedì (per le ristampe nuove);
  - dal monitor, in automatico, quando cambi la lista carte in config.json.

Uso:
    python build_card_index.py               # ricostruisce sempre
    python build_card_index.py --if-outdated # solo se la lista carte è cambiata
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from cardtrader_api import CardTraderClient

CONFIG_FILE = "config.json"
INDEX_FILE = "cards_index.json"
INDEX_VERSION = 2


def normalize(name: str) -> str:
    return " ".join(str(name).split()).lower()


def find_prop(bp, *needles):
    """Cerca nelle proprietà della blueprint una chiave che contenga una
    delle parole date (Cardtrader usa nomi come 'yugioh_rarity')."""
    for container in (bp.get("fixed_properties"), bp.get("properties_hash"), bp):
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            if any(n in key.lower() for n in needles) and value not in (None, "") \
                    and not isinstance(value, (dict, list)):
                return str(value).strip()
    return None


def cards_fingerprint(config) -> str:
    names = sorted(normalize(c) for c in config["cards"])
    return hashlib.sha1(json.dumps(names).encode()).hexdigest()[:12]


def index_is_current(config) -> bool:
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            idx = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return (isinstance(idx, dict) and idx.get("version") == INDEX_VERSION
            and idx.get("fingerprint") == cards_fingerprint(config))


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)

    if "--if-outdated" in sys.argv and index_is_current(config):
        print("Indice carte già aggiornato, niente da fare.")
        return

    token = os.environ.get("CARDTRADER_TOKEN")
    if not token:
        sys.exit("Errore: variabile d'ambiente CARDTRADER_TOKEN non impostata.")

    client = CardTraderClient(token)
    wanted_names = {normalize(c) for c in config["cards"]}
    found_names = set()

    print("Recupero l'ID del gioco Yu-Gi-Oh!...")
    game_id = client.get_yugioh_game_id()
    print(f"  -> game_id = {game_id}")

    print("Recupero la lista delle espansioni Yu-Gi-Oh!...")
    expansions = client.get_expansions(game_id)
    print(f"  -> {len(expansions)} espansioni trovate")

    out = {}
    total = 0
    for i, exp in enumerate(expansions, 1):
        exp_id = exp["id"]
        exp_name = exp.get("name", str(exp_id))
        if i % 50 == 0 or i == len(expansions):
            print(f"[{i}/{len(expansions)}] ...")
        try:
            blueprints = client.get_blueprints_for_expansion(exp_id)
        except Exception as e:
            print(f"  ! Errore sul set '{exp_name}', salto: {e}")
            continue

        kept = {}
        for bp in blueprints:
            if not isinstance(bp, dict) or "id" not in bp:
                continue
            name_norm = normalize(bp.get("name", ""))
            if name_norm not in wanted_names:
                continue
            found_names.add(name_norm)
            kept[str(bp["id"])] = {
                "name": bp.get("name"),
                "number": find_prop(bp, "collector_number", "card_number"),
                "rarity": find_prop(bp, "rarity"),
            }
        if kept:
            out[str(exp_id)] = {
                "name": exp_name,
                "code": (exp.get("code") or "").lower(),
                "blueprints": kept,
            }
            total += len(kept)

    missing = sorted(wanted_names - found_names)
    print("\n=== RISULTATO ===")
    print(f"Carte trovate: {len(wanted_names) - len(missing)}/{len(wanted_names)}")
    print(f"Versioni (blueprint): {total} in {len(out)} espansioni")
    if missing:
        print("Carte NON trovate (controlla il nome esatto in inglese):")
        for m in missing:
            print(f"  - {m}")

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "version": INDEX_VERSION,
            "fingerprint": cards_fingerprint(config),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "missing": missing,
            "expansions": out,
        }, f, indent=1, ensure_ascii=False)
    print(f"\nSalvato in {INDEX_FILE}")


if __name__ == "__main__":
    main()
