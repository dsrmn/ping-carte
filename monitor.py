"""
Esegue UNA scansione dei prezzi Cardtrader per la lista di carte Yu-Gi-Oh!
e notifica su Telegram le occasioni trovate. Pensato per essere lanciato
da GitHub Actions ogni 20 minuti circa (vedi .github/workflows/monitor.yml).

PREZZO DI RIFERIMENTO di ogni versione di carta:
  - mediana delle inserzioni attuali (tolti gli estremi, config: trim_pct),
    considerando tutti i venditori che rispettano i filtri;
  - se c'è abbastanza storico (config: price_history.min_days_for_reference
    giorni negli ultimi 30) si usa il PIÙ BASSO tra la mediana attuale e la
    mediana storica: così un rialzo improvviso non crea false occasioni.

OCCASIONE: inserzione di un venditore CT Zero (config: ct_zero_only) che costa
  - almeno discount_below_average_pct% meno del riferimento, e
  - almeno min_saving_eur in meno (evita "occasioni" da pochi centesimi).
Un'inserzione già notificata viene segnalata di nuovo solo se il prezzo
scende ancora di almeno notifications.renotify_if_cheaper_pct%.

Una volta al giorno (dopo daily_summary.hour) arriva un riepilogo con le
carte salite o scese di più nell'ultima settimana.

Variabili d'ambiente: CARDTRADER_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Opzioni: --dry-run  stampa i messaggi nel log senza mandarli né salvare nulla
"""
import html
import os
import statistics
import sys
import time
from datetime import datetime, timedelta

from cardtrader_api import CardTraderClient

from state import (CONFIG_FILE, BOT_STATE_FILE, SENT_ALERTS_FILE, HISTORY_FILE, TZ,
                   now, today, load_json, save_json, load_bot_state, load_sent_alerts,
                   load_wishlist, prune_sent_alerts, effective_discount)
from telegram_bot import Telegram

INDEX_FILE = "cards_index.json"
INDEX_VERSION = 3
STATS_FILE = "format_library_stats.json"

CONDITION_ORDER = ["Mint", "Near Mint", "Slightly Played", "Moderately Played", "Played", "Poor"]
CONDITION_SHORT = {"Mint": "M", "Near Mint": "NM", "Slightly Played": "SP",
                   "Moderately Played": "MP", "Played": "PL", "Poor": "PO"}
MAX_LISTINGS_PER_MESSAGE = 5
SUSPICIOUS_DISCOUNT = 80  # oltre questo sconto il riferimento è probabilmente falsato
# Emoji della condizione nei messaggi
CONDITION_EMOJI = {"Mint": "🟢", "Near Mint": "🟢", "Slightly Played": "💚", "Moderately Played": "🟠"}
# Classi di condizione con riferimento separato: una MP si confronta con le MP
CONDITION_CLASS = {"Mint": "NM", "Near Mint": "NM", "Slightly Played": "SP",
                   "Moderately Played": "MP", "Played": "PL", "Poor": "PO"}


def condition_rank(condition_name):
    try:
        return CONDITION_ORDER.index(condition_name)
    except ValueError:
        return None


