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

# Parole chiave per riconoscere i giochi nella risposta di /games.
GAME_KEYWORDS = {
    "yugioh": ["yu-gi-oh", "yugioh"],
    "pokemon": ["pokemon", "pokémon"],
    "onepiece": ["one piece", "onepiece", "one-piece"],
}


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
        resp = self.session.get(url, params=params, timeout=60)
        time.sleep(self.request_delay)
        if resp.status_code == 429:
            # Rate limited: aspetta e riprova una volta
            wait = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limit raggiunto, attendo {wait}s...")
            time.sleep(wait)
            resp = self.session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _as_list(data):
        """Normalizza una risposta che può essere lista o dict a lista di dict."""
        if isinstance(data, dict):
            for key in ("array", "expansions", "blueprints", "games", "data"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return list(data.values())
        return data

    def get_games(self):
        """Ritorna la lista dei giochi supportati (Yu-Gi-Oh!, Magic, Pokemon, ecc.)"""
        return self._as_list(self._get("/games"))

    def get_game_id(self, game_key: str):
        """game_key: 'yugioh', 'pokemon' o 'onepiece'."""
        keywords = GAME_KEYWORDS[game_key]
        for g in self.get_games():
            if not isinstance(g, dict):
                continue
            text = f"{g.get('name', '')} {g.get('display_name', '')}".lower()
            if any(k in text for k in keywords):
                return g["id"]
        raise RuntimeError(
            f"Non trovo il gioco '{game_key}' nella risposta di /games. "
            "Controlla manualmente la risposta API per il nome esatto."
        )

    def get_yugioh_game_id(self):
        return self.get_game_id("yugioh")

    def get_expansions(self, game_id: int):
        """Ritorna tutte le espansioni/set per un gioco.
        Alcune versioni dell'API ignorano il filtro game_id: filtriamo anche qui."""
        exps = self._as_list(self._get("/expansions", params={"game_id": game_id}))
        return [e for e in exps if isinstance(e, dict) and e.get("game_id", game_id) == game_id]

    def get_blueprints_for_expansion(self, expansion_id: int):
        """Ritorna tutte le 'blueprint' (= versioni astratte di carta) di un set"""
        return self._as_list(self._get("/blueprints/export", params={"expansion_id": expansion_id}))

    @staticmethod
    def _flatten_products(data):
        """L'API restituisce un dict {blueprint_id: [inserzioni]} oppure una
        lista piatta: normalizziamo sempre a dict {blueprint_id(int): [..]}."""
        grouped = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if not isinstance(v, list):
                    continue
                try:
                    key = int(k)
                except (TypeError, ValueError):
                    key = None
                for p in v:
                    bp = p.get("blueprint_id", key) if isinstance(p, dict) else key
                    grouped.setdefault(bp, []).append(p)
        elif isinstance(data, list):
            for p in data:
                if isinstance(p, dict):
                    grouped.setdefault(p.get("blueprint_id"), []).append(p)
        return grouped

    def get_marketplace_products(self, blueprint_id: int, language: str | None = "en", foil: bool | None = None):
        """Inserzioni attive per UNA blueprint (lista)."""
        params = {"blueprint_id": blueprint_id}
        if language:
            params["language"] = language
        if foil is not None:
            params["foil"] = "true" if foil else "false"
        grouped = self._flatten_products(self._get("/marketplace/products", params=params))
        return [p for lst in grouped.values() for p in lst]

    def get_marketplace_products_by_expansion(self, expansion_id: int, language: str | None = "en"):
        """Inserzioni attive di TUTTE le carte di un'espansione, con una sola
        richiesta. Ritorna dict {blueprint_id: [inserzioni]}.
        Molto più veloce che chiedere una blueprint alla volta."""
        params = {"expansion_id": expansion_id}
        if language:
            params["language"] = language
        return self._flatten_products(self._get("/marketplace/products", params=params))
