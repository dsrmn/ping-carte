name: Build card index

# Si lancia manualmente dalla scheda "Actions" di GitHub (pulsante "Run workflow").
# Va eseguito una volta all'inizio, e di nuovo solo se aggiungi carte nuove
# a config.json.
on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build-index:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Installa dipendenze
        run: pip install -r requirements.txt

      - name: Costruisci indice carte
        env:
          CARDTRADER_TOKEN: ${{ secrets.CARDTRADER_TOKEN }}
        run: python build_card_index.py

      - name: Salva cards_index.json nel repository
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@users.noreply.github.com"
          git add cards_index.json
          if ! git diff --staged --quiet; then
            git commit -m "Aggiorna indice carte"
            for i in 1 2 3 4 5; do
              git pull --rebase origin main && git push && break
              echo "Push fallito (tentativo $i), riprovo tra 5 secondi..."
              sleep 5
            done
          fi
