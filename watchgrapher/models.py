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

Rolex|Submariner (No Date)|ref 14060 / 14060M|1990-2012|rolex_3130|check
Rolex|Sea-Dweller|ref 1665|1967-1983|rolex_1570|sure
Rolex|Sea-Dweller|ref 16660 / 16600|1983-2008|rolex_3135|sure
Rolex|GMT-Master|ref 1675|1959-1980|rolex_1570|sure
Rolex|GMT-Master II|ref 16710 / 16760|1983-2007|rolex_3186|check
Rolex|Yacht-Master 42|ref 226658 / 226659|2019-|rolex_3235|sure
Rolex|Datejust 36|ref 116200 / 116234|2005-2018|rolex_3135|sure
Rolex|Milgauss|ref 116400GV|2007-2023|rolex_3131|sure
Rolex|Day-Date 40|ref 228238 / 228239|2015-|rolex_3255|check
Rolex|Sky-Dweller|ref 326934 / 336934|2017-|rolex_3235|check

Tudor|Black Bay GMT|ref 79830RB|2018-|tudor_mt5602|sure
Tudor|Black Bay Pro|ref 79470|2022-|tudor_mt5602|sure
Tudor|Black Bay Chrono|ref 79360N / 79360DK|2017-|breitling_b01|check
Tudor|1926|ref 91350 / 91450|2018-|sw200_1|check
Tudor|Royal|ref 28500 / 28600|2020-|sw200_1|check
Tudor|Glamour Date / Date+Day|ref 55000 / 56000|2011-|sw221_1|check

Omega|Speedmaster '57 / Chronoscope|cal 9900 / 9906|2011-|omega_8500|check
Omega|Seamaster 300 Master Co-Axial|cal 8400 / 8912|2014-|omega_8500|sure
Omega|Seamaster PloProf 1200M|cal 8500 / 8912|2009-|omega_8500|sure
Omega|De Ville Prestige / Hour Vision|cal 2500 / 8500|2002-|omega_8500|check
Omega|Speedmaster Mark II / 125|cal 1040 / 1041|1970-1980|lemania_5100|check
Omega|Seamaster Cosmic / Geneve|cal 552 / 565|1968-1975|omega_565|sure
Omega|Constellation Pie-Pan|cal 551 / 561|1958-1966|omega_565|check
Omega|Seamaster Aqua Terra Worldtimer|cal 8938|2017-|omega_8500|check
Omega|Speedmaster '57 Co-Axial (pre-2021)|cal 9300|2013-2021|omega_8500|check

Seiko|Prospex 62MAS reissue|SPB143 / SPB185 / SPB149|2020-|seiko_6r15|sure
Seiko|Prospex Sumo|SPB321 / SPB323|2022-|seiko_6r15|sure
Seiko|Prospex Marinemaster 300|SBDX017 / SBDX023 / SLA021|2015-|seiko_8l35|sure
Seiko|Prospex 1968 Hi-Beat Diver|SLA025 / SLA037 / SLA063|2018-|seiko_8l35|check
Seiko|Prospex Alpinist|SPB121 / SPB199 / SPB201|2020-|seiko_6r15|sure
Seiko|Presage Sharp Edged|SPB165 / SPB167 / SPB227|2020-|seiko_6r15|sure
Seiko|Presage Craftsmanship / Arita|SPB393 / SPB399|2022-|seiko_6r15|check
Seiko|King Seiko KSK reissue|SPB279 / SPB281 / SPB285|2022-|seiko_6r15|check
Seiko|King Seiko|SJE083 / SJE087 / SJE089|2021-|seiko_6l35|check
Seiko|5 Sports GMT|SSK001 / SSK003 / SSK005|2022-|seiko_nh35|check
Seiko|5 Sports|SRPD / SRPG / SRPJ (4R36)|2019-|seiko_nh35|sure
Seiko|Seiko 5|SNXS / SNK / SNKE / SNKL (7S26)|1996-2019|seiko_7s26|sure
Seiko|Seiko 5 Sea Urchin|SNZF17 / SNZF15 (7S36)|2007-|seiko_7s26|sure
Seiko|SARB033 / SARB035|6R15|2008-2018|seiko_6r15|sure
Seiko|6138 Bullhead / Panda|6138-0040 / 6138-8020|1969-1979|seiko_6139|check
Seiko|6309 Turtle|6309-7040 / 6309-729x|1976-1988|seiko_6309|sure
Seiko|6105 Willard (first gen)|6105-8000 / 6105-8009|1968-1970|seiko_6105|sure
Seiko|6119 5 Sports / 6106|6119-8100 / 6106-8100|1968-1978|seiko_6119|check
Seiko|SCVS Spirit|4S15|1996-2005|seiko_4s15|check

