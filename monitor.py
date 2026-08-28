name: Monitor Cardtrader prices

# Gira automaticamente ogni 20 minuti, e puoi anche lanciarlo a mano
# dalla scheda "Actions" di GitHub.
on:
  schedule:
    - cron: "*/20 * * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Installa dipendenze
        run: pip install -r requirements.txt

      - name: Esegui scansione prezzi
        env:
          CARDTRADER_TOKEN: ${{ secrets.CARDTRADER_TOKEN }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python monitor.py

      - name: Salva lo storico delle notifiche inviate
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@users.noreply.github.com"
          git add sent_alerts.json
          if ! git diff --staged --quiet; then
            git commit -m "Aggiorna alert inviati"
            for i in 1 2 3 4 5; do
              git pull --rebase origin main && git push && break
              echo "Push fallito (tentativo $i), riprovo tra 5 secondi..."
              sleep 5
            done
          fi
