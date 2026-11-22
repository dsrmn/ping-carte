# ping-carte

Bot che cerca occasioni di prezzo su carte **Yu-Gi-Oh!** su **Cardtrader**
(solo venditori **CT Zero** di USA e Canada) e le manda su Telegram.
Gira su GitHub Actions, avviato da cron-job.org ogni 15 minuti.

## Come funziona

1. **Lista carte** – ogni lunedì `update_card_list.py` legge le decklist dei
   tornei su Format Library per ogni formato in `format_library.formats`
   (Edison 200 carte, Goat 100, HAT 100) e mette in `config.json → cards`
   l'unione delle carte più giocate. Classifiche complete in
   `format_library_stats.json`; nei messaggi compare la posizione della carta
   in ogni formato (es. "⭐ n° 12 in Edison · n° 3 in Goat").
2. **Indice** – `build_card_index.py` trova su Cardtrader tutte le versioni
   (set/rarità, con immagine) di quelle carte (`cards_index.json`). Si rifà da
   solo se la lista cambia.
3. **Monitor** – `monitor.py` scarica le inserzioni un'espansione alla volta,
   calcola i prezzi di riferimento e manda prima i messaggi della lista
   desideri, poi un messaggio per carta, dagli sconti più alti (max
   `notifications.max_messages_per_run` messaggi per giro).

## Quando è un'occasione

**Prezzo di riferimento**: mediana delle inserzioni attuali (tolti gli
estremi, `trim_pct`) di tutti i venditori che rispettano i filtri, calcolata
separatamente per:
- **edizione** (1ª edizione / Unlimited);
- **condizione** (Near Mint, Slightly Played, Moderately Played): una MP si
  confronta con le MP. Se di una condizione ci sono meno di
  `min_listings_for_average` inserzioni, le Near Mint usano la mediana di
  tutte le condizioni; le altre vengono saltate.

Dopo `price_history.min_days_for_reference` giorni di storico si usa il più
basso tra la mediana di oggi e quella delle ultime settimane.

**Occasione**: inserzione CT Zero (`ct_zero_only`) di un venditore in
`deal_seller_countries` che costa almeno `discount_below_average_pct`% e
almeno `min_saving_eur` in meno del riferimento. Sconti oltre l'80% sono
segnati come sospetti. Un'inserzione già segnalata torna solo se cala di
almeno `notifications.renotify_if_cheaper_pct`%.

## Lista desideri

`/voglio Nome carta 15` → avviso appena una qualsiasi versione della carta è
in vendita a 15 € o meno (CT Zero, stessi Paesi). Se la carta non era
monitorata viene aggiunta. `/desideri` mostra la lista, `/nonvoglio Nome`
toglie una carta.

## Riepilogo serale

Dopo le `daily_summary.hour` (20:00): occasioni del giorno, carte salite o
scese di almeno `move_pct`% rispetto a 7 giorni prima, e **quasi occasioni**
(sconto tra `near_deal_min_pct`% e lo sconto minimo) ancora in vendita.

## Salute del bot

- Se un giro fallisce arriva un messaggio con il link al log (al massimo uno
  ogni 2 ore).
- Se tra due giri passano più di `health.max_gap_minutes` minuti, al giro
  successivo arriva un avviso.
- Con `/scadenza AAAA-MM-GG` il bot avvisa `health.token_warning_days` giorni
  prima che scada il token di cron-job.org.

## Comandi Telegram

Il bot li legge all'inizio di ogni giro.

| Comando | Cosa fa |
|---|---|
| `/stato` | impostazioni e situazione |
| `/pausa` · `/riprendi` | ferma / riattiva le notifiche |
| `/sconto 35` | sconto minimo in % (`/sconto` da solo = valore di config) |
| `/aggiungi Nome carta` · `/rimuovi Nome carta` | carte monitorate |
| `/voglio Nome carta 15` · `/nonvoglio Nome` · `/desideri` | lista desideri |
| `/oggi` | migliori occasioni notificate oggi |
| `/scadenza 2027-11-20` | scadenza del token di cron-job.org |
| `/aiuto` | elenco comandi |

Accetta comandi solo dalla chat `TELEGRAM_CHAT_ID`.

## Dove stanno i dati

Gli stati che cambiano a ogni giro (`sent_alerts.json`, `price_history.json`,
`bot_state.json`, `wishlist.json`) stanno nel ramo **`stato`**, che viene
sostituito a ogni giro da un solo commit: la storia del repository non cresce.
Il ramo principale contiene solo codice, configurazione e indice carte.

## Workflow (scheda Actions)

- **Monitor Cardtrader prices** – avviato da cron-job.org. Con "Run workflow"
  e `dry_run` attivo stampa i messaggi nel log senza mandarli né salvare nulla.
- **Build card index** – automatico il lunedì; a mano per aggiornare subito.

Secrets necessari: `CARDTRADER_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Test senza rete: `python tests/test_offline.py`