Grand Seiko|Evolution 9 White Birch|SLGH005 / SLGH017|2021-|seiko_9sa5|sure
Grand Seiko|Hi-Beat 36000|SBGH / SBGJ (9S85 / 9S86)|2009-|seiko_9s85|sure
Grand Seiko|Heritage 62GS / 44GS|SBGR / SBGH|2016-|seiko_9s65|sure
Grand Seiko|Elegance hand-wind|SBGW231 / SBGW235 / SBGW291 (9S64)|2016-|seiko_9s65|check

Citizen|Series 8 831 / 870|Miyota 9051|2022-|miyota_9110|check
Citizen|Tsuyosa|NJ0150 / NJ0170 (Miyota 8210)|2022-|miyota_8215|check
Citizen|Promaster Mechanical Diver Fugu|NY0040 / NY0100 (Miyota 8203/8204)|1989-|miyota_8215|check
Citizen|The Citizen|NC0000 / NC0060 (Cal. 0950)|2021-|citizen_0950|check

Hamilton|Khaki Field Murph|H-10|2021-|eta_c07|sure
Hamilton|Khaki Field Titanium Auto|H-10|2020-|eta_c07|sure
Hamilton|Khaki Navy Frogman / Scuba Auto|H-10|2018-|eta_c07|check
Hamilton|Jazzmaster Open Heart / Auto|H-10|2015-|eta_c07|check
Hamilton|Ventura Auto / Elvis80|H-10|2018-|eta_c07|check
Hamilton|Pan Europ Auto|H-30 (ETA 2824-2)|2011-|eta_2824_2|check
Hamilton|Khaki Aviation Chrono / Intra-Matic Chrono|H-31 (ETA 7753)|2012-|eta_7750|check

Longines|Spirit Zulu Time|L899 (ETA A31 GMT)|2022-|eta_a31|check
Longines|Master Collection Annual Calendar|L897|2019-|eta_a31|check
Longines|Record|L888.4 chronometer, silicon|2018-|eta_a31|sure
Longines|Heritage Legend Diver 39 / Skin Diver|L888.5|2022-|eta_a31|sure
Longines|Conquest (2023-)|L888.5 / L788|2023-|eta_a31|sure
Longines|Flagship Heritage|L609 / L704 (ETA 2892)|2017-|eta_2892a2|check

Tissot|PRX Powermatic 80|T137.407 / T137.207|2021-|eta_c07|sure
Tissot|Chemin des Tourelles Powermatic 80|T139.807|2016-|eta_c07|sure
Tissot|Seastar 1000 Powermatic 80 / 36|T120.407 / T120.807|2018-|eta_c07|sure
Tissot|Heritage 1973 / Navigator Chrono|A05.231 (Valjoux 7750)|2019-|eta_7750|check
Tissot|PRX Chronograph Automatic|A05.H31 (Valjoux 7753)|2023-|eta_7750|check

Certina|DS-1 / DS Action Diver / DS PH200M|Powermatic 80 (C07.611)|2016-|eta_c07|sure
Certina|DS Podium Chrono|Valjoux 7750|2012-|eta_7750|check
Mido|Ocean Star 200 / 200C / GMT|Caliber 80 (C07.621 / C07.661)|2016-|eta_c07|sure
Mido|Multifort Patrimony / Datometer|Caliber 80|2018-|eta_c07|sure
Mido|Commander / Baroncelli|Caliber 80|2017-|eta_c07|check
Rado|Captain Cook 42 / 39 / Ceramic|R734 (C07.611)|2017-|eta_c07|sure
Rado|True / DiaMaster / Anatom Automatic|R764 (C07)|2016-|eta_c07|check

