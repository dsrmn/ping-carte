# Cardtrader Deal Monitor — Yu-Gi-Oh!

Monitora la tua lista di carte Yu-Gi-Oh! su Cardtrader e ti avvisa su
Telegram quando trova un'occasione. Gira automaticamente su GitHub Actions,
**senza bisogno di lasciare il computer acceso**.

## Passo 1 — Crea un account GitHub

1. Vai su https://github.com/signup
2. Segui la procedura (email, password, username)

## Passo 2 — Crea un nuovo repository

1. Una volta loggato, clicca sul "+" in alto a destra → "New repository"
2. Dai un nome (es. `cardtrader-monitor`)
3. Scegli **Private** (importante: contiene la logica del tuo monitoraggio)
4. Clicca "Create repository"

## Passo 3 — Carica i file

Nella pagina del repository appena creato, clicca su "uploading an existing file"
(o "Add file" → "Upload files") e trascina dentro TUTTI i file e cartelle che
ti ho preparato:

- `cardtrader_api.py`
- `build_card_index.py`
- `monitor.py`
- `requirements.txt`
- `config.json`
- la cartella `.github` (con dentro `workflows/build-index.yml` e `workflows/monitor.yml`)

⚠️ Nota: **non** caricare token o password nei file — li abbiamo già tolti da
`config.json`, li inseriremo nel passo successivo in un posto sicuro.

Poi clicca "Commit changes" in basso per salvare.

## Passo 4 — Inserisci i tuoi token come "Secrets"

1. Nel repository, vai su **Settings** (in alto) → **Secrets and variables**
   → **Actions**
2. Clicca "New repository secret" e crea questi 3 secret (uno alla volta):
   - Nome: `CARDTRADER_TOKEN` — Valore: il tuo token Cardtrader
   - Nome: `TELEGRAM_BOT_TOKEN` — Valore: il token del tuo bot Telegram
   - Nome: `TELEGRAM_CHAT_ID` — Valore: `5865854346`

## Passo 5 — Costruisci l'indice delle carte (una volta sola)

1. Vai sulla scheda **Actions** del repository
2. Se richiesto, clicca "I understand my workflows, go ahead and enable them"
3. Nella lista a sinistra clicca **"Build card index"**
4. Clicca il pulsante **"Run workflow"** (a destra) → "Run workflow"
5. Aspetta qualche minuto che finisca (icona verde = successo). Se clicchi
   sull'esecuzione puoi vedere il log e controllare quali carte sono state
   trovate

## Passo 6 — Il monitoraggio parte da solo

Il workflow **"Monitor Cardtrader prices"** è già impostato per girare
automaticamente ogni 20 minuti. Non devi fare nient'altro: riceverai un
messaggio Telegram ogni volta che trova un'occasione.

Puoi anche lanciarlo manualmente in qualsiasi momento dalla scheda Actions,
allo stesso modo del passo 5, scegliendo "Monitor Cardtrader prices".

## Modificare le impostazioni in futuro

Per cambiare la soglia di prezzo, lo sconto minimo, o la lista di carte:
1. Apri `config.json` nel repository GitHub (clicca sul file, poi la matita
   per modificarlo — si può fare anche da telefono, dal browser)
2. Salva le modifiche ("Commit changes")
3. Se hai aggiunto nuove carte, rilancia manualmente il workflow
   "Build card index" (passo 5) per trovarle

## Note importanti

- Il repository deve restare **Private** dato che contiene la logica delle
  tue occasioni (i token restano comunque sempre al sicuro nei Secrets,
  mai visibili nel codice)
- Ogni occasione viene notificata una sola volta (tracciata in
  `sent_alerts.json`, aggiornato automaticamente dal workflow)
- Se un giorno vuoi resettare le notifiche già inviate, elimina il contenuto
  di `sent_alerts.json` dal repository (lascialo come `[]`)
- Se qualcosa non funziona, controlla il log dell'esecuzione nella scheda
  Actions: ti dice esattamente dove si è fermato. Gli endpoint dell'API
  Cardtrader usati sono documentati in `cardtrader_api.py` — se l'API
  restituisce errori inattesi, controlla la documentazione aggiornata su
  https://api.cardtrader.com/docs
