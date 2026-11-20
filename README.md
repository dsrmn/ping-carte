# ping-carte

Bot che cerca occasioni di prezzo su carte **Yu-Gi-Oh!** su **Cardtrader**
(solo venditori **CT Zero**) e le manda su Telegram. Gira da solo su GitHub
Actions ogni 20 minuti circa.

## Come funziona

1. **Lista carte** – ogni lunedì `update_card_list.py` legge le decklist dei
   tornei Edison su Format Library e mette in `config.json → cards` le carte
   più giocate (`format_library.top_n`). Classifica completa in
   `format_library_stats.json`.
2. **Indice** – `build_card_index.py` trova su Cardtrader tutte le versioni
   (set/rarità) di quelle carte (`cards_index.json`). Si rifà da solo se la
   lista cambia.
3. **Monitor** – `monitor.py` scarica le inserzioni un'espansione alla volta,
   calcola il prezzo di riferimento e manda un messaggio per carta, prima
   quelle con lo sconto più alto (max `notifications.max_messages_per_run`
   messaggi per giro, il resto al giro dopo).

## Quando è un'occasione

**Prezzo di riferimento** di ogni versione: mediana delle inserzioni attuali
(tolti gli estremi, `trim_pct`) di tutti i venditori che rispettano i filtri.
Dopo `price_history.min_days_for_reference` giorni di storico si usa il più
basso tra la mediana di oggi e quella delle ultime settimane, così un rialzo
improvviso non crea false occasioni.

**Occasione**: inserzione di un venditore CT Zero (`ct_zero_only`) che costa
- almeno `discount_below_average_pct`% meno del riferimento, **e**
- almeno `min_saving_eur` in meno (niente occasioni da pochi centesimi).

Un'inserzione già segnalata viene risegnalata solo se il prezzo scende di
almeno `notifications.renotify_if_cheaper_pct`%. Le inserzioni segnalate
vengono dimenticate dopo `notifications.forget_after_days` giorni.

Filtri: condizione minima (`min_condition`), solo inglese, Paesi del
venditore (`allowed_seller_countries`, vuoto = tutti); nei set dopo Abyss
Rising solo le rarità in `allowed_rarities`.

## Riepilogo serale

Dopo le `daily_summary.hour` (20:00) arriva un riepilogo con le occasioni del
giorno e le carte salite o scese di almeno `daily_summary.move_pct`% rispetto
a 7 giorni prima (servono 7 giorni di storico, in `price_history.json`).

## Comandi Telegram

Il bot li legge a ogni giro, quindi rispondono entro una ventina di minuti.

| Comando | Cosa fa |
|---|---|
| `/stato` | impostazioni e occasioni di oggi |
| `/pausa` · `/riprendi` | ferma / riattiva le notifiche |
| `/sconto 35` | sconto minimo in % (`/sconto` da solo = valore di config) |
| `/aggiungi Nome carta` | monitora una carta in più (nome inglese esatto) |
| `/rimuovi Nome carta` | smetti di monitorarla (anche dopo l'aggiornamento settimanale) |
| `/oggi` | migliori occasioni notificate oggi |
| `/aiuto` | elenco comandi |

Accetta comandi solo dalla chat `TELEGRAM_CHAT_ID`.

## Workflow (scheda Actions)

- **Monitor Cardtrader prices** – automatico. Con "Run workflow" e `dry_run`
  attivo stampa i messaggi nel log senza mandarli né salvare nulla.
- **Build card index** – automatico il lunedì; a mano per aggiornare subito.

Secrets necessari: `CARDTRADER_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Test senza rete: `python tests/test_offline.py`
