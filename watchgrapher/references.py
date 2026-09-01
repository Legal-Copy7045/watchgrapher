"""
Reference numbers -> case material, bezel, nickname, movement.

Two mechanisms, because watch references split into two kinds.

Some brands encode the information. A modern Rolex reference is systematic:
the last digit of the number is the case metal and the letter suffix is the
bezel colour in French, so 126613LB decodes to yellow Rolesor with a blue
bezel without anyone having to write that row down. That decoder covers
references nobody has catalogued yet.

Everything else needs a table. Nicknames especially -- there is no rule that
turns 116610LV into "Hulk".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Reference:
    brand: str
    model: str
    reference: str
    years: str = ""
    caliber_key: str = ""
    material: str = ""
    bezel: str = ""
    crystal: str = "Sapphire"
    nickname: str = ""
    notes: str = ""


# --------------------------------------------------------------------------
# Rolex reference decoding
# --------------------------------------------------------------------------

# Final digit of a 5- or 6-digit Rolex reference = case metal.
ROLEX_METAL = {
    "0": "Oystersteel",
    "1": "Everose Rolesor (steel and Everose gold)",
    "2": "Rolesium (steel with platinum bezel)",
    "3": "Yellow Rolesor (steel and yellow gold)",
    "4": "Steel and white gold",
    "5": "Everose gold",
    "6": "Platinum",
    "8": "Yellow gold",
    "9": "White gold",
}

# Letter suffix = bezel or crystal colour, abbreviated in French.
ROLEX_SUFFIX = {
    "LN": ("Black", "Lunette noire"),
    "LB": ("Blue", "Lunette bleue"),
    "LV": ("Green", "Lunette verte"),
    "BLRO": ("Blue and red", "Bleu / rouge -- the 'Pepsi' bezel"),
    "BLNR": ("Blue and black", "Bleu / noir -- the 'Batman' bezel"),
    "CHNR": ("Brown and black", "Chocolat / noir -- the 'Root Beer' bezel"),
    "GV": ("Green sapphire crystal", "Glace verte"),
    "NR": ("Black", "Noire"),
}

ROLEX_FAMILY = {
    "1266": "Submariner Date", "1246": "Submariner (No Date)",
    "1167": "GMT-Master II", "1267": "GMT-Master II",
    "1166": "Submariner Date", "1666": "Sea-Dweller", "1266_sd": "Sea-Dweller",
    "1165": "Daytona", "1265": "Daytona",
    "1162": "Datejust / Yacht-Master", "1262": "Datejust / Yacht-Master",
    "1142": "Air-King / Oyster Perpetual", "1242": "Explorer",
    "1169": "Milgauss", "2265": "Explorer II", "2165": "Explorer II",
}


def decode_rolex(ref: str) -> Optional[Reference]:
    """
    Decode a modern Rolex reference. Returns None if it does not look like
    one of the systematic 5- or 6-digit numbers -- vintage four-digit
    references such as 5513 or 1675 do not use this scheme.
    """
    r = ref.strip().upper().replace(" ", "").replace("-", "")
    digits = ""
    for ch in r:
        if ch.isdigit():
            digits += ch
        else:
            break
    suffix = r[len(digits):]
    if len(digits) not in (5, 6):
        return None

    metal = ROLEX_METAL.get(digits[-1])
    if metal is None:
        return None

    bezel, bez_note = "", ""
    if suffix in ROLEX_SUFFIX:
        bezel, bez_note = ROLEX_SUFFIX[suffix]

    fam = ""
    for n in (4, 3):
        if digits[:n] in ROLEX_FAMILY:
            fam = ROLEX_FAMILY[digits[:n]]
            break

    note = ("Decoded from the reference number: the last digit is the case metal "
            "and the letter suffix is the bezel colour abbreviated in French.")
    if bez_note:
        note += f" {bez_note}."
    if len(digits) == 6 and digits[0] == "1" and digits[1] == "2":
        note += " Six-digit numbering starting 12 indicates the current generation."

    return Reference(brand="Rolex", model=fam, reference=r, material=metal,
                     bezel=bezel, crystal="Sapphire", notes=note)


# --------------------------------------------------------------------------
# Curated reference table
# --------------------------------------------------------------------------
# Brand | Model | Reference | Years | caliber_key | Material | Bezel | Nickname | Notes
RAW = """
Rolex|Submariner Date|126610LN|2020-|rolex_3235|Oystersteel|Black|||
Rolex|Submariner Date|126610LV|2020-|rolex_3235|Oystersteel|Green|Starbucks / Cermit|Black dial with green bezel
Rolex|Submariner Date|126613LN|2020-|rolex_3235|Yellow Rolesor (steel and yellow gold)|Black|Two-tone Sub|
Rolex|Submariner Date|126613LB|2020-|rolex_3235|Yellow Rolesor (steel and yellow gold)|Blue|Bluesy|Blue dial and blue bezel on two-tone
Rolex|Submariner Date|126618LB|2020-|rolex_3235|Yellow gold|Blue||
Rolex|Submariner Date|126619LB|2020-|rolex_3235|White gold|Blue|Smurf|
Rolex|Submariner (No Date)|124060|2020-|rolex_3230|Oystersteel|Black||
Rolex|Submariner Date|116610LN|2010-2020|rolex_3135|Oystersteel|Black|Ceramic Sub|
Rolex|Submariner Date|116610LV|2010-2020|rolex_3135|Oystersteel|Green|Hulk|Green dial and green bezel
Rolex|Submariner Date|116613LB|2009-2020|rolex_3135|Yellow Rolesor (steel and yellow gold)|Blue|Bluesy|
Rolex|Submariner Date|16610LV|2003-2010|rolex_3135|Oystersteel|Green|Kermit|50th anniversary, black dial
Rolex|Submariner Date|16610|1989-2010|rolex_3135|Oystersteel|Black||
Rolex|Submariner (No Date)|114060|2012-2020|rolex_3130|Oystersteel|Black||
Rolex|Submariner|5513|1962-1989|rolex_1570|Stainless steel|Black||Acrylic crystal
Rolex|Submariner|5512|1959-1978|rolex_1570|Stainless steel|Black||Chronometer, acrylic crystal
Rolex|GMT-Master II|126710BLRO|2018-|rolex_3186|Oystersteel|Blue and red|Pepsi|
Rolex|GMT-Master II|126710BLNR|2019-|rolex_3186|Oystersteel|Blue and black|Batman / Batgirl|Batgirl on Jubilee bracelet
Rolex|GMT-Master II|126711CHNR|2018-|rolex_3186|Everose Rolesor (steel and Everose gold)|Brown and black|Root Beer|
Rolex|GMT-Master II|116710BLNR|2013-2019|rolex_3186|Oystersteel|Blue and black|Batman|
Rolex|GMT-Master II|116710LN|2007-2019|rolex_3186|Oystersteel|Black||
Rolex|GMT-Master II|16710|1989-2007|rolex_3186|Stainless steel|Interchangeable||Pepsi, Coke or black inserts
Rolex|GMT-Master|1675|1959-1980|rolex_1570|Stainless steel|Blue and red|Pepsi|Acrylic crystal
Rolex|Sea-Dweller|126600|2017-|rolex_3235|Oystersteel|Black|Single Red SD43|43mm
Rolex|Sea-Dweller|116600|2014-2017|rolex_3135|Oystersteel|Black|SD4000|
Rolex|Sea-Dweller|16600|1989-2008|rolex_3135|Stainless steel|Black||
Rolex|Deepsea|126660|2018-|rolex_3235|Oystersteel|Black|D-Blue if gradient dial|
Rolex|Daytona|116500LN|2016-2023|rolex_4130|Oystersteel|Black ceramic|Panda if white dial|
Rolex|Daytona|116520|2000-2016|rolex_4130|Stainless steel|Steel engraved||
Rolex|Daytona|126500LN|2023-|rolex_4130|Oystersteel|Black ceramic||
Rolex|Datejust 41|126334|2016-|rolex_3235|White Rolesor (steel and white gold)|Fluted white gold||
Rolex|Datejust 41|126300|2016-|rolex_3235|Oystersteel|Smooth||
Rolex|Datejust 36|126233|2018-|rolex_3235|Yellow Rolesor (steel and yellow gold)|Fluted yellow gold||
Rolex|Datejust 36|116234|2005-2018|rolex_3135|White Rolesor (steel and white gold)|Fluted white gold||
Rolex|Datejust|16233|1988-2005|rolex_3135|Yellow Rolesor (steel and yellow gold)|Fluted yellow gold||
Rolex|Datejust|1601|1959-1977|rolex_1570|Stainless steel or Rolesor|Fluted||Acrylic crystal
Rolex|Explorer|124270|2021-|rolex_3230|Oystersteel|Smooth||36mm
Rolex|Explorer|214270|2010-2021|rolex_3130|Oystersteel|Smooth||39mm
Rolex|Explorer|14270|1989-2001|rolex_3000|Stainless steel|Smooth||
Rolex|Explorer|1016|1963-1989|rolex_1570|Stainless steel|Smooth||Acrylic crystal
Rolex|Explorer II|216570|2011-2021|rolex_3186|Oystersteel|Steel 24h|Polar if white dial|
Rolex|Explorer II|16570|1989-2011|rolex_3186|Stainless steel|Steel 24h|Polar if white dial|
Rolex|Milgauss|116400GV|2007-2023|rolex_3131|Oystersteel|Smooth|GV / Green Glass|Green sapphire crystal
Rolex|Milgauss|116400|2007-2016|rolex_3131|Oystersteel|Smooth||
Rolex|Air-King|126900|2022-|rolex_3235|Oystersteel|Smooth||40mm, crown guards
Rolex|Air-King|116900|2016-2022|rolex_3131|Oystersteel|Smooth||40mm
Rolex|Air-King|114200|2007-2014|rolex_3130|Oystersteel|Smooth||34mm, COSC
Rolex|Air-King|14000|1989-2000|rolex_3000|Stainless steel|Smooth||34mm, first sapphire crystal
Rolex|Air-King|5500|1957-1989|rolex_1520|Stainless steel|Smooth||34mm, acrylic crystal
Rolex|Oyster Perpetual|124300|2020-|rolex_3230|Oystersteel|Smooth|Celebration if bubble dial|41mm
Rolex|Oyster Perpetual|116000|2007-2020|rolex_3130|Oystersteel|Smooth||36mm
Rolex|Yacht-Master 40|126622|2019-|rolex_3235|Rolesium (steel with platinum bezel)|Platinum||
Rolex|Day-Date 40|228238|2015-|rolex_3155|Yellow gold|Fluted|President|
Rolex|Day-Date|118238|2000-2015|rolex_3155|Yellow gold|Fluted|President|

