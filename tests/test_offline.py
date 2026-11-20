"""Test senza rete con un Cardtrader finto. Uso: python tests/test_offline.py"""
import json, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build_card_index, monitor  # noqa: E402


def prod(pid, bp, eur, country="US", cond="Near Mint", rarity="Secret Rare", first=True):
    return {"id": pid, "blueprint_id": bp, "price": {"cents": int(round(eur * 100)), "currency": "EUR"},
            "user": {"username": f"venditore{pid}", "country_code": country},
            "properties_hash": {"condition": cond, "yugioh_rarity": rarity, "first_edition": first, "yugioh_language": "en"}}


class FakeCT:
    def __init__(self, token=None): pass
    def get_yugioh_game_id(self): return 4
    def get_expansions(self, gid):
        return [{"id": 100, "game_id": 4, "name": "Phantom Darkness", "code": "ptdn"},
                {"id": 200, "game_id": 4, "name": "Legendary Collection 2", "code": "lcgx"},
                {"id": 300, "game_id": 4, "name": "Altro", "code": "xxx"}]
    def get_blueprints_for_expansion(self, eid):
        return {100: [{"id": 1, "name": "Dark Armed Dragon", "fixed_properties": {"collector_number": "PTDN-EN019", "yugioh_rarity": "Secret Rare"}}],
                200: [{"id": 2, "name": "Dark Armed Dragon", "fixed_properties": {"collector_number": "LCGX-EN198"}},
                      {"id": 3, "name": "Sangan", "fixed_properties": {"collector_number": "LCGX-EN999"}}],
                300: [{"id": 4, "name": "Non in lista"}]}[eid]
    def get_marketplace_products_by_expansion(self, eid, language="en"):
        if eid == 100:  # vecchio set: qualsiasi rarità; rif 20€, una a 12€ (-40%)
            return {1: [prod(1, 1, 20), prod(2, 1, 21), prod(3, 1, 19), prod(4, 1, 12), prod(5, 1, 8, country="IT")]}
        if eid == 200:  # set nuovo: serve rarità ammessa
            return {2: [prod(10, 2, 2.0, rarity="Common"), prod(11, 2, 2.5, rarity="Common"), prod(12, 2, 3.0, rarity="Common")],
                    3: [prod(20, 3, 2.0), prod(21, 3, 2.5, cond="Played"), prod(22, 3, 2.2)]}
        return {}


tmp = tempfile.mkdtemp()
for f in ("config.json",):
    shutil.copy(os.path.join(ROOT, f), tmp)
os.chdir(tmp)
cfg = json.load(open("config.json"))
cfg["cards"] = ["Dark Armed Dragon", "Sangan"]
cfg["old_expansion_codes"] = ["ptdn"]
json.dump(cfg, open("config.json", "w"))

build_card_index.CardTraderClient = FakeCT
monitor.CardTraderClient = FakeCT
os.environ["CARDTRADER_TOKEN"] = "x"

sys.argv = ["build_card_index.py"]
build_card_index.main()
idx = json.load(open("cards_index.json"))
assert idx["version"] == 2 and set(idx["expansions"]) == {"100", "200"}
assert build_card_index.index_is_current(cfg)

json.dump(["3:22"], open("sent_alerts.json", "w"))
sys.argv = ["monitor.py", "--dry-run"]
monitor.main()
assert json.load(open("sent_alerts.json")) == ["3:22"], "il dry-run non deve toccare lo storico"

settings = {"criteria": cfg["deal_criteria"], "min_listings": 2, "countries": {"US", "CA"},
            "old_expansion_codes": {"ptdn"}, "allowed_rarities": {"secret rare"}, "trim_pct": 0.1,
            "min_condition_rank": 3}
fake = FakeCT()
d1 = monitor.check_expansion(idx["expansions"]["100"], fake.get_marketplace_products_by_expansion(100), settings, set())
assert [d["alert_key"] for d in d1["Dark Armed Dragon"]] == ["1:4"], d1   # quella IT a 8€ esclusa
d2 = monitor.check_expansion(idx["expansions"]["200"], fake.get_marketplace_products_by_expansion(200), settings, {"3:22"})
assert "Dark Armed Dragon" not in d2                                     # Common in set nuovo: escluse
assert [d["alert_key"] for d in d2["Sangan"]] == ["3:20"], d2            # Played esclusa, 3:22 già inviata
print("TUTTO OK")

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
