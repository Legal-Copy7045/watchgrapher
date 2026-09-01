"""
Watch model -> movement index.

The first question at the bench is rarely "which caliber is this" -- it is
"what is in an Air-King". This maps model names and reference numbers onto
caliber keys in the movement database.

The awkward truth this has to handle: one model name usually means several
movements. An Air-King has carried five different calibers across seven
decades, and picking the wrong one puts the lift angle out by enough to
misread amplitude badly. So entries are per-reference and per-generation, with
year ranges, and the finder shows all candidates rather than guessing.

Format: Brand | Model | Reference / variant | Years | caliber_key | confidence
confidence:  sure  = well documented
             check = generally accepted, but verify against the movement
Where a run changed movement mid-way, both entries appear.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ModelEntry:
    brand: str
    model: str
    variant: str
    years: str
    caliber_key: str
    confidence: str = "sure"

    @property
    def label(self) -> str:
        v = f" {self.variant}" if self.variant else ""
        return f"{self.brand} {self.model}{v}"


RAW = """
Rolex|Air-King|ref 6552|1953-1957|rolex_1030|check
Rolex|Air-King|ref 5500|1957-1989|rolex_1520|sure
Rolex|Air-King|ref 14000|1989-2000|rolex_3000|sure
Rolex|Air-King|ref 14000M / 14010M|2000-2007|rolex_3130|sure
Rolex|Air-King|ref 114200 / 114210 / 114234|2007-2014|rolex_3130|sure
Rolex|Air-King|ref 116900|2016-2022|rolex_3131|sure
Rolex|Air-King|ref 126900|2022-|rolex_3235|sure
Rolex|Submariner|ref 5512 / 5513|1959-1989|rolex_1570|sure
Rolex|Submariner|ref 16800 / 168000 / 16610|1979-2010|rolex_3135|sure
Rolex|Submariner|ref 114060 / 116610|2010-2020|rolex_3135|sure
Rolex|Submariner|ref 124060 / 126610|2020-|rolex_3235|sure
Rolex|Sea-Dweller|ref 16600|1989-2008|rolex_3135|sure
Rolex|Sea-Dweller|ref 126600|2017-|rolex_3235|sure
Rolex|GMT-Master II|ref 16710|1989-2007|rolex_3186|check
Rolex|GMT-Master II|ref 116710|2007-2018|rolex_3186|sure
Rolex|GMT-Master II|ref 126710|2018-|rolex_3186|sure
Rolex|Datejust|ref 1601 / 1603|1959-1977|rolex_1570|sure
Rolex|Datejust|ref 16234 / 16220|1988-2005|rolex_3135|sure
Rolex|Datejust|ref 116200 / 116234|2005-2018|rolex_3135|sure
Rolex|Datejust 36 / 41|ref 126200 / 126300|2018-|rolex_3235|sure
Rolex|Explorer|ref 1016|1963-1989|rolex_1570|sure
Rolex|Explorer|ref 14270 / 114270|1989-2010|rolex_3000|check
Rolex|Explorer|ref 214270|2010-2021|rolex_3130|sure
Rolex|Explorer 36|ref 124270|2021-|rolex_3230|sure
Rolex|Explorer II|ref 16570|1989-2011|rolex_3186|check
Rolex|Explorer II|ref 216570 / 226570|2011-|rolex_3186|sure
Rolex|Milgauss|ref 116400|2007-2023|rolex_3131|sure
Rolex|Oyster Perpetual|ref 114300 / 116000|2007-2020|rolex_3130|sure
Rolex|Oyster Perpetual|ref 124300 / 126000|2020-|rolex_3230|sure
Rolex|Daytona|ref 116520|2000-2016|rolex_4130|sure
Rolex|Daytona|ref 116500|2016-2023|rolex_4130|sure
Rolex|Day-Date|ref 118238 / 118239|2000-2015|rolex_3155|check
Rolex|Yacht-Master|ref 116622|2012-2019|rolex_3135|sure
Rolex|Yacht-Master|ref 126622|2019-|rolex_3235|sure
Rolex|Datejust 31 / 34|ref 178240 / 115200|2005-2018|rolex_2235|check