Tudor|Black Bay 58|79030N|2018-|tudor_mt5602|Stainless steel|Black|BB58|39mm
Tudor|Black Bay 58|79030B|2020-|tudor_mt5602|Stainless steel|Blue|BB58 Navy Blue|
Tudor|Black Bay|79230N|2016-|tudor_mt5602|Stainless steel|Black||
Tudor|Black Bay|79230B|2016-|tudor_mt5602|Stainless steel|Blue||
Tudor|Black Bay|79220R|2012-2016|tudor_2824|Stainless steel|Burgundy|Red Bezel ETA|ETA 2824-2 era
Tudor|Black Bay 54|79000N|2023-|tudor_mt5602|Stainless steel|Black||37mm
Tudor|Pelagos|25600TN|2015-|tudor_mt5602|Titanium|Black||
Tudor|Pelagos|25600TB|2015-|tudor_mt5602|Titanium|Blue||
Tudor|Ranger|79950|2022-|tudor_mt5602|Stainless steel|Smooth||39mm

Omega|Speedmaster Professional|311.30.42.30.01.005|2014-2021|omega_1861|Stainless steel|Black tachymeter|Moonwatch|Hesalite crystal
Omega|Speedmaster Professional|310.30.42.50.01.002|2021-|omega_1861|Stainless steel|Black ceramic|Moonwatch 3861|Sapphire or Hesalite
Omega|Seamaster Diver 300M|210.30.42.20.01.001|2018-|omega_8500|Stainless steel|Black ceramic||Wave dial
Omega|Seamaster Diver 300M|2531.80|1993-2011|omega_1120|Stainless steel|Blue|Bond Seamaster|
Omega|Seamaster Planet Ocean|2201.50|2005-2011|omega_2500|Stainless steel|Black||Co-Axial, lift angle 38
Omega|Aqua Terra 150M|220.10.41.21.03.001|2017-|omega_8500|Stainless steel|Smooth||
Omega|Railmaster|220.10.40.20.01.001|2017-|omega_8500|Stainless steel|Smooth||

