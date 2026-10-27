"""Devanagari-aware text normalisation, confusion detection and lexicon checks.

Handwriting OCR in Devanagari fails in *predictable* ways: a scribe writes
`ष` where the word has `श`, drops a nukta, confuses `ं` (anusvara) with `ँ`
(candrabindu), or writes a rare codepoint that does not exist in the word.
This module turns those failures into review flags instead of silent errors.

Everything here is pure-python and unit-testable without network access.
"""

from __future__ import annotations

import pathlib

import re
import unicodedata

# ---------------------------------------------------------------------------
# Unicode ranges
# ---------------------------------------------------------------------------

DEVANAGARI_START = 0x0900
DEVANAGARI_END = 0x097F

DEVANAGARI_DIGITS = "०१२३४५६७८९"
ASCII_DIGITS = "0123456789"

#: Precomposed nukta letters -> their base consonant.
#: Written with explicit code points because the precomposed (U+0958..U+095F)
#: and decomposed (base + U+093C) forms look identical in most editors but are
#: *different strings* in Python - getting this wrong silently no-ops the
#: normaliser.
_PRECOMPOSED_NUKTA: dict[int, int] = {
    0x0958: 0x0915,  # क़
    0x0959: 0x0916,  # ख़
    0x095A: 0x0917,  # ग़
    0x095B: 0x091C,  # ज़
    0x095C: 0x0921,  # ड़
    0x095D: 0x0922,  # ढ़
    0x095E: 0x092A,  # फ़
    0x095F: 0x092F,  # य़
}

#: Combining nukta sign, U+093C.
NUKTA_SIGN = "़"

#: Precomposed nukta letter -> base + nukta sign.
NUKTA_DECOMPOSE: dict[str, str] = {
    chr(pre): chr(base) + NUKTA_SIGN for pre, base in _PRECOMPOSED_NUKTA.items()
}

#: base + nukta sign -> precomposed nukta letter.
NUKTA_COMPOSE: dict[str, str] = {
    chr(base) + NUKTA_SIGN: chr(pre) for pre, base in _PRECOMPOSED_NUKTA.items()
}


def is_devanagari_char(ch: str) -> bool:
    return DEVANAGARI_START <= ord(ch) <= DEVANAGARI_END


def contains_devanagari(text: str) -> bool:
    return any(is_devanagari_char(c) for c in text)


def script_of(text: str) -> str:
    """Return 'devanagari', 'latin' or 'unknown' for a string."""
    if contains_devanagari(text):
        return "devanagari"
    if any("a" <= c.lower() <= "z" for c in text):
        return "latin"
    return "unknown"


# ---------------------------------------------------------------------------
# Confusion tables
# ---------------------------------------------------------------------------

#: Characters that are easily mistaken for one another in handwriting.
#: Maps a character to the set of alternatives it is confused with.
DEVA_LOOKALIKES: dict[str, tuple[str, ...]] = {
    "ट": ("ठ", "ढ"),
    "ठ": ("ट", "थ"),
    "ड": ("ढ", "द"),
    "ढ": ("ड", "द"),
    "द": ("ड", "ढ", "ध"),
    "ध": ("द", "ध"),
    "त": ("थ", "त्त"),
    "थ": ("त", "ध"),
    "न": ("ण", "ऩ", "न"),
    "ण": ("न", "न"),
    "ऩ": ("न",),
    "ब": ("व",),
    "व": ("ब",),
    "ल": ("ळ",),
    "ळ": ("ल",),
    "श": ("ष", "स"),
    "ष": ("श", "स"),
    "स": ("श", "ष"),
    "ह": ("ङ",),
    "क": ("ख", "फ"),
    "ख": ("क", "ग"),
    "ग": ("घ", "ख"),
    "घ": ("ग",),
    "च": ("छ",),
    "छ": ("च",),
    "ज": ("झ", "ज"),
    "झ": ("ज",),
    "ञ": ("ज", "ज्ञ"),
    "ज्ञ": ("ञ",),
    "य": ("य़",),
    "र": ("ऱ",),
    "ऱ": ("र", "ऋ"),
    "ऋ": ("ऱ", "ॠ"),
    "ॠ": ("ऋ",),
    "प": ("फ",),
    "फ": ("प",),
    # Independent vowels
    "इ": ("ई",),
    "ई": ("इ",),
    "उ": ("ऊ",),
    "ऊ": ("उ",),
    "ओ": ("औ",),
    "औ": ("ओ",),
    # Vowel signs
    "ि": ("ी",),
    "ी": ("ि", "ा"),
    "ा": ("ी",),
    "ु": ("ू",),
    "ू": ("ु",),
    "ृ": ("ॄ",),
    "ॄ": ("ृ",),
    "े": ("ै",),
    "ै": ("े",),
    "ो": ("ौ",),
    "ौ": ("ो",),
    # Marks
    "ं": ("ँ", "ः"),
    "ँ": ("ं",),
    "ः": ("ं",),
}

