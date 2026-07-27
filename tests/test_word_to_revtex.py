import importlib.util
import json
import tempfile
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


class WordHighlightNormalizationTests(unittest.TestCase):
    def test_highlight_wrapper_is_removed_and_nested_latex_is_preserved(self) -> None:
        source = (
            r"\hl{Highlighted \textbf{nested text}, \cite{ref1}, "
            r"and \ensuremath{\alpha}.}"
        )

        self.assertEqual(
            word_to_revtex.normalize_tex(source),
            r"Highlighted \textbf{nested text}, \cite{ref1}, "
            r"and \ensuremath{\alpha}.",
        )

    def test_multiple_and_nested_highlights_are_removed(self) -> None:
        source = r"\hl{First \hl{nested} part} and \hl{second part}."

        result = word_to_revtex.normalize_tex(source)

        self.assertEqual(result, "First nested part and second part.")
        self.assertNotIn(r"\hl", result)

    def test_unbalanced_highlight_fails_before_compilation(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Unbalanced \\hl command"):
            word_to_revtex.normalize_tex(r"\hl{unfinished")


class WordMathStyleTests(unittest.TestCase):
    def test_plain_math_letters_are_marked_but_numbers_are_not(self) -> None:
        source = """<m:oMath xmlns:m="urn:math">
          <m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>ABC-2</m:t></m:r>
        </m:oMath>"""

        marked, counts = word_to_revtex._mark_math_run_styles_xml(source)

        sentinel = (
            word_to_revtex.MATH_ROMAN_BEGIN
            + "ABC"
            + word_to_revtex.MATH_ROMAN_END
        )
        self.assertIn(sentinel + "-2", marked)
        self.assertEqual(
            counts,
            {
                "plain": 1,
                "bold_upright": 0,
                "italic_greek_capital": 0,
                "bold_italic_greek_capital": 0,
            },
        )

    def test_bold_upright_and_bold_italic_remain_distinguishable(self) -> None:
        source = """<m:oMath xmlns:m="urn:math">
          <m:r><m:rPr><m:sty m:val="b"/></m:rPr><m:t>A</m:t></m:r>
          <m:r><m:rPr><m:sty m:val="bi"/></m:rPr><m:t>x</m:t></m:r>
        </m:oMath>"""

        marked, counts = word_to_revtex._mark_math_run_styles_xml(source)

        self.assertIn(word_to_revtex.MATH_ROMAN_BEGIN + "A", marked)
        self.assertNotIn(word_to_revtex.MATH_ROMAN_BEGIN + "x", marked)
        self.assertEqual(
            counts,
            {
                "plain": 0,
                "bold_upright": 1,
                "italic_greek_capital": 0,
                "bold_italic_greek_capital": 0,
            },
        )

    def test_markers_become_roman_inside_math(self) -> None:
        marked = (
            r"U^{"
            + word_to_revtex.MATH_ROMAN_BEGIN
            + "ABC"
            + word_to_revtex.MATH_ROMAN_END
            + "}"
        )

        self.assertEqual(word_to_revtex.normalize_tex(marked), r"U^{\mathrm{ABC}}")

    def test_bold_upright_combines_bm_and_roman(self) -> None:
        marked = (
            r"\mathbf{"
            + word_to_revtex.MATH_ROMAN_BEGIN
            + "A"
            + word_to_revtex.MATH_ROMAN_END
            + "}"
        )

        self.assertEqual(word_to_revtex.normalize_tex(marked), r"\bm{\mathrm{A}}")

    def test_default_italic_greek_capital_uses_var_command(self) -> None:
        source = """<m:oMath xmlns:m="urn:math">
          <m:r><m:t>Ω</m:t></m:r>
        </m:oMath>"""

        marked, counts = word_to_revtex._mark_math_run_styles_xml(source)
        pandoc_like = marked.replace("Ω", r"\Omega ")
        result = word_to_revtex.normalize_tex(pandoc_like)

        self.assertIn(r"\varOmega", result)
        self.assertNotIn(r"\Omega", result)
        self.assertEqual(counts["italic_greek_capital"], 1)

    def test_plain_greek_capital_stays_upright(self) -> None:
        source = """<m:oMath xmlns:m="urn:math">
          <m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>Ω</m:t></m:r>
        </m:oMath>"""

        marked, counts = word_to_revtex._mark_math_run_styles_xml(source)
        pandoc_like = marked.replace("Ω", r"\Omega ")
        result = word_to_revtex.normalize_tex(pandoc_like)

        self.assertIn(r"\mathrm{\Omega}", result)
        self.assertNotIn(r"\varOmega", result)
        self.assertEqual(counts["plain"], 1)

    def test_bold_italic_greek_capital_combines_bm_and_var(self) -> None:
        marked = (
            r"\mathbf{"
            + word_to_revtex.MATH_ITALIC_GREEK_BEGIN
            + r"\Omega "
            + word_to_revtex.MATH_ITALIC_GREEK_END
            + "}"
        )

        self.assertEqual(word_to_revtex.normalize_tex(marked), r"\bm{\varOmega}")

    def test_unicode_italic_omega_is_not_flattened(self) -> None:
        self.assertEqual(
            word_to_revtex.normalize_tex("𝛺"),
            r"\ensuremath{\varOmega}",
        )

    def test_unicode_italic_beta_in_text_becomes_latex(self) -> None:
        self.assertEqual(
            word_to_revtex.normalize_tex("the 𝛽-component"),
            r"the \ensuremath{\beta}-component",
        )

    def test_all_unicode_mathematical_italic_greek_is_normalized(self) -> None:
        source = "".join(word_to_revtex.MATHEMATICAL_ITALIC_GREEK_LATEX)

        result = word_to_revtex.normalize_tex(source)

        self.assertFalse(
            any(
                character in result
                for character in word_to_revtex.MATHEMATICAL_ITALIC_GREEK_LATEX
            )
        )
        self.assertIn(r"\ensuremath{\varGamma}", result)
        self.assertIn(r"\ensuremath{\beta}", result)
        self.assertIn(r"\ensuremath{\varkappa}", result)


def numbered_equation_block(punctuation: str, label: str = "1") -> dict:
    return {
        "t": "Para",
        "c": [
            {"t": "Math", "c": [{"t": "DisplayMath"}, "x = y"]},
            {"t": "Str", "c": punctuation},
            {"t": "Space"},
            {"t": "Str", "c": f"({label})"},
        ],
    }


class EquationPunctuationTests(unittest.TestCase):
    def test_equation_block_returns_source_punctuation(self) -> None:
        self.assertEqual(
            word_to_revtex.equation_block(numbered_equation_block(",")),
            ("x = y", "1", ","),
        )
        self.assertEqual(
            word_to_revtex.equation_block(numbered_equation_block(".", "2")),
            ("x = y", "2", "."),
        )

    def test_equation_punctuation_is_inside_the_display(self) -> None:
        rendered = word_to_revtex.make_equation("x = y", "1", ",")["c"][1]

        self.assertIn("x = y,\n\\tag{1}", rendered)

    def test_punctuation_remains_inside_a_scaled_display(self) -> None:
        rendered = word_to_revtex.make_equation("x" * 116, "1", ".")["c"][1]

        self.assertIn("\\fitcolumn{" + "x" * 116 + ".}", rendered)

    def test_comma_suppresses_indent_on_the_following_paragraph(self) -> None:
        continuation = {"t": "Para", "c": [{"t": "Str", "c": "where"}]}

        converted, labels = word_to_revtex.prepare_body(
            [numbered_equation_block(","), continuation], []
        )

        self.assertEqual(labels, {"1"})
        self.assertEqual(
            converted[1]["c"][0],
            {"t": "RawInline", "c": ["latex", "\\noindent "]},
        )

    def test_period_keeps_the_following_paragraph_indent(self) -> None:
        following = {"t": "Para", "c": [{"t": "Str", "c": "The"}]}

        converted, _labels = word_to_revtex.prepare_body(
            [numbered_equation_block("."), following], []
        )

        self.assertEqual(converted[1], following)

    def test_word_no_indent_overrides_period_fallback(self) -> None:
        following = {"t": "Para", "c": [{"t": "Str", "c": "Thus,"}]}

        converted, _labels = word_to_revtex.prepare_body(
            [numbered_equation_block(".", "2"), following], [], {"2": False}
        )

        self.assertEqual(
            converted[1]["c"][0],
            {"t": "RawInline", "c": ["latex", "\\noindent "]},
        )

    def test_word_indent_overrides_comma_fallback(self) -> None:
        following = {"t": "Para", "c": [{"t": "Str", "c": "where"}]}

        converted, _labels = word_to_revtex.prepare_body(
            [numbered_equation_block(",", "4"), following], [], {"4": True}
        )

        self.assertEqual(converted[1], following)

    def test_word_style_and_direct_indent_are_resolved(self) -> None:
        styles_xml = f"""<w:styles xmlns:w="{word_to_revtex.WORD_NS}">
          <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
            <w:pPr><w:ind w:firstLine="0"/></w:pPr>
          </w:style>
          <w:style w:type="paragraph" w:styleId="body">
            <w:basedOn w:val="Normal"/>
            <w:pPr><w:ind w:firstLine="567"/></w:pPr>
          </w:style>
        </w:styles>"""
        document_xml = f"""<w:document
            xmlns:w="{word_to_revtex.WORD_NS}"
            xmlns:m="{word_to_revtex.MATH_NS}">
          <w:body>
            <w:p><m:oMath><m:r><m:t>x=y</m:t></m:r></m:oMath><w:r><w:t>. (2)</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="body"/><w:ind w:firstLine="0"/></w:pPr><w:r><w:t>Thus</w:t></w:r></w:p>
            <w:p><m:oMath><m:r><m:t>x=y</m:t></m:r></m:oMath><w:r><w:t>. (3)</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="body"/></w:pPr><w:r><w:t>This</w:t></w:r></w:p>
          </w:body>
        </w:document>"""

        result = word_to_revtex.equation_following_paragraph_indents_from_xml(
            document_xml, styles_xml
        )

        self.assertEqual(result, {"2": False, "3": True})


class AutomaticPipelineTests(unittest.TestCase):
    def test_default_release_filename_includes_uppercase_journal(self) -> None:
        source = Path("01_source") / "draft.docx"

        self.assertEqual(
            word_to_revtex.release_filename(source, "prl"),
            "draft_PRL.pdf",
        )
        self.assertEqual(
            word_to_revtex.release_filename(source, "prb"),
            "draft_PRB.pdf",
        )

    def test_release_name_adds_pdf_and_rejects_paths(self) -> None:
        source = Path("draft.docx")

        self.assertEqual(
            word_to_revtex.release_filename(source, "prl", "revised"),
            "revised.pdf",
        )
        with self.assertRaises(ValueError):
            word_to_revtex.release_filename(source, "prl", "nested/revised.pdf")
        with self.assertRaises(ValueError):
            word_to_revtex.release_filename(source, "prl", "revised.zip")

    def test_final_log_rejects_release_blocking_warnings(self) -> None:
        log = """Overfull \\hbox (1.0pt too wide)
LaTeX Warning: There were undefined references.
Package natbib Warning: There were undefined citations.
pdfTeX warning (ext4): destination with the same identifier
has been already used, duplicate ignored
"""

        self.assertEqual(
            word_to_revtex.final_log_failures(log),
            [
                "overfull box",
                "undefined citation",
                "undefined references",
                "duplicate PDF destination",
            ],
        )

    def test_final_log_allows_reviewable_notices(self) -> None:
        log = """Underfull \\hbox (badness 10000)
No file manuscript.bbl.
Class revtex4-2 Warning: Deferred float stuck during clearpage processing.
"""

        self.assertEqual(word_to_revtex.final_log_failures(log), [])
        self.assertEqual(
            word_to_revtex.final_log_review_notices(log),
            {
                "underfull boxes": 1,
                "missing manual-bibliography .bbl": 1,
                "REVTeX float-placement warnings": 1,
            },
        )

    def test_report_update_and_cleanup_preserve_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            output_dir = root / "converted"
            output_dir.mkdir()
            report_path = output_dir / "conversion_report.json"
            report_path.write_text(json.dumps({"journal": "prl"}), encoding="utf-8")
            manuscript = output_dir / "manuscript.tex"
            manuscript.write_text("source", encoding="utf-8")
            release_pdf = root / "release" / "draft_PRL.pdf"
            release_pdf.parent.mkdir()
            release_pdf.write_bytes(b"pdf")
            for filename in word_to_revtex.BUILD_PRODUCTS:
                (output_dir / filename).write_text("build", encoding="utf-8")

            word_to_revtex.update_release_report(
                output_dir,
                {"engine": "pdflatex", "passes": 3},
                release_pdf,
                "abc123",
            )
            removed = word_to_revtex.clean_build_products(output_dir)

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["release"]["filename"], "draft_PRL.pdf")
            self.assertEqual(report["release"]["sha256"], "abc123")
            self.assertEqual(len(removed), len(word_to_revtex.BUILD_PRODUCTS))
            self.assertTrue(manuscript.is_file())
            self.assertTrue(report_path.is_file())

if __name__ == "__main__":
    unittest.main()