Seiko|SKX007|SKX007K1 / SKX007J1|1996-2019|seiko_7s26|Stainless steel|Black|Skex|Hardlex crystal
Seiko|SKX009|SKX009K1 / SKX009J1|1996-2019|seiko_7s26|Stainless steel|Blue and red|Pepsi SKX|Hardlex crystal
Seiko|Turtle|SRP777|2016-|seiko_nh35|Stainless steel|Black|Turtle|Hardlex crystal
Seiko|Turtle|SRPC49|2017-|seiko_nh35|Stainless steel|Black|PADI Turtle|
Seiko|Samurai|SRPB51|2017-|seiko_nh35|Stainless steel|Black|Samurai|Hardlex crystal
Seiko|Sumo|SBDC001|2007-2016|seiko_6r15|Stainless steel|Black|Sumo|
Seiko|Sumo|SPB103|2019-|seiko_6r15|Stainless steel|Black|Blue Sumo|
Seiko|Alpinist|SARB017|2006-2018|seiko_6r15|Stainless steel|Smooth|Alpinist|Green dial, gold hands
Seiko|Alpinist|SPB121|2020-|seiko_6r15|Stainless steel|Smooth|Baby Alpinist|
Seiko|Prospex Willard|SPB151|2020-|seiko_6r15|Stainless steel|Black|Willard|
Seiko|Seiko 5 Sports|SRPD55|2019-|seiko_nh35|Stainless steel|Black||Hardlex crystal
Seiko|Presage Cocktail Time|SRPB41|2016-|seiko_nh35|Stainless steel|Smooth|Starlight|
Seiko|6105 Diver|6105-8110|1970-1977|seiko_6105|Stainless steel|Black|Captain Willard|Acrylic crystal
Seiko|6139 Chronograph|6139-6002|1969-1979|seiko_6139|Stainless steel|Tachymeter|Pogue|

