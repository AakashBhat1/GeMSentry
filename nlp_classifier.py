"""
GeMSentry NLP Tender Segregation & Classification Engine.

Categorizes tender RFPs into semantic canonical industry domains based on
title, item category, primary item, business line, and PDF content text.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from gemsentry.textmatch import count_hits, keyword_hit

logger = logging.getLogger("gemsentry.nlp")

# Canonical Industry Domains Taxonomy
CANONICAL_DOMAINS: Dict[str, Dict[str, Any]] = {
    "ai_and_data_science": {
        "label": "AI & Data Science",
        "description": "Artificial Intelligence, Machine Learning, Deep Learning, LLMs, NLP, Chatbots, Data Analytics",
        "strong": [
            "artificial intelligence",
            "machine learning",
            "deep learning",
            "large language model",
            "llm",
            "generative ai",
            "chatbot",
            "computer vision",
            "natural language processing"
        ],
        "keywords": [
            # No bare "ml": it is a whole word in "500 ml" 193x in the corpus,
            # and "machine learning" already covers the concept.
            "artificial intelligence", "ai", "machine learning", "deep learning",
            "large language model", "llm", "generative ai", "genai", "nlp",
            "natural language processing", "chatbot", "image recognition", "speech recognition",
            "object detection", "computer vision", "data analytics", "predictive analytics",
            "neural network", "data science", "algorithm development", "text analytics"
        ]
    },
    "software_and_web_dev": {
        "label": "Software & Web Development",
        "description": "Software Development, Web Portals, Mobile Apps, CRM, ERP, Custom Applications",
        "strong": [
            "erp",
            "crm",
            "web portal",
            "software development",
            "application development",
            "mobile application"
        ],
        "keywords": [
            "software development", "application development", "app development",
            "web portal", "website development", "crm", "erp", "api integration",
            "enterprise software", "mobile application", "web development", "custom software",
            "backend development", "frontend development", "full stack", "software solution",
            "software license", "software portal", "portal development", "software maintenance"
        ]
    },
    "cybersecurity_and_network_security": {
        "label": "Cybersecurity & Network Security",
        "description": "Cybersecurity, VAPT, Firewalls, Endpoint Security, SOC, Encryption",
        "strong": [
            "vapt",
            "siem",
            "firewall",
            "penetration testing",
            "endpoint security",
            "antivirus"
        ],
        "keywords": [
            "cybersecurity", "cyber security", "penetration testing", "firewall",
            "endpoint security", "vulnerability assessment", "vapt", "soc",
            "security operations center", "threat detection", "encryption",
            "network security", "zero trust", "antivirus", "malware",
            "information security", "security audit", "siem", "utm"
        ]
    },
    "cloud_and_it_infrastructure": {
        "label": "Cloud & IT Infrastructure",
        "description": "Cloud Computing, Servers, Databases, Data Centers, Storage, IT Hardware",
        "strong": [
            "aws",
            "azure",
            "rack server",
            "blade server",
            "san storage",
            "nas storage",
            "data center",
            "hypervisor"
        ],
        "keywords": [
            "cloud computing", "cloud hosting", "aws", "azure", "server",
            "data center", "storage array", "virtual machine", "hypervisor",
            "disaster recovery", "san storage", "nas storage", "it hardware",
            "rack server", "blade server", "database management", "database server",
            "compute cluster", "workstation", "desktop computer", "laptop",
            "random access memory", "ram module", "memory module", "operating system", "hard disk", "ssd", "oracle database", "database", "processor", "network switch", "router", "printer", "scanner"
        ]
    },
    "biometrics_and_surveillance": {
        "label": "Biometrics & Surveillance",
        "description": "Biometric Access Control, Facial Recognition, CCTV, Video Analytics",
        "strong": [
            "cctv",
            "anpr",
            "biometric",
            "facial recognition",
            "facial based",
            "face based",
            "facial authentication",
            "face authentication",
            "fingerprint",
            "iris scanner",
            "ip camera",
            "rfid"
        ],
        "keywords": [
            "biometric", "facial recognition", "facial based", "face based",
            "facial authentication", "face authentication", "fingerprint", "iris scanner",
            "access control", "cctv", "surveillance", "ip camera",
            "video analytics", "anpr", "rfid", "smart card",
            "turnstile", "boom barrier", "visitor management", "frs",
            "dash camera", "body worn camera", "dome camera", "bullet camera", "nvr", "dvr"
        ]
    },
    "automation_and_robotics": {
        "label": "Automation & Robotics",
        "description": "RPA, Industrial Automation, SCADA, PLC, Drones, IoT, Edge Computing",
        "strong": [
            "drone",
            "drones",
            "quadcopter",
            "uav",
            "unmanned aerial vehicle",
            "scada",
            "plc",
            "robotic process automation",
            "rpa",
            "robotics"
        ],
        "keywords": [
            "automation", "rpa", "robotic process automation", "robotics",
            "scada", "plc", "iot", "internet of things", "edge computing",
            "drone", "drones", "quadcopter", "uav", "autonomous vehicle",
            "unmanned aerial vehicle", "sensor network", "telemetry"
        ]
    },
    "electronics_and_electrical": {
        "label": "Electronics & Electrical",
        "description": "Power Supplies, UPS, Batteries, Rectifiers, Cables, Resistors, PCB",
        "strong": [
            "ups",
            "rectifier",
            "rectifiers",
            "pcb",
            "inverter",
            "transformer",
            "switchgear",
            "power supply",
            "resistor",
            "resistors"
        ],
        "keywords": [
            "power supply", "ups", "battery", "inverter", "rectifier", "rectifiers",
            "resistor", "resistors", "amplifier", "amplifiers", "cable", "cables",
            "connector", "connectors", "terminal block", "busbar",
            "electrical equipment", "transformer", "switchgear", "pcb",
            "circuit board", "harness", "wire", "voltage stabilizer", "dg set"
        ]
    },
    "industrial_and_mechanical": {
        "label": "Industrial & Mechanical",
        "description": "Industrial Machinery, Lab Testing, Valves, Piping, HVAC, Pumps",
        "strong": [
            "hvac",
            "compressor",
            "pressure vessel",
            "lathe machine",
            "hydraulic"
        ],
        "keywords": [
            "industrial lab testing", "industrial mechanical", "valves", "piping",
            "hvac", "pump", "generator", "machining", "fabrication",
            "metallurgy", "test equipment", "pressure vessel", "compressor",
            "pipe fittings", "lathe machine", "hydraulic",
            "ball bearing", "bearing", "gearbox", "conveyor"
        ]
    },
    "medical_and_lab": {
        "label": "Medical & Laboratory",
        "description": "Medical Devices, Hospital Equipment, Lab Testing, Diagnostics",
        "strong": [
            "ventilator",
            "ultrasound",
            "centrifuge",
            "microscope",
            "patient monitor",
            "medical device"
        ],
        "keywords": [
            "medical equipment", "healthcare", "lab equipment", "hospital",
            "diagnostic", "patient monitor", "ventilator", "surgical",
            "pharmaceutical", "laboratory testing", "microscope", "centrifuge",
            "reagent", "medical device", "ultrasound",
            "deep freezer", "laboratory oven", "incubator", "autoclave", "laboratory equipment", "immunization"
        ]
    },
    "civil_and_facility_maintenance": {
        "label": "Civil & Facility Maintenance",
        "description": "Civil Construction, Renovation, Cleaning, Manpower, Facility Services",
        "strong": [
            "housekeeping",
            "pest control",
            "civil construction",
            "building construction"
        ],
        "keywords": [
            "civil construction", "building construction", "renovation", "plumbing",
            "painting", "cleaning supplies", "janitorial", "manpower",
            "security guard", "facility management", "housekeeping", "sanitation",
            "maintenance service", "pest control"
        ]
    },
    "logistics_and_supplies": {
        "label": "Logistics & Supplies",
        "description": "Freight, Transport, Agricultural Supplies, Tarpaulins, General Items",
        "strong": [
            "tarpaulins",
            "warehousing",
            "freight",
            "supply chain"
        ],
        "keywords": [
            "logistics services", "transportation", "freight", "supply chain",
            "agri supplies", "agricultural", "tarpaulins", "stationery",
            "general supplies", "packaging material", "cargo", "warehousing"
        ]
    },
    "solar_and_renewable": {
        "label": "Solar & Renewable Energy",
        "description": "Solar PV, Rooftop Solar, EPC, Inverters, Renewable Power Plants",
        "strong": [
            "solar", "photovoltaic", "solar pv", "rooftop solar", "solar plant",
            "solar power plant", "windmill", "wind turbine", "solar epc"
        ],
        "keywords": [
            "solar", "solar pv", "photovoltaic", "solar panel", "solar module",
            "rooftop solar", "solar plant", "solar power plant", "solar epc",
            "solar street light", "solar pump", "renewable energy", "grid tied",
            "off grid", "net metering", "windmill", "wind turbine", "biogas",
            "solar inverter", "mppt", "string inverter"
        ]
    },
    "smart_metering_and_ami": {
        "label": "Smart Metering & AMI",
        "description": "Smart Energy Meters, AMI/AMR, CT/PT Meters, Prepaid Metering, DLMS",
        "strong": [
            "smart meter", "energy meter", "ami", "amr", "prepaid meter",
            "dlms", "ct operated meter", "ht meter", "net meter"
        ],
        "keywords": [
            "smart meter", "energy meter", "electricity meter", "ami", "amr",
            "prepaid meter", "postpaid meter", "dlms", "ct operated meter",
            "ct meter", "ht meter", "lt meter", "net meter", "meter reading",
            "metering", "hes", "mdm", "meter data", "static meter",
            "three phase meter", "single phase meter"
        ]
    },
    "rf_and_communication": {
        "label": "RF & Communication",
        "description": "RF Components, Antennas, Transceivers, Radios, Waveguides, Telecom Links",
        "strong": [
            "antenna", "transceiver", "waveguide", "attenuator", "rf amplifier",
            "coaxial", "vhf", "uhf", "satcom", "base station"
        ],
        "keywords": [
            "antenna", "transceiver", "waveguide", "attenuator", "coaxial",
            "rf amplifier", "radio set", "vhf", "uhf", "satcom", "satellite link",
            "base station", "repeater", "modem", "leased line", "bandwidth",
            "optical fibre", "optical fiber", "ofc", "telecom"
        ]
    },
    "audio_video_and_display": {
        "label": "Audio, Video & Display",
        "description": "LED Video Walls, Displays, Projectors, PA Systems, Conference Audio",
        "strong": [
            "video wall", "led display", "projector", "public address",
            "conference system", "loudspeaker", "digital signage"
        ],
        "keywords": [
            "video wall", "led display", "led video", "display panel", "projector",
            "public address", "pa system", "conference system", "microphone",
            "loudspeaker", "speaker", "amplifier system", "digital signage",
            "video conferencing", "interactive panel", "smart board",
            "tv", "television", "smart tv", "led tv", "set top box", "monitor"
        ]
    },
    "gis_and_dgps_survey": {
        "label": "GIS & DGPS Survey / Geospatial",
        "description": "DGPS Survey, GIS Mapping, Topographic Survey, Drone/LIDAR Survey, Cadastral & Land Survey",
        "strong": [
            "dgps survey", "gis survey", "dgps", "gis mapping", "topographic survey",
            "topographical survey", "cadastral survey", "drone survey", "lidar survey",
            "bathymetric survey", "hydrographic survey", "total station survey",
            "geospatial survey", "contour survey", "aerial survey", "rtk survey", "gnss survey"
        ],
        "keywords": [
            "dgps survey", "gis survey", "dgps", "gis mapping", "gis", "differential gps",
            "geospatial", "geospatial survey", "geospatial mapping", "topographic survey",
            "topographical survey", "cadastral survey", "drone survey", "uav survey",
            "aerial survey", "lidar survey", "airborne lidar", "bathymetric survey",
            "hydrographic survey", "total station survey", "total station", "contour survey",
            "land survey", "boundary survey", "georeferencing", "orthomosaic",
            "photogrammetry", "digital elevation model", "ground control points", "gcp",
            "remote sensing", "satellite imagery", "utility mapping", "subsurface utility",
            "gnss survey", "rtk survey", "geotechnical survey", "geophysical survey",
            "as-built survey", "thematic mapping"
        ]
    },
    "uncategorized_general": {
        "label": "General / Uncategorized",
        "description": "General Tenders or Tenders without strong domain specific matches",
        "keywords": []
    }
}


def extract_pdf_text_sample(pdf_path: str, max_pages: int = 5) -> str:
    """Safely extract text sample from top N pages of a PDF file using pypdf."""
    if not os.path.exists(pdf_path):
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        extracted = []
        num_pages = min(len(reader.pages), max_pages)
        for i in range(num_pages):
            text = reader.pages[i].extract_text()
            if text:
                extracted.append(text)
        return "\n".join(extracted)
    except Exception as e:
        logger.debug(f"Failed to extract text from {pdf_path}: {e}")
        return ""


def normalize_text(text: str) -> str:
    """Normalize text by lowercasing and replacing non-alphanumeric chars with spaces."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s_\-]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# Field weights. The search keyword stays low: it records which of the user's