Oris|Aquis Date Calibre 400|Oris 400|2020-|oris_400|sure
Oris|ProPilot X Calibre 400|Oris 400|2021-|oris_400|sure
Oris|Big Crown Pointer Date Calibre 403 / 473|Oris 403 (Cal 400 base)|2022-|oris_400|sure
Oris|Divers Sixty-Five 40 / 42 / 38|Oris 733 (Sellita SW200-1)|2015-|sw200_1|sure
Oris|Big Crown ProPilot Big Date|Oris 751 (Sellita SW220)|2014-|sw221_1|check
Oris|Artelier / Artix Date|Oris 733 (Sellita SW200-1)|2010-|sw200_1|check

Sinn|556 I / 556 A / 556 RS|Sellita SW200-1|2010-|sw200_1|sure
Sinn|104 St Sa / 105 St Sa / 856|Sellita SW220-1|2013-|sw221_1|check
Sinn|EZM 3 / U50 / U1 SE / T50|Sellita SW300-1|2005-|sw300_1|check
Sinn|103 St Sa (chrono)|ETA 7750 / Sellita SW500|1996-|eta_7750|check
Sinn|140 / 142 (space chrono)|Lemania 5100|1985-1998|lemania_5100|check

Squale|1521 / Y1545 / Master / Super-Squale|Sellita SW200-1|2010-|sw200_1|check
Doxa|SUB 300 / 300T / 200 / 600T|Sellita SW200-1|2019-|sw200_1|check
Steinhart|Ocean One / Ocean 39 / OVM|Sellita SW200-1|2008-|sw200_1|check
Steinhart|Ocean One GMT|ETA 2893-2 / Soprod C125|2012-|eta_2893_2|check

Christopher Ward|C60 Trident Pro 300 / 600|Sellita SW200-1|2016-|sw200_1|sure
Christopher Ward|C65 Aquitaine / Dune / Super Compressor|Sellita SW200-1|2020-|sw200_1|sure
Christopher Ward|C63 Sealander GMT|Sellita SW330-2|2021-|sw330_2|check
Christopher Ward|C1 Bel Canto|CW / La Joux-Perret G100 base|2022-|ljp_g100|check
Christopher Ward|The Twelve / C1 Morgan|Sellita SW200-1 / SW300-1|2019-|sw200_1|check

Halios|Seaforth / Fairwind / Universa|Sellita SW200-1|2016-|sw200_1|sure
Monta|Oceanking / Atlas / Noble / Triumph|Sellita SW300-1 (Monta M-22)|2018-|sw300_1|sure
Baltic|Aquascaphe / Aquascaphe GMT / Dual-Crown|Miyota 9039 / 9075|2019-|miyota_9015|sure
Baltic|MR01 Micro-rotor|Peseux 7040 / Hangzhou micro-rotor|2021-|hangzhou_5000a|check
Nodus|Sector / Contrail / Avalon / Retrospect|Miyota 9039 (regulated) / Seiko NH35|2018-|miyota_9015|check
Lorier|Neptune / Falcon / Hyperion / Gemini|Miyota 9039|2018-|miyota_9015|sure
Traska|Freediver / Summiteer / Venturer|Miyota 9039|2019-|miyota_9015|sure
Farer|Aqua Compressor / Automatic / Lander|Sellita SW200-1 / SW300-1|2018-|sw200_1|check
Formex|Essence / Reef / Field Automatic|Sellita SW300-1 chronometer|2018-|sw300_1|check
Zelos|Swordfish / Abyss / Mako / Blacktip|Seiko NH35 / Miyota 9015|2016-|seiko_nh35|check
Unimatic|Modello Uno / Due / Tre|Seiko NH35A|2015-|seiko_nh35|sure
Serica|4512 / 5303 / 8315|Soprod M100 / La Joux-Perret G100|2019-|soprod_a10|check
anOrdain|Model 1 / Model 2|Sellita SW210-1 (hand-wind)|2018-|sw210_1|sure
Vertex|M100 / M100B / M60 AquaLion|Sellita SW200-1|2016-|sw200_1|check
Yema|Superman / Navygraf / Urban Traveller|Yema MBP1000 / Seiko NH35|2019-|seiko_nh35|check
Autodromo|Group B / Intereuropa / Prototipo|Miyota 9015 / Seiko NH35|2016-|miyota_9015|check