Tissot|PRX Powermatic 80|T137.407.11.041.00|2021-|eta_c07|Stainless steel|Smooth|PRX|
Tissot|Seastar 1000|T120.407.11.041.00|2018-|eta_c07|Stainless steel|Blue||
Hamilton|Khaki Field Auto|H70455133|2016-|eta_c07|Stainless steel|Smooth||38mm
Hamilton|Khaki Field Mechanical|H69439931|2018-|eta_2824_2|Stainless steel|Smooth||38mm hand-wind
Oris|Divers Sixty-Five|01 733 7707 4064|2015-|sw200_1|Stainless steel|Black|65|Domed sapphire
Oris|Aquis Date|01 733 7730 4135|2017-|sw200_1|Stainless steel|Blue ceramic||
Longines|HydroConquest|L3.781.4.56.6|2018-|eta_a31|Stainless steel|Blue ceramic||
Longines|Spirit|L3.810.4.53.6|2020-|eta_a31|Stainless steel|Smooth||
Sinn|556 A|556.014|2000-|sw200_1|Stainless steel|Smooth||
Christopher Ward|C60 Trident Pro 600|C60-42ADA1|2019-|sw200_1|Stainless steel|Blue ceramic||
Vostok|Amphibia|420 series|1967-|vostok_2416|Stainless steel|Rotating||Acrylic crystal
Sea-Gull|1963 Air Force|D304|1963-|st19|Stainless steel|Smooth|Chinese 1963|Acrylic or sapphire
Invicta|Pro Diver|8926OB|2000-|seiko_nh35|Stainless steel|Steel|Submariner homage|Mineral crystal
"""


# Second block: broader coverage. Same format.
# Brand | Model | Reference | Years | caliber_key | Material | Bezel | Nickname | Notes
RAW2 = """
Rolex|Datejust 41|126334|2016-|rolex_3235|White Rolesor (steel and white gold)|Fluted white gold||
Rolex|Turn-O-Graph|116264|2004-2011|rolex_3135|White Rolesor (steel and white gold)|Rotating|Thunderbird|
Rolex|Yacht-Master 42|226659|2019-|rolex_3235|White gold|Black ceramic||
Rolex|Sky-Dweller|326934|2017-|rolex_3186|White Rolesor (steel and white gold)|Fluted||Annual calendar
Rolex|Oyster Perpetual 36|126000|2020-|rolex_3230|Oystersteel|Smooth||
Rolex|Oyster Perpetual 39|114300|2015-2020|rolex_3130|Oystersteel|Smooth||
Rolex|Explorer II|226570|2021-|rolex_3186|Oystersteel|Steel 24h||
Rolex|GMT-Master II|116719BLRO|2014-2019|rolex_3186|White gold|Blue and red|White gold Pepsi|
Rolex|Submariner Date|16613|1988-2009|rolex_3135|Yellow Rolesor (steel and yellow gold)|Blue or black||
Rolex|Sea-Dweller|1665|1967-1983|rolex_1570|Stainless steel|Black|Double Red Sea-Dweller|Acrylic crystal
Rolex|Daytona|6263|1969-1987|eta_7750|Stainless steel|Black acrylic|Big Red|Manual Valjoux 727 -- verify
Rolex|Datejust|16220|1988-2005|rolex_3135|Stainless steel|Smooth or engine turned||
Rolex|Milgauss|116400GV Z-Blue|2014-2023|rolex_3131|Oystersteel|Smooth|Z-Blue|Green sapphire crystal

Tudor|Black Bay Chrono|79360N|2021-|eta_7750|Stainless steel|Steel tachymeter||MT5813 base -- verify
Tudor|Black Bay GMT|79830RB|2018-|tudor_mt5602|Stainless steel|Blue and red|Pepsi BB|
Tudor|Black Bay Bronze|79250BA|2016-|tudor_mt5602|Bronze|Bronze||
Tudor|Black Bay Fifty-Eight 925|79010SG|2021-|tudor_mt5602|Silver|Taupe||
Tudor|Pelagos FXD|25707B/21|2021-|tudor_mt5602|Titanium|Blue||
Tudor|Prince Oysterdate|75090|1989-1996|eta_2824_2|Stainless steel|Smooth||
Tudor|Heritage Chrono|70330N|2010-2019|eta_7750|Stainless steel|Steel||
Tudor|1926|91350|2018-|sw200_1|Stainless steel|Smooth||