# search terms found the bid, and 31% of records carry several (up to 491
# chars), so it is a hint about the query, not evidence about the bid.
WEIGHT_TITLE = 3.5
WEIGHT_CATEGORY = 2.5
WEIGHT_KEYWORD_HINT = 1.0
WEIGHT_PDF_PER_HIT = 1.0
MAX_PDF_SCORE_PER_KEYWORD = 3.0

# Item categories often enumerate every accessory in a bundle. Keep their
# combined vote below a lead-title match so three incidental accessories do
# not overrule the product named by the tender title.
MAX_CATEGORY_SCORE_PER_DOMAIN = 3.0

# Total PDF contribution per domain, capped below the title weight. An RFP body
# is mostly boilerplate -- terms, delivery, inspection, warranty -- that name
# many domains in passing. Uncapped, 20 keywords x 3.0 let the body outvote the
# title 60 to 3.5, which filed "Toilet Paper Roll type 2" under Medical &
# Laboratory and "Dig in post 1.6 mtr" under Electronics. The title is the
# bid's actual subject; the body only corroborates.
MAX_PDF_SCORE_PER_DOMAIN = 3.0

# A product noun ("quadcopter") identifies what is being bought; a purpose word
# ("surveillance") qualifies many domains. Without this, "Repair of Quadcopter
# for High Altitude Surveillance" tied 3.5-3.5 and was decided by dict order.
STRONG_KEYWORD_BONUS = 1.5

