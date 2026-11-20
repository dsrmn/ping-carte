"""File di stato del bot, salvati nel repository dal workflow a ogni giro.

  bot_state.json      pausa, impostazioni cambiate da Telegram, ultimo riepilogo
  sent_alerts.json    inserzioni già notificate (con prezzo e data)
  price_history.json  una mediana al giorno per ogni versione di carta
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
CONFIG_FILE = "config.json"
BOT_STATE_FILE = "bot_state.json"
SENT_ALERTS_FILE = "sent_alerts.json"
HISTORY_FILE = "price_history.json"


def now():
    return datetime.now(TZ)


def today():
    return now().date().isoformat()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data, indent=2, compact=False):
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        f.write("\n")


def load_bot_state():
    st = load_json(BOT_STATE_FILE, {})
    st.setdefault("paused", False)
    st.setdefault("overrides", {})
    st.setdefault("telegram_offset", 0)
    st.setdefault("last_summary_date", None)
    return st


def effective_discount(config, bot_state):
    return bot_state["overrides"].get(
        "discount_below_average_pct", config["deal_criteria"]["discount_below_average_pct"])


def load_sent_alerts():
    """{chiave: {"p": prezzo_cent, "d": data, "n": carta, "s": sconto%}}.
    Converte il vecchio formato (lista di chiavi)."""
    raw = load_json(SENT_ALERTS_FILE, {})
    if isinstance(raw, list):
        d = today()
        return {k: {"p": None, "d": d} for k in raw}
    return raw


def prune_sent_alerts(sent, days):
    limit = (now() - timedelta(days=days)).date().isoformat()
    return {k: v for k, v in sent.items() if (v.get("d") or "") >= limit}
