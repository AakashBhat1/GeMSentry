"""Factory defaults for scoring config and company profile."""


DEFAULT_SCORING_CONFIG = {
    "version": 1,
    "weights": {
        "emd": 2.0,
        "startup_exemption": 1.5,
        "mse_exemption": 1.5,
        "prebid": 0.0,
        "date_window": 1.0,
        "epbg": 0.5
    },
    "emd": {
        "free_threshold_inr": 200000,
        "max_penalty_threshold_inr": 2000000,
        "value_multiplier": 20
    },
    "date_window": {
        "min_days": 7,
        "full_credit_days": 14,
        "min_days_to_bid": 5
    },
    "epbg": {
        "free_threshold_pct": 3.0,
        "max_penalty_pct": 10.0
    },
    "unknown_subscore": 0.5,
    # A tender that simply does not relax Experience/Turnover puts us on normal
    # terms — that is neutral, not a risk. Relaxations score above this floor as
    # a genuine bonus; set to 0.0 to treat an absent relaxation as a negative.
    "no_relaxation_floor": 0.5,
    "status_thresholds": {
        "shortlist_min": 90,
        "reject_max": 40
    },
    "fit": {
        "weights": {
            "relevance": 3.0,
            "serviceability": 1.0,
            "value_fit": 1.0,
            "buyer_affinity": 0.0,
            "eligibility_factor": 2.0
        },
        "fit_min": 65,
        "review_band": 8,
        "unknown_buyer_subscore": 0.4,
        "turnover_gap_subscore": 0.3,
        "weak_relevance_subscore": 0.5,
        # BE-30 omnibus guard: bundled bids of this many line items whose lead
        # item matches nothing and whose matching items fall below the ratio are
        # treated as incidental matches, not relevance. Set at 8 because genuine
        # small kits (a 7-item Arduino/relay bundle) legitimately lead with an
        # item we do not keyword — the guard targets 15-45 item omnibus bids.
        "omnibus_min_items": 8,
        "omnibus_min_match_ratio": 0.34,
        # A single matched keyword this short, when it is not a declared
        # strong_keyword, is treated as coincidence rather than relevance.
        "lone_acronym_max_len": 3
    },
    "priority": {
        "fit_weight": 0.6,
        "risk_weight": 0.4,
        "exemption_boost": 1.1
    },
    "download_policy": {
        "skip_zero_relevance_download": True,
        "download_workers": 4,
        # 0 = auto (cpu_count - 1, capped at 4). PDF text extraction is
        # CPU-bound, so this fans out over processes, not threads.
        "analysis_workers": 0,
        # Per-request ceiling for the raw-HTTP PDF fetch. A host that does not
        # answer at all burns this whole budget, so keep it tight.
        "download_timeout": 15,
        # The Playwright fallback is sequential and costs ~15s per failure;
        # cap it so a degraded portal cannot stall a run for half an hour.
        "max_browser_fallbacks": 25
    }
}


