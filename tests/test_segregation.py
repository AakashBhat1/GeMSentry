"""
Regression tests for relevance segregation guards (BE-30).

Two defects let junk into Shortlisted, both found by auditing the live corpus:

  1. Plural tolerance inverted on keywords already ending in 's': the pattern
     `\\bups?\\b` made the real final letter optional, so keyword "ups" matched
     the bare word "up" — a 46-item gym bid ("Sit up bench") scored as Power
     Supply.
  2. Item boundaries were lost: title + primary_item + item_category were
     concatenated into one string, so a single incidental match inside a 30-item
     omnibus bid counted as fully as a match in the bid's actual subject.
     ("Bagpipe Plastic Drone" -> strong Drone match.)

All fixtures below are real titles from tenders/downloads.

Run:  python tests/test_segregation.py   (or pytest tests/test_segregation.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402

CFG = scraper.load_scoring_config()
PROFILE = scraper.load_company_profile()
BASE_SIGNALS = {"est_value_inr": 100000, "buyer_org": None, "buyer_dept": None,
                "consignee_state": None, "mii_required": "unknown",
                "mse_pref": "unknown"}


def relevance(title, item_category=None, primary_item=None):
    """Return (subscore, business_line_id) for a bid's content."""
    signals = dict(BASE_SIGNALS)
    signals["item_category"] = item_category or title
    signals["primary_item"] = primary_item or (title.split(",")[0] if title else None)
    _, fb, bl = scraper.compute_fit_score(
        {}, signals, {"verdict": "unknown"}, PROFILE, CFG,
        card_meta={"title": title})
    sub = next((c["subscore"] for c in fb if c.get("criterion") == "relevance"), 0)
    return sub, (bl or {}).get("id")


# --- Real omnibus bids that used to score as strong matches -----------------

GYM = ("Treadmill,Cross trainer,Multi Gym six station,Climber Steps,Smith machine "
       "with squat rack,Flat bench,Sit up bench,Multi adjustable bench,Olympic flat "
       "incline and decline bench,Seated calf extension bench,Dumbbell,03 tier "
       "dumbbell rack,Barbell stand,Weight plate rack,Incline lever row,Standing twister")

BAGPIPE = ("Bagpipe Chanter Reed 2MM Copper,Bagpipe Practice Chanter Reed,Bagpipe "
           "Plastic Drone,Practice Stick,Bass Drum 28 Inch with ITBP Logo,Tenor Drum "
           "18 Inches with ITBP Logo,Side Drum 14 Inches with ITBP Logo,Bagpipe Brooch "
           "ITBP Steel,Coat with Zari Decoration OCM,Belt Waist White,Shoulder Cord")

BOOKS = ("Operation Sindoor Before and Beyond,Operation Sindoor India Shadow War,"
         "Operation Sindoor,Kashmir Paheli,Military Inc,Drone Tech for Beginners,"
         "Drone Development,Backpack to Rucksack,Lets Talk,Fighting to the End,"
         "The Mossad,Artifical Intelligence,Thinking Fast and slow")

PAINT = ("H1 A 8010-000575 PAINT FINISHING MATT RFU AIR DRYING B,H1A 8010-000229 "
         "PAINT RFU MARKING BLACK,H1 A 8010-000243 PAINT RFU MARKING WHITE,H1 A "
         "8010-000252 PAINT RFU MATT FINISH OLIVE GREEN,H1 A 8010-000114 PAINT RFU "
         "ALUMINIUM WATER RESIS,H1 8010-007488 PAINT RFU AD BR SPR SYN ENA SEA GREEN,"
         "H1 A 8010-000300 PAINT RFU AZURE BLUE,H1 A 8010-000400 PAINT THINNER,"
         "H1 A 8010-000500 PAINT PRIMER,H1 A 8010-000600 PAINT VARNISH")

# --- Real bids that must keep matching --------------------------------------

DRONE_KIT = ("UAV FRAME,INTERNAL COMBUSTION ENGINE,BLDC MOTORS,BATTERY,FLIGHT "
             "CONTROLLER,TRANSMITTER,RECEIVER,PROPELLER,GIMBAL,LANDING GEAR")

