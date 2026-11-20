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
        return {100: [{"id": 1, "name": "Dark Armed Dragon", "fixed_properties": {"collector_number": "PTDN-EN019"}}],
                200: [{"id": 2, "name": "Dark Armed Dragon"}, {"id": 3, "name": "Sangan"}]}[eid]
    def get_marketplace_products_by_expansion(self, eid, language="en"):
        return MARKET.get(eid, {})


class FakeTG:
    sent = []
    def __init__(self, token, chat_id, dry_run=False):
        self.chat_id = str(chat_id)
        self.updates = []
    def send(self, text):
        FakeTG.sent.append(text)
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
