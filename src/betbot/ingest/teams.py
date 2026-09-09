"""Registro canonico de equipos y resolucion de alias.

EL FALLO MAS CARO DE TODO EL SISTEMA VIVE AQUI.

The Odds API dice "Los Angeles Clippers". ESPN dice "LA Clippers". Retrosheet
dice "LAN". FBref dice "Man City" donde el libro dice "Manchester City". Si el
nombre con el que entrenas no coincide con el nombre que llega en las odds, el
modelo devuelve None para TODOS los eventos, el bot no emite ninguna senal, y no
hay ni un error en el log: parece que simplemente no hay valor en el mercado.
Ese fallo es silencioso y puede durar meses.

La defensa es esta: un nombre canonico por equipo (el de The Odds API, porque es
el que hay que casar en el momento de la senal), todo lo demas como alias, y
modo estricto que revienta ante un nombre desconocido en vez de descartarlo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from betbot.types import Sport


class UnknownTeamError(ValueError):
    """Nombre de equipo no resoluble. En modo estricto detiene la ingesta."""


def _normalize(name: str) -> str:
    """Clave de busqueda: sin acentos, sin puntuacion, minusculas, sin espacios extra."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Nombre canonico -> alias adicionales. El canonico y su propia forma
# normalizada se registran siempre, no hace falta repetirlos aqui.
NBA_TEAMS: dict[str, list[str]] = {
    "Atlanta Hawks": ["Hawks", "ATL", "Atlanta"],
    "Boston Celtics": ["Celtics", "BOS", "Boston"],
    "Brooklyn Nets": ["Nets", "BKN", "BRK", "Brooklyn", "New Jersey Nets", "NJN"],
    "Charlotte Hornets": ["Hornets", "CHA", "CHO", "Charlotte", "Charlotte Bobcats", "CHH"],
    "Chicago Bulls": ["Bulls", "CHI", "Chicago"],
    "Cleveland Cavaliers": ["Cavaliers", "CLE", "Cleveland"],
    "Dallas Mavericks": ["Mavericks", "DAL", "Dallas"],
    "Denver Nuggets": ["Nuggets", "DEN", "Denver"],
    "Detroit Pistons": ["Pistons", "DET", "Detroit"],
    "Golden State Warriors": ["Warriors", "GSW", "GS", "Golden State"],
    "Houston Rockets": ["Rockets", "HOU", "Houston"],
    "Indiana Pacers": ["Pacers", "IND", "Indiana"],
    "Los Angeles Clippers": ["LAC", "LA Clippers", "Clippers", "San Diego Clippers"],
    "Los Angeles Lakers": ["LAL", "LA Lakers", "Lakers", "Minneapolis Lakers"],
    "Memphis Grizzlies": ["Grizzlies", "MEM", "Memphis", "Vancouver Grizzlies", "VAN"],
    "Miami Heat": ["Heat", "MIA", "Miami"],
    "Milwaukee Bucks": ["Bucks", "MIL", "Milwaukee"],
    "Minnesota Timberwolves": ["Timberwolves", "MIN", "Minnesota", "Wolves"],
    "New Orleans Pelicans": ["Pelicans", "NOP", "New Orleans", "New Orleans Hornets", "NOH", "NOK"],
    "New York Knicks": ["Knicks", "NYK", "New York", "Knicks"],
    "Oklahoma City Thunder": ["Thunder", "OKC", "Oklahoma City", "Seattle SuperSonics", "SEA"],
    "Orlando Magic": ["Magic", "ORL", "Orlando"],
    "Philadelphia 76ers": ["PHI", "Philadelphia", "Sixers", "76ers"],
    "Phoenix Suns": ["Suns", "PHX", "PHO", "Phoenix"],
    "Portland Trail Blazers": ["POR", "Portland", "Trail Blazers", "Blazers", "Trailblazers"],
    "Sacramento Kings": ["Kings", "SAC", "Sacramento", "Kansas City Kings"],
    "San Antonio Spurs": ["Spurs", "SAS", "SA", "San Antonio"],
    "Toronto Raptors": ["Raptors", "TOR", "Toronto"],
    "Utah Jazz": ["Jazz", "UTA", "Utah", "New Orleans Jazz"],
    "Washington Wizards": ["Wizards", "WAS", "WSB", "Washington", "Washington Bullets"],
}

