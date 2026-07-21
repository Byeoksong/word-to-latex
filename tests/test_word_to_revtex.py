import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "00_word_to_revtex.py"
MODULE_SPEC = importlib.util.spec_from_file_location("word_to_revtex", MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Cannot load converter module from {MODULE_PATH}")

word_to_revtex = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(word_to_revtex)


class BoldMathNormalizationTests(unittest.TestCase):
    def test_mathbf_becomes_bold_italic_bm(self) -> None:
        source = r"\(\mathbf{x}+\mathbf{\Lambda}+\mathbf{v}_{I}\)"

        result = word_to_revtex.normalize_tex(source)

        self.assertEqual(result, r"\(\bm{x}+\bm{\Lambda}+\bm{v}_{I}\)")
        self.assertNotIn(r"\mathbf", result)

    def test_existing_bm_and_text_bold_are_unchanged(self) -> None:
        source = r"\(\bm{q}\) and \textbf{bold text}"

        self.assertEqual(word_to_revtex.normalize_tex(source), source)

    def test_only_the_exact_mathbf_command_is_normalized(self) -> None:
        source = r"\mathbfcal{x}"

        self.assertEqual(word_to_revtex.normalize_tex(source), source)


if __name__ == "__main__":
    unittest.main()
