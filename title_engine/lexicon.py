"""Category-agnostic language resources used by the verifier and formatter.

Nothing here is specific to a product category. These are general facts about
English and units (gents = men, litre = L, SS = stainless steel) that apply to
cotton fabric and hydraulic presses alike. The LLM does the category-specific
thinking; this file only lets plain code check that the LLM did not invent
anything.
"""

# Words that can appear in a title without being grounded in the supplier input.
# They carry no product attribute on their own.
CONNECTORS = {
    "for", "with", "and", "of", "in", "on", "to", "the", "a", "an", "by", "per",
    "or", "x", "from", "cum", "up", "upto", "use", "usage", "type", "each",
}

# Equivalence classes. Two words in the same class mean the same thing, so a
# title word is grounded if any word of its class is in the input, and two
# words of one class in a title are a redundancy.
# "kind" decides how the class is treated: units are not counted as duplicates
# (e.g. "40 x 40 x 5 mm" or "200 ml Each").
SYNONYM_CLASSES = {
    # audience
    "aud_men": ("audience", {"men", "man", "male", "males", "gents", "gent", "gentleman", "gentlemen"}),
    "aud_women": ("audience", {"women", "woman", "female", "females", "ladies", "lady"}),
    "aud_kids": ("audience", {"kids", "kid", "children", "child", "childrens"}),
    "aud_boys": ("audience", {"boys", "boy"}),
    "aud_girls": ("audience", {"girls", "girl"}),
    "aud_unisex": ("audience", {"unisex"}),
    # units
    "u_litre": ("unit", {"l", "ltr", "ltrs", "litre", "litres", "liter", "liters", "lt", "lts"}),
    "u_ml": ("unit", {"ml", "millilitre", "millilitres", "milliliter", "milliliters"}),
    "u_watt": ("unit", {"w", "watt", "watts", "wattage"}),
    "u_kw": ("unit", {"kw", "kilowatt", "kilowatts"}),
    "u_hp": ("unit", {"hp", "horsepower"}),
    "u_volt": ("unit", {"v", "volt", "volts", "voltage"}),
    "u_amp": ("unit", {"a", "amp", "amps", "ampere", "amperes"}),
    "u_kg": ("unit", {"kg", "kgs", "kilogram", "kilograms", "kilo", "kilos"}),
    "u_g": ("unit", {"g", "gm", "gms", "gram", "grams", "gr"}),
    "u_mg": ("unit", {"mg", "milligram", "milligrams"}),
    "u_mm": ("unit", {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}),
    "u_cm": ("unit", {"cm", "centimeter", "centimeters", "centimetre", "centimetres"}),
    "u_m": ("unit", {"m", "meter", "meters", "metre", "metres", "mtr", "mtrs"}),
    "u_inch": ("unit", {"in", "inch", "inches"}),
    "u_ft": ("unit", {"ft", "feet", "foot"}),
    "u_deg": ("unit", {"deg", "degree", "degrees"}),
    "u_ton": ("unit", {"ton", "tons", "tonne", "tonnes", "t"}),
    "u_kelvin": ("unit", {"k", "kelvin"}),
    "u_sq": ("unit", {"sq", "square"}),
    "u_pcs": ("unit", {"pc", "pcs", "piece", "pieces", "nos", "no"}),
    "u_hz": ("unit", {"hz", "hertz"}),
    # spelling variants (British / American / Indian English)
    "sp_colour": ("word", {"colour", "color", "colours", "colors", "coloured", "colored"}),
    "sp_grey": ("word", {"grey", "gray"}),
    "sp_aluminium": ("word", {"aluminium", "aluminum"}),
    "sp_galvanised": ("word", {"galvanised", "galvanized"}),
    "sp_fibre": ("word", {"fibre", "fiber"}),
    "sp_jewellery": ("word", {"jewellery", "jewelry"}),
    "sp_tyre": ("word", {"tyre", "tyres", "tire", "tires"}),
    "sp_mould": ("word", {"mould", "mold", "moulded", "molded"}),
    "sp_centre": ("word", {"centre", "center", "centres", "centers"}),
    "sp_and": ("word", {"and", "&"}),
}

# Abbreviations that expand to several words. A title may use either form.
ABBREVIATIONS = {
    "ss": ("stainless", "steel"),
    "ms": ("mild", "steel"),
    "gi": ("galvanized", "iron"),
    "ci": ("cast", "iron"),
    "hdpe": ("high", "density", "polyethylene"),
    "ldpe": ("low", "density", "polyethylene"),
    "pp": ("polypropylene",),
    "pu": ("polyurethane",),
    "frp": ("fibre", "reinforced", "plastic"),
    "sqmm": ("sq", "mm"),
}