MLB_TEAMS: dict[str, list[str]] = {
    "Arizona Diamondbacks": ["ARI", "Arizona", "D-backs", "Diamondbacks"],
    "Atlanta Braves": ["ATL", "Atlanta"],
    "Baltimore Orioles": ["BAL", "Baltimore"],
    "Boston Red Sox": ["BOS", "Boston"],
    "Chicago Cubs": ["CHC", "CHN", "Cubs"],
    "Chicago White Sox": ["CWS", "CHA", "CHW", "White Sox"],
    "Cincinnati Reds": ["CIN", "Cincinnati"],
    "Cleveland Guardians": ["CLE", "Cleveland", "Cleveland Indians"],
    "Colorado Rockies": ["COL", "Colorado"],
    "Detroit Tigers": ["DET", "Detroit"],
    "Houston Astros": ["HOU", "Houston"],
    "Kansas City Royals": ["KC", "KCA", "KCR", "Kansas City"],
    "Los Angeles Angels": ["LAA", "ANA", "Angels", "Anaheim Angels",
                            "Los Angeles Angels of Anaheim", "California Angels"],
    "Los Angeles Dodgers": ["LAD", "LAN", "Dodgers"],
    "Miami Marlins": ["MIA", "FLO", "FLA", "Florida Marlins", "Marlins"],
    "Milwaukee Brewers": ["MIL", "Milwaukee"],
    "Minnesota Twins": ["MIN", "Minnesota"],
    "New York Mets": ["NYM", "NYN", "Mets"],
    "New York Yankees": ["NYY", "NYA", "Yankees"],
    # "ATH" es el codigo que Retrosheet usa desde la mudanza de 2025. Sin este
    # alias se pierden ~160 partidos por temporada en silencio.
    "Oakland Athletics": ["OAK", "ATH", "Athletics", "A's", "As",
                           "Sacramento Athletics", "Las Vegas Athletics"],
    "Philadelphia Phillies": ["PHI", "Philadelphia"],
    "Pittsburgh Pirates": ["PIT", "Pittsburgh"],
    "San Diego Padres": ["SD", "SDN", "SDP", "San Diego"],
    "San Francisco Giants": ["SF", "SFN", "SFG", "San Francisco"],
    "Seattle Mariners": ["SEA", "Seattle"],
    "St. Louis Cardinals": ["STL", "SLN", "St Louis Cardinals", "Saint Louis Cardinals"],
    "Tampa Bay Rays": ["TB", "TBA", "TBR", "Tampa Bay", "Tampa Bay Devil Rays"],
    "Texas Rangers": ["TEX", "Texas"],
    "Toronto Blue Jays": ["TOR", "Toronto"],
    "Washington Nationals": ["WSH", "WAS", "WSN", "Washington", "Montreal Expos", "MON"],
}

