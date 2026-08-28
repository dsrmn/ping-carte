"""
Da eseguire UNA VOLTA (o ogni tanto per catturare nuove ristampe/edizioni).

Scarica tutte le espansioni Yu-Gi-Oh! da Cardtrader, poi tutte le blueprint
(= carte) di ogni espansione, e salva in cards_index.json la corrispondenza
tra i nomi delle carte che ti interessano e i loro blueprint_id su Cardtrader
(una carta può avere più blueprint_id: una per ogni edizione/stampa).

Uso:
    python build_card_index.py
"""
import json
import os
import sys
from cardtrader_api import CardTraderClient

CONFIG_FILE = "config.json"
INDEX_FILE = "cards_index.json"


def normalize(name: str) -> str:
    return name.strip().lower()


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)

    token = os.environ.get("CARDTRADER_TOKEN")
    if not token:
        sys.exit("Errore: variabile d'ambiente CARDTRADER_TOKEN non impostata.")

    client = CardTraderClient(token)
    wanted_names = {normalize(c) for c in config["cards"]}
    found = {name: [] for name in wanted_names}

    print("Recupero l'ID del gioco Yu-Gi-Oh!...")
    game_id = client.get_yugioh_game_id()
    print(f"  -> game_id = {game_id}")

    print("Recupero la lista delle espansioni Yu-Gi-Oh!...")
    expansions = client.get_expansions(game_id)
    print(f"  -> {len(expansions)} espansioni trovate")

    for i, exp in enumerate(expansions, 1):
        exp_id = exp["id"]
        exp_name = exp.get("name", exp_id)
        print(f"[{i}/{len(expansions)}] Analizzo set '{exp_name}'...")
        try:
            blueprints = client.get_blueprints_for_expansion(exp_id)
        except Exception as e:
            print(f"  ! Errore su questo set, salto: {e}")
            continue

        for bp in blueprints:
            bp_name_norm = normalize(bp.get("name", ""))
            if bp_name_norm in wanted_names:
                found[bp_name_norm].append({
                    "blueprint_id": bp["id"],
                    "expansion": exp_name,
                    "card_name": bp.get("name"),
                })

    # Report finale
    missing = [name for name, ids in found.items() if not ids]
    print("\n=== RISULTATO ===")
    print(f"Carte trovate: {len(found) - len(missing)}/{len(found)}")
    if missing:
        print("Carte NON trovate (controlla il nome esatto in inglese):")
        for m in missing:
            print(f"  - {m}")

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(found, f, indent=2, ensure_ascii=False)
    print(f"\nSalvato in {INDEX_FILE}")


if __name__ == "__main__":
    main()