# Marketing and subjective words. If one appears in a title and the supplier
# did not write it, it is treated as a critical violation (an unverifiable claim).
PROMO_WORDS = {
    "best", "premium", "quality", "top", "superior", "excellent", "finest", "perfect",
    "amazing", "awesome", "great", "durable", "reliable", "genuine", "original",
    "authentic", "branded", "latest", "new", "trendy", "stylish", "fancy", "attractive",
    "beautiful", "elegant", "luxury", "luxurious", "exclusive", "unique", "cheap",
    "affordable", "lowest", "discount", "offer", "sale", "hot", "popular", "leading",
    "trusted", "certified", "eco", "heavy", "professional", "advanced", "superb",
    "ultra", "super", "deluxe", "designer", "imported", "export", "free", "guaranteed",
    "warranty", "strong", "long", "lasting", "high", "efficient", "smart", "modern",
}

# The purely subjective subset. Even when the supplier wrote one of these
# ("Best Quality Rice"), keeping it in the title is flagged for a human check.
# Words like "high", "heavy" or "free" are excluded because, when stated, they
# are usually real specs ("High Pressure", "Heavy Duty", "BPA Free").
SUBJECTIVE_WORDS = {
    "best", "premium", "quality", "top", "superior", "excellent", "finest", "perfect",
    "amazing", "awesome", "great", "beautiful", "attractive", "elegant", "stylish",
    "fancy", "trendy", "luxury", "luxurious", "exclusive", "unique", "cheap",
    "affordable", "lowest", "superb", "deluxe", "trusted", "leading", "popular", "hot",
}
# ("genuine" is left out on purpose: "Genuine Leather" is a material grade, not praise.)

NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}

# Spec values that carry no information at all and are dropped before the LLM
# sees them. Vague-but-real values ("Good", "Standard") are kept and left to the
# LLM to judge; dropping them here would hide what the supplier actually wrote.
EMPTY_VALUES = {
    "", "-", "--", ".", "na", "n/a", "n.a", "n.a.", "none", "nil", "null", "not applicable",
    "not available", "as per requirement", "as per requirements", "as per customer requirement",
    "as per client requirement", "as per customer requirements", "as required",
}

# ---------------------------------------------------------------- word roles
# General English vocabulary used to check word ORDER (e.g. colour before material:
# "Black Leather", never "Leather Black"). Words that are both a colour and a
# material (gold, silver, copper, rose) are left out on purpose.
COLOUR_WORDS = {
    "black", "white", "red", "blue", "green", "yellow", "brown", "grey", "gray", "pink", "purple",
    "orange", "cream", "beige", "golden", "maroon", "navy", "ivory", "violet", "cyan", "magenta",
    "tan", "olive", "khaki", "peach", "turquoise", "mustard", "multicolour", "multicolor",
    "transparent", "offwhite",
}
MATERIAL_WORDS = {
    "cotton", "leather", "steel", "stainless", "iron", "aluminium", "aluminum", "brass", "plastic",
    "pvc", "upvc", "cpvc", "hdpe", "ldpe", "rubber", "wood", "wooden", "glass", "paper", "cardboard",
    "corrugated", "polyester", "nylon", "rayon", "silk", "wool", "woollen", "linen", "jute", "denim",
    "velvet", "satin", "chiffon", "georgette", "viscose", "ceramic", "porcelain", "marble", "granite",
    "fibre", "fiber", "titanium", "zinc", "chrome", "acrylic", "foam", "canvas", "khadi", "mesh",
    "metal", "bamboo", "cane", "terracotta", "leatherette", "rexine", "polycarbonate", "silicone",
    "ss", "ms", "gi", "ci", "polypropylene", "polyurethane", "crepe", "muslin", "organza",
}
PATTERN_WORDS = {
    "printed", "plain", "striped", "checked", "embroidered", "solid", "floral", "dotted", "polka",
    "textured", "glossy", "matte", "matt", "polished", "brushed", "galvanized", "galvanised",
    "coated", "painted", "laminated", "woven", "knitted", "quilted",
}
# Spec attribute names -> role, so "Colour: Brown" makes "brown" a colour
ATTRIBUTE_ROLE_HINTS = [
    ("brand", ("brand", "make", "manufacturer", "company")),
    ("colour", ("colour", "color", "shade")),
    ("pattern", ("pattern", "print", "design", "finish", "work")),
    ("material", ("material", "fabric", "metal")),
]

