"""
Invio dei messaggi e comandi Telegram.

Il bot non è sempre acceso (gira su GitHub Actions ogni 20 minuti), quindi
i comandi vengono letti all'inizio di ogni giro: la risposta può arrivare
dopo qualche minuto. Vengono accettati solo i messaggi dalla chat
TELEGRAM_CHAT_ID.

Comandi:
  /stato               impostazioni attuali e ultime occasioni
  /pausa  /riprendi    ferma o riavvia le notifiche
  /sconto 35           sconto minimo in % (vuoto = torna al valore di config)
  /aggiungi Nome carta aggiunge una carta da monitorare (nome inglese esatto)
  /rimuovi Nome carta  smette di monitorare una carta
  /oggi                le migliori occasioni notificate oggi
  /voglio Nome 15      avvisa quando la carta scende sotto 15 € (lista desideri)
  /nonvoglio Nome      toglie la carta dalla lista desideri
  /desideri            mostra la lista desideri
  /scadenza 2027-11-20 data di scadenza del token di cron-job.org (avviso 7 giorni prima)
  /aiuto               questo elenco

Uso dal workflow:  python telegram_bot.py   (elabora i comandi in attesa)
"""
import html
import os
import sys

import requests

import re
from datetime import date

from state import (CONFIG_FILE, BOT_STATE_FILE, WISHLIST_FILE, load_json, save_json, load_bot_state,
                   load_sent_alerts, load_wishlist, effective_discount, today)

API = "https://api.telegram.org/bot{token}/{method}"

HELP = (
    "<b>Comandi disponibili</b>\n"
    "/stato — impostazioni e situazione\n"
    "/pausa — ferma le notifiche\n"
    "/riprendi — riattiva le notifiche\n"
    "/sconto 35 — sconto minimo in % (/sconto da solo = valore predefinito)\n"
    "/aggiungi Nome carta — monitora una carta in più\n"
    "/rimuovi Nome carta — smetti di monitorare una carta\n"
    "/oggi — migliori occasioni di oggi\n"
    "/voglio Nome carta 15 — avvisami quando scende sotto 15 €\n"
    "/nonvoglio Nome carta — toglila dalla lista desideri\n"
    "/desideri — la lista desideri\n"
    "/scadenza 2027-11-20 — scadenza del token di cron-job.org\n"
    "/aiuto — questo elenco\n\n"
    "Il bot legge i comandi a ogni giro (ogni 20 minuti circa)."
)


class Telegram:
    def __init__(self, token, chat_id, dry_run=False):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.dry_run = dry_run

    def _call(self, method, **payload):
        resp = requests.post(API.format(token=self.token, method=method), json=payload, timeout=30)
        return resp

    def send(self, text, image_url=None):
        """True se inviato (o in dry-run). Con image_url Telegram mostra una
        piccola anteprima dell'immagine accanto al testo."""
        if self.dry_run:
            print("----- [dry-run] messaggio Telegram -----\n" + text
                  + (f"\n[immagine: {image_url}]" if image_url else "") + "\n")
            return True
        if image_url:
            preview = {"url": image_url, "prefer_small_media": True, "show_above_text": False}
        else:
            preview = {"is_disabled": True}
        try:
            resp = self._call("sendMessage", chat_id=self.chat_id, text=text,
                              parse_mode="HTML", link_preview_options=preview)
        except requests.RequestException as e:
            print(f"  ! Errore invio Telegram: {e}")
            return False
        if resp.status_code == 400 and image_url:
            # anteprima rifiutata (es. immagine non raggiungibile): riprova senza
            print(f"  ! Anteprima immagine rifiutata, invio senza: {resp.text[:120]}")
            return self.send(text)
        if resp.status_code != 200:
            print(f"  ! Errore invio Telegram: {resp.status_code} {resp.text}")
            return False
        return True

    def get_updates(self, offset):
        resp = self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
        resp.raise_for_status()
        return resp.json().get("result", [])