CCTV_KIT = ("CCTV CAMERA BULLET,CCTV CAMERA PTZ,HARD DISK,UPS,NETWORK VIDEO "
            "RECORDER,24 core OFC cable,SWITCH,RACK,MONITOR,POWER SUPPLY")

ARDUINO_KIT = ("Arduino Uno,LCDDisplay,Relay Module,DC Motor,12V Relay 30 Amp,"
               "Elctric Bd,Arduino Uno Programming")


# --- The plural-inversion bug -----------------------------------------------

def test_keyword_ending_in_s_does_not_match_singular():
    """"ups" must not match the bare word "up" (gym bid regression)."""
    _, fb, bl = scraper.compute_fit_score(
        {}, dict(BASE_SIGNALS, item_category="Sit up bench", primary_item="Sit up bench"),
        {"verdict": "unknown"}, PROFILE, CFG, card_meta={"title": "Sit up bench"})
    sub = next(c["subscore"] for c in fb if c.get("criterion") == "relevance")
    assert sub == 0, f"'Sit up bench' matched a business line: {bl}"


def test_plural_tolerance_still_works_for_normal_keywords():
    """Singular keywords must still hit their plural ("connector" -> "connectors")."""
    sub, bl = relevance("Supply of Connectors and Cables")
    assert sub > 0 and bl == "components"


def test_real_ups_still_matches():
    sub, bl = relevance("Online UPS 10 KVA with battery bank")
    assert sub > 0, "a genuine UPS bid must still match"


# --- The omnibus dilution guard ---------------------------------------------

def test_gym_bid_is_not_power_supply():
    sub, bl = relevance(GYM)
    assert sub == 0, f"gym equipment scored {sub} as {bl}"


def test_bagpipe_drone_is_not_a_drone_bid():
    """A bagpipe's pipe is literally called a drone — the classic homonym."""
    sub, bl = relevance(BAGPIPE)
    assert sub < 1.0, f"bagpipe bid scored a full match as {bl}"


def test_book_list_is_not_a_drone_bid():
    """Library procurement of books *about* drones is not a drone tender."""
    sub, bl = relevance(BOOKS)
    assert sub < 1.0, f"book list scored a full match as {bl}"


def test_paint_list_is_not_ai_it():
    """"Azure" is a paint colour, not the cloud platform."""
    sub, bl = relevance(PAINT)
    assert sub == 0, f"paint list scored {sub} as {bl}"


# --- Genuine bundles must survive -------------------------------------------

def test_genuine_drone_bundle_keeps_full_match():
    sub, bl = relevance(DRONE_KIT)
    assert sub >= 1.0 and bl == "drone"


def test_genuine_cctv_bundle_keeps_full_match():
    sub, bl = relevance(CCTV_KIT)
    assert sub >= 1.0


def test_small_genuine_kit_is_not_diluted():
    """
    A 7-item Arduino/relay kit is a real components bid. The guard must not
    fire on small bundles that merely lead with an item we do not keyword.
    """
    sub, bl = relevance(ARDUINO_KIT)
    assert sub > 0, "genuine small electronics kit was diluted away"
    assert bl == "components"


def test_single_item_bid_never_diluted():
    sub, bl = relevance("Rotorcraft Drone")
    assert sub >= 1.0 and bl == "drone"


# --- Lone-acronym guard -----------------------------------------------------

def test_lone_acronym_is_not_relevance():
    """
    "CRM" on a chemicals bid is Certified Reference Material, not customer
    relationship management. A single short acronym that is not a declared
    strong_keyword is coincidence, not evidence.
    """
    sub, bl = relevance(
        "Buffer Solution pH 4.01",
        item_category=("Buffer Solution pH 4.01 , Sodium Chloride CRM , "
                       "Potassium Hydrogen Diiodate , Sulfate standard solution"))
    assert sub == 0, f"lone 'CRM' match scored {sub} as {bl}"


def test_strong_acronyms_are_exempt():
    """Acronyms that are unambiguous in this domain are declared strong."""
    for title in ("UAV for survey", "Online UPS 10 KVA with battery bank"):
        sub, bl = relevance(title)
        assert sub > 0, f"{title!r} lost its match"


