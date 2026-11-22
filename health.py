"""
Avvisa su Telegram quando un giro del monitor fallisce.
Lanciato dal workflow solo in caso di errore (if: failure()).
Per non riempire la chat, al massimo un avviso ogni 2 ore.
"""
import os
from datetime import datetime, timedelta

from state import BOT_STATE_FILE, TZ, now, load_bot_state, save_json
from telegram_bot import Telegram

MIN_INTERVAL = timedelta(hours=2)


def main():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    bot_state = load_bot_state()
    bot_state["failures_in_a_row"] = bot_state.get("failures_in_a_row", 0) + 1
    last = bot_state.get("last_failure_alert")
    if last and now() - datetime.fromisoformat(last) < MIN_INTERVAL:
        print("Avviso di errore già mandato da poco: salto.")
        save_json(BOT_STATE_FILE, bot_state)
        return
    url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
           f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    n = bot_state["failures_in_a_row"]
    text = (f"❌ <b>Un giro del bot è fallito</b> alle {now().astimezone(TZ).strftime('%H:%M')}"
            + (f" ({n} di fila)" if n > 1 else "") +
            f".\n<a href=\"{url}\">Apri il log su GitHub</a>")
    if Telegram(token, chat).send(text):
        bot_state["last_failure_alert"] = now().isoformat(timespec="seconds")
    save_json(BOT_STATE_FILE, bot_state)


if __name__ == "__main__":
    main()