Tudor|Black Bay 58|ref 79030|2018-|tudor_mt5602|sure
Tudor|Black Bay|ref 79230 / 79730|2016-|tudor_mt5602|sure
Tudor|Black Bay|ref 79220 (ETA era)|2012-2016|tudor_2824|sure
Tudor|Pelagos|ref 25600|2015-|tudor_mt5602|sure
Tudor|Ranger|ref 79950|2022-|tudor_mt5602|sure
Tudor|Black Bay 54 / 68|ref 79000 / 79600|2023-|tudor_mt5602|check
Tudor|Submariner|ref 79090 / 76100|1969-1999|eta_2824_2|check

Omega|Speedmaster Professional|Moonwatch, cal 861/1861|1968-2021|omega_1861|sure
Omega|Speedmaster Professional|Moonwatch, cal 321|1957-1968|omega_321|sure
Omega|Speedmaster|Reduced / Automatic|1988-2011|eta_7750|check
Omega|Seamaster 300M|ref 2531.80, cal 1120|1993-2011|omega_1120|sure
Omega|Seamaster 300M|ref 210.30, cal 8800|2018-|omega_8500|sure
Omega|Seamaster Planet Ocean|cal 2500|2005-2011|omega_2500|sure
Omega|Seamaster Planet Ocean|cal 8500 / 8900|2011-|omega_8500|sure
Omega|Seamaster Aqua Terra|cal 2500|2003-2012|omega_2500|sure
Omega|Seamaster Aqua Terra|cal 8500 / 8900|2012-|omega_8500|sure
Omega|Railmaster|cal 8806|2017-|omega_8500|check
Omega|Constellation|cal 1120|1995-2007|omega_1120|check
Omega|Seamaster De Ville|cal 550 / 560 series|1958-1969|omega_565|sure
Omega|Seamaster / Constellation|cal 1010 / 1020 / 1030|1970-1980|omega_1030|sure

Seiko|SKX007 / SKX009 / SKX013|7S26|1996-2019|seiko_7s26|sure
Seiko|SKX173 / SKX175|7S26|1996-2015|seiko_7s26|sure
Seiko|Turtle SRP777 / SRPD|4R36|2016-|seiko_nh35|sure
Seiko|Samurai SRPB / SRPE|4R35|2017-|seiko_nh35|sure
Seiko|Sumo SBDC001 / SPB103|6R15 / 6R35|2007-|seiko_6r15|sure
Seiko|Alpinist SARB017|6R15|2006-2018|seiko_6r15|sure
Seiko|Alpinist SPB121 / SPB155|6R35|2020-|seiko_6r15|sure
Seiko|5 Sports SRPD / SRPG|4R36|2019-|seiko_nh35|sure
Seiko|Presage Cocktail Time SRPB / SSA|4R35 / 4R57|2016-|seiko_nh35|sure
Seiko|Presage SPB / SARX|6R35|2019-|seiko_6r15|check
Seiko|Prospex Willard SPB151 / SPB153|6R35|2020-|seiko_6r15|sure
Seiko|King Seiko SPB / SJE|6R31 / 6L35|2022-|seiko_6r15|check
Seiko|Seiko 5 SNK / SNKL|7S26|1996-2019|seiko_7s26|sure
Seiko|Bell-Matic|4006A|1967-1978|seiko_6139|check
Seiko|6139 Chronograph|Pogue / 6139-6002|1969-1979|seiko_6139|sure
Seiko|6105 Diver|6105-8110 Captain Willard|1968-1977|seiko_6105|sure
Grand Seiko|Snowflake SBGA / Hi-Beat SBGH|9S65 / 9S85|2010-|seiko_9s65|check
Grand Seiko|SBGR / SBGW|9S65 / 9S64|2005-|seiko_9s65|check