def trimmed_median(values, trim_pct):
    """Mediana dopo aver tolto trim_pct dei valori più bassi e più alti.
    Con un numero pari di valori prende il più basso dei due centrali
    (median_low): così un'inserzione a prezzo assurdo non gonfia il
    riferimento quando le inserzioni sono poche."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    k = int(n * trim_pct)
    trimmed = sorted_vals[k:n - k] if n - 2 * k >= 1 else sorted_vals
    return statistics.median_low(trimmed)


def find_prop(product, *needles):
    props = product.get("properties_hash", {}) or {}
    for key, value in props.items():
        if any(n in key.lower() for n in needles):
            return value
    return None


def get_rarity(product):
    value = find_prop(product, "rarity")
    return str(value).strip().lower() if value else None


def is_ct_zero(product):
    """Il venditore spedisce tramite CT Zero (hub di Cardtrader)."""
    user = product.get("user") or {}
    for obj in (user, product):
        for key in ("can_sell_via_hub", "ct_zero", "hub"):
            if obj.get(key) is True:
                return True
    return False


def eur(value):
    return f"{value:.2f} €".replace(".", ",")


def flag(country_code):
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


# ---------------------------------------------------------------------------
# Storico prezzi
# ---------------------------------------------------------------------------

def history_reference(series, min_days):
    """Mediana delle mediane giornaliere degli ultimi 30 giorni (oggi escluso)."""
    limit = (now() - timedelta(days=30)).date().isoformat()
    d = today()
    vals = [c for day, c in series.items() if limit <= day < d]
    if len(vals) < min_days:
        return None
    return statistics.median(vals) / 100


def value_days_ago(series, days):
    """Mediana registrata circa `days` giorni fa (tolleranza ±1 giorno)."""
    for delta in (days, days + 1, days - 1):
        day = (now() - timedelta(days=delta)).date().isoformat()
        if day in series:
            return series[day] / 100
    return None


def prune_history(history, keep_days):
    limit = (now() - timedelta(days=keep_days)).date().isoformat()
    for bp_id in list(history):
        history[bp_id] = {d: c for d, c in history[bp_id].items() if d >= limit}
        if not history[bp_id]:
            del history[bp_id]


# ---------------------------------------------------------------------------
# Scansione
# ---------------------------------------------------------------------------

def edition_key(product):
    fe = find_prop(product, "first_edition", "1st")
    if fe is True or str(fe).lower() in ("true", "1", "yes"):
        return "1st"
    if fe is False or str(fe).lower() in ("false", "0", "no"):
        return "unl"
    return "?"


def deals_for_group(bp_id, bp, exp, edition, eligible, settings, sent_alerts, history, d, near):
    """Occasioni tra le inserzioni di UNA versione e UNA edizione.

    Riferimento per classe di condizione (NM, SP, MP): un'inserzione MP si
    confronta con la mediana delle MP. Se di quella condizione ci sono troppe
    poche inserzioni, si usa la mediana di tutte le condizioni, ma solo per
    le inserzioni Near Mint/Mint (per una MP sarebbe un confronto falsato)."""
    found = []
    if not eligible:
        return found
    base = f"{bp_id}:{edition}" if edition != "?" else bp_id

    def reference(items, key):
        current = trimmed_median([x for x, _, _ in items], settings["trim_pct"])
        series = history.setdefault(key, {})
        if d not in series:  # una registrazione al giorno (la prima del giorno)
            series[d] = int(round(current * 100))
        hist_ref = history_reference(series, settings["min_history_days"])
        ref = min(current, hist_ref) if hist_ref else current
        return ref, bool(hist_ref) and hist_ref < current

    overall = reference(eligible, base) if len(eligible) >= settings["min_listings"] else None
    by_class = {}
    for item in eligible:
        cls = CONDITION_CLASS.get((item[1].get("properties_hash") or {}).get("condition"), "?")
        by_class.setdefault(cls, []).append(item)
    refs = {}
    for cls, items in by_class.items():
        if len(items) >= settings["min_listings"]:
            ref, from_hist = reference(items, f"{base}:{cls}")
            refs[cls] = (ref, from_hist, len(items), cls)
        elif cls == "NM" and overall:
            refs[cls] = (overall[0], overall[1], len(eligible), None)

    for price, p, hub in eligible:
        cls = CONDITION_CLASS.get((p.get("properties_hash") or {}).get("condition"), "?")
        if cls not in refs:
            continue
        ref, from_hist, n_ref, ref_cls = refs[cls]
        if settings["ct_zero_only"] and not hub:
            continue
        if settings["deal_countries"] and (p.get("user") or {}).get("country_code") not in settings["deal_countries"]:
            continue
        if ref - price < settings["min_saving"]:
            continue
        discount = round((1 - price / ref) * 100)
        if discount < settings["discount"]:
            if discount >= settings["near_min"]:
                near.append({"name": bp["name"], "expansion": exp["name"], "rarity": bp.get("rarity"),
                             "price": price, "ref": round(ref, 2), "discount": discount,
                             "condition": CONDITION_SHORT.get((p.get("properties_hash") or {}).get("condition"), "?"),
                             "url": f"https://www.cardtrader.com/en/cards/{bp_id}"})
            continue
        alert_key = f"{bp_id}:{p.get('id')}"
        previous = sent_alerts.get(alert_key)
        cheaper_than = None
        if previous is not None:
            prev_cents = previous.get("p")
            if not prev_cents or price * 100 > prev_cents * (1 - settings["renotify_pct"] / 100):
                continue
            cheaper_than = prev_cents / 100
        found.append(listing_info(p, price, bp_id, bp, exp) | {
            "ref": round(ref, 2),
            "ref_is_history": from_hist,
            "ref_condition": ref_cls,
            "discount": discount,
            "n_listings": n_ref,
            "cheaper_than": cheaper_than,
            "alert_key": alert_key,
        })
    return found


def listing_info(p, price, bp_id, bp, exp):
    user = p.get("user") or {}
    rarity = find_prop(p, "rarity") or bp.get("rarity")
    return {
        "price": price,
        "expansion": exp["name"],
        "number": bp.get("number"),
        "rarity": str(rarity) if rarity else None,
        "condition": (p.get("properties_hash") or {}).get("condition"),
        "first_edition": find_prop(p, "first_edition", "1st"),
        "seller": user.get("username", "?"),
        "country": user.get("country_code"),
        "url": f"https://www.cardtrader.com/en/cards/{bp_id}",
        "image": bp.get("image"),
    }


def wishlist_hits(bp_id, bp, exp, eligible, settings, wishlist, sent_alerts):
    """Inserzioni sotto il prezzo obiettivo della lista desideri."""
    w = wishlist.get(bp["name"].lower())
    if not w:
        return []
    hits = []
    for price, p, hub in eligible:
        if price > w["max"]:
            continue
        if settings["ct_zero_only"] and not hub:
            continue
        if settings["deal_countries"] and (p.get("user") or {}).get("country_code") not in settings["deal_countries"]:
            continue
        key = f"w:{bp_id}:{p.get('id')}"
        previous = sent_alerts.get(key)
        cheaper_than = None
        if previous is not None:
            prev_cents = previous.get("p")
            if not prev_cents or price * 100 > prev_cents * (1 - settings["renotify_pct"] / 100):
                continue
            cheaper_than = prev_cents / 100
        hits.append(listing_info(p, price, bp_id, bp, exp) | {
            "target": w["max"], "cheaper_than": cheaper_than, "alert_key": key, "discount": 0})
    return hits


def check_expansion(exp, products_by_bp, settings, sent_alerts, history, stats, wishlist=None):
    """Ritorna {nome carta: [occasioni]} per una espansione e aggiorna lo storico.
    Le quasi occasioni finiscono in stats["near"], i desideri in stats["wishes"]."""
    found = {}
    stats.setdefault("near", [])
    stats.setdefault("wishes", {})
    require_rarity_filter = exp.get("code", "") not in settings["old_expansion_codes"]
    d = today()

    for bp_id, bp in exp["blueprints"].items():
        eligible = []
        for p in products_by_bp.get(int(bp_id), []):
            price_info = p.get("price", {}) or {}
            cents = price_info.get("cents")
            if cents is None or price_info.get("currency", "EUR") != "EUR":
                continue
            if p.get("on_vacation") or (p.get("user") or {}).get("on_vacation"):
                continue
            if p.get("graded"):  # carte gradate: prezzi a parte
                continue
            if settings["countries"] and (p.get("user") or {}).get("country_code") not in settings["countries"]:
                continue
            if settings["min_condition_rank"] is not None:
                rank = condition_rank((p.get("properties_hash") or {}).get("condition"))
                if rank is None or rank > settings["min_condition_rank"]:
                    continue
            if require_rarity_filter:
                rarity = get_rarity(p) or (bp.get("rarity") or "").lower() or None
                if rarity is None or rarity not in settings["allowed_rarities"]:
                    continue
            hub = is_ct_zero(p)
            stats["listings"] += 1
            stats["ct_zero"] += hub
            eligible.append((cents / 100.0, p, hub))

        # Prima edizione e Unlimited hanno prezzi molto diversi: riferimento separato
        by_edition = {}
        for item in eligible:
            by_edition.setdefault(edition_key(item[1]), []).append(item)
        for edition, items in by_edition.items():
            deals = deals_for_group(bp_id, bp, exp, edition, items, settings, sent_alerts, history, d,
                                    stats["near"])
            if deals:
                found.setdefault(bp["name"], []).extend(deals)
        hits = wishlist_hits(bp_id, bp, exp, eligible, settings, wishlist or {}, sent_alerts)
        if hits:
            stats["wishes"].setdefault(bp["name"], []).extend(hits)
    return found


# ---------------------------------------------------------------------------
# Messaggi
# ---------------------------------------------------------------------------

def details_line(d):
    parts = []
    if d["condition"]:
        parts.append(f"{d['condition']} ({CONDITION_SHORT.get(d['condition'], '?')})")
    fe = d["first_edition"]
    if fe is True or str(fe).lower() in ("true", "1", "yes"):
        parts.append("1ª edizione")
    elif fe is False or str(fe).lower() in ("false", "0", "no"):
        parts.append("Unlimited")
    parts.append("CT Zero")
    return " · ".join(parts)


def format_message(card_name, deals, popularity):
    deals = sorted(deals, key=lambda d: (d["discount"] >= SUSPICIOUS_DISCOUNT, -d["discount"]))
    n = len(deals)
    best = deals[0]
    title = f"🃏 <b>{html.escape(card_name)}</b>"
    if popularity:
        title += f"  ⭐ n° {popularity} in Edison"
    lines = [title,
             f"{n} occasion{'e' if n == 1 else 'i'} · la migliore a <b>{eur(best['price'])}</b> (−{best['discount']}%)"]
    for d in deals[:MAX_LISTINGS_PER_MESSAGE]:
        edition = " · ".join(html.escape(x) for x in (d["expansion"], d["number"], d["rarity"]) if x)
        cond = f" {d['ref_condition']}" if d.get("ref_condition") else ""
        ref_note = (f"media{cond} ultime settimane" if d["ref_is_history"]
                    else f"mediana{cond} su {d['n_listings']} inserzioni")
        lines += ["", f"💰 <b>{eur(d['price'])}</b> invece di {eur(d['ref'])} (−{d['discount']}%) · {ref_note}"]
        if d["discount"] >= SUSPICIOUS_DISCOUNT:
            lines.append("⚠️ Sconto sospetto: forse la mediana è falsata da inserzioni a prezzo esagerato, controlla")
        if d["cheaper_than"]:
            lines.append(f"⬇️ Ribassata: prima era {eur(d['cheaper_than'])}")
        lines.append(f"📦 {edition}")
        lines.append(f"{CONDITION_EMOJI.get(d['condition'], '✅')} {html.escape(details_line(d))}")
        country = d.get("country") or ""
        lines.append(f"👤 {html.escape(str(d['seller']))} {flag(country)} {html.escape(country)}".rstrip())
        lines.append(f"🔗 <a href=\"{html.escape(d['url'], quote=True)}\">Apri su Cardtrader</a>")
    if n > MAX_LISTINGS_PER_MESSAGE:
        lines += ["", f"…e altre {n - MAX_LISTINGS_PER_MESSAGE} inserzioni"]
    return "\n".join(lines)


def format_wish_message(card_name, hits):
    hits = sorted(hits, key=lambda h: h["price"])
    lines = [f"🎯 <b>Desiderio: {html.escape(card_name)}</b>",
             f"In vendita a <b>{eur(hits[0]['price'])}</b> · il tuo obiettivo era {eur(hits[0]['target'])}"]
    for h in hits[:MAX_LISTINGS_PER_MESSAGE]:
        edition = " · ".join(html.escape(x) for x in (h["expansion"], h["number"], h["rarity"]) if x)
        lines += ["", f"💰 <b>{eur(h['price'])}</b>"]
        if h["cheaper_than"]:
            lines.append(f"⬇️ Ribassata: prima era {eur(h['cheaper_than'])}")
        lines.append(f"📦 {edition}")
        lines.append(f"{CONDITION_EMOJI.get(h['condition'], '✅')} {html.escape(details_line(h))}")
        country = h.get("country") or ""
        lines.append(f"👤 {html.escape(str(h['seller']))} {flag(country)} {html.escape(country)}".rstrip())
        lines.append(f"🔗 <a href=\"{html.escape(h['url'], quote=True)}\">Apri su Cardtrader</a>")
    if len(hits) > MAX_LISTINGS_PER_MESSAGE:
        lines += ["", f"…e altre {len(hits) - MAX_LISTINGS_PER_MESSAGE} inserzioni"]
    lines += ["", "Per toglierla: /nonvoglio " + html.escape(card_name)]
    return "\n".join(lines)


def near_deals_section(near, cfg, settings):
    if not near:
        return []
    best = {}
    for n in near:  # una riga per carta/espansione: la migliore
        k = (n["name"], n["expansion"])
        if k not in best or n["discount"] > best[k]["discount"]:
            best[k] = n
    rows = sorted(best.values(), key=lambda n: -n["discount"])[:cfg.get("max_lines", 10)]
    lines = ["", f"👀 <b>Quasi occasioni</b> (sconto {settings['near_min']}–{settings['discount'] - 1}%, ancora in vendita)"]
    for n in rows:
        label = " · ".join(html.escape(x) for x in (n["name"], n["expansion"], n["rarity"], n["condition"]) if x)
        lines.append(f"• <a href=\"{html.escape(n['url'], quote=True)}\">{label}</a>: "
                     f"{eur(n['price'])} invece di {eur(n['ref'])} (−{n['discount']}%)")
    return lines


def build_daily_summary(index, history, sent_alerts, cfg, near=None, settings=None):
    """Carte salite/scese di più rispetto a 7 giorni fa + occasioni del giorno."""
    d = today()
    moves = []
    labels = {}
    for exp in index["expansions"].values():
        for bp_id, bp in exp["blueprints"].items():
            labels[bp_id] = " · ".join(x for x in (bp["name"], exp["name"], bp.get("rarity")) if x)
    ed_label = {"1st": "1ª ed.", "unl": "Unlimited"}
    for key, series in history.items():
        parts = key.split(":")
        if len(parts) > 2:  # serie per condizione: nel riepilogo usiamo quella complessiva
            continue
        bp_id, edition = parts[0], (parts[1] if len(parts) > 1 else "")
        if bp_id not in labels or d not in series:
            continue
        cur = series[d] / 100
        old = value_days_ago(series, 7)
        if old is None or max(cur, old) < cfg["min_reference_eur"]:
            continue
        change = round((cur / old - 1) * 100)
        if abs(change) >= cfg["move_pct"]:
            label = labels[bp_id] + (f" · {ed_label[edition]}" if edition in ed_label else "")
            moves.append((change, label, old, cur))
    n_today = sum(1 for v in sent_alerts.values() if v.get("d") == d and v.get("n"))
    lines = [f"📊 <b>Riepilogo del {now().strftime('%d/%m')}</b>",
             f"Occasioni notificate oggi: {n_today} (dettagli con /oggi)"]
    near_lines = near_deals_section(near or [], cfg, settings) if settings else []
    if not moves:
        has_week = any(value_days_ago(s, 7) is not None for s in history.values())
        if has_week:
            lines.append(f"Nessuna carta salita o scesa di più del {cfg['move_pct']}% in una settimana.")
        else:
            lines.append("Il confronto con la settimana scorsa partirà quando avrò 7 giorni di prezzi.")
        return "\n".join(lines + near_lines)
    ups = sorted([m for m in moves if m[0] > 0], key=lambda m: -m[0])[:cfg["max_lines"]]
    downs = sorted([m for m in moves if m[0] < 0], key=lambda m: m[0])[:cfg["max_lines"]]
    if ups:
        lines += ["", "📈 <b>In salita</b> (rispetto a 7 giorni fa)"]
        lines += [f"• {html.escape(l)}: {eur(o)} → {eur(c)} (+{ch}%)" for ch, l, o, c in ups]
    if downs:
        lines += ["", "📉 <b>In discesa</b>"]
        lines += [f"• {html.escape(l)}: {eur(o)} → {eur(c)} ({ch}%)" for ch, l, o, c in downs]
    return "\n".join(lines + near_lines)


# ---------------------------------------------------------------------------
# Salute del bot
# ---------------------------------------------------------------------------

def health_messages(bot_state, cfg):
    """Avvisi da mandare all'inizio del giro: bot rimasto fermo, token in scadenza."""
    msgs = []
    last = bot_state.get("last_run_at")
    if last:
        last_dt = datetime.fromisoformat(last)
        gap = (now() - last_dt).total_seconds() / 60
        if gap > cfg.get("max_gap_minutes", 60):
            hours = f"{int(gap // 60)} h {int(gap % 60)} min" if gap >= 60 else f"{int(gap)} min"
            msgs.append(f"⚠️ <b>Il bot è rimasto fermo per {hours}</b>, dalle "
                        f"{last_dt.astimezone(TZ).strftime('%H:%M del %d/%m')} a ora. "
                        "Se succede spesso, controlla il job su cron-job.org.")
    exp = bot_state.get("cron_token_expires")
    if exp and bot_state.get("last_token_warning") != today():
        days = (datetime.fromisoformat(exp).date() - now().date()).days
        if days <= cfg.get("token_warning_days", 7):
            when = "è scaduto" if days < 0 else ("scade oggi" if days == 0 else f"scade tra {days} giorni")
            msgs.append(f"🔑 <b>Il token GitHub di cron-job.org {when}</b> ({exp}). Creane uno nuovo su "
                        "github.com/settings/personal-access-tokens, mettilo come password su cron-job.org, "
                        "poi scrivimi /scadenza con la nuova data.")
            bot_state["_token_warning_pending"] = True
    return msgs