# Evidence floor: below this there is nothing to classify on. A lone search
# keyword hint (1.0) can never carry a bid on its own.
MIN_SCORE = 2.5


def score_domain(keywords, title, category_text, keyword_hint, pdf_text, strong=()):
    """Return (score, reasons) for one domain against a tender's text fields.

    Matching is whole-word: substring matching put 690 bids in AI & Data
    Science because "ai" sits inside maintenance, repair, air, paint and chair.
    """
    title_score = 0.0     # lead title: the strongest subject evidence
    category_score = 0.0  # bundled category/accessories: capped corroboration
    hint_score = 0.0      # the search term that found it (noisy)
    pdf_score = 0.0       # RFP body: corroboration only
    reasons = []
    strong_terms = {normalize_text(t) for t in strong}
    normalized_terms = {normalize_text(keyword) for keyword in keywords}
    seen_families = set()
    for keyword in keywords:
        term = normalize_text(keyword)
        if not term:
            continue
        # Taxonomies often list both singular and plural aliases. The shared
        # matcher already treats those as equivalent, so scoring both would
        # count one occurrence twice (amplifier + amplifiers, cable + cables).
        family = term
        if term.endswith("s") and term[:-1] in normalized_terms:
            family = term[:-1]
        elif f"{term}s" in normalized_terms:
            family = term
        if family in seen_families:
            continue
        seen_families.add(family)

        in_subject = False
        in_title = keyword_hit(term, title)
        if in_title:
            in_subject = True
            title_score += WEIGHT_TITLE
            reasons.append(f"Title: '{keyword}'")

        if keyword_hit(term, category_text):
            in_subject = True
            category_score += WEIGHT_CATEGORY
            reasons.append(f"Category: '{keyword}'")

        # Strong-product bonus belongs to the lead title only. A strong term
        # buried in accessories (speaker bid + power amplifier) must not
        # overrule what the tender is actually buying.
        if in_title and term in strong_terms:
            title_score += STRONG_KEYWORD_BONUS
            reasons.append(f"Product term: '{keyword}'")

        if keyword_hit(term, keyword_hint):
            hint_score += WEIGHT_KEYWORD_HINT
            reasons.append(f"Keyword hint: '{keyword}'")

        pdf_hits = count_hits(term, pdf_text)
        if pdf_hits:
            pdf_score += min(pdf_hits * WEIGHT_PDF_PER_HIT, MAX_PDF_SCORE_PER_KEYWORD)
            reasons.append(f"PDF content ({pdf_hits}x): '{keyword}'")

    # The RFP body only corroborates a subject match. On its own it would
    # classify bids whose title says nothing about the domain -- boilerplate
    # put "Toilet Paper Roll type 2" in Medical and "Dig in post 1.6 mtr" in
    # Electronics purely on body text.
    subject_score = title_score + min(category_score, MAX_CATEGORY_SCORE_PER_DOMAIN)
    if subject_score > 0:
        return subject_score + hint_score + min(pdf_score, MAX_PDF_SCORE_PER_DOMAIN), reasons
    return hint_score, [r for r in reasons if r.startswith("Keyword hint")]