Orient|Bambino|F6724 / F6722|2011-|orient_f6922|sure
Orient|Mako II / Ray II|F6922|2016-|orient_f6922|sure
Orient|Kamasu / Mako 40|F6922|2019-|orient_f6922|sure

Tissot|PRX Powermatic 80|ref T137.407|2021-|eta_c07|sure
Tissot|Seastar 1000 Powermatic 80|ref T120.407|2018-|eta_c07|sure
Tissot|Le Locle Powermatic 80|ref T006.407|2015-|eta_c07|sure
Tissot|Gentleman Powermatic 80|ref T127.407|2019-|eta_c07|sure
Tissot|Visodate|ETA 2836-2|2010-|eta_2836_2|check
Tissot|PRS 516 / Chrono XL|ETA 7750 / Valjoux|2010-|eta_7750|check

Hamilton|Khaki Field Mechanical|H-50 (ETA 2801-2)|2015-|eta_2824_2|check
Hamilton|Khaki Field Auto|H-10 (ETA C07.111)|2016-|eta_c07|sure
Hamilton|Khaki Navy Scuba / Jazzmaster|H-10|2016-|eta_c07|sure
Hamilton|Intra-Matic|H-10|2017-|eta_c07|check
Hamilton|Khaki Aviation Chrono|H-21 (ETA 7753)|2012-|eta_7750|check

Longines|HydroConquest|L888 (ETA A31.L01)|2018-|eta_a31|sure
Longines|Spirit|L888.4|2020-|eta_a31|sure
Longines|Legend Diver|L888|2018-|eta_a31|check
Longines|Master Collection|L888 / L619 (ETA 2892)|2015-|eta_2892a2|check
Longines|Conquest / Flagship vintage|cal 280 / 340 / 430|1955-1975|eta_2824_2|check

Oris|Aquis Date|Oris 733 (Sellita SW200-1)|2013-|sw200_1|sure
Oris|Divers Sixty-Five|Oris 733 (Sellita SW200-1)|2015-|sw200_1|sure
Oris|Big Crown Pointer Date|Oris 754 (SW200 base)|2010-|sw200_1|check
Oris|Aquis / ProPilot Calibre 400|Oris 400|2020-|oris_400|sure

Sinn|556|Sellita SW200-1 / ETA 2824-2|2000-|sw200_1|check
Sinn|104 St Sa|Sellita SW200-1|2013-|sw200_1|sure
Sinn|U1 / U2|Sellita SW200-1|2005-|sw200_1|check
Sinn|103 St|ETA 7750 / Sellita SW500|1961-|eta_7750|check

Christopher Ward|C60 Trident|Sellita SW200-1|2014-|sw200_1|sure
Christopher Ward|C65 Trident|Sellita SW200-1|2016-|sw200_1|sure
Christopher Ward|C63 Sealander|Sellita SW200-1|2020-|sw200_1|check

Steinhart|Ocean 39 / Ocean One|ETA 2824-2 / Sellita SW200-1|2008-|eta_2824_2|check
Squale|1521 / 20 Atmos|ETA 2824-2 / Sellita SW200-1|2010-|eta_2824_2|check
Doxa|SUB 300 / SUB 200|ETA 2824-2 / Sellita SW200-1|2019-|eta_2824_2|check
Certina|DS Action / DS PH200M|Powermatic 80 (C07)|2017-|eta_c07|sure
Mido|Ocean Star|Caliber 80 (ETA C07.621)|2016-|eta_c07|sure
Rado|Captain Cook|Rado R763 (ETA C07.611)|2017-|eta_c07|sure
Baltic|Aquascaphe|Miyota 9039|2019-|miyota_9015|sure
Baltic|HMS 002 / Bicompax|Miyota 8N24 / Seagull ST19|2017-|miyota_8215|check
Lorier|Neptune / Falcon|Miyota 9015|2018-|miyota_9015|sure
Traska|Freediver / Summiteer|Miyota 9039|2019-|miyota_9015|sure
Invicta|8926 / 9937 Pro Diver|Seiko NH35A|2000-|seiko_nh35|sure