DEFAULT_COMPANY_PROFILE = {
    "version": 1,
    "company": {
        "legal_name": "Earnest Tactical Solutions Pvt. Ltd.",
        "short_name": "ETSPL",
        "incorporation_ym": "2020-03",
        "hq_state": "Haryana",
        "hq_city": "Gurgaon"
    },
    "eligibility": {
        "annual_turnover_inr": 1800000,
        "years_experience": 6,
        "registrations": {"mse_udyam": True, "startup_dpiit": True},
        "certifications": ["ISO 9001:2015"],
        "can_meet_make_in_india": True,
        "max_order_value_inr": None,
        "turnover_waivable_by_exemption": True
    },
    "serviceability": {
        "all_india": True,
        "soft_avoid_states": [
            "Tamil Nadu", "Kerala", "Karnataka",
            "Andhra Pradesh", "Telangana", "Puducherry"
        ],
        "soft_avoid_reason": "Local monopoly on these product categories in South India",
        "soft_avoid_penalty": 0.5
    },
    "business_lines": [
        {
            "id": "drone",
            "label": "Drone / UAV",
            "priority": 1.0,
            "strong_keywords": [
                "drone", "drones", "uav", "unmanned aerial", "quadcopter",
                "multirotor", "aerostat"
            ],
            "keywords": [
                "drone", "drones", "uav", "unmanned aerial", "multirotor",
                "quadcopter", "aerostat", "gis", "mapping", "surveillance",
                "reconnaissance"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "power_supply",
            "label": "Power Supply / Electrical",
            "priority": 1.0,
            "strong_keywords": [
                "power supply", "rectifier", "lvpsu", "hvpsu",
                "static convertor", "solid state power amplifier",
                "battery charger", "voltage regulator", "ups"
            ],
            "keywords": [
                "power supply", "ac-dc", "ac dc", "rectifier", "alternator",
                "amplifier", "ups", "voltage regulator", "lvpsu", "hvpsu",
                "power unit", "static convertor", "power conversion",
                "battery charger", "solid state power amplifier", "power system",
                "psu"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "ai_it",
            "label": "AI / IT / Electronics",
            "priority": 1.0,
            "strong_keywords": [
                "cctv", "ip camera", "nvr", "video analytics", "anpr",
                "number plate recognition", "artificial intelligence",
                "machine learning", "computer vision", "surveillance system",
                "command and control"
            ],
            "keywords": [
                "artificial intelligence", "ai based", "ai-based", "software",
                "server", "radar", "cctv", "camera", "connectors", "harness",
                "rugged laptop", "military grade", "repairing", "electronics",
                "data acquisition", "network switch", "router", "display",
                "laptop", "notebook", "machine learning", "deep learning",
                "computer vision", "object detection", "video analytics", "anpr",
                "number plate recognition", "software development",
                "web application", "mobile app", "mobile application", "dashboard",
                "portal", "saas", "api", "ip camera", "nvr", "vms",
                "command and control", "perimeter security", "surveillance system"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "biometrics",
            "label": "Biometrics & Facial Recognition",
            "priority": 1.0,
            "strong_keywords": [
                "facial recognition", "face recognition", "biometric",
                "biometrics", "biometric attendance", "iris recognition",
                "iris scanner", "fingerprint", "e-kyc", "aadhaar authentication"
            ],
            "keywords": [
                "facial recognition", "face recognition", "biometric",
                "biometrics", "frs", "fingerprint", "iris scanner",
                "iris recognition", "access control", "biometric attendance",
                "e-kyc", "aadhaar authentication"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "components",
            "label": "Electronic Components & Spares",
            "priority": 1.0,
            "strong_keywords": ["wiring harness", "printed circuit board", "smps"],
            "keywords": [
                "resistor", "cable", "connector", "relay", "rectifier",
                "capacitor", "transformer", "fuse", "contactor",
                "circuit breaker", "terminal block", "wiring harness",
                "toggle switch", "limit switch", "servo motor", "pcb",
                "printed circuit board", "diode", "transistor", "oscillator",
                "potentiometer", "switchgear", "mcb", "smps", "inductor",
                "crimping", "lugs", "heat shrink sleeve", "arduino",
                "raspberry pi", "microcontroller", "development board",
                "sensor module"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "smart_meter_ami",
            "label": "Smart Metering & AMI",
            "priority": 1.0,
            "strong_keywords": [
                "smart meter", "smart metering", "ami",
                "advanced metering infrastructure", "amisp", "hes",
                "head end system", "mdm", "meter data management",
                "smart prepaid meter", "rdss smart meter", "dbfoot smart meter"
            ],
            "keywords": [
                "smart meter", "smart energy meter", "electricity smart meter",
                "energy meter", "electronic energy meter", "digital energy meter",
                "multifunction meter", "net meter", "prepaid meter",
                "smart prepaid meter", "ami", "advanced metering infrastructure",
                "amisp", "ami service provider", "meter data management", "mdm",
                "hes", "head end system", "rf mesh", "rf communication",
                "nb-iot meter", "lorawan meter", "gprs meter",
                "cellular smart meter", "iot energy meter", "remote meter reading",
                "automatic meter reading", "amr", "smart metering",
                "distribution metering", "feeder metering", "dt metering",
                "consumer metering", "lt meter", "ht meter", "three phase meter",
                "single phase meter", "mdas", "scada integration",
                "energy accounting", "smart grid", "rdss smart meter",
                "dbfoot smart meter", "meter installation", "meter replacement",
                "smart meter o&m", "ct operated meter", "current transformer",
                "meter box", "meter enclosure", "meter communication module",
                "dcu", "data concentrator unit", "gateway", "meter testing equipment"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "solar_renewable",
            "label": "Solar & Renewable Energy",
            "priority": 1.0,
            "strong_keywords": [
                "solar power plant", "solar epc", "rooftop solar",
                "grid connected rooftop solar", "on grid solar", "bess",
                "battery energy storage system", "pm surya ghar", "pm kusum",
                "sitc solar"
            ],
            "keywords": [
                "solar power plant", "solar epc", "solar project", "solar pv",
                "solar photovoltaic", "renewable energy", "green energy",
                "rooftop solar", "grid connected rooftop solar", "on grid solar",
                "off grid solar", "hybrid solar", "net metering",
                "pm surya ghar", "residential rooftop solar",
                "government building solar",
                "design supply installation testing commissioning",
                "sitc solar", "epc solar", "turnkey solar",
                "solar installation", "solar commissioning", "solar o&m",
                "annual maintenance solar", "solar panel", "solar module",
                "mono perc", "topcon module", "bifacial module",
                "solar inverter", "string inverter", "central inverter",
                "hybrid inverter", "battery energy storage system", "bess",
                "lithium battery", "solar battery", "solar cable",
                "solar mounting structure", "module mounting structure",
                "solar junction box", "10 kw solar", "25 kw solar",
                "50 kw solar", "100 kw solar", "250 kw solar", "500 kw solar",
                "1 mw solar", "ground mounted solar", "solar street light",
                "solar high mast", "solar led street light", "solar pump",
                "solar water pump", "pm kusum", "kusum component b",
                "kusum component c"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management"
            ]
        },
        {
            "id": "gis_dgps_survey",
            "label": "DGPS & GIS Survey / Geospatial",
            "priority": 1.0,
            "strong_keywords": [
                "dgps survey", "gis survey", "dgps", "gis mapping",
                "topographic survey", "topographical survey", "cadastral survey",
                "drone survey", "aerial survey", "lidar survey",
                "bathymetric survey", "hydrographic survey", "total station survey",
                "geospatial survey", "land survey", "contour survey",
                "rtk survey", "gnss survey"
            ],
            "keywords": [
                "dgps survey", "gis survey", "dgps", "gis mapping", "gis",
                "differential gps", "geospatial", "geospatial survey",
                "geospatial mapping", "topographic survey", "topographical survey",
                "cadastral survey", "drone survey", "uav survey", "aerial survey",
                "lidar survey", "airborne lidar", "bathymetric survey",
                "hydrographic survey", "total station survey", "total station",
                "contour survey", "land survey", "boundary survey",
                "georeferencing", "orthomosaic", "photogrammetry", "dem",
                "dtm", "dsm", "digital elevation model", "ground control points",
                "gcp", "cartography", "remote sensing", "satellite imagery",
                "utility mapping", "subsurface utility engineering", "sue survey",
                "gnss survey", "rtk survey", "geotechnical survey",
                "geophysical survey", "as-built survey", "thematic mapping"
            ],
            "exclude_keywords": [
                "manpower", "housekeeping", "deputation", "security guard",
                "sanitation", "catering", "facility management",
                "survey monkey", "feedback survey", "customer survey",
                "satisfaction survey"
            ]
        }
    ],
    "buyer_affinity": {
        "INDIAN AIR FORCE": 1.0,
        "INDIAN ARMY": 0.85,
        "INDIAN NAVY": 0.75,
        "HAL": 0.75,
        "DRDO": 0.65,
        "BHARAT PETROLEUM": 0.5,
        "DEFENCE": 0.6,
        "SECI": 0.95,
        "NTPC": 0.9,
        "NHPC": 0.85,
        "SJVN": 0.85,
        "NLC INDIA": 0.85,
        "REC PDCL": 0.9,
        "DISCOM": 0.85,
        "MNRE": 0.85,
        "STATE ELECTRICITY BOARD": 0.85
    },
    "value_preference": {
        "sweet_min_inr": 500000,
        "sweet_max_inr": 50000000
    },
    "active_preset": "main",
    "value_presets": {
        "main": {
            "label": "Main (₹5L–₹5Cr)",
            "sweet_min_inr": 500000,
            "sweet_max_inr": 50000000,
            "keywords": [],
            "workspace": ""
        },
        "small_supply": {
            "label": "Small Supply (₹60k–₹2L)",
            "sweet_min_inr": 60000,
            "sweet_max_inr": 200000,
            "keywords": [
                "resistors", "cables", "connectors", "relay", "rectifiers",
                "harness", "amplifier", "psu", "power supply"
            ],
            "workspace": "personel"
        },
        "gis_survey": {
            "label": "DGPS & GIS Survey (₹1L–₹5Cr)",
            "sweet_min_inr": 100000,
            "sweet_max_inr": 50000000,
            "keywords": [
                "dgps", "dgps survey", "differential gps", "gis survey",
                "gis mapping", "topographic survey", "cadastral survey",
                "drone survey", "lidar survey", "total station survey",
                "geospatial survey", "land survey", "contour survey",
                "rtk survey", "gnss survey", "photogrammetry"
            ],
            "workspace": "gis_survey"
        }
    },
    "avoid_rules": {
        "gem_q2_category": True,
        "prefer_custom_bids": True
    }
}

# Indian states for consignee matching (lowercase keys)