# ---------------------------------------------------------------- untrusted input
# Text in a supplier field that tries to instruct the AI. Matched case-insensitively; the
# sentence from the match onwards is removed before the AI sees it, and the listing goes to review.
INSTRUCTION_PATTERNS = [
    r"\bignore\b.{0,40}\b(instructions?|rules|above|previous|prior)\b",
    r"\bdisregard\b",
    r"\bforget\b.{0,30}\b(instructions?|rules|above|everything)\b",
    r"\b(system|developer)\s*(prompt|message|instruction)",
    r"\b(system|assistant)\s*:",
    r"\bnote to (the )?(ai|model|assistant|bot)\b",
    r"\byou are (now )?(an? )?(ai|assistant|model|chatgpt|gpt)\b",
    r"\bact as\b",
    r"\b(write|make|set|change|output|return|respond|reply|generate|print)\b.{0,25}\btitle\b",
    r"\btitle\s+(must|should|shall|has to)\b",
    r"\bnew instructions?\b",
    r"<\s*/?\s*(system|instruction|prompt|supplier_input)\b",
    r"\b(ai|model|chatgpt|gpt)\s+ko\b",                 # Hinglish: "AI ko bolo ..."
    r"\btitle\s+(me|mein|main)\b.{0,30}\b(likho|likhna|likhe|daalo|dalo)\b",
]
# Contact details, links and prices: a marketplace title must never carry these.
CONTACT_PATTERNS = [
    # Indian mobile (10 digits starting 6-9, optional +91 / 0 and a split) and landline with STD code;
    # deliberately narrow so size lists such as "1200 1400 1600 mm" are not mistaken for phones
    # the lead-in word goes with the number: "Call 98765 43210" must not leave a stray "Call" behind
    ("phone number", r"(?:\b(?:call|contact|ph|phone|mob|mobile|tel|whatsapp|wa)\b\.?\s*(?:no\.?|number|us|on|at)?"
                     r"[\s.:-]*)?"
                     r"(?:\+91[\s-]?|(?<!\d)0)?(?<!\d)[6-9]\d{4}[\s-]?\d{5}(?!\d)|(?<!\d)0\d{2,4}[\s-]\d{6,8}(?!\d)"),
    ("email address", r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    ("web link", r"(?:\bvisit\s+(?:us\s+(?:at\s+)?)?)?(?:(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|in|net|org|co\.in)\b)"),
    ("price", r"(?:₹|\brs\.?|\binr)\s*\d[\d,]*(?:\.\d+)?(?:\s*/-)?(?:\s*only)?|\b\d[\d,]*\s*/-"),
    # "contact" alone is not here: "Contact Lens" is a product
    ("sales pitch", r"\b(?:call now|call us|contact (?:us|now|me|no\.?)|contact for (?:price|rate|details)|whatsapp|"
                    r"free delivery|free shipping|cash on delivery|cod available|best price|lowest price|best rate)\b"),
]

# Acronyms of 4+ letters that stay upper-case. Shorter all-caps words (SS, PVC,
# LED) always stay upper-case; longer ones are softened ("PIPE" -> "Pipe").
KEEP_UPPER = {
    "HDPE", "LDPE", "LLDPE", "UPVC", "CPVC", "HDMI", "CCTV", "NEMA", "ASTM", "ANSI",
    "WIFI", "SATA", "DDR4", "DDR5", "IP65", "IP67", "IP68",
}

# Casing for units and common abbreviations when the formatter normalises a title.
UNIT_CASE = {
    "ml": "ml", "kg": "kg", "kgs": "kg", "mm": "mm", "cm": "cm", "mg": "mg", "gm": "gm", "m": "m",
    "cm2": "cm2", "mm2": "mm2", "m2": "m2", "ft": "ft", "in": "in", "g": "g", "sq": "sq",
    "l": "L", "ltr": "Ltr", "w": "W", "kw": "kW", "hp": "HP", "v": "V", "gsm": "GSM",
    "rpm": "RPM", "mah": "mAh", "hz": "Hz", "psi": "PSI", "k": "K", "sqmm": "sq mm",
}


def _build_class_index():
    index = {}
    for class_id, (kind, words) in SYNONYM_CLASSES.items():
        for w in words:
            index[w] = (class_id, kind)
    return index


WORD_TO_CLASS = _build_class_index()

ABBREV_PARTS = {}
for _abbr, _parts in ABBREVIATIONS.items():
    for _p in _parts:
        ABBREV_PARTS.setdefault(_p, set()).add(_abbr)
