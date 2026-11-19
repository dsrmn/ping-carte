# ping-carte

Bot che cerca occasioni di prezzo su carte **Yu-Gi-Oh!** su **Cardtrader** e
le manda su Telegram. Gira da solo su GitHub Actions circa una volta all'ora.

## Come funziona

1. **Lista carte** – ogni lunedì `update_card_list.py` legge le decklist dei
   tornei Edison su Format Library e mette in `config.json → cards` le carte
   più giocate (quante: `format_library.top_n`). La classifica completa
   finisce in `format_library_stats.json`.
   - `extra_cards`: carte da monitorare sempre
   - `excluded_cards`: carte da non monitorare mai
2. **Indice** – `build_card_index.py` trova su Cardtrader tutte le versioni
   (set/rarità) di quelle carte e le salva in `cards_index.json`, raggruppate
   per espansione. Si rifà da solo se la lista cambia.
3. **Monitor** – `monitor.py` scarica le inserzioni un'espansione alla volta,
   calcola il prezzo di riferimento di ogni versione e manda un messaggio per
   carta con le occasioni trovate.

## Quando è un'occasione

Prezzo di riferimento = mediana delle inserzioni (tolti gli estremi, `trim_pct`).
- Riferimento ≤ `fixed_price_threshold_eur` → occasione ogni inserzione ≤ soglia.
- Altrimenti → occasione se costa almeno `discount_below_average_pct`% in meno.

Filtri: Paesi del venditore (`allowed_seller_countries`), condizione minima
(`min_condition`), solo inglese; nei set dopo Abyss Rising solo le rarità in
`allowed_rarities` (i set fino ad Abyss Rising sono in `old_expansion_codes`).

## Workflow (scheda Actions)

- **Monitor Cardtrader prices** – automatico. Con "Run workflow" e
  `dry_run` attivo stampa i messaggi nel log senza mandarli.
- **Build card index** – automatico il lunedì; lancialo a mano per
  aggiornare subito lista carte e indice.

Secrets necessari: `CARDTRADER_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Test senza rete: `python tests/test_offline.py`