Omega|Speedmaster Professional|145.022|1968-1988|omega_1861|Stainless steel|Black tachymeter|Moonwatch|Cal 861, acrylic
Omega|Speedmaster Professional|105.012|1963-1968|omega_321|Stainless steel|Black tachymeter||Cal 321, acrylic
Omega|Speedmaster Racing|326.30.40|2017-|eta_7750|Stainless steel|Tachymeter||Co-Axial 9900 -- verify
Omega|Seamaster 300|233.30.41|2014-|omega_8500|Stainless steel|Black||Master Co-Axial 8400
Omega|Seamaster Planet Ocean|215.30.44|2016-|omega_8500|Stainless steel|Black ceramic||
Omega|Aqua Terra|231.10.42|2011-2017|omega_8500|Stainless steel|Smooth||
Omega|De Ville Prestige|424.10.40|2010-|omega_2500|Stainless steel|Smooth||Co-Axial 2500, lift 38
Omega|Seamaster Cosmic|166.026|1968-1975|omega_565|Stainless steel|Smooth||Cal 565
Omega|Constellation Pie Pan|168.005|1964-1969|omega_565|Stainless steel or gold|Smooth|Pie Pan|Cal 561
Omega|Geneve|166.0163|1970-1979|omega_565|Stainless steel|Smooth||Cal 1012 -- verify
Omega|Railmaster|2914-1|1957-1963|omega_565|Stainless steel|Smooth||Cal 284, acrylic
Omega|Flightmaster|145.026|1969-1977|omega_1861|Stainless steel|Smooth||Cal 911

Seiko|SKX011|SKX011J1|1996-2019|seiko_7s26|Stainless steel|Orange|Orange Monster kin|Hardlex
Seiko|Monster|SKX779 / SRP307|2000-|seiko_7s26|Stainless steel|Black|Black Monster|Hardlex
Seiko|Sumo|SPB321|2022-|seiko_6r15|Stainless steel|Blue||
Seiko|Marinemaster|SBDX001|2000-2015|seiko_9s65|Stainless steel|Black|MM300|Cal 8L35 -- verify
Seiko|Prospex Turtle|SRPE93|2020-|seiko_nh35|Stainless steel|Black||
Seiko|Prospex Baby Tuna|SRP637|2014-|seiko_nh35|Stainless steel|Black|Baby Tuna|
Seiko|Prospex Solar Tuna|SNE getting|2010-|seiko_nh35|Stainless steel|Black|Tuna|Quartz variants exist
Seiko|Presage Sharp Edged|SPB167|2021-|seiko_6r15|Stainless steel|Smooth||
Seiko|Presage Enamel|SPB045|2017-|seiko_6r15|Stainless steel|Smooth||
Seiko|Alpinist|SBDC087|2020-|seiko_6r15|Stainless steel|Smooth||
Seiko|5 Sports GMT|SSK001|2022-|seiko_nh35|Stainless steel|Blue and black||Cal 4R34
Seiko|King Seiko|SPB281|2022-|seiko_6r15|Stainless steel|Smooth||Cal 6R31
Seiko|Seiko 5|SNKE01|1996-2019|seiko_7s26|Stainless steel|Smooth||Hardlex
Seiko|Seiko 5|SNXS73|1996-2019|seiko_7s26|Stainless steel|Smooth||Hardlex
Seiko|Lord Matic|5606-7000|1968-1975|seiko_7009|Stainless steel|Smooth||Cal 5606
Seiko|Grand Seiko|SBGA211|2013-|seiko_9s65|Titanium|Smooth|Snowflake|Spring Drive -- not a lever escapement
Seiko|Grand Seiko|SBGR251|2015-|seiko_9s65|Stainless steel|Smooth||Cal 9S65
Seiko|Grand Seiko|SBGH273|2019-|seiko_9s85|Stainless steel|Smooth||Hi-Beat 36000

Citizen|Promaster Diver|NY0040|1990-|miyota_8215|Stainless steel|Black|Fugu|Cal 8203
Citizen|Tsuyosa|NJ0150|2022-|miyota_9015|Stainless steel|Smooth||Cal 8210
Citizen|Series 8|NB6011|2022-|miyota_9015|Stainless steel|Smooth||Cal 0950
Orient|Star Classic|RK-AU0002|2018-|orient_f6922|Stainless steel|Smooth||Cal F6N4
Orient|Ray II|FAA02004|2016-|orient_f6922|Stainless steel|Black||
Orient|Kamasu|RA-AA0004|2019-|orient_f6922|Stainless steel|Black||Sapphire
Orient|Bambino V2|FAC00009|2013-|orient_f6922|Stainless steel|Smooth||Mineral