Vostok|Amphibia|2409 (hand-wind) / 2416B (auto)|1967-|vostok_2416|sure
Vostok|Komandirskie|2414 / 2431|1965-|vostok_2409|sure
Poljot|Sturmanskie / Okean Chrono|3133|1976-|poljot_3133|sure
Raketa|Big Zero / 24 Hour|2609 / 2623|1970-|raketa_2609|check

Sea-Gull|1963 Air Force Chronograph|ST19|1963-|st19|sure
Sea-Gull|Ocean Star / M199S|ST2130|2010-|st2130|check
Sea-Gull|Pilot / Flieger|ST3600 (6497 clone)|2010-|st3600|sure

IWC|Mark XVII / XVIII|cal 30110 (ETA 2892 base)|2012-|iwc_30110|sure
IWC|Mark XX|cal 32111|2022-|iwc_52010|check
IWC|Pilot Chronograph|cal 79320 / 69375|2000-|iwc_79350|check
IWC|Portugieser 7 Day|cal 52010 / 52610|2015-|iwc_52010|sure

Zenith|El Primero Chronomaster|cal 400 / 4021|1969-|zenith_400|sure
Panerai|Luminor Base / Marina 8 Days|P.3000 / P.5000|2011-|panerai_p3000|sure
Nomos|Tangente / Club|Alpha / DUW 4101|1992-|nomos_alpha|sure
Junghans|Max Bill Automatic|J800.1 (ETA 2824-2 base)|2010-|eta_2824_2|check
Stowa|Flieger Klassik|ETA 2824-2 / Sellita SW200-1|2005-|eta_2824_2|check
Stowa|Marine Original|Unitas 6498-1|2005-|eta_6497_1|sure
Blancpain|Fifty Fathoms|cal 1151|2003-|blancpain_1151|sure
Jaeger-LeCoultre|Reverso / Master|cal 822 / 899|1990-|jlc_889|check
Audemars Piguet|Royal Oak Jumbo|cal 2120 / 7121|1972-|jlc_920|check
Cartier|Santos / Ballon Bleu|cal 1847 MC (Kenissi)|2016-|cartier_1904|check
Cartier|Tank / Santos|cal 1904 MC|2010-|cartier_1904|sure
Breitling|Superocean / Navitimer|ETA 2824-2 / 7750 base|1990-|eta_7750|check
Eterna|KonTiki|Eterna 3902A / SW200|2010-|eterna_3902|check
"""


def _parse():
    out = []
    for line in RAW.strip().splitlines():
        line = line.strip()
        if not line or line.count("|") < 4:
            continue
        bits = [b.strip() for b in line.split("|")]
        conf = bits[5] if len(bits) > 5 else "sure"
        out.append(ModelEntry(bits[0], bits[1], bits[2], bits[3], bits[4], conf))
    return out


MODELS: List[ModelEntry] = _parse()


def _norm(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def search_models(query: str, include_missing: bool = False) -> List[ModelEntry]:
    """
    Find model entries. Matches brand, model name, variant text and reference
    numbers, so "air king", "airking", "5500", "skx007" and "126900" all work.

    Entries whose caliber is not in the movement database are dropped unless
    `include_missing`, since offering a selection that cannot be applied is
    worse than offering nothing.
    """
    from .calibers import CALIBERS

    q = _norm(query)
    if not q:
        rows = list(MODELS)
    else:
        rows = [m for m in MODELS
                if q in _norm(m.brand + m.model + m.variant + m.years)]
    if not include_missing:
        rows = [m for m in rows if m.caliber_key in CALIBERS]
    # Brand, then model, then chronological within the model.
    rows.sort(key=lambda m: (m.brand, m.model, m.years))
    return rows


def brands() -> List[str]:
    return sorted({m.brand for m in MODELS})