#: Codepoints that are essentially never used in Hindi or Sanskrit school
#: text.  Their presence almost always means the OCR hallucinated or mangled a
#: glyph.  Built from explicit code points because these characters look
#: identical to common ones in most editors.
#:
#: Deliberately *excluded*: candrabindu (U+0901), anusvara (U+0902),
#: visarga (U+0903), the danda pair (U+0964/0965), the Devanagari digits
#: (U+0966..U+096F), the abbreviation sign (U+0970) and OM (U+0950) - all of
#: which occur in ordinary Hindi and Sanskrit text.
RARE_DEVANAGARI = set(
    "\u0900"          # inverted candrabindu (Vedic only)
    "\u0904"          # short A
    "\u0934"          # LLLA
    "\u093a\u093b"    # OE / OOE vowel signs
    "\u0951\u0952\u0953\u0954"   # Vedic stress / accent signs
    "\u0961\u0962\u0963"         # vocalic LL and its signs
    "\u0971\u0972\u0973\u0974\u0975\u0976\u0977"
    "\u0978\u0979\u097a\u097b\u097c\u097d\u097e\u097f"
)

#: Devanagari characters that are *invalid* inside a word (virama on a vowel
#: that cannot take it) – kept simple, mostly used for warnings.
INVALID_VIRAMA_AFTER = set("अआइईउऊऋएऐओऔअंअः")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_MULTISPACE = re.compile(r"[ \t  -  ]+")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?%)\]}])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[{])[ \t]+")
_SPACE_AROUND_DANDA = re.compile(r"[ \t]*([।॥])[ \t]*")


def _fix_nukta_positions(text: str) -> str:
    """Move a nukta onto the letter it belongs to.

    Handwriting frequently puts the dot under the vowel sign instead of the
    consonant ("बडी़" for "बड़ी"), or in front of the letter ("़क" for
    "क़").  Both are fixed here so the lexicon can match the word.
    """
    # Collapse a duplicated nukta: "क़़" -> "क़".
    text = re.sub(f"{NUKTA_SIGN}{NUKTA_SIGN}", NUKTA_SIGN, text)

    # A nukta that landed *after* the vowel sign belongs to the consonant
    # in front of it: "बडी़" -> "बड़ी".
    text = re.sub(
        f"([\\u093E-\\u094C\\u0962\\u0963]){NUKTA_SIGN}",
        lambda m: NUKTA_SIGN + m.group(1),
        text,
    )

    # A nukta that sits in front of a letter and is not already attached to
    # one moves onto that letter: "़क" -> "क़".  A nukta that already follows
    # a letter (or a virama) belongs to it and is left alone, so "क़न" and
    # "क़ी" survive untouched.
    text = re.sub(
        f"(?<![\\u0904-\\u0939\\u094D]){NUKTA_SIGN}"
        f"([\\u0904-\\u0914\\u0915-\\u0939])",
        lambda m: m.group(1) + NUKTA_SIGN,
        text,
    )
    return text