Hamilton|Khaki Field Murph|H70605731|2021-|eta_c07|Stainless steel|Smooth|Murph|
Hamilton|Khaki Navy Frogman|H77805335|2019-|eta_c07|Titanium|Black||
Hamilton|Jazzmaster Open Heart|H32705141|2015-|eta_c07|Stainless steel|Smooth||
Hamilton|Intra-Matic Chrono|H38429130|2020-|eta_7750|Stainless steel|Smooth||Cal H-51
Hamilton|Pan Europ|H35405741|2011-|eta_c07|Stainless steel|Blue||
Tissot|Heritage Visodate|T019.430|2010-|eta_2836_2|Stainless steel|Smooth||
Tissot|Chemin des Tourelles|T099.407|2016-|eta_c07|Stainless steel|Smooth||
Tissot|PRX 35mm|T137.207|2022-|eta_c07|Stainless steel|Smooth||
Certina|DS PH200M|C036.407|2018-|eta_c07|Stainless steel|Black||
Certina|DS Action Diver|C032.607|2017-|eta_c07|Stainless steel|Blue||
Mido|Ocean Star Tribute|M026.830|2020-|eta_c07|Stainless steel|Blue||
Mido|Multifort|M005.430|2015-|eta_c07|Stainless steel|Smooth||
Rado|Captain Cook 42|R32105153|2019-|eta_c07|Stainless steel|Blue ceramic||
Longines|Legend Diver|L3.774.4|2018-|eta_a31|Stainless steel|Internal rotating||
Longines|Heritage Military|L2.819.4|2018-|eta_a31|Stainless steel|Smooth||
Longines|Master Collection|L2.628.4|2010-|eta_2892a2|Stainless steel|Smooth||Cal L619
Longines|Conquest V.H.P.|L3.716|2017-|eta_a31|Stainless steel|Smooth||Quartz variants exist

Oris|Big Crown ProPilot|01 751 7761|2014-|sw200_1|Stainless steel|Smooth||Cal 751
Oris|Aquis Small Second|01 743 7733|2016-|sw200_1|Stainless steel|Blue ceramic||
Oris|Divers Sixty-Five 40mm|01 733 7707|2018-|sw200_1|Stainless steel|Black||
Sinn|556 I|556.010|2010-|sw200_1|Stainless steel|Smooth||
Sinn|104 St Sa A|104.010|2013-|sw200_1|Stainless steel|Smooth||
Sinn|U50|1050.010|2020-|sw200_1|Submarine steel|Black||
Sinn|EZM 3|603.010|2002-|sw200_1|Stainless steel|Black||
Stowa|Flieger Klassik 36|FL36|2015-|eta_2824_2|Stainless steel|Smooth||
Stowa|Marine Original|MO40|2005-|eta_6497_1|Stainless steel|Smooth||
Laco|Augsburg|861690|2010-|miyota_8215|Stainless steel|Smooth||Cal Laco 24
Laco|Pilot Watch Original|862101|2015-|eta_2824_2|Stainless steel|Smooth||
Damasko|DA36|DA36|2010-|eta_2824_2|Ice-hardened steel|Smooth||
Muhle Glashutte|Seebataillon|M1-41-03|2015-|sw200_1|Stainless steel|Black||
Nomos|Tangente 38|165|2015-|nomos_alpha|Stainless steel|Smooth||Cal Alpha
Nomos|Club Campus|736|2018-|nomos_alpha|Stainless steel|Smooth||
Junghans|Max Bill Automatic|027/4700|2010-|eta_2824_2|Stainless steel|Smooth||Plexiglass, cal J800.1
Meistersinger|Neo|NE401|2014-|sw200_1|Stainless steel|Smooth||Single hand
Glashutte Original|SeaQ|1-39-11|2019-|eta_2892a2|Stainless steel|Black||Cal 39-11 -- verify

Christopher Ward|C65 Trident|C65-41ADA1|2018-|sw200_1|Stainless steel|Black||
Christopher Ward|C63 Sealander GMT|C63-39ADA1|2021-|sw200_1|Stainless steel|Smooth||
Baltic|Aquascaphe Dual Crown|AQ-DC|2021-|miyota_9015|Stainless steel|Internal rotating||
Baltic|HMS 002|HMS002|2017-|miyota_8215|Stainless steel|Smooth||Cal 8N24
Baltic|Bicompax 002|BC002|2019-|st19|Stainless steel|Smooth||Seagull ST1901
Farer|Aqua Compressor|Endeavour|2019-|sw200_1|Stainless steel|Internal rotating||
Halios|Seaforth|Seaforth IV|2021-|sw200_1|Stainless steel|Smooth||
Monta|Oceanking|OK-01|2018-|sw200_1|Stainless steel|Black ceramic||
Lorier|Neptune Series IV|NEP4|2022-|miyota_9015|Stainless steel|Black||Acrylic crystal
Lorier|Falcon Series III|FAL3|2021-|miyota_9015|Stainless steel|Smooth||Acrylic crystal
Traska|Freediver|FD-2|2021-|miyota_9015|Stainless steel|Black||
Zelos|Mako|Mako 300M|2020-|seiko_nh35|Titanium|Ceramic||
Islander|Mount Hope|ISL-01|2019-|seiko_nh35|Stainless steel|Black||
Vaer|A5 Field|A5|2020-|seiko_nh35|Stainless steel|Smooth||
Marathon|GSAR|WW194006|1990-|seiko_nh35|Stainless steel|Black||Cal Sellita SW200 in some runs
Nivada Grenchen|Antarctic|68000|2021-|sw200_1|Stainless steel|Smooth||
Yema|Superman|YSUP|2019-|seiko_nh35|Stainless steel|Rotating||
Serica|4512|4512|2021-|sw200_1|Stainless steel|Smooth||
Furlan Marri|Disco Volante|MR-Blue|2021-|st19|Stainless steel|Smooth||Seagull ST1901 mecaquartz variants exist

