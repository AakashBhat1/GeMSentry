"""
GeMSentry NLP Tender Segregation & Classification Engine.

Categorizes tender RFPs into semantic canonical industry domains based on
title, item category, primary item, business line, and PDF content text.
"""
from __future__ import annotations

import os
import re
import math
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gemsentry.nlp")

# Canonical Industry Domains Taxonomy
CANONICAL_DOMAINS: Dict[str, Dict[str, Any]] = {
    "ai_and_data_science": {
        "label": "AI & Data Science",
        "description": "Artificial Intelligence, Machine Learning, Deep Learning, LLMs, NLP, Chatbots, Data Analytics",
        "keywords": [
            "artificial intelligence", "ai", "machine learning", "ml", "deep learning",
            "large language model", "llm", "generative ai", "genai", "nlp",
            "natural language processing", "chatbot", "image recognition", "speech recognition",
            "object detection", "computer vision", "data analytics", "predictive analytics",
            "neural network", "data science", "algorithm development", "text analytics"
        ]
    },
    "software_and_web_dev": {
        "label": "Software & Web Development",
        "description": "Software Development, Web Portals, Mobile Apps, CRM, ERP, Custom Applications",
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
        "keywords": [
            "cloud computing", "cloud hosting", "aws", "azure", "server",
            "data center", "storage array", "virtual machine", "hypervisor",
            "disaster recovery", "san storage", "nas storage", "it hardware",
            "rack server", "blade server", "database management", "database server",
            "compute cluster", "workstation", "desktop computer", "laptop"
        ]
    },
    "biometrics_and_surveillance": {
        "label": "Biometrics & Surveillance",
        "description": "Biometric Access Control, Facial Recognition, CCTV, Video Analytics",
        "keywords": [
            "biometric", "facial recognition", "fingerprint", "iris scanner",
            "access control", "cctv", "surveillance", "ip camera",
            "video analytics", "anpr", "rfid", "smart card",
            "turnstile", "boom barrier", "visitor management", "frs"
        ]
    },
    "automation_and_robotics": {
        "label": "Automation & Robotics",
        "description": "RPA, Industrial Automation, SCADA, PLC, Drones, IoT, Edge Computing",
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
        "keywords": [
            "power supply", "ups", "battery", "inverter", "rectifier", "rectifiers",
            "resistor", "resistors", "amplifier", "amplifiers", "cables",
            "electrical equipment", "transformer", "switchgear", "pcb",
            "circuit board", "harness", "wire", "voltage stabilizer", "dg set"
        ]
    },
    "industrial_and_mechanical": {
        "label": "Industrial & Mechanical",
        "description": "Industrial Machinery, Lab Testing, Valves, Piping, HVAC, Pumps",
        "keywords": [
            "industrial lab testing", "industrial mechanical", "valves", "piping",
            "hvac", "pump", "generator", "machining", "fabrication",
            "metallurgy", "test equipment", "pressure vessel", "compressor",
            "pipe fittings", "lathe machine", "hydraulic"
        ]
    },
    "medical_and_lab": {
        "label": "Medical & Laboratory",
        "description": "Medical Devices, Hospital Equipment, Lab Testing, Diagnostics",
        "keywords": [
            "medical equipment", "healthcare", "lab equipment", "hospital",
            "diagnostic", "patient monitor", "ventilator", "surgical",
            "pharmaceutical", "laboratory testing", "microscope", "centrifuge",
            "reagent", "medical device", "ultrasound"
        ]
    },
    "civil_and_facility_maintenance": {
        "label": "Civil & Facility Maintenance",
        "description": "Civil Construction, Renovation, Cleaning, Manpower, Facility Services",
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
        "keywords": [
            "logistics services", "transportation", "freight", "supply chain",
            "agri supplies", "agricultural", "tarpaulins", "stationery",
            "general supplies", "packaging material", "cargo", "warehousing"
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


def extract_ngrams(text: str, max_n: int = 3) -> Counter:
    """Extract 1-gram, 2-gram, and 3-gram frequencies from normalized text."""
    words = text.split()
    counts = Counter()
    for w in words:
        if len(w) > 1:
            counts[w] += 1
    for n in range(2, max_n + 1):
        for i in range(len(words) - n + 1):
            ngram = " ".join(words[i:i + n])
            counts[ngram] += 1
    return counts


def classify_tender(
    tender: Dict[str, Any],
    pdf_text: Optional[str] = None,
    pdf_path: Optional[str] = None,
    min_confidence: float = 0.15
) -> Dict[str, Any]:
    """
    Classify a tender into a canonical domain based on weighted NLP feature matching.
    
    Weights:
      - Title: 3.5x
      - Primary Item / Item Category / Business Line: 2.5x
      - Raw Search Keyword: 1.0x
      - PDF Text Sample: 1.0x
    """
    title = normalize_text(tender.get("title", ""))
    item_cat = normalize_text(tender.get("item_category", ""))
    primary_item = normalize_text(tender.get("primary_item", ""))
    business_line = normalize_text(tender.get("business_line", ""))
    raw_kw = normalize_text(tender.get("keyword", ""))

    # Extract PDF text sample if path provided and pdf_text not passed
    if not pdf_text and pdf_path:
        pdf_text = extract_pdf_text_sample(pdf_path, max_pages=5)
    pdf_norm = normalize_text(pdf_text or "")

    title_ngrams = extract_ngrams(title)
    cat_ngrams = extract_ngrams(f"{item_cat} {primary_item} {business_line}")
    kw_ngrams = extract_ngrams(raw_kw)
    pdf_ngrams = extract_ngrams(pdf_norm)

    domain_scores: Dict[str, float] = {}
    matched_reasons: Dict[str, List[str]] = {}

    for domain_key, domain_info in CANONICAL_DOMAINS.items():
        if domain_key == "uncategorized_general":
            continue

        keywords = domain_info["keywords"]
        score = 0.0
        reasons = []

        for kw in keywords:
            kw_norm = normalize_text(kw)
            if not kw_norm:
                continue

            # Title match (Highest weight: 3.5)
            if kw_norm in title or title_ngrams.get(kw_norm, 0) > 0:
                score += 3.5
                reasons.append(f"Title: '{kw}'")

            # Category / Business line match (High weight: 2.5)
            if kw_norm in f"{item_cat} {primary_item} {business_line}" or cat_ngrams.get(kw_norm, 0) > 0:
                score += 2.5
                reasons.append(f"Category: '{kw}'")

            # Keyword hint match (Moderate weight: 1.0)
            if kw_norm in raw_kw or kw_ngrams.get(kw_norm, 0) > 0:
                score += 1.0
                reasons.append(f"Keyword hint: '{kw}'")

            # PDF text match (Weight: 1.0 per occurrence, max 3.0 per keyword)
            pdf_hits = pdf_ngrams.get(kw_norm, 0)
            if pdf_hits > 0:
                pdf_score = min(pdf_hits * 1.0, 3.0)
                score += pdf_score
                reasons.append(f"PDF content ({pdf_hits}x): '{kw}'")

        domain_scores[domain_key] = score
        matched_reasons[domain_key] = reasons

    # Determine winning domain
    best_domain = "uncategorized_general"
    best_score = 0.0

    if domain_scores:
        sorted_domains = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)
        top_domain, top_score = sorted_domains[0]
        if top_score > 0.0:
            best_domain = top_domain
            best_score = top_score

    # Compute normalized confidence score (0.0 to 1.0)
    total_score_sum = sum(domain_scores.values()) or 1.0
    confidence = min(best_score / 10.0, 1.0) if best_score > 0 else 0.0

    # Fallback to uncategorized if score is below threshold
    if best_score < 1.5:
        best_domain = "uncategorized_general"
        confidence = 0.0

    domain_info = CANONICAL_DOMAINS.get(best_domain, CANONICAL_DOMAINS["uncategorized_general"])

    return {
        "domain": best_domain,
        "domain_label": domain_info["label"],
        "confidence": round(confidence, 2),
        "raw_score": round(best_score, 2),
        "matched_reasons": matched_reasons.get(best_domain, []),
        "all_domain_scores": {k: round(v, 2) for k, v in domain_scores.items() if v > 0}
    }