# ---------------------------------------------------------------------------

def _find_card(name, cards):
    low = name.strip().lower()
    for c in cards:
        if c.lower() == low:
            return c
    return None


def _eur(v):
    return f"{v:.2f} €".replace(".", ",")


def _add_card(name, config):
    """Aggiunge la carta alla lista monitorata (se manca). True se aggiunta."""
    fl = config["format_library"]
    if _find_card(name, config["cards"]):
        return False
    config["cards"].append(name)
    if not _find_card(name, fl["extra_cards"]):
        fl["extra_cards"].append(name)
    fl["excluded_cards"] = [c for c in fl["excluded_cards"] if c.lower() != name.lower()]
    return True


def handle_command(text, config, bot_state, sent_alerts, wishlist=None):
    """Applica un comando e ritorna (risposta, config_cambiata)."""
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    fl = config.setdefault("format_library", {})
    fl.setdefault("extra_cards", [])
    fl.setdefault("excluded_cards", [])

    if cmd in ("/start", "/aiuto", "/help"):
        return HELP, False

    if cmd == "/pausa":
        bot_state["paused"] = True
        return "⏸ Notifiche in pausa. Scrivi /riprendi per riattivarle.", False

    if cmd == "/riprendi":
        bot_state["paused"] = False
        return "▶️ Notifiche riattivate.", False

    if cmd == "/sconto":
        if not arg:
            bot_state["overrides"].pop("discount_below_average_pct", None)
            return f"Sconto minimo tornato al valore predefinito: {effective_discount(config, bot_state)}%.", False
        try:
            value = int(arg.rstrip("%"))
            assert 5 <= value <= 90
        except (ValueError, AssertionError):
            return "Scrivi un numero tra 5 e 90, es. /sconto 35", False
        bot_state["overrides"]["discount_below_average_pct"] = value
        return f"✅ Sconto minimo impostato al {value}%.", False

    if wishlist is None:
        wishlist = {}

    if cmd == "/voglio":
        m = re.match(r"^(.*\S)\s+(\d+(?:[.,]\d{1,2})?)\s*€?$", arg)
        if not m:
            return "Scrivi nome inglese e prezzo massimo, es. /voglio Dark Armed Dragon 15", False
        name, price = m.group(1).strip(), float(m.group(2).replace(",", "."))
        name = _find_card(name, config["cards"]) or name
        added = _add_card(name, config)
        wishlist[name.lower()] = {"name": name, "max": price, "since": today()}
        extra = " L'ho aggiunta anche alle carte monitorate: la cerco su Cardtrader al prossimo giro." if added else ""
        return (f"🎯 Ti avviso quando <b>{html.escape(name)}</b> è in vendita a {_eur(price)} o meno "
                f"(CT Zero, qualsiasi versione).{extra}"), added

    if cmd == "/nonvoglio":
        item = wishlist.pop(arg.strip().lower(), None) if arg else None
        if not item:
            return f"«{html.escape(arg)}» non è nella lista desideri. Vedi /desideri", False
        return f"Tolta <b>{html.escape(item['name'])}</b> dalla lista desideri.", False

    if cmd == "/desideri":
        if not wishlist:
            return "La lista desideri è vuota. Aggiungi con /voglio Nome carta 15", False
        lines = ["🎯 <b>Lista desideri</b>"]
        for w in sorted(wishlist.values(), key=lambda w: w["name"]):
            lines.append(f"• {html.escape(w['name'])} — fino a {_eur(w['max'])}")
        return "\n".join(lines), False

    if cmd == "/scadenza":
        try:
            d = date.fromisoformat(arg.strip())
        except ValueError:
            return "Scrivi la data come AAAA-MM-GG, es. /scadenza 2027-11-20", False
        bot_state["cron_token_expires"] = d.isoformat()
        return f"✅ Ti avviso 7 giorni prima del {d.strftime('%d/%m/%Y')}, quando scade il token.", False

    if cmd == "/aggiungi":
        if not arg:
            return "Scrivi il nome inglese della carta, es. /aggiungi Dark Armed Dragon", False
        if not _add_card(arg, config):
            return f"{html.escape(arg)} è già monitorata.", False
        return (f"✅ Aggiunta <b>{html.escape(arg)}</b>. La cerco su Cardtrader al prossimo giro: "
                "se il nome non è esatto te lo dico."), True

    if cmd == "/rimuovi":
        found = _find_card(arg, config["cards"]) if arg else None
        if not found:
            return f"Non trovo «{html.escape(arg)}» tra le carte monitorate.", False
        config["cards"] = [c for c in config["cards"] if c != found]
        fl["extra_cards"] = [c for c in fl["extra_cards"] if c.lower() != found.lower()]
        if not _find_card(found, fl["excluded_cards"]):
            fl["excluded_cards"].append(found)
        return f"🗑 Non monitoro più <b>{html.escape(found)}</b> (anche dopo l'aggiornamento settimanale).", True

    if cmd == "/stato":
        d = today()
        n_today = sum(1 for v in sent_alerts.values() if v.get("d") == d and v.get("n"))
        state = "⏸ in pausa" if bot_state["paused"] else "▶️ attivo"
        return (f"<b>Stato bot Yu-Gi-Oh!</b>\n"
                f"Notifiche: {state}\n"
                f"Carte monitorate: {len(config['cards'])}\n"
                f"Sconto minimo: {effective_discount(config, bot_state)}%\n"
                f"Solo CT Zero: {'sì' if config.get('ct_zero_only', True) else 'no'}\n"
                f"Occasioni notificate oggi: {n_today}\n"
                f"Lista desideri: {len(wishlist)} carte\n"
                f"Scadenza token cron-job.org: {bot_state.get('cron_token_expires') or 'non impostata (/scadenza)'}"), False

    if cmd == "/oggi":
        d = today()
        rows = [v for v in sent_alerts.values() if v.get("d") == d and v.get("n")]
        if not rows:
            return "Oggi nessuna occasione notificata, per ora.", False
        rows.sort(key=lambda v: -(v.get("s") or 0))
        lines = [f"<b>Migliori occasioni di oggi</b> ({len(rows)} in totale)"]
        for v in rows[:10]:
            price = f"{v['p'] / 100:.2f} €".replace(".", ",") if v.get("p") else "?"
            lines.append(f"• {html.escape(v['n'])} — {price} (−{v.get('s', '?')}%)")
        return "\n".join(lines), False

    return "Comando non riconosciuto. Scrivi /aiuto per l'elenco.", False