def normalize_nukta(text: str, prefer_precomposed: bool = False) -> str:
    """Make nukta usage consistent across a string.

    Devanagari has both precomposed nukta letters (U+0958..U+095F) and the
    decomposed base+nukta pair.  Mixing the two in one document looks wrong and
    breaks search.  We always normalise to the decomposed form unless the
    caller asks for precomposed.
    """
    if prefer_precomposed:
        text = _fix_nukta_positions(text)
        for decomposed, composed in NUKTA_COMPOSE.items():
            text = text.replace(decomposed, composed)
        return text

    # Decompose precomposed forms first, then fix the nukta positions.
    for composed, decomposed in NUKTA_DECOMPOSE.items():
        text = text.replace(composed, decomposed)
    text = _fix_nukta_positions(text)
    return text


def normalize_devanagari(
    text: str,
    *,
    prefer_precomposed_nukta: bool = False,
    convert_digits: bool = False,
    danda_to_period: bool = False,
) -> str:
    """Full Devanagari-aware normalisation pass.

    Steps (in order):
      1. Unicode NFC composition – puts combining marks in canonical order.
      2. Nukta style unification.
      3. Whitespace tidy-up + Devanagari punctuation spacing rules.
      4. Optional digit / danda conversion.
    """
    if not text:
        return text

    # 1. Canonical composition.
    text = unicodedata.normalize("NFC", text)

    # 2. Nukta unification.  Note that we must NOT re-run NFC afterwards when
    # the precomposed style was requested: the precomposed nukta letters
    # (U+0958..U+095F) are Unicode "composition exclusions", so NFC would
    # immediately decompose them again.
    text = normalize_nukta(text, prefer_precomposed_nukta)
    if not prefer_precomposed_nukta:
        text = unicodedata.normalize("NFC", text)

    # 3. Whitespace.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTISPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    # Devanagari danda takes no space before it and a single space after.
    text = _SPACE_AROUND_DANDA.sub(r"\1 ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"^[ \t]+", "", text)

    # 4. Optional conversions.
    if convert_digits:
        text = "".join(
            ASCII_DIGITS[DEVANAGARI_DIGITS.index(c)] if c in DEVANAGARI_DIGITS else c
            for c in text
        )
    if danda_to_period:
        text = text.replace("॥", ".").replace("।", ".")

    return text.strip()


# ---------------------------------------------------------------------------
# Confusion / validity detection
# ---------------------------------------------------------------------------


def find_lookalikes(text: str) -> list[dict[str, object]]:
    """Locate characters that are commonly confused with something else.

    We do *not* auto-correct these – we only know the character is ambiguous,
    not which reading the scribe intended.  Returning them lets the UI highlight
    the exact spot for a human decision.
    """
    hits: list[dict[str, object]] = []
    for idx, ch in enumerate(text):
        alts = DEVA_LOOKALIKES.get(ch)
        if not alts:
            continue
        # Only report the first occurrence of each ambiguous character to keep
        # the review list short and actionable.
        hits.append(
            {
                "index": idx,
                "char": ch,
                "alternatives": list(alts),
                "context": text[max(0, idx - 12): idx + 12],
            }
        )
    return hits


def find_rare_characters(text: str) -> list[dict[str, object]]:
    """Rare / almost certainly invalid Devanagari codepoints."""
    out: list[dict[str, object]] = []
    for idx, ch in enumerate(text):
        if ch in RARE_DEVANAGARI:
            out.append(
                {
                    "index": idx,
                    "char": ch,
                    "name": unicodedata.name(ch, "UNKNOWN"),
                    "context": text[max(0, idx - 12): idx + 12],
                }
            )
    return out


def find_suspicious_virama(text: str) -> list[dict[str, object]]:
    """A virama directly after a vowel is invalid in Devanagari."""
    out: list[dict[str, object]] = []
    for idx in range(1, len(text)):
        if text[idx] == "्" and text[idx - 1] in INVALID_VIRAMA_AFTER:
            out.append({"index": idx, "context": text[max(0, idx - 12): idx + 12]})
    return out


def double_spaces(text: str) -> int:
    return len(re.findall(r"  +", text))


# ---------------------------------------------------------------------------
# Lexicon / spell checking
# ---------------------------------------------------------------------------

#: Word characters.  The danda pair (U+0964/0965) is *punctuation*, not a
#: letter, so it must terminate a word - otherwise "है।" is treated as one
#: token and never matches the lexicon entry "है".
#: The avagraha (U+093D) and the abbreviation sign (U+0970) are signs,
#: not letters, so they break a word: "धर्मोऽर्थः" is two words.
_WORD_RE = re.compile(r"[\u0900-\u093C\u093E-\u0963\u0966-\u096F\u0971-\u097F\u0980-\u09FFA-Za-z]+")


def words_of(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _load_lexicon(filename: str) -> set[str]:
    """Read a newline-delimited lexicon file that ships next to this module."""
    path = pathlib.Path(__file__).with_name(filename)
    words: set[str] = set()
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            word = raw.strip()
            if not word or word.startswith("#"):
                continue
            words.add(word)
    except OSError:  # pragma: no cover - the file ships with the package
        pass
    return words


#: Words that routinely appear in Indian school examination papers (Hindi).
#: Stems are preferred so that inflected forms still match; the
#: suffix-stripping pass below handles the rest.
HINDI_LEXICON = _load_lexicon("lexicon_hi.txt")

#: Sanskrit vocabulary, used by the same check.
SANSKRIT_LEXICON = _load_lexicon("lexicon_sa.txt")


#: Common Hindi/Sanskrit suffixes – a word ending in one of these is very
#: likely inflected from a lexicon stem, so we do not flag it as unknown.
DEVA_SUFFIXES = (
    "ों", "ें", "ाने", "ाना", "ीं", "ाओं", "ाएं", "ाती", "ाते", "ाता",
    "ती", "ते", "ता", "ने", "ना", "नी", "ने", "ी", "े", "ा", "ि", "ु",
    "ू", "ं", "ः", "यों", "ओं", "एं", "कर", "वाले", "वाली", "वाला",
    "रूप", "त्मक", "सक", "मय", "मान", "मे", "से", "को", "का", "की",
    "के", "म्", "य", "म्",
    # Sanskrit verb / noun endings that are not simple vowel suffixes.
    "न्तु", "न्ती", "न्ते", "न्ति", "न्ता", "न्त", "सि", "सा", "सः",
    "न्तः", "ति", "थी", "थे", "थ", "ो", "ै",
)

#: Very common English school vocabulary.  Kept as one compact string so the
#: module stays readable; split once at import time.
ENGLISH_LEXICON = set("""
the and of to in a is that for it as was with be by on not this are or from at
which but have an had they you were their one all we can her has there been if
more when will would who so no said what up its about into than them only other
new some could time these two may then do first any my now such like our over
man me even most made after also did many before must through back years where
much your way well down should because each just those people how too little
state good very make world still own see men work long get here between both
life being under never day same another know while last might us great old year
off come since against go came right used take three states him few house use
during without again place around however home small found thought went say part
once general high school every does got group important question answer marks
total duration section page write read choose correct following below above given
find show prove define list give calculate solve draw label compare explain
describe discuss note briefly ten five twenty hundred
calculate calculate value values unit units function functions variable variables
equation equations solution solutions number numbers digit digits sum total
product factor factors prime primes composite divisor remainder quotient
integer fraction decimal ratio proportion percentage interest simple compound
profit loss cost price discount tax income expenditure budget balance
triangle triangles circle circles square squares rectangle area perimeter
volume surface radius diameter circumference angle angles degree degrees
parallel perpendicular bisector tangent chord arc radius theorem proof
prove prove prove contradiction hypothesis conclusion
energy force mass velocity acceleration momentum gravity friction electricity
magnet current voltage resistance power energy work heat temperature pressure
volume density chemical reaction acid base salt solution solvent mixture
compound element atom molecule gas liquid solid crystal
cell cells tissue organ organism photosynthesis respiration
plant plants root stem leaf leaves flower fruit seed germination
food nutrition diet protein vitamin mineral balanced
history ancient medieval modern civilization culture heritage monument
geography climate rainfall temperature latitude longitude equator
ocean continent island desert valley plateau river
literature poetry prose novel drama essay chapter stanza verse
grammar noun pronoun adjective adverb verb conjunction preposition tense
sentence paragraph punctuation
government constitution parliament president election minister policy
economy trade industry agriculture commerce
science physics chemistry biology mathematics computer internet
constitution freedom equality justice rights citizen
maximum minimum maximum marks total marks obtained percentage
space provided neatly legibly calculator compass ruler protractor
compulsory objective short answer long answer section sub-section
regular polygon hexagon pentagon octagon rectangle rhombus trapezium
acute obtuse right angle angles degrees radians vertices sides
arithmetic progression terms series geometric mean median mode
quadratic roots root equation variable coefficient constant expression
simplify evaluate factorise expand identity remainder theorem
hypothesis conclusion converse proof sum product quotient divisor
triangle triangles circle circles square squares area perimeter
volume surface radius diameter circumference parallel perpendicular
bisector tangent chord arc sector segment
distance displacement velocity acceleration momentum inertia gravity
friction mass weight density pressure temperature heat energy power
electric current voltage resistance battery cell circuit
reflection refraction lens mirror prism spectrum ray
reaction acid alkali salt solution solvent mixture compound element
atom molecule gas liquid solid crystal photosynthesis respiration
cell tissue organ organism enzyme nutrient vitamin mineral
civilisation civilization culture heritage monument inscription
climate rainfall temperature latitude longitude equator
ocean continent island desert valley plateau river glacier
poetry prose novel drama essay chapter stanza verse genre
grammar noun pronoun adjective adverb verb conjunction preposition
tense sentence paragraph punctuation capital letter
government constitution parliament president election minister policy
economy trade industry agriculture commerce inflation budget
science physics chemistry biology mathematics computer internet
constitution freedom equality justice rights citizen
""".split())

#: Common English word endings that signal a regular inflection.
_ENGLISH_SUFFIXES = (
    "ing", "ed", "es", "s", "ly", "er", "est", "tion", "sion", "ness", "ment",
    "able", "ible", "ful", "ous", "ive", "al", "ic", "ity", "ies", "ise",
    "ize", "ise", "ify", "ance", "ence", "ship", "hood", "ward", "wise",
    "less", "ate", "ble", "ple", "cle", "gle", "tle", "ary", "ery", "ory",
    "ist", "ism", "ure", "age", "ary", "ial", "ual", "ance", "ence", "gy",
    "sis", "ism", "ist", "ize", "ise", "ward", "some", "time", "work",
    "ical", "ally", "ness", "ment", "ture", "sion", "able",
    "um", "ic", "ics", "ular", "itude", "ary", "ery", "ory", "ial", "ual",
)

#: Common English word beginnings that signal a regular derivation.
_ENGLISH_PREFIXES = (
    "un", "re", "in", "im", "il", "ir", "dis", "non", "pre", "post", "sub",
    "super", "inter", "intra", "trans", "over", "under", "mis", "anti",
    "auto", "multi", "micro", "macro", "semi", "mid", "out", "up", "fore",
    "co", "de", "ex", "re", "bi", "tri", "uni", "mono", "poly",
)


def _stem_variants(word: str) -> list[str]:
    """Alternative spellings of a word that a lexicon entry may use.

    Sanskrit and Hindi carry a lot of final decoration: a visarga
    ("सुखिनः"), a final virama ("सम्यक्") or a sandhi vowel
    ("धर्मो").  Each of those is stripped in turn so that the stem can be
    looked up.
    """
    variants = [word]
    if word.endswith("\u0903"):          # visarga
        trimmed = word[:-1]
        variants.append(trimmed)
        if trimmed.endswith("\u093E"):   # + long a
            variants.append(trimmed[:-1])
    if word.endswith("\u094D"):          # final virama
        variants.append(word[:-1])
    return variants


def _in_lexicon(word: str) -> bool:
    """True when the word (or one of its stems) is really in a lexicon.

    This is stricter than :func:`_devanagari_known`: short words are *not*
    accepted here.  The distinction matters when we propose a corrected
    spelling - "हें" is two characters long, but it is not a word, and
    offering it as a fix would be worse than offering nothing.
    """
    for variant in _stem_variants(word):
        if variant in HINDI_LEXICON or variant in SANSKRIT_LEXICON:
            return True
        # Strip common inflectional suffixes and re-test.
        for suf in DEVA_SUFFIXES:
            if variant.endswith(suf) and len(variant) - len(suf) >= 2:
                stem = variant[: -len(suf)]
                if stem in HINDI_LEXICON or stem in SANSKRIT_LEXICON:
                    return True
                for inner in _stem_variants(stem):
                    if inner in HINDI_LEXICON or inner in SANSKRIT_LEXICON:
                        return True
    return False


def _is_concrete_word(word: str) -> bool:
    """True when the word itself occurs in a lexicon.

    Stricter than :func:`_in_lexicon`: inflected forms are *not* accepted.
    A suggested correction must be a word we can actually write down, not a
    fragment produced by stripping a suffix off something else.
    """
    for variant in _stem_variants(word):
        if variant in HINDI_LEXICON or variant in SANSKRIT_LEXICON:
            return True
    return False


def _devanagari_known(word: str) -> bool:
    """True when the word is recognisable as Hindi/Sanskrit.

    Two-character words are accepted without a lexicon lookup: in
    Devanagari they are overwhelmingly grammatical particles ("है", "का",
    "को", "में") and flagging them would produce pure noise.
    """
    if len(word) <= 2:
        return True
    if _in_lexicon(word):
        return True
    # A word made only of digits/punctuation is fine.
    if all(c in DEVANAGARI_DIGITS or c.isspace() for c in word):
        return True
    return False


def find_confusable_words(text: str) -> list[dict[str, object]]:
    """Find words that are unknown but look like a mis-read of a real word.

    This is the *precise* companion to :func:`find_lookalikes`.  A raw
    look-alike scan flags every ``श``/``स``/``ष`` in the document, which
    produces nothing but noise - those three glyphs are all common in
    perfectly good Hindi and Sanskrit words.

    Two conditions must both hold before a word is reported:

    1. the word is **not** a known word (it is not in the lexicon, even
       after the inflectional suffixes have been stripped), and
    2. swapping one ambiguous character for one of its look-alikes turns the
       word into a **known** word.

    Condition 2 is what makes the flag actionable.  ``विधार्थी`` is reported
    as a possible mis-reading of ``विद्यार्थी`` (द/ध), because swapping the
    single ``ध`` for ``द`` yields a word the lexicon knows.  A word that is
    merely absent from the lexicon still gets the softer ``unknown_word``
    flag, which is the right level of severity for "we do not recognise
    this word" - as opposed to "this word is probably wrong in exactly
    this way".
    """
    hits: list[dict[str, object]] = []
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if not contains_devanagari(word):
            continue
        if _devanagari_known(word):
            continue
        for idx, ch in enumerate(word):
            alternatives = DEVA_LOOKALIKES.get(ch)
            if not alternatives:
                continue
            for alt in alternatives:
                if alt == ch:
                    continue
                candidate = word[:idx] + alt + word[idx + 1:]
                if _is_concrete_word(candidate):
                    hits.append(
                        {
                            "index": match.start() + idx,
                            "char": ch,
                            "alternatives": [a for a in alternatives if a != ch],
                            "suggestion": candidate,
                            "word": word,
                            "context": text[
                                max(0, match.start() - 12): match.end() + 12
                            ],
                        }
                    )
                    break
            else:
                continue
            break
    return hits


def unknown_words(text: str) -> list[str]:
    """Words that look like real words but are not in our lexicon."""
    out: list[str] = []
    for w in words_of(text):
        if not w:
            continue
        if any(c in DEVANAGARI_DIGITS for c in w):
            continue
        if contains_devanagari(w):
            if not _devanagari_known(w):
                out.append(w)
        else:
            lw = w.lower()
            # Only reasonably long words are worth checking, and only when the
            # word does not look like a regular English derivation.
            if len(lw) >= 5 and lw not in ENGLISH_LEXICON and lw.isalpha():
                if not _looks_like_english_word(lw):
                    out.append(w)
    return out


def _looks_like_english_word(w: str) -> bool:
    """Cheap English morphology check: common suffixes/prefixes."""
    for suf in _ENGLISH_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return True
    for pre in _ENGLISH_PREFIXES:
        if w.startswith(pre) and len(w) - len(pre) >= 3:
            return True
    return False
