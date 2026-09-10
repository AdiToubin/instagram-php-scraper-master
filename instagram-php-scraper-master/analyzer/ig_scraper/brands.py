"""
Brand keyword detection.

Python port of brand_detector.php's text-based matching only. The image
color/logo detection (detectLogosInImage) was deliberately dropped in the
migration - it depended on ext-imagick/ext-gd, which composer.json never
declared as a hard dependency, so it likely never ran in production anyway.
"""

import re
from typing import Any, Dict, List


class BrandDetector:
    def __init__(self) -> None:
        self._db = self._load_brand_database()

    @staticmethod
    def _load_brand_database() -> Dict[str, Dict[str, List[str]]]:
        return {
            "fashion": {
                "nike": ["Nike", "swoosh", "just do it"],
                "adidas": ["Adidas", "three stripes", "impossible is nothing"],
                "zara": ["Zara", "zara home"],
                "h&m": ["H&M", "hennes mauritz", "hennes & mauritz"],
                "uniqlo": ["Uniqlo", "lifewear"],
                "shein": ["SHEIN", "she in", "sheinside"],
                "forever21": ["Forever 21", "forever 21", "f21"],
                "mango": ["Mango", "mng"],
                "pull&bear": ["Pull & Bear", "pull and bear", "pullandbear"],
                "bershka": ["Bershka", "bsk"],
                "stradivarius": ["Stradivarius", "strd"],
                "massimo_dutti": ["Massimo Dutti", "md"],
                "cos": ["COS", "collection of style"],
                "weekday": ["Weekday", "wknd"],
                "monki": ["Monki"],
                "other_stories": ["& Other Stories", "other stories"],
            },
            "beauty": {
                "sephora": ["Sephora", "sepho"],
                "ulta": ["Ulta", "ulta beauty"],
                "loreal": ["L'Oreal", "loreal paris"],
                "maybelline": ["Maybelline", "maybe she's born with it"],
                "revlon": ["Revlon"],
                "clinique": ["Clinique"],
                "estee_lauder": ["Estée Lauder", "estee lauder"],
                "mac": ["MAC", "make-up art cosmetics"],
                "nars": ["NARS"],
                "urban_decay": ["Urban Decay"],
            },
            "tech": {
                "apple": ["Apple", "iphone", "ipad", "macbook", "airpods"],
                "samsung": ["Samsung", "galaxy"],
                "google": ["Google", "pixel"],
                "microsoft": ["Microsoft", "xbox", "surface"],
                "amazon": ["Amazon", "alexa", "echo"],
                "tesla": ["Tesla"],
                "sony": ["Sony", "playstation"],
                "nintendo": ["Nintendo", "switch"],
            },
            "food": {
                "mcdonalds": ["McDonald's", "mcdonalds", "big mac", "happy meal"],
                "kfc": ["KFC", "kentucky fried chicken"],
                "starbucks": ["Starbucks", "frappuccino"],
                "cocacola": ["Coca-Cola", "coca cola", "coke"],
                "pepsi": ["Pepsi", "pepsi cola"],
                "nestle": ["Nestlé", "nestle"],
                "unilever": ["Unilever"],
            },
            "automotive": {
                "bmw": ["BMW", "ultimate driving machine"],
                "mercedes": ["Mercedes-Benz", "mercedes", "the best or nothing"],
                "audi": ["Audi", "vorsprung durch technik"],
                "volkswagen": ["Volkswagen", "vw"],
                "toyota": ["Toyota", "let's go places"],
                "honda": ["Honda"],
                "ford": ["Ford", "built tough"],
                "tesla": ["Tesla"],
            },
        }

    def detect_brands(self, image_url: str, text: str) -> List[Dict[str, Any]]:
        # image_url is kept in the signature for parity with brand_detector.php's
        # detectBrands(imageUrl, text) - it's unused since logo/color detection
        # was dropped (see docstring above).
        brands = self._detect_in_text(text or "")
        brands = self._dedupe(brands)
        brands.sort(key=lambda b: b["confidence"], reverse=True)
        return brands

    def _detect_in_text(self, text: str) -> List[Dict[str, Any]]:
        text_lower = text.lower()
        out: List[Dict[str, Any]] = []
        for category, brand_data in self._db.items():
            for keywords in brand_data.values():
                brand_name = keywords[0]
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        out.append({
                            "value": brand_name,
                            "confidence": self._confidence(keyword, text),
                            "method": "text",
                            "category": category,
                            "matched_keyword": keyword,
                        })
                        break
        return out

    @staticmethod
    def _confidence(keyword: str, text: str) -> float:
        keyword_lower = keyword.lower()
        text_lower = text.lower()
        confidence = 0.75
        if keyword in text:
            confidence += 0.10
        if re.search(r"\b" + re.escape(keyword_lower) + r"\b", text_lower):
            confidence += 0.10
        if re.search(r"@" + re.escape(keyword_lower) + r"\b", text_lower):
            confidence += 0.05
        return min(0.95, confidence)

    @staticmethod
    def _dedupe(brands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        best: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for b in brands:
            key = b["value"]
            if key not in best:
                best[key] = b
                order.append(key)
            elif b["confidence"] > best[key]["confidence"]:
                best[key] = b
        return [best[k] for k in order]