San Martin|Sub Homage|SN0007|2019-|seiko_nh35|Stainless steel|Ceramic||NH35 or PT5000 options
San Martin|Diver 62MAS|SN0121|2021-|pt5000|Stainless steel|Ceramic||
Steeldive|SD1970|SD1970|2020-|seiko_nh35|Stainless steel|Ceramic||
Heimdallr|Sharkmaster 300|SM300|2019-|seiko_nh35|Stainless steel|Black||
Proxima|MM300 Homage|PX1682|2020-|seiko_nh35|Stainless steel|Black||
Pagani Design|PD-1617|PD1617|2021-|seiko_nh35|Stainless steel|Ceramic||
Cronos|Sub Homage|L6011|2020-|pt5000|Stainless steel|Ceramic||
Baltany|Field Watch|S2033|2021-|seiko_nh35|Stainless steel|Smooth||
Sea-Gull|1963 38mm|D304-38|2010-|st19|Stainless steel|Smooth||Acrylic
Sea-Gull|Ocean Star|816.523|2015-|st2130|Stainless steel|Black||
Merkur|Vintage Chronograph|MK-CH|2019-|st19|Stainless steel|Smooth||

Vostok|Amphibia SE|420|2015-|vostok_2416|Stainless steel|Rotating||Acrylic
Vostok|Komandirskie 811|811|1990-|vostok_2409|Chrome plated base metal|Rotating||Acrylic
Vostok|Amphibia Scuba Dude|710|1990-|vostok_2416|Stainless steel|Rotating|Scuba Dude|Acrylic
Raketa|Big Zero|2609.HA|1980-|raketa_2609|Chrome plated base metal|Smooth|Big Zero|Acrylic
Raketa|Copernicus|2609|1980-|raketa_2609|Chrome plated base metal|Smooth|Copernicus|Acrylic
Poljot|Sturmanskie Gagarin|2609|1960-|poljot_2609|Chrome plated base metal|Smooth|Gagarin|Acrylic
Poljot|Okean Chronograph|3133|1980-|poljot_3133|Stainless steel|Tachymeter||Acrylic