def test_multi_word_single_hit_still_weak():
    """The guard targets short acronyms only, not ordinary single matches."""
    sub, bl = relevance("Supply of Potentiometer assembly")
    assert sub > 0


# --- Business-line tie-break ------------------------------------------------

def test_line_chosen_by_lead_item_not_profile_order():
    """
    A CCTV bid that also lists a UPS and a power supply must file under AI/IT.
    Business lines used to be scanned in profile order with a strict `>`, so
    whichever line reached 1.0 first won — putting camera bids under Power
    Supply purely because power_supply is declared earlier.
    """
    sub, bl = relevance(CCTV_KIT)
    assert bl == "ai_it", f"CCTV bundle filed under {bl}"
    assert sub >= 1.0


def test_surveillance_bundle_files_under_ai_it():
    """Real tender that was filed under Power Supply before the tie-break fix."""
    title = ("PTZ Camera,PTZ Camera,PTZ Camera,PTZ Camera,PTZ Camera,PTZ Camera,"
             "PTZ Camera,AI Bases SW,AI Bases SW,AI Bases SW,AI Bases SW,"
             "Licenses OS,Licenses OS,Licenses OS,System Hardware")
    sub, bl = relevance(title)
    assert bl == "ai_it", f"surveillance bundle filed under {bl}"
    assert sub > 0, "surveillance bundle lost its match entirely"


# --- Splitter ---------------------------------------------------------------

def test_promoted_acronyms_match_on_their_own():
    """
    pcb / plc / fuse / iot / gis are unambiguous in this company's domain, so
    they are declared strong_keywords and count as evidence alone. Without
    that they hit the lone-acronym guard and genuine bids scored zero:
    "H.265 Codec PCB", "SWITCH FUSE UNIT FN400", "IOT EDGE GATEWAY".
    """
    for text in ("IOT EDGE GATEWAY",
                 "SWITCH FUSE UNIT FN400",
                 "RF AMPLIFIER PCB / TEST BOARD 75 MHZ",
                 "GIS related Survey Services"):
        sub, bl = relevance(text)
        assert sub >= 1.0, f"{text!r} scored {sub} as {bl}"


def test_undeclared_acronyms_are_still_coincidence():
    """Promoting five terms must not disarm the guard for the rest."""
    sub, bl = relevance("Certified Reference Material CRM for laboratory use")
    assert sub == 0, f"lone 'CRM' scored {sub} as {bl}"


def test_business_line_cites_only_its_own_keywords():
    """
    Cross-line corroboration pools hits from every business line to decide the
    score. The reported evidence must still be the winning line's own
    keywords: crediting Drone / UAV with "software" is a false audit trail.
    """
    _, _, bl = scraper.compute_fit_score(
        {}, dict(BASE_SIGNALS, item_category="CRIME ANALYTICS AND MAPPING SOFTWARE",
                 primary_item="CRIME ANALYTICS AND MAPPING SOFTWARE"),
        {"verdict": "unknown"}, PROFILE, CFG,
        card_meta={"title": "CRIME ANALYTICS AND MAPPING SOFTWARE"})
    assert bl is not None
    line = next(l for l in PROFILE["business_lines"] if l["label"] == bl["label"])
    own = {k.lower() for k in line["keywords"]}
    foreign = [k for k in bl["matched_keywords"] if k.lower() not in own]
    assert not foreign, f"{bl['label']} credited with foreign keywords: {foreign}"


def test_split_bid_items():
    assert scraper.split_bid_items("A,B,C", None, None) == ["A", "B", "C"]
    assert scraper.split_bid_items("Single Item", None, None) == []
    assert scraper.split_bid_items("", None, None) == []
    # richer source wins (title is often truncated)
    got = scraper.split_bid_items("A,B", None, "A , B , C , D")
    assert got == ["A", "B", "C", "D"]


def test_guard_is_configurable():
    fit = CFG.get("fit", {})
    assert int(fit.get("omnibus_min_items", 8)) >= 2
    assert 0.0 <= float(fit.get("omnibus_min_match_ratio", 0.34)) <= 1.0
    assert scraper.validate_scoring_config(CFG) is None


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