# NFL. Los codigos de nflverse incluyen sedes historicas: STL/LA (Rams),
# SD/LAC (Chargers), OAK/LV (Raiders). Sin mapearlos, cada mudanza parte una
# franquicia en dos equipos distintos y ambos arrancan sin historial.
NFL_TEAMS: dict[str, list[str]] = {
    "Arizona Cardinals": ["ARI", "Cardinals", "Arizona"],
    "Atlanta Falcons": ["ATL", "Falcons", "Atlanta"],
    "Baltimore Ravens": ["BAL", "Ravens", "Baltimore"],
    "Buffalo Bills": ["BUF", "Bills", "Buffalo"],
    "Carolina Panthers": ["CAR", "Panthers", "Carolina"],
    "Chicago Bears": ["CHI", "Bears", "Chicago"],
    "Cincinnati Bengals": ["CIN", "Bengals", "Cincinnati"],
    "Cleveland Browns": ["CLE", "Browns", "Cleveland"],
    "Dallas Cowboys": ["DAL", "Cowboys", "Dallas"],
    "Denver Broncos": ["DEN", "Broncos", "Denver"],
    "Detroit Lions": ["DET", "Lions", "Detroit"],
    "Green Bay Packers": ["GB", "GNB", "Packers", "Green Bay"],
    "Houston Texans": ["HOU", "Texans", "Houston"],
    "Indianapolis Colts": ["IND", "Colts", "Indianapolis"],
    "Jacksonville Jaguars": ["JAX", "JAC", "Jaguars", "Jacksonville"],
    "Kansas City Chiefs": ["KC", "KAN", "Chiefs", "Kansas City"],
    "Las Vegas Raiders": ["LV", "OAK", "LVR", "Raiders", "Oakland Raiders"],
    "Los Angeles Chargers": ["LAC", "SD", "SDG", "Chargers", "San Diego Chargers"],
    "Los Angeles Rams": ["LA", "LAR", "STL", "Rams", "St. Louis Rams", "St Louis Rams"],
    "Miami Dolphins": ["MIA", "Dolphins", "Miami"],
    "Minnesota Vikings": ["MIN", "Vikings", "Minnesota"],
    "New England Patriots": ["NE", "NWE", "Patriots", "New England"],
    "New Orleans Saints": ["NO", "NOR", "Saints", "New Orleans"],
    "New York Giants": ["NYG", "Giants"],
    "New York Jets": ["NYJ", "Jets"],
    "Philadelphia Eagles": ["PHI", "Eagles", "Philadelphia"],
    "Pittsburgh Steelers": ["PIT", "Steelers", "Pittsburgh"],
    "San Francisco 49ers": ["SF", "SFO", "49ers", "San Francisco", "Niners"],
    "Seattle Seahawks": ["SEA", "Seahawks", "Seattle"],
    "Tampa Bay Buccaneers": ["TB", "TAM", "Buccaneers", "Bucs", "Tampa Bay"],
    "Tennessee Titans": ["TEN", "Titans", "Tennessee"],
    "Washington Commanders": ["WAS", "WSH", "Commanders", "Washington",
                               "Washington Football Team", "Washington Redskins"],
}

# Premier League. El futbol es el caso feo: cada fuente abrevia distinto.
EPL_TEAMS: dict[str, list[str]] = {
    "Arsenal": ["Arsenal FC"],
    "Aston Villa": ["Villa"],
    "AFC Bournemouth": ["Bournemouth"],
    "Brentford": ["Brentford FC"],
    "Brighton and Hove Albion": ["Brighton", "Brighton & Hove Albion"],
    "Chelsea": ["Chelsea FC"],
    "Crystal Palace": ["Palace"],
    "Everton": ["Everton FC"],
    "Fulham": ["Fulham FC"],
    "Ipswich Town": ["Ipswich"],
    "Leicester City": ["Leicester"],
    "Liverpool": ["Liverpool FC"],
    "Manchester City": ["Man City", "Manchester City FC"],
    "Manchester United": ["Man United", "Man Utd", "Manchester Utd", "Manchester United FC"],
    "Newcastle United": ["Newcastle", "Newcastle Utd"],
    "Nottingham Forest": ["Nott'm Forest", "Notts Forest", "Forest"],
    "Southampton": ["Southampton FC"],
    "Tottenham Hotspur": ["Tottenham", "Spurs"],
    "West Ham United": ["West Ham", "West Ham Utd"],
    "Wolverhampton Wanderers": ["Wolves", "Wolverhampton"],
    # Historicos, presentes en datasets largos
    "Leeds United": ["Leeds"],
    "Burnley": ["Burnley FC"],
    "Sheffield United": ["Sheffield Utd", "Sheffield United FC"],
    "Luton Town": ["Luton"],
    "Watford": ["Watford FC"],
    "Norwich City": ["Norwich"],
    "West Bromwich Albion": ["West Brom", "West Bromwich"],
    "Stoke City": ["Stoke"],
    "Swansea City": ["Swansea"],
    "Huddersfield Town": ["Huddersfield"],
    "Cardiff City": ["Cardiff"],
    "Hull City": ["Hull"],
    "Middlesbrough": ["Boro"],
    "Sunderland": ["Sunderland AFC"],
    "Queens Park Rangers": ["QPR"],
    "Wigan Athletic": ["Wigan"],
    "Bolton Wanderers": ["Bolton"],
    "Blackburn Rovers": ["Blackburn"],
    "Birmingham City": ["Birmingham"],
    "Derby County": ["Derby"],
    "Portsmouth": ["Pompey"],
    "Reading": ["Reading FC"],
    "Charlton Athletic": ["Charlton"],
    "Blackpool": ["Blackpool FC"],
    "Sheffield Wednesday": ["Sheff Wed", "Sheffield Weds"],
    "Coventry City": ["Coventry"],
    "Wimbledon": ["Wimbledon FC", "AFC Wimbledon"],
    "Barnsley": ["Barnsley FC"],
    "Bradford City": ["Bradford"],
    "Oldham Athletic": ["Oldham"],
}

