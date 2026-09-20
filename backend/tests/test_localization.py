"""The English shell has to be English all the way down.

Reported: onboarding, notifications, button labels and the written analysis
still came back half Turkish. These tests keep the dictionary honest — they
find the user-visible Turkish in the source and fail when it has no English,
so the gap cannot quietly reopen with the next feature.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT.parent / "frontend"

TR_CHARS = set("ıİğĞşŞçÇöÖüÜ")
TR_WORDS = re.compile(
    r"(?<![\w-])(ve|bir|bu|için|ile|olarak|daha|kadar|göre|yok|var|seni|senin|"
    r"değil|hiç|her|tüm|yeni|sonra|önce|henüz|lütfen|tekrar|ya da|veya|ama)"
    r"(?![\w-])", re.I)
CODEY = re.compile(
    r"(class=|data-|aria-|style=|/api/|\$\{|^#|^\.|^\[|^<|https?://|^[a-z-]+$|"
    r"[{}()<>;]|^[A-Z_]+$|\bfunction\b|=>)")


def _dictionary() -> dict[str, str]:
    source = (FRONTEND / "js" / "i18n.js").read_text()
    block = source.split("const EN = Object.freeze({", 1)[1].split("\n});", 1)[0]
    pairs = re.findall(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'", block)
    return {k.replace("\\'", "'"): v.replace("\\'", "'") for k, v in pairs}


def _is_turkish_prose(text: str, keys) -> bool:
    text = " ".join(text.split())
    if len(text) < 3 or text in keys or CODEY.search(text):
        return False
    if not re.search(r"[A-Za-zıİğĞşŞçÇöÖüÜ]{2}", text):
        return False
    return bool(set(text) & TR_CHARS) or bool(TR_WORDS.search(text))


class DictionaryHealthTests(unittest.TestCase):
    def setUp(self):
        self.entries = _dictionary()

    def test_no_key_is_defined_twice_with_a_different_answer(self):
        """A later duplicate silently wins, which is how a good line is lost."""
        source = (FRONTEND / "js" / "i18n.js").read_text()
        block = source.split("const EN = Object.freeze({", 1)[1].split("\n});", 1)[0]
        pairs = re.findall(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'", block)
        by_key: dict[str, set] = {}
        for key, value in pairs:
            by_key.setdefault(key, set()).add(value)
        conflicting = {k: v for k, v in by_key.items() if len(v) > 1}
        self.assertEqual(conflicting, {}, f"conflicting duplicates: {conflicting}")

    def test_every_placeholder_survives_the_translation(self):
        """A dropped {count} renders the brace literally to the reader."""
        broken = {
            key: value for key, value in self.entries.items()
            if set(re.findall(r"\{(\w+)\}", key)) != set(re.findall(r"\{(\w+)\}", value))
        }
        self.assertEqual(broken, {}, f"placeholder mismatch: {broken}")

    def test_no_english_value_is_still_turkish(self):
        leftovers = {
            key: value for key, value in self.entries.items()
            if set(value) & TR_CHARS
        }
        self.assertEqual(leftovers, {}, f"untranslated values: {leftovers}")


class ShellCoverageTests(unittest.TestCase):
    """Every Turkish string a reader can see must have an English answer."""

    def setUp(self):
        self.keys = set(_dictionary())

    def _missing(self, texts):
        return sorted({
            " ".join(text.split()) for text in texts
            if _is_turkish_prose(text, self.keys)
        })

    def test_the_markup_has_no_untranslated_text(self):
        from bs4 import BeautifulSoup, Comment

        soup = BeautifulSoup((FRONTEND / "index.html").read_text(), "html.parser")
        texts = [
            str(node) for node in soup.find_all(string=True)
            if not isinstance(node, Comment)
            and node.parent.name not in {"script", "style"}
        ]
        for element in soup.find_all(True):
            for attribute in ("placeholder", "title", "aria-label"):
                if element.has_attr(attribute):
                    texts.append(element[attribute])

        self.assertEqual(self._missing(texts), [])

    def test_the_shell_scripts_have_no_untranslated_text(self):
        texts = []
        literal = re.compile(
            r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`([^`]*)`", re.S)
        for js in sorted((FRONTEND / "js").glob("*.js")):
            if js.name == "i18n.js":
                continue
            source = re.sub(r"/\*.*?\*/", "", js.read_text(), flags=re.S)
            source = re.sub(r"(?m)^\s*//.*$", "", source)
            for match in literal.finditer(source):
                if match.group(3) is not None:
                    raw = re.sub(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "\x00", match.group(3))
                    texts.extend(re.findall(
                        r'(?:title|aria-label|placeholder)="([^"\x00]*)"', raw))
                    texts.extend(re.sub(r"<[^>]*>", "\x00", raw).split("\x00"))
                    continue
                texts.append((match.group(1) or match.group(2) or "")
                             .replace("\\'", "'").replace('\\"', '"'))

        self.assertEqual(self._missing(texts), [])

    def test_a_sentence_built_by_interpolation_goes_through_t(self):
        """The observer matches whole text nodes, so `${x} film` never matches."""
        offenders = []
        for js in sorted((FRONTEND / "js").glob("*.js")):
            if js.name == "i18n.js":
                continue
            for line_no, line in enumerate(js.read_text().splitlines(), 1):
                if line.strip().startswith("//"):
                    continue
                for lit in re.findall(r"`([^`]*)`", line):
                    if "${" not in lit or "<" in lit:
                        continue
                    static = re.sub(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "…", lit)
                    if (set(static) & TR_CHARS) or TR_WORDS.search(static):
                        offenders.append(f"{js.name}:{line_no} {static.strip()[:70]}")

        self.assertEqual(offenders, [])


class ServerMessageCoverageTests(unittest.TestCase):
    """The shell shows server messages verbatim, so they need entries too."""

    def setUp(self):
        self.keys = set(_dictionary())

    def test_every_error_detail_has_an_english_answer(self):
        missing = set()
        for py in sorted((ROOT / "app").glob("*.py")):
            source = py.read_text()
            for match in re.finditer(
                r'detail=\(?\s*((?:"[^"]*"|\'[^\']*\')'
                r'(?:\s*\n?\s*(?:"[^"]*"|\'[^\']*\'))*)', source
            ):
                text = " ".join(
                    "".join(part) for part in
                    re.findall(r'"([^"]*)"|\'([^\']*)\'', match.group(1))
                )
                text = " ".join(text.split())
                if text and (set(text) & TR_CHARS) and text not in self.keys:
                    missing.add(text)

        self.assertEqual(sorted(missing), [])


class WrittenAnalysisLanguageTests(unittest.TestCase):
    """The AI prose is stored, so a language change has to rewrite it."""

    def setUp(self):
        self.main = (ROOT / "app" / "main.py").read_text()
        self.llm = (ROOT / "app" / "llm.py").read_text()

    def test_the_prompt_can_be_switched_to_english(self):
        self.assertIn("OUTPUT LANGUAGE OVERRIDE", self.llm)
        for name in ("def analyze_taste", "def rank_candidates"):
            block = self.llm.split(name, 1)[1].split(") -> ", 1)[0]
            self.assertIn("locale", block, name)

    def test_changing_the_language_rewrites_the_stored_prose(self):
        block = self.main.split('@app.post("/api/profile/locale")', 1)[1]
        block = block.split("\n@app.", 1)[0]

        self.assertIn("clear_taste_narrative", block)
        # Resolved at the request: a member left on "auto" has no stored
        # preference to read, and used to get Turkish prose under an English UI.
        self.assertIn("_refresh_locale_taste(account, _response_locale(account, request))", block)
        rebuild = self.main.split("async def _refresh_locale_taste", 1)[1]
        rebuild = rebuild.split("\nasync def ", 1)[0]
        self.assertIn("locale=locale", rebuild)


class RandomReasonLanguageTests(unittest.TestCase):
    """The random mode writes its own reasons instead of paying for an LLM."""

    def test_every_written_reason_has_both_languages(self):
        from app.main import _RANDOM_REASONS, _add_random_reasons, _community_reason

        for key, forms in _RANDOM_REASONS.items():
            self.assertEqual(set(forms), {"tr", "en"}, key)
            self.assertNotEqual(forms["tr"], forms["en"], key)
            # A dropped placeholder would print the brace to the reader.
            self.assertEqual(
                set(re.findall(r"\{(\w+)\}", forms["tr"])),
                set(re.findall(r"\{(\w+)\}", forms["en"])),
                key,
            )

    def test_an_english_spin_is_explained_in_english(self):
        """Reported: reasons came back Turkish under an English interface."""
        from types import SimpleNamespace

        from app.main import _add_random_reasons, _community_reason

        english = _community_reason(4, 4.2, "en")
        self.assertIn("cinephiles on Movienotes", english)
        self.assertNotIn("sinefil", english)

        film = SimpleNamespace(director="Céline Sciamma", genres=["Drama"], reason="")
        _add_random_reasons([film], source="discover", locale="en")
        self.assertIn("from TMDb", film.reason)
        self.assertIn("Céline Sciamma", film.reason)
        self.assertNotIn("Topluluk", film.reason)

    def test_the_stream_resolves_the_language_before_it_starts(self):
        """The generator runs after the response opens; the request is gone."""
        main_py = (ROOT / "app" / "main.py").read_text()
        block = main_py.split('@app.post("/api/random")', 1)[1].split("\n@app.", 1)[0]

        header, generator = block.split("async def generate():", 1)
        self.assertIn("response_locale = _response_locale(account, request)", header)
        self.assertIn("locale=response_locale", generator)


if __name__ == "__main__":
    unittest.main()