def classify_tender(
    tender: Dict[str, Any],
    pdf_text: Optional[str] = None,
    pdf_path: Optional[str] = None,
    min_confidence: float = 0.15
) -> Dict[str, Any]:
    """Classify a tender into a canonical domain.

    The result drives which folder a downloaded RFP is filed under, so an
    unsure answer belongs in ``uncategorized_general`` rather than a confident
    wrong bucket. A domain wins only if it clears ``MIN_SCORE`` and its
    confidence -- which blends evidence strength with its lead over the
    runner-up -- reaches ``min_confidence``.

    Weights: title 3.5, category/business line 2.5, search keyword 1.0,
    PDF text 1.0 per hit (capped at 3.0 per keyword).
    """
    stored_analysis = tender.get("analysis") or {}
    if not isinstance(stored_analysis, dict):
        stored_analysis = {}

    def stored_field(name):
        return tender.get(name) or stored_analysis.get(name) or ""

    business_line = stored_field("business_line")
    if isinstance(business_line, dict):
        business_line = " ".join(filter(None, [
            str(business_line.get("id") or ""),
            str(business_line.get("label") or ""),
        ]))

    title = normalize_text(tender.get("title", ""))
    category_text = normalize_text(" ".join([
        str(stored_field("item_category")),
        str(stored_field("primary_item")),
        str(business_line),
    ]))
    keyword_hint = normalize_text(tender.get("keyword", ""))

    if not pdf_text and pdf_path:
        pdf_text = extract_pdf_text_sample(pdf_path, max_pages=5)
    pdf_norm = normalize_text(pdf_text or "")

    domain_scores: Dict[str, float] = {}
    matched_reasons: Dict[str, List[str]] = {}
    for domain_key, domain_info in CANONICAL_DOMAINS.items():
        if domain_key == "uncategorized_general":
            continue
        score, reasons = score_domain(
            domain_info["keywords"], title, category_text, keyword_hint, pdf_norm,
            strong=domain_info.get("strong", ()),
        )
        if score > 0:
            domain_scores[domain_key] = score
            matched_reasons[domain_key] = reasons

    # Equal totals are common on bundled bids. Prefer the domain that explains
    # the lead title over one supported only by accessories or the search hint;
    # falling back to dictionary order recreated the profile-order tie bug.
    ranked = sorted(
        domain_scores.items(),
        key=lambda kv: (
            kv[1],
            sum(
                1 for reason in matched_reasons.get(kv[0], [])
                if reason.startswith("Title:")
            ),
        ),
        reverse=True,
    )
    best_domain, best_score = (ranked[0] if ranked else ("uncategorized_general", 0.0))
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence blends "is there enough evidence" with "is the winner clearly
    # ahead". A bid matching two domains equally is not 100% confident just
    # because it scored highly on both.
    if best_score > 0:
        margin = (best_score - runner_up) / (best_score + runner_up)
        confidence = min(best_score / 10.0, 1.0) * (0.5 + 0.5 * margin)
    else:
        margin = 0.0
        confidence = 0.0

    # One gate, not two: a weak lead already shows up as low confidence, so a
    # separate hard margin cut only threw away reasonable calls on multi-item
    # bids ("Voltage Stabiliser + Smart Surveillance Camera") where any of the
    # top domains is a defensible filing choice.
    if best_score < MIN_SCORE or confidence < min_confidence:
        best_domain, confidence = "uncategorized_general", 0.0

    domain_info = CANONICAL_DOMAINS[best_domain]
    return {
        "domain": best_domain,
        "domain_label": domain_info["label"],
        "confidence": round(confidence, 2),
        "raw_score": round(best_score, 2),
        "margin": round(margin, 2),
        "runner_up_score": round(runner_up, 2),
        "matched_reasons": matched_reasons.get(best_domain, []),
        "all_domain_scores": {k: round(v, 2) for k, v in domain_scores.items()},
    }