_SPORT_TABLES: dict[Sport, dict[str, list[str]]] = {
    Sport.NBA: NBA_TEAMS,
    Sport.MLB: MLB_TEAMS,
    Sport.NFL: NFL_TEAMS,
    Sport.SOCCER_EPL: EPL_TEAMS,
}


@dataclass
class TeamRegistry:
    """Resuelve cualquier variante de nombre a su forma canonica.

    `strict=True` (por defecto) lanza UnknownTeamError ante un nombre no
    reconocido. Es deliberado: es preferible que la ingesta se detenga y te
    obligue a anadir el alias, a que descarte partidos en silencio y entrene un
    modelo con la mitad de la temporada.
    """

    sport: Sport
    strict: bool = True
    _lookup: dict[str, str] = field(default_factory=dict, repr=False)
    unresolved: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        table = _SPORT_TABLES.get(self.sport, {})
        for canonical_name, aliases in table.items():
            self._register(canonical_name, canonical_name)
            for alias in aliases:
                self._register(alias, canonical_name)

    def _register(self, alias: str, canonical_name: str) -> None:
        key = _normalize(alias)
        existing = self._lookup.get(key)
        if existing and existing != canonical_name:
            raise ValueError(
                f"alias ambiguo '{alias}': mapea a '{existing}' y a '{canonical_name}'"
            )
        self._lookup[key] = canonical_name

    def add_alias(self, alias: str, canonical_name: str) -> None:
        """Anade un alias en caliente (util al descubrir una variante nueva)."""
        if _normalize(canonical_name) not in self._lookup:
            raise UnknownTeamError(f"equipo canonico desconocido: {canonical_name}")
        self._register(alias, canonical_name)

    def resolve(self, name: str) -> str | None:
        """Nombre canonico, o None si no se reconoce y no estamos en modo estricto."""
        if not name or not name.strip():
            if self.strict:
                raise UnknownTeamError("nombre de equipo vacio")
            return None

        key = _normalize(name)
        hit = self._lookup.get(key)
        if hit:
            return hit

        # Ultimo intento: sufijos de club que unas fuentes ponen y otras no.
        for suffix in (" fc", " afc", " cf", " sc"):
            if key.endswith(suffix):
                hit = self._lookup.get(key[: -len(suffix)])
                if hit:
                    return hit

        self.unresolved.add(name)
        if self.strict:
            raise UnknownTeamError(
                f"equipo no reconocido en {self.sport.name}: '{name}'. "
                f"Anadelo a teams.py o usa strict=False. "
                f"Si no lo haces, este equipo desaparece del entrenamiento en silencio."
            )
        return None

    @property
    def canonical_names(self) -> list[str]:
        return sorted(set(self._lookup.values()))


def canonical(sport: Sport, name: str) -> str | None:
    """Atajo de un solo uso. Para lotes, instancia TeamRegistry y reutilizalo."""
    return TeamRegistry(sport, strict=False).resolve(name)