def process_commands(tg, config, bot_state, sent_alerts, wishlist=None):
    """Legge i messaggi arrivati dall'ultimo giro. Ritorna True se config
    è cambiato (carte aggiunte/rimosse)."""
    try:
        updates = tg.get_updates(bot_state["telegram_offset"])
    except Exception as e:
        print(f"  ! Non riesco a leggere i comandi Telegram: {e}")
        return False
    changed = False
    for u in updates:
        bot_state["telegram_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        if str((msg.get("chat") or {}).get("id")) != tg.chat_id:
            continue  # messaggi da altre chat: ignorati
        if not text.startswith("/"):
            continue
        print(f"Comando ricevuto: {text}")
        reply, cfg_changed = handle_command(text, config, bot_state, sent_alerts, wishlist)
        changed = changed or cfg_changed
        tg.send(reply)
    return changed


def main():
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID non impostati")
    config = load_json(CONFIG_FILE, None)
    bot_state = load_bot_state()
    sent_alerts = load_sent_alerts()
    wishlist = load_wishlist()
    tg = Telegram(token, chat_id)
    if process_commands(tg, config, bot_state, sent_alerts, wishlist):
        save_json(CONFIG_FILE, config)
    save_json(BOT_STATE_FILE, bot_state)
    save_json(WISHLIST_FILE, wishlist)


if __name__ == "__main__":
    main()
