"""Test senza rete con Cardtrader, Telegram e Format Library finti.
Uso: python tests/test_offline.py"""
import json, os, shutil, sys, tempfile
from datetime import timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build_card_index, monitor, telegram_bot, state  # noqa: E402


def prod(pid, bp, eur, country="US", cond="Near Mint", rarity="Secret Rare", hub=True):
    return {"id": pid, "blueprint_id": bp, "price": {"cents": int(round(eur * 100)), "currency": "EUR"},
            "user": {"username": f"venditore{pid}", "country_code": country, "can_sell_via_hub": hub},
            "properties_hash": {"condition": cond, "yugioh_rarity": rarity, "first_edition": True}}


MARKET = {
    # vecchio set: qualsiasi rarità. Mediana 20 €; 12 € CT Zero = occasione; 8 € NON CT Zero = no
    100: {1: [prod(1, 1, 20), prod(2, 1, 21), prod(3, 1, 19), prod(4, 1, 12), prod(5, 1, 8, hub=False)]},
    # set nuovo: Common esclusa; Sangan mediana ~2,2 €: 1,40 € (−36%, risparmio 0,80) sì; 2,00 € no
    200: {2: [prod(10, 2, 2.0, rarity="Common"), prod(11, 2, 2.5, rarity="Common")],
          3: [prod(20, 3, 2.2), prod(21, 3, 2.5, cond="Played"), prod(22, 3, 2.3), prod(23, 3, 1.4), prod(24, 3, 2.0)]},
}


class FakeCT:
    def __init__(self, token=None): pass
    def get_yugioh_game_id(self): return 4
    def get_expansions(self, gid):
        return [{"id": 100, "game_id": 4, "name": "Phantom Darkness", "code": "ptdn"},
                {"id": 200, "game_id": 4, "name": "Legendary Collection 2", "code": "lcgx"}]
    def get_blueprints_for_expansion(self, eid):
        return {100: [{"id": 1, "name": "Dark Armed Dragon", "fixed_properties": {"collector_number": "PTDN-EN019"},
                       "image": {"url": "/uploads/blueprints/image/1/dad.jpg", "show": {"url": "/uploads/blueprints/image/1/show_dad.jpg"}}}],
                200: [{"id": 2, "name": "Dark Armed Dragon"}, {"id": 3, "name": "Sangan"}]}[eid]
    def get_marketplace_products_by_expansion(self, eid, language="en"):
        return MARKET.get(eid, {})


class FakeTG:
    sent = []
    def __init__(self, token, chat_id, dry_run=False):
        self.chat_id = str(chat_id)
        self.updates = []
    images = []
    def send(self, text, image_url=None):
        FakeTG.sent.append(text)
        FakeTG.images.append(image_url)
        return True
    def get_updates(self, offset):
        return [u for u in self.updates if u["update_id"] >= offset]


tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(ROOT, "config.json"), tmp)
os.chdir(tmp)
cfg = json.load(open("config.json"))
cfg["cards"] = ["Dark Armed Dragon", "Sangan"]
cfg["old_expansion_codes"] = ["ptdn"]
cfg["daily_summary"]["hour"] = 0          # riepilogo subito, per il test
json.dump(cfg, open("config.json", "w"))
build_card_index.CardTraderClient = FakeCT
monitor.CardTraderClient = FakeCT
monitor.Telegram = FakeTG
monitor.time.sleep = lambda s: None
os.environ.update({"CARDTRADER_TOKEN": "x", "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})

sys.argv = ["build_card_index.py"]
build_card_index.main()

# --- 1° giro: vecchio storico in formato lista ------------------------------
json.dump(["3:22"], open("sent_alerts.json", "w"))
sys.argv = ["monitor.py"]
monitor.main()
sent = json.load(open("sent_alerts.json"))
assert set(k for k, v in sent.items() if v.get("n")) == {"1:4", "3:23"}, sent
assert "1:5" not in sent, "inserzione non CT Zero non deve essere notificata"
assert "3:24" not in sent, "2,00 € su 2,20 € non è un'occasione"
msgs = [m for m in FakeTG.sent if m.startswith("🃏")]
assert len(msgs) == 2 and "Dark Armed Dragon" in msgs[0], msgs   # prima lo sconto più alto (−40%)
assert any(m.startswith("📊") for m in FakeTG.sent), "manca il riepilogo"
assert FakeTG.images[0] == "https://www.cardtrader.com/uploads/blueprints/image/1/show_dad.jpg", FakeTG.images
hist = json.load(open("price_history.json"))
assert hist["1:1st"][state.today()] == 1900
print(msgs[0], "\n")

# --- 2° giro: niente doppioni; ribasso di un'inserzione già notificata ------
FakeTG.sent = []
MARKET[100][1][3] = prod(4, 1, 10)          # da 12 € a 10 € (−17%)
monitor.main()
msgs = [m for m in FakeTG.sent if m.startswith("🃏")]
assert len(msgs) == 1 and "Ribassata: prima era 12,00 €" in msgs[0], FakeTG.sent
assert not any(m.startswith("📊") for m in FakeTG.sent), "riepilogo già inviato oggi"

# --- storico: riferimento dalle settimane precedenti ------------------------
series = {(state.now() - timedelta(days=i)).date().isoformat(): 1500 for i in range(1, 10)}
assert monitor.history_reference(series, 7) == 15.0
assert monitor.history_reference(dict(list(series.items())[:3]), 7) is None

# --- comandi Telegram -------------------------------------------------------
bs = state.load_bot_state()
sa = state.load_sent_alerts()
c = json.load(open("config.json"))
r, ch = telegram_bot.handle_command("/sconto 40", c, bs, sa); assert bs["overrides"]["discount_below_average_pct"] == 40
r, ch = telegram_bot.handle_command("/pausa", c, bs, sa); assert bs["paused"]
r, ch = telegram_bot.handle_command("/aggiungi Jinzo", c, bs, sa); assert ch and "Jinzo" in c["cards"]
r, ch = telegram_bot.handle_command("/rimuovi sangan", c, bs, sa); assert ch and "Sangan" not in c["cards"]
assert "Sangan" in c["format_library"]["excluded_cards"]
r, ch = telegram_bot.handle_command("/oggi", c, bs, sa); assert "Dark Armed Dragon" in r, r
r, ch = telegram_bot.handle_command("/stato", c, bs, sa); assert "in pausa" in r and "40%" in r
tg = FakeTG("t", "42")
tg.updates = [{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/pausa"}},     # altra chat: ignorato
              {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/riprendi"}}]
FakeTG.sent = []
telegram_bot.process_commands(tg, c, bs, sa)
assert bs["telegram_offset"] == 7 and not bs["paused"] and len(FakeTG.sent) == 1
print("MONITOR E COMANDI OK")

# --- Format Library finto -----------------------------------------------
import update_card_list  # noqa: E402


class FakeResp:
    def __init__(self, data): self.data, self.status_code = data, 200
    def raise_for_status(self): pass
    def json(self): return self.data


class FakeSession:
    headers = {}
    def get(self, url, params=None, timeout=None):
        if url.endswith("/decks"):
            page = params["page"]
            assert params["filter"] == "format:eq:Edison,origin:eq:event"
            n = 100 if page == 1 else 30
            return FakeResp([{"id": (page - 1) * 100 + i + 1} for i in range(n)])
        deck_id = int(url.rsplit("/", 1)[1])
        main = [{"name": "Dark Armed Dragon"}] * 2 + [{"name": "Sangan"}]
        if deck_id % 2:
            main += [{"name": "Rara"}] * 3
        return FakeResp({"main": main, "extra": [{"name": "Stardust Dragon"}], "side": []})


update_card_list.REQUEST_DELAY = 0
update_card_list.requests.Session = FakeSession
cfg = json.load(open("config.json"))
cfg["format_library"] = {"format": "Edison", "top_n": 3, "max_decks": 400,
                         "extra_cards": ["Carta Mia"], "excluded_cards": ["Sangan"]}
json.dump(cfg, open("config.json", "w"))
update_card_list.main()
cfg = json.load(open("config.json"))
assert cfg["cards"] == ["Dark Armed Dragon", "Stardust Dragon", "Rara", "Carta Mia"], cfg["cards"]
stats = json.load(open("format_library_stats.json"))
assert stats["decks_read"] == 130 and stats["ranking"][0] == {"name": "Dark Armed Dragon", "decks": 130, "copies": 260}
assert not build_card_index.index_is_current(cfg)   # lista cambiata -> l'indice va rifatto
print("FORMAT LIBRARY OK")

# --- riferimento per condizione, quasi occasioni, desideri -----------------
settings = {"discount": 30, "min_saving": 0.5, "ct_zero_only": True, "min_listings": 3,
            "countries": set(), "deal_countries": {"US", "CA"}, "old_expansion_codes": {"ptdn"},
            "allowed_rarities": {"secret rare"}, "trim_pct": 0.1, "min_condition_rank": 3,
            "min_history_days": 7, "renotify_pct": 10, "near_min": 20}
exp = {"name": "Phantom Darkness", "code": "ptdn",
       "blueprints": {"1": {"name": "Dark Armed Dragon", "number": "PTDN-EN019", "rarity": "Secret Rare"}}}
products = {1: [prod(1, 1, 20), prod(2, 1, 21), prod(3, 1, 22),                       # NM ~21
                prod(4, 1, 12, cond="Moderately Played"), prod(5, 1, 13, cond="Moderately Played"),
                prod(6, 1, 12.5, cond="Moderately Played"),                          # MP ~12,5: nessuna occasione
                prod(7, 1, 15.5),                                                    # NM −26%: quasi occasione
                prod(8, 1, 9, cond="Slightly Played")]}                              # SP sola: niente riferimento
stats = {"listings": 0, "ct_zero": 0}
wl = {"dark armed dragon": {"name": "Dark Armed Dragon", "max": 13, "since": "x"}}
found = monitor.check_expansion(exp, products, settings, {}, {}, stats, wl)
assert found == {}, found                       # le MP a 12 € non sono occasioni contro le MP
assert [n["price"] for n in stats["near"]] == [15.5], stats["near"]
wish = stats["wishes"]["Dark Armed Dragon"]
assert sorted(h["price"] for h in wish) == [9, 12, 12.5, 13], wish   # tutte quelle fino a 13 €
products[1].append(prod(9, 1, 13.0))           # NM a 13 € contro mediana NM: −38%
found = monitor.check_expansion(exp, products, settings, {}, {}, {"listings": 0, "ct_zero": 0}, {})
d = found["Dark Armed Dragon"][0]
assert d["alert_key"] == "1:9" and d["ref_condition"] == "NM", d
print(monitor.format_wish_message("Dark Armed Dragon", wish).splitlines()[:2])
summary = monitor.build_daily_summary({"expansions": {}}, {}, {}, {"min_reference_eur": 2, "move_pct": 25, "max_lines": 10},
                                      stats["near"], settings)
assert "Quasi occasioni" in summary and "15,50 €" in summary, summary

# --- comandi lista desideri / scadenza ---------------------------------------
c = json.load(open("config.json")); bs = state.load_bot_state(); wl2 = {}
r, ch = telegram_bot.handle_command("/voglio Dark Armed Dragon 15,50", c, bs, {}, wl2)
assert wl2["dark armed dragon"]["max"] == 15.5 and not ch, r
r, ch = telegram_bot.handle_command("/voglio Nuova Carta 3", c, bs, {}, wl2)
assert ch and "Nuova Carta" in c["cards"]
r, ch = telegram_bot.handle_command("/desideri", c, bs, {}, wl2); assert "15,50 €" in r
r, ch = telegram_bot.handle_command("/nonvoglio nuova carta", c, bs, {}, wl2); assert "nuova carta" not in wl2
r, ch = telegram_bot.handle_command("/scadenza 2026-11-25", c, bs, {}, wl2); assert bs["cron_token_expires"] == "2026-11-25"

# --- salute ------------------------------------------------------------------
bs = {"last_run_at": (state.now() - timedelta(minutes=95)).isoformat(),
      "cron_token_expires": (state.now() + timedelta(days=3)).date().isoformat()}
msgs = monitor.health_messages(bs, {"max_gap_minutes": 60, "token_warning_days": 7})
assert len(msgs) == 2 and "fermo per 1 h 35 min" in msgs[0] and "tra 3 giorni" in msgs[1], msgs
bs = {"last_run_at": (state.now() - timedelta(minutes=16)).isoformat()}
assert monitor.health_messages(bs, {"max_gap_minutes": 60}) == []
print("CONDIZIONI, DESIDERI, SALUTE OK")