def main():
    dry_run = "--dry-run" in sys.argv
    ct_token = os.environ.get("CARDTRADER_TOKEN")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    required = [("CARDTRADER_TOKEN", ct_token)]
    if not dry_run:
        required += [("TELEGRAM_BOT_TOKEN", tg_token), ("TELEGRAM_CHAT_ID", tg_chat)]
    missing_env = [n for n, v in required if not v]
    if missing_env:
        sys.exit(f"Errore: variabili d'ambiente mancanti: {', '.join(missing_env)}")

    config = load_json(CONFIG_FILE, None)
    if config is None:
        sys.exit("config.json non trovato!")
    index = load_json(INDEX_FILE, None)
    if not isinstance(index, dict) or index.get("version") != INDEX_VERSION:
        sys.exit("cards_index.json mancante o nel formato vecchio: esegui build_card_index.py")

    bot_state = load_bot_state()
    sent_alerts = load_sent_alerts()
    wishlist = load_wishlist()
    history = load_json(HISTORY_FILE, {})
    tg = Telegram(tg_token, tg_chat, dry_run)
    notif = config.get("notifications", {})
    ph = config.get("price_history", {})

    settings = {
        "discount": effective_discount(config, bot_state),
        "min_saving": config["deal_criteria"].get("min_saving_eur", 0),
        "ct_zero_only": config.get("ct_zero_only", True),
        "min_listings": config.get("min_listings_for_average", 3),
        "countries": set(config.get("allowed_seller_countries", [])),
        # Paesi dei venditori da SEGNALARE (il riferimento usa comunque tutti i Paesi sopra)
        "deal_countries": set(config.get("deal_seller_countries", [])),
        "old_expansion_codes": {c.strip().lower() for c in config.get("old_expansion_codes", [])},
        "allowed_rarities": {r.strip().lower() for r in config.get("allowed_rarities", [])},
        "trim_pct": config.get("trim_pct", 0.1),
        "min_condition_rank": condition_rank(config.get("min_condition", "Moderately Played")),
        "min_history_days": ph.get("min_days_for_reference", 7),
        "renotify_pct": notif.get("renotify_if_cheaper_pct", 10),
        "near_min": config.get("daily_summary", {}).get("near_deal_min_pct", 20),
    }

    for msg in health_messages(bot_state, config.get("health", {})):
        if tg.send(msg) and bot_state.pop("_token_warning_pending", False):
            bot_state["last_token_warning"] = today()
    bot_state.pop("_token_warning_pending", None)

    popularity = {}
    for i, r in enumerate(load_json(STATS_FILE, {}).get("ranking", []), 1):
        popularity.setdefault(r["name"], i)

    # Carte aggiunte con /aggiungi ma non trovate su Cardtrader
    missing = [m for m in index.get("missing", []) if m not in bot_state.get("reported_missing", [])]
    if missing and not bot_state["paused"]:
        if tg.send("⚠️ Non trovo su Cardtrader queste carte (controlla il nome inglese esatto):\n"
                   + "\n".join(f"• {html.escape(m)}" for m in missing)):
            bot_state.setdefault("reported_missing", []).extend(missing)

    client = CardTraderClient(ct_token)
    expansions = index["expansions"]
    n_bp = sum(len(e["blueprints"]) for e in expansions.values())
    start = time.time()
    print(f"[{now().strftime('%Y-%m-%d %H:%M:%S')}] Scansione di {n_bp} versioni in {len(expansions)} espansioni"
          f" · sconto minimo {settings['discount']}% · solo CT Zero: {settings['ct_zero_only']}"
          f"{' · IN PAUSA' if bot_state['paused'] else ''}")

    stats = {"listings": 0, "ct_zero": 0, "near": [], "wishes": {}}
    sample_printed = False
    deals_by_card = {}
    for i, (exp_id, exp) in enumerate(expansions.items(), 1):
        if i % 50 == 0 or i == len(expansions):
            print(f"  ...controllate {i}/{len(expansions)} espansioni")
        try:
            products = client.get_marketplace_products_by_expansion(int(exp_id), language="en")
        except Exception as e:
            print(f"  ! Errore recuperando le inserzioni di {exp['name']}: {e}")
            continue
        if not sample_printed:
            for lst in products.values():
                if lst:
                    p = lst[0]
                    print(f"  Campi di un'inserzione: {sorted(p)}; venditore: {sorted((p.get('user') or {}))}")
                    sample_printed = True
                    break
        for card, deals in check_expansion(exp, products, settings, sent_alerts, history, stats, wishlist).items():
            deals_by_card.setdefault(card, []).extend(deals)

    print(f"Inserzioni idonee: {stats['listings']}, di cui CT Zero: {stats['ct_zero']}")
    if settings["ct_zero_only"] and stats["listings"] and not stats["ct_zero"]:
        print("ATTENZIONE: nessuna inserzione riconosciuta come CT Zero: controlla is_ct_zero() "
              "con i campi stampati sopra.")

    pending = sum(len(v) for v in deals_by_card.values())
    print(f"Occasioni da notificare: {pending} su {len(deals_by_card)} carte")
    sent_msgs = 0
    total = 0
    max_msgs = notif.get("max_messages_per_run", 30)
    if bot_state["paused"]:
        print("Bot in pausa: nessuna notifica inviata.")
    else:
        # Prima la lista desideri
        for card, hits in sorted(stats["wishes"].items()):
            if sent_msgs >= max_msgs:
                break
            if not tg.send(format_wish_message(card, hits), image_url=hits[0].get("image")):
                break
            for h in hits:
                sent_alerts[h["alert_key"]] = {"p": int(round(h["price"] * 100)), "d": today(), "n": card, "s": 0}
            sent_msgs += 1
            print(f"  -> Desiderio notificato: {card} ({len(hits)})")
            if not dry_run:
                time.sleep(1)

        def priority(kv):
            # prima gli sconti "credibili" più alti, in fondo quelli sospetti
            best = max((x["discount"] for x in kv[1] if x["discount"] < SUSPICIOUS_DISCOUNT), default=None)
            if best is None:
                return (1, 0, popularity.get(kv[0], 9999))
            return (0, -best, popularity.get(kv[0], 9999))
        ordered = sorted(deals_by_card.items(), key=priority)
        for card, deals in ordered:
            if sent_msgs >= max_msgs:
                print(f"  Limite di {max_msgs} messaggi raggiunto: il resto al prossimo giro.")
                break
            best = sorted(deals, key=lambda x: (x["discount"] >= SUSPICIOUS_DISCOUNT, -x["discount"]))[0]
            if not tg.send(format_message(card, deals, popularity.get(card)), image_url=best.get("image")):
                print("  Telegram ha rifiutato l'invio: il resto verrà ritentato al prossimo giro.")
                break
            for x in deals:
                sent_alerts[x["alert_key"]] = {"p": int(round(x["price"] * 100)), "d": today(),
                                               "n": card, "s": x["discount"]}
            sent_msgs += 1
            total += len(deals)
            print(f"  -> Notificata: {card} ({len(deals)})")
            if not dry_run:
                time.sleep(1)

        summary_cfg = config.get("daily_summary", {})
        if (summary_cfg and bot_state.get("last_summary_date") != today()
                and now().hour >= summary_cfg.get("hour", 20)):
            if tg.send(build_daily_summary(index, history, sent_alerts, summary_cfg, stats["near"], settings)):
                bot_state["last_summary_date"] = today()
                print("  -> Inviato il riepilogo giornaliero")

    if not dry_run:
        prune_history(history, ph.get("keep_days", 60))
        save_json(HISTORY_FILE, history, compact=True)
        save_json(SENT_ALERTS_FILE, prune_sent_alerts(sent_alerts, notif.get("forget_after_days", 60)), indent=0)
        bot_state["last_run_at"] = now().isoformat(timespec="seconds")
        bot_state["failures_in_a_row"] = 0
        save_json(BOT_STATE_FILE, bot_state)
    print(f"Scansione completata in {round(time.time() - start)}s. "
          f"Messaggi: {sent_msgs}, occasioni notificate: {total}.")


if __name__ == "__main__":
    main()
