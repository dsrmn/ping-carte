"""
Wrapper minimale per l'API pubblica di Cardtrader (v2).
Documentazione ufficiale: https://api.cardtrader.com/docs

NOTA: gli endpoint qui sotto rispecchiano la documentazione pubblica di
Cardtrader al momento della scrittura. Se l'API restituisce 404/errori
di formato, controlla la documentazione aggiornata su api.cardtrader.com/docs
e aggiusta i path in questo file: è l'unico punto dove sono definiti.
"""
import time
import requests

BASE_URL = "https://api.cardtrader.com/api/v2"


class CardTraderClient:
    def __init__(self, token: str, request_delay: float = 0.6):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        # Piccolo delay tra le richieste per rispettare i rate limit
        self.request_delay = request_delay

    def _get(self, path: str, params: dict | None = None):
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        time.sleep(self.request_delay)
        if resp.status_code == 429:
            # Rate limited: aspetta e riprova una volta
            wait = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limit raggiunto, attendo {wait}s...")
            time.sleep(wait)
            resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_games(self):
        """Ritorna la lista dei giochi supportati (Yu-Gi-Oh!, Magic, Pokemon, ecc.)"""
        return self._get("/games")

    def get_yugioh_game_id(self):
        games = self.get_games()
        for g in games:
            name = g.get("name", "").lower()
            if "yu-gi-oh" in name or "yugioh" in name:
                return g["id"]
        raise RuntimeError(
            "Non trovo il gioco Yu-Gi-Oh! nella risposta di /games. "
            "Controlla manualmente la risposta API per il nome esatto."
        )

    def get_expansions(self, game_id: int):
        """Ritorna tutte le espansioni/set per un gioco"""
        return self._get("/expansions", params={"game_id": game_id})

    def get_blueprints_for_expansion(self, expansion_id: int):
        """Ritorna tutte le 'blueprint' (= versioni astratte di carta) di un set"""
        return self._get("/blueprints/export", params={"expansion_id": expansion_id})

    def get_marketplace_products(self, blueprint_id: int):
        """Ritorna tutte le inserzioni attive in vendita per una blueprint_id"""
        data = self._get("/marketplace/products", params={"blueprint_id": blueprint_id})
        # L'API può restituire un dict raggruppato per blueprint_id o una lista piatta
        # a seconda dell'endpoint/versione: normalizziamo a lista.
        if isinstance(data, dict):
            products = []
            for v in data.values():
                if isinstance(v, list):
                    products.extend(v)
            return products
        return data