IWC|Mark XVIII|IW327001|2016-2022|iwc_30110|Stainless steel|Smooth||Cal 35111 in some
IWC|Big Pilot|IW500401|2002-2016|iwc_52010|Stainless steel|Smooth||Cal 51111
IWC|Portugieser Chrono|IW371446|2000-2019|eta_7750|Stainless steel|Smooth||Cal 79350
IWC|Aquatimer|IW329001|2014-|iwc_30110|Stainless steel|Internal rotating||
IWC|Pilot Chronograph|IW377709|2012-|iwc_79350|Stainless steel|Smooth||
Jaeger-LeCoultre|Reverso Classic|Q2548440|2016-|jlc_889|Stainless steel|Smooth||Cal 965
Jaeger-LeCoultre|Master Control Date|Q1548530|2017-|jlc_889|Stainless steel|Smooth||Cal 899
Jaeger-LeCoultre|Polaris|Q9008471|2018-|jlc_889|Stainless steel|Internal rotating||Cal 898
Zenith|Chronomaster El Primero|03.2040|2010-|zenith_400|Stainless steel|Tachymeter||
Zenith|Defy Classic|95.9000|2018-|zenith_400|Titanium|Smooth||Cal Elite 670
Panerai|Luminor Marina|PAM01312|2019-|panerai_p3000|Stainless steel|Smooth||Cal P.9010
Panerai|Radiomir|PAM00992|2018-|panerai_p3000|Stainless steel|Smooth||Cal P.6000
Panerai|Luminor Base 8 Days|PAM00560|2013-|panerai_p3000|Stainless steel|Smooth||Cal P.5000
Breitling|Navitimer 01|AB0121|2011-|eta_7750|Stainless steel|Slide rule||Cal B01 in-house
Breitling|Superocean Heritage|A10380|2017-|eta_2892a2|Stainless steel|Black ceramic||Cal B20 / Tudor MT5612
Breitling|Chronomat|AB0134|2020-|eta_7750|Stainless steel|Rider tabs||Cal B01
TAG Heuer|Carrera Calibre 5|WAR211A|2013-|sw200_1|Stainless steel|Smooth||ETA 2824 / SW200
TAG Heuer|Aquaracer Calibre 5|WAY201A|2015-|sw200_1|Stainless steel|Black ceramic||
TAG Heuer|Monaco Calibre 11|CAW211P|2015-|eta_7750|Stainless steel|Smooth||
Bell & Ross|BR 03-92|BR0392|2010-|sw200_1|Ceramic|Smooth||
Ball|Engineer II|NM2026C|2012-|eta_2824_2|Stainless steel|Smooth||
Alpina|Startimer Pilot|AL-525|2015-|sw200_1|Stainless steel|Smooth||
Frederique Constant|Classics Index|FC-303|2012-|sw200_1|Stainless steel|Smooth||
Maurice Lacroix|Aikon Automatic|AI6008|2018-|sw200_1|Stainless steel|Smooth||
Raymond Weil|Freelancer|2731-ST|2015-|sw200_1|Stainless steel|Smooth||
Bulova|Lunar Pilot Auto|96B403|2023-|eta_7750|Stainless steel|Tachymeter||Sellita SW510
Bulova|Oceanographer Devil Diver|96B350|2020-|miyota_8215|Stainless steel|Rotating|Devil Diver|
Timex|Marlin Automatic|TW2T22800|2018-|miyota_8215|Stainless steel|Smooth||Acrylic
Victorinox|INOX Mechanical|241836|2018-|sw200_1|Stainless steel|Smooth||
Doxa|SUB 300T|879.10|2020-|sw200_1|Stainless steel|Rotating|Professional if orange|
Squale|1521|1521-026|2015-|eta_2824_2|Stainless steel|Black||
Steinhart|Ocean One Vintage|103-0654|2012-|eta_2824_2|Stainless steel|Black||
Blancpain|Fifty Fathoms Bathyscaphe|5000-1110|2013-|blancpain_1151|Stainless steel|Black ceramic||Cal 1315
Audemars Piguet|Royal Oak 15500|15500ST|2019-|jlc_920|Stainless steel|Octagonal||Cal 4302
Cartier|Santos Medium|WSSA0029|2018-|cartier_1904|Stainless steel|Smooth||Cal 1847 MC
Cartier|Tank Must|WSTA0041|2021-|cartier_1904|Stainless steel|Smooth||Quartz variants exist
Eterna|KonTiki Date|1220.41|2013-|eterna_3902|Stainless steel|Smooth||
Oris|ProPilot X Calibre 400|01 400 7778|2021-|oris_400|Titanium|Smooth||
"""

RAW = RAW + RAW2


def _parse():
    out = []
    for line in RAW.strip().splitlines():
        line = line.strip()
        if not line or line.count("|") < 7:
            continue
        b = [x.strip() for x in line.split("|")]
        b += [""] * (9 - len(b))
        crystal = "Sapphire"
        note = b[8]
        low = note.lower()
        if "acrylic" in low:
            crystal = "Acrylic"
        elif "hesalite" in low:
            crystal = "Hesalite"
        elif "hardlex" in low:
            crystal = "Hardlex"
        elif "mineral" in low:
            crystal = "Mineral"
        out.append(Reference(brand=b[0], model=b[1], reference=b[2], years=b[3],
                             caliber_key=b[4], material=b[5], bezel=b[6],
                             crystal=crystal, nickname=b[7], notes=note))
    return out


REFERENCES: List[Reference] = _parse()


def _norm(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def find(query: str) -> List[Reference]:
    """Search references by brand, model, reference number or nickname."""
    q = _norm(query)
    if not q:
        return list(REFERENCES)
    return [r for r in REFERENCES
            if q in _norm(r.brand + r.model + r.reference + r.nickname)]


def for_model(brand: str, model: str) -> List[Reference]:
    """Every catalogued reference for one model, for populating a dropdown."""
    qb, qm = _norm(brand), _norm(model)
    out = [r for r in REFERENCES
           if _norm(r.brand) == qb and (qm in _norm(r.model) or _norm(r.model) in qm)]
    out.sort(key=lambda r: r.years, reverse=True)
    return out


def lookup(reference: str, brand: str = "") -> Optional[Reference]:
    """
    Exact reference lookup, falling back to the Rolex decoder so uncatalogued
    references still populate material and bezel.
    """
    q = _norm(reference)
    if not q:
        return None
    for r in REFERENCES:
        if _norm(r.reference) == q:
            return r
    for r in REFERENCES:
        if q in _norm(r.reference):
            return r
    if not brand or _norm(brand) == "rolex":
        return decode_rolex(reference)
    return None


def brands() -> List[str]:
    return sorted({r.brand for r in REFERENCES})


def models_for(brand: str) -> List[str]:
    qb = _norm(brand)
    return sorted({r.model for r in REFERENCES if _norm(r.brand) == qb})
