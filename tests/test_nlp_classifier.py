"""Regression tests for tender domain classification.

The classifier decides which folder a downloaded RFP is filed under, so the
bar is: never file a bid confidently into the wrong domain. Every fixture
below is a real title from the live corpus.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import nlp_classifier as N  # noqa: E402
from gemsentry.textmatch import count_hits, keyword_hit  # noqa: E402


def classify(title, keyword="", **extra):
    return N.classify_tender({"title": title, "keyword": keyword, **extra})


class TestKeywordHit(unittest.TestCase):
    def test_matches_whole_words_only(self):
        self.assertTrue(keyword_hit("drone", "surveillance drone for defence"))
        self.assertFalse(keyword_hit("ai", "maintenance of the building"))
        self.assertFalse(keyword_hit("ai", "repair of air conditioner"))
        self.assertFalse(keyword_hit("rpa", "supply of tarpaulin sheets"))
        self.assertFalse(keyword_hit("soc", "associated civil works"))
        self.assertFalse(keyword_hit("erp", "enterprise wide rollout"))

    def test_matches_standalone_acronym(self):
        self.assertTrue(keyword_hit("ai", "ai based analytics platform"))
        self.assertTrue(keyword_hit("erp", "erp implementation"))

    def test_tolerates_trailing_plural(self):
        self.assertTrue(keyword_hit("connector", "supply of connectors"))
        self.assertTrue(keyword_hit("drone", "supply of drones"))

    def test_keyword_ending_in_s_is_exact(self):
        """'ups' must not match the bare word 'up' (the Sit-up-bench defect)."""
        self.assertFalse(keyword_hit("ups", "sit up bench for gym"))
        self.assertTrue(keyword_hit("ups", "online ups 10 kva"))

    def test_matches_multi_word_phrases(self):
        self.assertTrue(keyword_hit("unmanned aerial vehicle", "one unmanned aerial vehicle"))
        self.assertFalse(keyword_hit("unmanned aerial vehicle", "unmanned ground vehicle"))

    def test_empty_inputs_are_false(self):
        for kw, text in (("", "abc"), ("abc", ""), ("", ""), (None, "abc"), ("abc", None)):
            self.assertFalse(keyword_hit(kw, text))

    def test_regex_metacharacters_are_literal(self):
        self.assertTrue(keyword_hit("c++", "supply of c++ compiler"))
        self.assertFalse(keyword_hit("a.c", "abc"))

    def test_count_hits(self):
        self.assertEqual(count_hits("drone", "drone and drones and drone"), 3)
        self.assertEqual(count_hits("ai", "maintenance repair air paint chair"), 0)
        self.assertEqual(count_hits("", "abc"), 0)


class TestSubstringFalsePositives(unittest.TestCase):
    """The defect that put 575 of 2555 real bids into AI & Data Science."""

    NOT_AI = [
        "Maintenance of Ancillary Areas of Artificial Turf Football Ground",
        "Replacement (Repair and Maintenance) of artificial turf grass volleyball court",
        "Camouflage Synthitic Net 40 x 50, Combat Harness, Artificial Grass",
        "Diethyl Ether Solvent Bottle of 500 ml, Povidone Iodine Germicidal",
        "ABC Type Fire Extinguisher Capacity 09 Kg",
        "Banner Large Size 12 x 20 feet, Memento for Doctors",
        "Supply of Chairs for Office",
        "Training of staff in first aid",
    ]

    def test_none_are_classified_as_ai(self):
        for title in self.NOT_AI:
            self.assertNotEqual(classify(title)["domain"], "ai_and_data_science", title)

    def test_noisy_search_keyword_cannot_carry_a_bid(self):
        """The keyword field records the query, not the bid; 31% hold many terms.

        The hint is worth 1.0, below MIN_SCORE, so it can never classify a bid
        on its own -- these titles must not be dragged into an AI bucket by it.
        """
        for title in self.NOT_AI:
            result = classify(title, keyword="ARTIFICIAL INTELLIGENCE, MACHINE LEARNING, IOT")
            self.assertNotEqual(result["domain"], "ai_and_data_science", title)

    def test_painting_work_is_facility_maintenance(self):
        """Not every reclassification is a rejection -- this one is simply right."""
        self.assertEqual(classify("Painting work of boundary wall")["domain"],
                         "civil_and_facility_maintenance")

    def test_tarpaulin_is_not_robotic_process_automation(self):
        self.assertNotEqual(classify("Supply of Tarpaulins HDPE")["domain"],
                            "automation_and_robotics")

    def test_millilitres_are_not_machine_learning(self):
        for title in ("Supply of 500 ml Diethyl Ether", "Reagent bottle 100 ml"):
            self.assertNotEqual(classify(title)["domain"], "ai_and_data_science", title)


class TestTruePositives(unittest.TestCase):
    """Fixing false positives must not silence the real matches."""

    CASES = [
        ("Surveillance Drone / Unmanned Aerial Vehicle for Defence", "automation_and_robotics"),
        ("Repair of Quadcopter for High Altitude Surveillance", "automation_and_robotics"),
        ("Custom Bid for Services - Whatsapp Chatbot", "ai_and_data_science"),
        ("Data Analytics Service Implementation", "ai_and_data_science"),
        ("Database Management System Software", "cloud_and_it_infrastructure"),
        ("Online UPS 10 KVA with battery bank", "electronics_and_electrical"),
        ("CCTV IP Camera with video analytics", "biometrics_and_surveillance"),
        ("Vulnerability Assessment and Penetration Testing VAPT", "cybersecurity_and_network_security"),
    ]

    def test_genuine_bids_still_classify(self):
        for title, expected in self.CASES:
            self.assertEqual(classify(title)["domain"], expected, title)


class TestBusinessLineCoverage(unittest.TestCase):
    """The folder taxonomy must cover the company's actual business lines.

    Solar and Smart Metering are live business lines but had no domain, so
    those bids -- which do pass the fit gate and get downloaded -- were all
    filed under "uncategorized". Titles below are real downloaded tenders.
    """

    CASES = [
        ("Supply and Installation of 100 KW Rooftop Solar PV Plant", "solar_and_renewable"),
        ("SITC of Solar Street Light with MPPT charge controller", "solar_and_renewable"),
        ("Three Phase Smart Energy Meter with DLMS protocol", "smart_metering_and_ami"),
        ("CT Operated Meter for HT consumers", "smart_metering_and_ami"),
        ("Pin Attenuator (A1)", "rf_and_communication"),
        ("Antenna 10 to 12 DBI 400 to 500 Mhz", "rf_and_communication"),
        ("Wall Mount Speaker", "audio_video_and_display"),
        ("Chairman Unit Microphone", "audio_video_and_display"),
        ("SITC of Chip on Board LED Video Wall", "audio_video_and_display"),
    ]

    def test_new_domains_classify_real_downloaded_bids(self):
        for title, expected in self.CASES:
            self.assertEqual(classify(title)["domain"], expected, title)

    def test_every_business_line_has_a_home(self):
        """No business line should have to fall back to uncategorized."""
        for title in ("Surveillance Drone for defence", "Online UPS 10 KVA",
                      "CCTV IP Camera", "Rooftop Solar PV plant",
                      "Smart Energy Meter DLMS", "Supply of cable and connectors"):
            self.assertNotEqual(classify(title)["domain"], "uncategorized_general", title)

    def test_plural_keyword_matches_singular(self):
        """The keyword is "cables"; 33 downloaded bids say "cable"."""
        self.assertEqual(classify("RG 9 Marine Cable for TVRO")["domain"],
                         "electronics_and_electrical")

    def test_tv_does_not_match_inside_tvro(self):
        self.assertNotEqual(classify("RG 9 Marine Cable for TVRO")["domain"],
                            "audio_video_and_display")


class TestPdfTextIsCorroborationOnly(unittest.TestCase):
    """RFP body text may support a subject match; it may never create one.

    An RFP body is mostly boilerplate (terms, delivery, inspection, warranty)
    that names many domains in passing. Left uncapped and unqualified it filed
    "Toilet Paper Roll type 2" under Medical & Laboratory.
    """

    BOILERPLATE = ("terms and conditions delivery inspection warranty "
                   "medical device laboratory equipment incubator autoclave "
                   "server database cable connector power supply ups")

    def test_body_text_alone_cannot_classify(self):
        for title in ("Toilet Paper Roll type 2", "Dig in post 1 point 6 mtr"):
            result = N.classify_tender({"title": title}, pdf_text=self.BOILERPLATE)
            self.assertEqual(result["domain"], "uncategorized_general", title)

    def test_body_text_corroborates_a_real_subject_match(self):
        bare = N.classify_tender({"title": "Online UPS 10 KVA"})
        with_pdf = N.classify_tender({"title": "Online UPS 10 KVA"},
                                     pdf_text="ups battery rectifier power supply cable")
        self.assertEqual(with_pdf["domain"], "electronics_and_electrical")
        self.assertGreater(with_pdf["raw_score"], bare["raw_score"])

    def test_body_contribution_is_capped_below_the_title(self):
        """20 keywords x 3.0 must not outvote a 3.5 title match."""
        flooded = " ".join(["medical device"] * 40 + ["incubator"] * 40 + ["autoclave"] * 40)
        result = N.classify_tender({"title": "Online UPS 10 KVA"}, pdf_text=flooded)
        self.assertEqual(result["domain"], "electronics_and_electrical")


class TestConfidenceAndThresholds(unittest.TestCase):
    def test_weak_evidence_falls_back_to_uncategorized(self):
        result = classify("Skid mounted HP Dosing System")
        self.assertEqual(result["domain"], "uncategorized_general")
        self.assertEqual(result["confidence"], 0.0)

    def test_min_confidence_is_honoured(self):
        title = "Data Analytics Service Implementation"
        self.assertEqual(N.classify_tender({"title": title}, min_confidence=0.0)["domain"],
                         "ai_and_data_science")
        self.assertEqual(N.classify_tender({"title": title}, min_confidence=0.99)["domain"],
                         "uncategorized_general")

    def test_a_clear_winner_scores_a_higher_margin_than_a_contested_bid(self):
        clear = classify("Supply of quadcopter drone uav")
        contested = classify("Repair of Quadcopter for High Altitude Surveillance")
        self.assertGreater(clear["margin"], contested["margin"])
        self.assertGreater(clear["confidence"], contested["confidence"])

    def test_product_term_outranks_a_generic_qualifier(self):
        """"quadcopter" identifies the goods; "surveillance" only qualifies them."""
        result = classify("Repair of Quadcopter for High Altitude Surveillance")
        self.assertEqual(result["domain"], "automation_and_robotics")
        self.assertGreater(result["all_domain_scores"]["automation_and_robotics"],
                           result["all_domain_scores"]["biometrics_and_surveillance"])

    def test_result_shape(self):
        result = classify("Surveillance Drone")
        for key in ("domain", "domain_label", "confidence", "raw_score",
                    "margin", "runner_up_score", "matched_reasons", "all_domain_scores"):
            self.assertIn(key, result)
        self.assertIn(result["domain"], N.CANONICAL_DOMAINS)

    def test_empty_tender_is_uncategorized(self):
        for tender in ({}, {"title": ""}, {"title": None, "keyword": None}):
            self.assertEqual(N.classify_tender(tender)["domain"], "uncategorized_general")

    def test_category_fields_contribute(self):
        """A bare title with a telling item_category should still classify."""
        result = N.classify_tender({"title": "Custom Bid for Goods",
                                    "item_category": "CCTV Surveillance Camera"})
        self.assertEqual(result["domain"], "biometrics_and_surveillance")

    def test_every_domain_key_has_a_label(self):
        for key, info in N.CANONICAL_DOMAINS.items():
            self.assertTrue(info.get("label"), key)
            self.assertIsInstance(info.get("keywords"), list)


if __name__ == "__main__":
    unittest.main()