Zodiac|Super Sea Wolf 53 / 68 / Skin|STP 1-11|2016-|stp_1_11|sure
Zodiac|Sea Wolf GMT|STP 6-15|2021-|stp_1_11|check
Bulova|Oceanographer Devil Diver / Snorkel|Miyota 8215 / Sellita SW200|2020-|miyota_8215|check

IWC|Pilot's Watch Mark XX|cal 32111|2023-|iwc_32110|sure
IWC|Pilot's Watch Automatic 41|cal 32111|2021-|iwc_32110|sure
IWC|Ingenieur 40|cal 32111|2023-|iwc_32110|sure
IWC|Big Pilot 43 / Portugieser Auto 40|cal 82100 / 82200|2020-|iwc_52010|check
IWC|Aquatimer Automatic / Portofino|cal 30120 / 35111 (Sellita base)|2011-|sw300_1|check

Breitling|Navitimer B01 / Chronomat B01 42 / Premier B01|B01|2018-|breitling_b01|sure
Breitling|Superocean Heritage / Superocean Automatic 42|B20 (Kenissi / Tudor MT base)|2017-|tudor_mt5602|sure
Breitling|Avenger Automatic 43 / 45|B17 / Sellita SW200|2019-|sw200_1|check

Panerai|Luminor Marina 44 / Submersible 42|P.9010 / P.900|2016-|panerai_p9000|sure
Panerai|Radiomir California / Luminor Base Logo|P.6000 (hand-wind)|2017-|panerai_p3000|check

Zenith|Chronomaster Sport / Original|El Primero 3600|2021-|zenith_400|sure
Zenith|Defy Skyline|El Primero 3620|2022-|zenith_400|check
Zenith|Elite Classic / Captain / Pilot Type 20|Elite 670 / 679|2003-|zenith_elite|check

Frederique Constant|Classics Index / Runabout / Highlife Auto|FC-303 (Sellita SW200)|2010-|fc_303|check
Frederique Constant|Slimline Automatic|FC-306|2014-|fc_303|check
Baume & Mercier|Clifton Baumatic|BM12 / BM13 / BM14|2018-|baumatic_bm13|sure
Baume & Mercier|Riviera Automatic|Baumatic BM13 / Sellita SW300|2021-|baumatic_bm13|check
Baume & Mercier|Classima Automatic|Sellita SW200-1|2015-|sw200_1|check

Nomos|Tangente / Club Campus / Orion / Ludwig|Alpha (hand-wind)|2005-|nomos_alpha|sure
Nomos|Tangente neomatik / Metro / Club Sport|DUW 3001|2015-|nomos_alpha|check
Nomos|Ahoi / Club Automat|DUW 5001 / Epsilon|2013-|nomos_alpha|check

Jaeger-LeCoultre|Master Ultra Thin / Master Control Date|cal 899|2013-|jlc_889|sure
Jaeger-LeCoultre|Master Ultra Thin Power Reserve / Reserve de Marche|cal 938 / 938A|2016-|jlc_938|sure
Jaeger-LeCoultre|Master Control Power Reserve|cal 938|2020-|jlc_938|check
Jaeger-LeCoultre|Polaris Automatic / Date|cal 898 / 899|2018-|jlc_889|sure
Jaeger-LeCoultre|Reverso Tribute Duoface|cal 854 / 1000|2016-|jlc_889|check

TAG Heuer|Carrera Calibre 5 / Aquaracer Calibre 5|Sellita SW200-1 / ETA 2824|2010-|sw200_1|check
TAG Heuer|Carrera Calibre 16 / Aquaracer Calibre 16|ETA 7750 / Valjoux|2004-|eta_7750|check
Heuer|Autavia / Carrera / Camaro (vintage)|Valjoux 72 / 7730 / 7734|1962-1985|valjoux_72|check
Universal Geneve|Polerouter / Polerouter Date|cal 215 / 218 microrotor|1955-1969|generic_18000|check
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
