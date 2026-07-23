#!/usr/bin/env python3
"""Convert, compile, validate, and publish a structured Word manuscript.

The converter deliberately keeps unstructured Word references as ``\\bibitem``
entries.  This avoids inventing or corrupting bibliographic metadata while still
producing APS-compatible, numbered citations. Use ``--source-only`` to stop
after generating the REVTeX source package.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable


PANDOC = shutil.which("pandoc")

BUILD_PRODUCTS = (
    "manuscript.aux",
    "manuscript.log",
    "manuscript.out",
    "manuscript.pdf",
    "manuscriptNotes.bib",
)

FINAL_LOG_FAILURES = (
    ("overfull box", re.compile(r"Overfull \\[hv]box")),
    (
        "undefined citation",
        re.compile(
            r"(?:LaTeX|Package natbib) Warning: Citation .+ undefined|"
            r"There were undefined citations",
            re.IGNORECASE,
        ),
    ),
    ("undefined reference", re.compile(r"LaTeX Warning: Reference .+ undefined")),
    ("undefined references", re.compile(r"There were undefined references")),
    ("multiply-defined label", re.compile(r"multiply[- ]defined labels?", re.IGNORECASE)),
    (
        "duplicate PDF destination",
        re.compile(
            r"destination with the same identifier[\s\S]{0,300}?duplicate ignored",
            re.IGNORECASE,
        ),
    ),
    (
        "unstable cross references",
        re.compile(r"Label\(s\) may have changed\. Rerun", re.IGNORECASE),
    ),
)

FINAL_LOG_REVIEW_NOTICES = (
    ("underfull boxes", re.compile(r"Underfull \\[hv]box")),
    ("missing manual-bibliography .bbl", re.compile(r"No file manuscript\.bbl")),
    (
        "REVTeX float-placement warnings",
        re.compile(r"Class revtex4-2 Warning:.*float", re.IGNORECASE),
    ),
)

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
OOXML_NS = {"w": WORD_NS, "m": MATH_NS}
WORD_TAG = "{" + WORD_NS + "}"
MATH_TAG = "{" + MATH_NS + "}"

MATH_ROMAN_BEGIN = "WTRMATHROMANBEGIN"
MATH_ROMAN_END = "WTRMATHROMANEND"
MATH_ITALIC_GREEK_BEGIN = "WTRMATHITALICGREEKBEGIN"
MATH_ITALIC_GREEK_END = "WTRMATHITALICGREEKEND"

ITALIC_GREEK_CAPITALS = {
    "Γ": r"\varGamma",
    "Δ": r"\varDelta",
    "Θ": r"\varTheta",
    "Λ": r"\varLambda",
    "Ξ": r"\varXi",
    "Π": r"\varPi",
    "Σ": r"\varSigma",
    "Υ": r"\varUpsilon",
    "Φ": r"\varPhi",
    "Ψ": r"\varPsi",
    "Ω": r"\varOmega",
}
UPRIGHT_GREEK_CAPITAL_COMMANDS = {
    r"\Gamma": r"\varGamma",
    r"\Delta": r"\varDelta",
    r"\Theta": r"\varTheta",
    r"\Lambda": r"\varLambda",
    r"\Xi": r"\varXi",
    r"\Pi": r"\varPi",
    r"\Sigma": r"\varSigma",
    r"\Upsilon": r"\varUpsilon",
    r"\Phi": r"\varPhi",
    r"\Psi": r"\varPsi",
    r"\Omega": r"\varOmega",
}

MATHEMATICAL_ITALIC_GREEK_LATEX = {
    character: command
    if len(command) == 1 or command.startswith("\\")
    else "\\" + command
    for character, command in zip(
        (
            "𝛢𝛣𝛤𝛥𝛦𝛧𝛨𝛩𝛪𝛫𝛬𝛭𝛮𝛯𝛰𝛱𝛲𝛳𝛴𝛵𝛶𝛷𝛸𝛹𝛺𝛻"
            "𝛼𝛽𝛾𝛿𝜀𝜁𝜂𝜃𝜄𝜅𝜆𝜇𝜈𝜉𝜊𝜋𝜌𝜍𝜎𝜏𝜐𝜑𝜒𝜓𝜔𝜕"
            "𝜖𝜗𝜘𝜙𝜚𝜛"
        ),
        (
            "A B \\varGamma \\varDelta E Z H \\varTheta I K \\varLambda M N "
            "\\varXi O \\varPi P \\varTheta \\varSigma T \\varUpsilon \\varPhi "
            "X \\varPsi \\varOmega \\nabla alpha beta gamma delta epsilon zeta "
            "eta theta iota kappa lambda mu nu xi o pi rho varsigma sigma tau "
            "upsilon phi chi psi omega partial varepsilon vartheta varkappa "
            "varphi varrho varpi"
        ).split(),
        strict=True,
    )
}

UNICODE_LATEX = {
    " ": " ",
    "‐": "-",
    "‑": "-",
    "‒": "--",
    "–": "--",
    "—": "---",
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "−": "--",
    "×": r"\ensuremath{\times}",
    "⨉": r"\ensuremath{\times}",
    "±": r"\ensuremath{\pm}",
    "α": r"\ensuremath{\alpha}",
    "β": r"\ensuremath{\beta}",
    "γ": r"\ensuremath{\gamma}",
    "δ": r"\ensuremath{\delta}",
    "μ": r"\ensuremath{\mu}",
    "Ω": r"\ensuremath{\Omega}",
    "Δ": r"\ensuremath{\Delta}",
    "θ": r"\ensuremath{\theta}",
    "∞": r"\ensuremath{\infty}",
    "→": r"\ensuremath{\rightarrow}",
    "↔": r"\ensuremath{\leftrightarrow}",
}
UNICODE_LATEX.update(
    {
        character: rf"\ensuremath{{{command}}}"
        for character, command in MATHEMATICAL_ITALIC_GREEK_LATEX.items()
    }
)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def positive_integer(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return result


def release_filename(input_docx: Path, journal: str, requested: str | None = None) -> str:
    """Return a safe PDF filename for the release directory."""
    if requested is None:
        return f"{input_docx.stem}_{journal.upper()}.pdf"
    if not requested.strip() or Path(requested).is_absolute() or any(
        separator in requested for separator in ("/", "\\")
    ):
        raise ValueError("--release-name must be a filename, not a path")
    candidate = Path(requested)
    if not candidate.suffix:
        return requested + ".pdf"
    if candidate.suffix.lower() != ".pdf":
        raise ValueError("--release-name must end in .pdf")
    return requested


def final_log_failures(log_text: str) -> list[str]:
    """Return release-blocking findings from the final LaTeX pass."""
    return [description for description, pattern in FINAL_LOG_FAILURES if pattern.search(log_text)]


def final_log_review_notices(log_text: str) -> dict[str, int]:
    """Count nonblocking findings that still merit visual review."""
    return {
        description: len(pattern.findall(log_text))
        for description, pattern in FINAL_LOG_REVIEW_NOTICES
        if pattern.search(log_text)
    }


def compile_manuscript(manuscript_path: Path, passes: int = 3) -> tuple[Path, dict[str, Any]]:
    """Compile a generated manuscript and validate the final log and PDF."""
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        raise RuntimeError(
            "pdflatex is required for automatic PDF creation but was not found on PATH. "
            "Install a TeX distribution or use --source-only."
        )

    for pass_number in range(1, passes + 1):
        try:
            run(
                [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    manuscript_path.name,
                ],
                cwd=manuscript_path.parent,
            )
        except subprocess.CalledProcessError as error:
            command_output = "\n".join(part for part in (error.stdout, error.stderr) if part)
            tail = "\n".join(command_output.splitlines()[-20:])
            detail = f"\n{tail}" if tail else ""
            raise RuntimeError(f"pdflatex pass {pass_number}/{passes} failed.{detail}") from error

    log_path = manuscript_path.with_suffix(".log")
    if not log_path.is_file():
        raise RuntimeError(f"pdflatex did not create the expected log: {log_path}")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    failures = final_log_failures(log_text)
    if failures:
        raise RuntimeError("final LaTeX log failed release checks: " + ", ".join(failures))

    pdf_path = manuscript_path.with_suffix(".pdf")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"pdflatex did not create a nonempty PDF: {pdf_path}")

    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        raise RuntimeError(
            "pdfinfo is required to validate the release PDF but was not found on PATH."
        )
    info_output = run([pdfinfo, str(pdf_path)]).stdout
    info_fields = {
        key.strip(): value.strip()
        for line in info_output.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    try:
        page_count = int(info_fields["Pages"])
    except (KeyError, ValueError) as error:
        raise RuntimeError("pdfinfo did not report a valid page count") from error
    if page_count < 1:
        raise RuntimeError("the compiled PDF contains no pages")

    return pdf_path, {
        "engine": "pdflatex",
        "passes": passes,
        "final_log_validation": "passed",
        "review_notices": final_log_review_notices(log_text),
        "pages": page_count,
        "page_size": info_fields.get("Page size", "unknown"),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_pdf(pdf_path: Path, release_dir: Path, filename: str) -> tuple[Path, str]:
    """Atomically copy a validated PDF to its final release filename."""
    release_dir.mkdir(parents=True, exist_ok=True)
    release_path = release_dir / filename
    if release_path.resolve() == pdf_path.resolve():
        raise ValueError("the release PDF path must differ from the working PDF path")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{filename}.", suffix=".tmp", dir=release_dir, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        shutil.copy2(pdf_path, temporary_path)
        source_hash = sha256_file(pdf_path)
        release_hash = sha256_file(temporary_path)
        if release_hash != source_hash:
            raise RuntimeError("release PDF checksum does not match the compiled PDF")
        temporary_path.replace(release_path)
        temporary_path = None
        return release_path, release_hash
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def update_release_report(
    output_dir: Path,
    compilation: dict[str, Any],
    release_path: Path,
    checksum: str,
) -> None:
    report_path = output_dir / "conversion_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["compilation"] = compilation
    report["release"] = {
        "filename": release_path.name,
        "sha256": checksum,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def clean_build_products(output_dir: Path) -> list[Path]:
    removed: list[Path] = []
    for filename in BUILD_PRODUCTS:
        path = output_dir / filename
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def _accept_revisions_xml(xml: str) -> tuple[str, int]:
    """Accept revision wrappers without reserializing the surrounding OOXML."""
    tag_pattern = re.compile(
        r"<(?P<closing>/?)w:(?P<tag>ins|del|moveTo|moveFrom)\b[^>]*>", re.IGNORECASE
    )
    keep = {"ins": True, "moveto": True, "del": False, "movefrom": False}
    output: list[str] = []
    stack: list[bool] = []
    skip_depth = 0
    cursor = 0
    processed = 0
    for match in tag_pattern.finditer(xml):
        if skip_depth == 0:
            output.append(xml[cursor : match.start()])
        tag = match.group("tag").lower()
        if not match.group("closing"):
            action = keep[tag]
            stack.append(action)
            if not action:
                skip_depth += 1
            processed += 1
        else:
            if not stack:
                raise ValueError(f"Unbalanced tracked-change closing tag: {match.group(0)}")
            action = stack.pop()
            if not action:
                skip_depth -= 1
        cursor = match.end()
    if stack:
        raise ValueError("Unbalanced tracked-change wrappers in word/document.xml")
    output.append(xml[cursor:])
    return "".join(output), processed


def accept_tracked_changes(source: Path, destination: Path) -> int:
    """Write a temporary DOCX with current tracked changes accepted.

    Word revision wrappers around OMML equations can otherwise be reduced to
    empty math by Pandoc.  The source file is never modified.
    """
    processed = 0
    replacements: dict[str, bytes] = {}
    with zipfile.ZipFile(source, "r") as archive:
        if "word/document.xml" in archive.namelist():
            document_xml = archive.read("word/document.xml").decode("utf-8")
            document_xml, processed = _accept_revisions_xml(document_xml)
            replacements["word/document.xml"] = document_xml.encode("utf-8")
        if "word/settings.xml" in archive.namelist():
            settings_xml = archive.read("word/settings.xml").decode("utf-8")
            settings_xml = re.sub(r"<w:trackRevisions\b[^>]*/>", "", settings_xml)
            replacements["word/settings.xml"] = settings_xml.encode("utf-8")

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
            for item in archive.infolist():
                output.writestr(item, replacements.get(item.filename, archive.read(item.filename)))
    return processed


def _mark_math_run_styles_xml(document_xml: str) -> tuple[str, dict[str, int]]:
    r"""Mark Word upright math runs so their style survives Pandoc.

    Pandoc currently drops OMML ``m:sty`` values that distinguish plain from
    italic math. Temporary sentinels survive the DOCX reader and are converted
    to ``\mathrm`` after Pandoc emits LaTeX. Bold-upright runs use the same
    inner sentinel; Pandoc's surrounding bold command is retained. Explicit or
    default italic uppercase Greek is converted to amsmath's ``\varGamma``-like
    alphabet because standard LaTeX uppercase Greek is otherwise upright.
    """
    counts = {
        "plain": 0,
        "bold_upright": 0,
        "italic_greek_capital": 0,
        "bold_italic_greek_capital": 0,
    }

    def mark_run(match: re.Match[str]) -> str:
        run_xml = match.group(0)
        style_match = re.search(
            r"<m:sty\b[^>]*\bm:val=(['\"])(bi|p|b|i)\1[^>]*/?>",
            run_xml,
            flags=re.IGNORECASE,
        )
        if style_match:
            style = style_match.group(2).lower()
        elif re.search(r"<m:nor\b[^>]*/?>", run_xml, flags=re.IGNORECASE):
            style = "p"
        else:
            # OMML's default math style is italic. This matters for uppercase
            # Greek because LaTeX renders \Omega-like commands upright unless
            # their italic \varOmega-like variants are requested explicitly.
            style = "i"

        marked_segments = 0

        def mark_text(text_match: re.Match[str]) -> str:
            nonlocal marked_segments
            decoded = html.unescape(text_match.group(2))

            if style in {"p", "b"}:
                def mark_letters(letters: re.Match[str]) -> str:
                    nonlocal marked_segments
                    marked_segments += 1
                    return MATH_ROMAN_BEGIN + letters.group(0) + MATH_ROMAN_END

                marked = re.sub(r"[^\W\d_]+", mark_letters, decoded, flags=re.UNICODE)
            else:
                def mark_greek_capital(character: re.Match[str]) -> str:
                    nonlocal marked_segments
                    marked_segments += 1
                    return (
                        MATH_ITALIC_GREEK_BEGIN
                        + character.group(0)
                        + MATH_ITALIC_GREEK_END
                    )

                greek_pattern = "[" + re.escape("".join(ITALIC_GREEK_CAPITALS)) + "]"
                marked = re.sub(greek_pattern, mark_greek_capital, decoded)
            escaped = html.escape(marked, quote=False)
            return text_match.group(1) + escaped + text_match.group(3)

        marked_run = re.sub(
            r"(<m:t\b[^>]*>)(.*?)(</m:t>)",
            mark_text,
            run_xml,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if marked_segments:
            keys = {
                "p": "plain",
                "b": "bold_upright",
                "i": "italic_greek_capital",
                "bi": "bold_italic_greek_capital",
            }
            key = keys[style]
            counts[key] += marked_segments
        return marked_run

    marked_xml = re.sub(
        r"<m:r\b[^>]*>.*?</m:r>",
        mark_run,
        document_xml,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return marked_xml, counts


def preserve_math_run_styles(source: Path, destination: Path) -> dict[str, int]:
    """Write a temporary DOCX whose OMML upright styles survive Pandoc."""
    counts = {
        "plain": 0,
        "bold_upright": 0,
        "italic_greek_capital": 0,
        "bold_italic_greek_capital": 0,
    }
    replacements: dict[str, bytes] = {}
    with zipfile.ZipFile(source, "r") as archive:
        if "word/document.xml" in archive.namelist():
            document_xml = archive.read("word/document.xml").decode("utf-8")
            document_xml, counts = _mark_math_run_styles_xml(document_xml)
            replacements["word/document.xml"] = document_xml.encode("utf-8")

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
            for item in archive.infolist():
                output.writestr(item, replacements.get(item.filename, archive.read(item.filename)))
    return counts


def _indent_attributes(element: ET.Element | None) -> dict[str, str]:
    if element is None:
        return {}
    return {name.rsplit("}", 1)[-1]: value for name, value in element.attrib.items()}


def _merge_indent(target: dict[str, str], element: ET.Element | None) -> None:
    attributes = _indent_attributes(element)
    if any(name in attributes for name in ("firstLine", "firstLineChars")):
        target.pop("hanging", None)
        target.pop("hangingChars", None)
    if any(name in attributes for name in ("hanging", "hangingChars")):
        target.pop("firstLine", None)
        target.pop("firstLineChars", None)
    target.update(attributes)


def _paragraph_text(paragraph: ET.Element) -> str:
    text_tags = {WORD_TAG + "t", MATH_TAG + "t"}
    return "".join(node.text or "" for node in paragraph.iter() if node.tag in text_tags)


def equation_following_paragraph_indents_from_xml(
    document_xml: str, styles_xml: str
) -> dict[str, bool]:
    """Return whether Word indents the paragraph following each numbered display."""
    document_root = ET.fromstring(document_xml)
    styles_root = ET.fromstring(styles_xml)

    default_indent: dict[str, str] = {}
    _merge_indent(
        default_indent,
        styles_root.find("w:docDefaults/w:pPrDefault/w:pPr/w:ind", OOXML_NS),
    )

    default_style_id: str | None = None
    style_definitions: dict[str, tuple[str | None, ET.Element | None]] = {}
    for style in styles_root.findall("w:style", OOXML_NS):
        if style.get(WORD_TAG + "type") != "paragraph":
            continue
        style_id = style.get(WORD_TAG + "styleId")
        if not style_id:
            continue
        if style.get(WORD_TAG + "default") == "1":
            default_style_id = style_id
        based_on = style.find("w:basedOn", OOXML_NS)
        style_definitions[style_id] = (
            based_on.get(WORD_TAG + "val") if based_on is not None else None,
            style.find("w:pPr/w:ind", OOXML_NS),
        )

    def paragraph_is_indented(paragraph: ET.Element) -> bool:
        paragraph_properties = paragraph.find("w:pPr", OOXML_NS)
        style_node = (
            paragraph_properties.find("w:pStyle", OOXML_NS)
            if paragraph_properties is not None
            else None
        )
        style_id = (
            style_node.get(WORD_TAG + "val") if style_node is not None else default_style_id
        )
        style_chain: list[str] = []
        seen: set[str] = set()
        while style_id and style_id not in seen:
            seen.add(style_id)
            style_chain.append(style_id)
            style_id = style_definitions.get(style_id, (None, None))[0]

        effective_indent = dict(default_indent)
        for inherited_style in reversed(style_chain):
            _merge_indent(
                effective_indent,
                style_definitions.get(inherited_style, (None, None))[1],
            )
        direct_indent = (
            paragraph_properties.find("w:ind", OOXML_NS)
            if paragraph_properties is not None
            else None
        )
        _merge_indent(effective_indent, direct_indent)

        value = effective_indent.get("firstLineChars")
        if value is None:
            value = effective_indent.get("firstLine")
        try:
            return value is not None and int(value) > 0
        except ValueError:
            return False

    body = document_root.find("w:body", OOXML_NS)
    if body is None:
        return {}
    paragraphs = list(body.iter(WORD_TAG + "p"))
    texts = [_paragraph_text(paragraph) for paragraph in paragraphs]
    decisions: dict[str, bool] = {}
    for index, text in enumerate(texts):
        match = re.search(r"[,.]?\s*\((\d+[a-z]?)\)\s*$", text, flags=re.IGNORECASE)
        if not match:
            continue
        following_index = index + 1
        while following_index < len(paragraphs) and not texts[following_index].strip():
            following_index += 1
        if following_index < len(paragraphs):
            decisions[match.group(1)] = paragraph_is_indented(paragraphs[following_index])
    return decisions


def equation_following_paragraph_indents(source: Path) -> dict[str, bool]:
    """Read effective post-equation first-line indentation from a DOCX."""
    with zipfile.ZipFile(source, "r") as archive:
        if "word/document.xml" not in archive.namelist():
            return {}
        document_xml = archive.read("word/document.xml").decode("utf-8")
        if "word/styles.xml" in archive.namelist():
            styles_xml = archive.read("word/styles.xml").decode("utf-8")
        else:
            styles_xml = f'<w:styles xmlns:w="{WORD_NS}"/>'
    return equation_following_paragraph_indents_from_xml(document_xml, styles_xml)


def inline_plain(inline: dict[str, Any]) -> str:
    kind = inline.get("t")
    content = inline.get("c")
    if kind == "Str":
        return str(content)
    if kind in {"Space", "SoftBreak", "LineBreak"}:
        return " "
    if kind == "Math":
        return str(content[1])
    if kind in {"Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
        return inlines_plain(content)
    if kind in {"Span", "Link", "Image"}:
        return inlines_plain(content[1])
    if kind == "Code":
        return str(content[1])
    if kind == "Quoted":
        return inlines_plain(content[1])
    if kind == "Note":
        return ""
    return ""


def inlines_plain(inlines: Iterable[dict[str, Any]]) -> str:
    text = "".join(inline_plain(inline) for inline in inlines)
    return re.sub(r"\s+", " ", text).strip()


def block_plain(block: dict[str, Any]) -> str:
    if block.get("t") in {"Para", "Plain", "Header"}:
        content = block["c"][-1] if block["t"] == "Header" else block["c"]
        return inlines_plain(content)
    return ""


def superscript_marker(inline: dict[str, Any]) -> str:
    if inline.get("t") == "Superscript":
        return "{" + inlines_plain(inline["c"]) + "}"
    return inline_plain(inline)


def marked_text(block: dict[str, Any]) -> str:
    if block.get("t") not in {"Para", "Plain"}:
        return ""
    return re.sub(r"\s+", " ", "".join(superscript_marker(i) for i in block["c"])).strip()


def starts_with_superscript(block: dict[str, Any]) -> bool:
    return block.get("t") in {"Para", "Plain"} and bool(block.get("c")) and block["c"][0].get("t") == "Superscript"


def doc_with_blocks(document: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"pandoc-api-version": document["pandoc-api-version"], "meta": {}, "blocks": blocks}
    return result


def pandoc_latex(document: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    proc = subprocess.run(
        [PANDOC or "pandoc", "-f", "json", "-t", "latex", "--wrap=preserve"],
        input=json.dumps(doc_with_blocks(document, blocks), ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


def replace_unicode(tex: str) -> str:
    for char, replacement in UNICODE_LATEX.items():
        tex = tex.replace(char, replacement)
    return tex


def normalize_citations(tex: str) -> str:
    bracketed = re.compile(r"\{\[\}(.*?)\{\]\}", re.DOTALL)

    def citation(match: re.Match[str]) -> str:
        body = match.group(1)
        if "\\hyperref[_Ref" not in body:
            return match.group(0)
        numbers = [int(n) for n in re.findall(r"\\hyperref\[_Ref\d+\]\{(\d+)\}", body)]
        if not numbers:
            return match.group(0)
        if len(numbers) == 2 and "--" in body and numbers[1] >= numbers[0]:
            numbers = list(range(numbers[0], numbers[1] + 1))
        keys = ",".join(f"ref{number}" for number in numbers)
        return rf"\cite{{{keys}}}"

    tex = bracketed.sub(citation, tex)
    tex = re.sub(r"\\protect\\phantomsection\\label\{_Ref\d+\}", "", tex)
    return tex


def normalize_tex(tex: str) -> str:
    tex = normalize_citations(tex)
    tex = replace_unicode(tex)
    tex = re.sub(
        re.escape(MATH_ROMAN_BEGIN) + r"(.*?)" + re.escape(MATH_ROMAN_END),
        lambda match: r"\mathrm{" + match.group(1).strip() + "}",
        tex,
        flags=re.DOTALL,
    )
    def restore_italic_greek(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        for upright, italic in UPRIGHT_GREEK_CAPITAL_COMMANDS.items():
            content = re.sub(re.escape(upright) + r"\s*", lambda _match: italic, content)
        return content

    tex = re.sub(
        re.escape(MATH_ITALIC_GREEK_BEGIN)
        + r"(.*?)"
        + re.escape(MATH_ITALIC_GREEK_END),
        restore_italic_greek,
        tex,
        flags=re.DOTALL,
    )
    # Pandoc maps bold Word math to \mathbf. Bold-upright Word runs already
    # contain an inner \mathrm marker at this point; changing the outer command
    # to \bm therefore preserves both bold-upright and bold-italic source runs
    # while also supporting bold Greek symbols.
    tex = re.sub(r"\\mathbf\b", lambda _match: r"\bm", tex)
    tex = tex.replace("{[}", "[").replace("{]}", "]")
    tex = tex.replace(r"\textasciitilde", r"\ensuremath{\sim}")
    # Word sometimes ends italic formatting one character before the end of a
    # word.  Join that split so the LaTeX source does not preserve a visual typo.
    tex = re.sub(r"\\emph\{([A-Za-z]{2,})\}([a-z])(?=[\s,.;:])", r"\\emph{\1\2}", tex)
    tex = re.sub(r"(?m)^\{\}(?=\S)", "", tex)
    tex = re.sub(r"[ \t]+\n", "\n", tex)
    tex = re.sub(r"\n{3,}", "\n\n", tex)
    return tex.strip()


def parse_authors(block: dict[str, Any]) -> list[dict[str, Any]]:
    marked = marked_text(block)
    authors: list[dict[str, Any]] = []
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", marked):
        raw_name = marked[cursor : match.start()].strip(" ,")
        raw_name = re.sub(r"^(?:and|&)\s+", "", raw_name, flags=re.IGNORECASE)
        marker = match.group(1)
        affiliation_ids = re.findall(r"\d+", marker)
        if raw_name:
            authors.append(
                {
                    "name": raw_name,
                    "affiliations": affiliation_ids,
                    "corresponding": "*" in marker,
                }
            )
        cursor = match.end()
    if authors:
        return authors

    plain = block_plain(block)
    parts = [p.strip() for p in re.split(r",|\band\b|&", plain) if p.strip()]
    return [{"name": part, "affiliations": [], "corresponding": False} for part in parts]


def parse_affiliations(blocks: list[dict[str, Any]]) -> dict[str, str]:
    affiliations: dict[str, str] = {}
    for fallback, block in enumerate(blocks, start=1):
        marked = marked_text(block)
        match = re.match(r"\{([^{}]+)\}(.*)", marked)
        if match:
            key = re.search(r"\d+", match.group(1))
            affiliations[key.group(0) if key else str(fallback)] = match.group(2).strip()
        else:
            affiliations[str(fallback)] = block_plain(block)
    return affiliations


def image_from_block(block: dict[str, Any]) -> dict[str, Any] | None:
    if block.get("t") not in {"Para", "Plain"} or len(block.get("c", [])) != 1:
        return None
    inline = block["c"][0]
    return inline if inline.get("t") == "Image" else None


def caption_number(block: dict[str, Any]) -> int | None:
    match = re.match(r"(?:FIG\.|Figure)\s*(\d+)\.?", block_plain(block), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def strip_caption_prefix(inlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    copied = copy.deepcopy(inlines)
    while copied and copied[0].get("t") in {"Space", "SoftBreak"}:
        copied.pop(0)
    if copied and copied[0].get("t") == "Strong":
        prefix = inlines_plain(copied[0]["c"])
        if re.match(r"(?:FIG\.|Figure)\s*\d+\.?", prefix, flags=re.IGNORECASE):
            copied.pop(0)
            while copied and copied[0].get("t") in {"Space", "SoftBreak"}:
                copied.pop(0)
            return copied
    plain_prefix = re.compile(r"^(?:FIG\.|Figure)\s*\d+\.?")
    if copied and copied[0].get("t") == "Str":
        copied[0]["c"] = plain_prefix.sub("", copied[0]["c"]).lstrip()
    return copied


def extract_figures(
    document: dict[str, Any], blocks: list[dict[str, Any]], media_root: Path, output_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    figures: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    index = 0
    while index < len(blocks):
        image = image_from_block(blocks[index])
        if image and index + 1 < len(blocks):
            number = caption_number(blocks[index + 1])
            if number is not None:
                attributes, _alt, target = image["c"]
                source = Path(target[0])
                if not source.is_absolute():
                    source = media_root / source
                if not source.exists():
                    candidates = list(media_root.rglob(Path(target[0]).name))
                    if not candidates:
                        raise FileNotFoundError(f"Extracted image not found: {target[0]}")
                    source = candidates[0]
                suffix = source.suffix.lower() or ".png"
                destination = figures_dir / f"figure{number}{suffix}"
                shutil.copy2(source, destination)
                sizes = dict(attributes[2])
                width_in = 0.0
                if sizes.get("width", "").endswith("in"):
                    width_in = float(sizes["width"][:-2])
                caption_block = copy.deepcopy(blocks[index + 1])
                caption_block["c"] = strip_caption_prefix(caption_block["c"])
                caption_tex = normalize_tex(pandoc_latex(document, [caption_block]))
                figures.append(
                    {
                        "number": number,
                        "file": destination.relative_to(output_dir).as_posix(),
                        "caption": caption_tex,
                        "width_in": width_in,
                    }
                )
                index += 2
                continue
        retained.append(blocks[index])
        index += 1
    return retained, sorted(figures, key=lambda item: item["number"])


def equation_block(block: dict[str, Any]) -> tuple[str, str, str] | None:
    if block.get("t") not in {"Para", "Plain"}:
        return None
    content = block.get("c", [])
    if not content or content[0].get("t") != "Math":
        return None
    tail = inlines_plain(content[1:])
    match = re.fullmatch(r"([,.]?)\s*\((\d+[a-z]?)\)", tail, flags=re.IGNORECASE)
    if not match:
        return None
    return content[0]["c"][1], match.group(2), match.group(1)


def make_equation(math: str, label: str, punctuation: str = "") -> dict[str, Any]:
    environment = "equation"
    punctuated_math = math + punctuation
    display_math = punctuated_math
    if len(math) > 115:
        # Keep display equations inside one column, but never enlarge a short
        # equation merely because its source contains verbose LaTeX commands.
        display_math = "\\fitcolumn{" + punctuated_math + "}"
    equation = (
        "\\stepcounter{equation}\n"
        f"\\begin{{{environment}}}\n"
        f"{display_math}\n"
        f"\\tag{{{label}}}\\label{{eq:{label}}}\n"
        f"\\end{{{environment}}}"
    )
    return {"t": "RawBlock", "c": ["latex", equation]}


def suppress_paragraph_indent(block: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    r"""Prefix the next text paragraph with ``\noindent``.

    Word stores a displayed equation and its following prose as separate
    paragraphs.  LaTeX otherwise decides indentation from its own paragraph
    rules, so an explicit marker is needed when Word's effective first-line
    indentation is zero.
    """
    if block.get("t") not in {"Para", "Plain"}:
        return block, False
    copied = copy.deepcopy(block)
    copied["c"].insert(0, {"t": "RawInline", "c": ["latex", "\\noindent "]})
    return copied, True


def figure_block(figure: dict[str, Any]) -> dict[str, Any]:
    environment = "figure"
    width = "\\columnwidth"
    text = (
        f"\\begin{{{environment}}}[tbp]\n"
        "\\centering\n"
        f"\\includegraphics[width={width}]{{{figure['file']}}}\n"
        f"\\caption{{{figure['caption']}}}\n"
        f"\\label{{fig:{figure['number']}}}\n"
        f"\\end{{{environment}}}"
    )
    return {"t": "RawBlock", "c": ["latex", text]}


def prepare_body(
    blocks: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    following_paragraph_indents: dict[str, bool] | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    converted: list[dict[str, Any]] = []
    equation_labels: set[str] = set()
    pending = {figure["number"]: figure for figure in figures}
    suppress_next_indent = False
    for block in blocks:
        equation = equation_block(block)
        if equation:
            math, label, punctuation = equation
            equation_labels.add(label)
            converted.append(make_equation(math, label, punctuation))
            if following_paragraph_indents and label in following_paragraph_indents:
                suppress_next_indent = not following_paragraph_indents[label]
            else:
                # Fall back to grammar when Word indentation metadata is not
                # available: a comma continues the same sentence.
                suppress_next_indent = punctuation == ","
        else:
            prepared_block = block
            if suppress_next_indent:
                prepared_block, indent_suppressed = suppress_paragraph_indent(block)
                if indent_suppressed:
                    suppress_next_indent = False
            converted.append(prepared_block)
        plain = block_plain(block)
        for number in list(pending):
            if re.search(rf"\bFig(?:ure)?s?\.?(?:~|\s)+{number}\b", plain, flags=re.IGNORECASE):
                converted.append(figure_block(pending.pop(number)))
    for number in sorted(pending):
        converted.append(figure_block(pending[number]))
    return converted, equation_labels


def normalize_crossrefs(tex: str, figures: list[dict[str, Any]], equation_labels: set[str]) -> str:
    for figure in figures:
        number = figure["number"]
        tex = re.sub(
            rf"\bFig\.\s*{number}\b",
            rf"Fig.~\\ref{{fig:{number}}}",
            tex,
        )
    for label in sorted(equation_labels, key=len, reverse=True):
        tex = re.sub(
            rf"\b(?:Eq\.|Equation)\s*\({re.escape(label)}\)",
            rf"Eq.~(\\ref{{eq:{label}}})",
            tex,
        )
    base_labels = sorted({re.match(r"\d+", label).group(0) for label in equation_labels})
    for base in base_labels:
        variants = sorted(label for label in equation_labels if label.startswith(base) and label != base)
        if variants:
            rendered = " and ".join(rf"(\\ref{{eq:{label}}})" for label in variants)
            tex = re.sub(
                rf"\bEq\.\s*\({base}\)",
                "Eqs.~" + rendered,
                tex,
            )
    return tex


def reference_environment(document: dict[str, Any], ordered_list: dict[str, Any]) -> str:
    items = ordered_list["c"][1]
    blocks: list[dict[str, Any]] = [
        {"t": "RawBlock", "c": ["latex", f"\\begin{{thebibliography}}{{{len(items)}}}"]}
    ]
    for number, item in enumerate(items, start=1):
        blocks.append({"t": "RawBlock", "c": ["latex", f"\\bibitem{{ref{number}}}"]})
        blocks.extend(item)
    blocks.append({"t": "RawBlock", "c": ["latex", "\\end{thebibliography}"]})
    return normalize_tex(pandoc_latex(document, blocks))


def frontmatter_tex(
    title: str,
    authors: list[dict[str, Any]],
    affiliations: dict[str, str],
    abstract: str,
) -> str:
    lines = [f"\\title{{{title}}}", ""]
    for author in authors:
        lines.append(f"\\author{{{author['name']}}}")
        if author["corresponding"]:
            lines.append("\\thanks{Corresponding author.}")
        for key in author["affiliations"]:
            if key in affiliations:
                lines.append(f"\\affiliation{{{affiliations[key]}}}")
        lines.append("")
    if authors and not any(author["affiliations"] for author in authors):
        for affiliation in affiliations.values():
            lines.append(f"\\affiliation{{{affiliation}}}")
    lines.extend(
        [
            # Do not emit \date: REVTeX prints “(Dated:)” even for \date{}.
            "",
            "\\begin{abstract}",
            abstract,
            "\\end{abstract}",
            "",
            "\\maketitle",
        ]
    )
    return "\n".join(lines)


def build_manuscript(input_docx: Path, output_dir: Path, journal: str, layout: str) -> Path:
    if not PANDOC:
        raise RuntimeError("Pandoc is required but was not found on PATH.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="word_to_revtex_") as temp_name:
        temp_dir = Path(temp_name)
        accepted_docx = temp_dir / "accepted.docx"
        revisions_accepted = accept_tracked_changes(input_docx, accepted_docx)
        following_paragraph_indents = equation_following_paragraph_indents(accepted_docx)
        styled_docx = temp_dir / "styled.docx"
        math_styles_preserved = preserve_math_run_styles(accepted_docx, styled_docx)
        json_path = temp_dir / "document.json"
        run(
            [
                PANDOC,
                str(styled_docx),
                f"--extract-media={temp_dir}",
                "-t",
                "json",
                "-o",
                str(json_path),
            ]
        )
        document = json.loads(json_path.read_text(encoding="utf-8"))
        all_blocks = document["blocks"]
        if len(all_blocks) < 5:
            raise ValueError("The document is too short for automatic manuscript inference.")

        title_block = all_blocks[0]
        author_block = all_blocks[1]
        affiliation_blocks: list[dict[str, Any]] = []
        cursor = 2
        while cursor < len(all_blocks) and starts_with_superscript(all_blocks[cursor]):
            affiliation_blocks.append(all_blocks[cursor])
            cursor += 1
        if not affiliation_blocks and cursor < len(all_blocks):
            affiliation_blocks.append(all_blocks[cursor])
            cursor += 1
        abstract_block = all_blocks[cursor]
        body_start = cursor + 1

        acknowledgment_index = next(
            (
                i
                for i in range(body_start, len(all_blocks))
                if re.match(r"Acknowledg(?:e)?ments?", block_plain(all_blocks[i]), flags=re.IGNORECASE)
            ),
            None,
        )
        reference_index = next(
            (
                i
                for i in range(body_start, len(all_blocks))
                if all_blocks[i].get("t") == "OrderedList" and len(all_blocks[i]["c"][1]) >= 3
            ),
            None,
        )
        body_end_candidates = [i for i in (acknowledgment_index, reference_index) if i is not None]
        body_end = min(body_end_candidates) if body_end_candidates else len(all_blocks)

        body_blocks = all_blocks[body_start:body_end]
        if acknowledgment_index is not None:
            ack_end = reference_index if reference_index is not None else len(all_blocks)
            acknowledgment_blocks = all_blocks[acknowledgment_index + 1 : ack_end]
        else:
            acknowledgment_blocks = []

        # Figures are often placed after the Word reference list.  Search the
        # entire post-frontmatter stream, then remove image/caption pairs from
        # each logical section before inserting floats near their first callout.
        searchable = all_blocks[body_start:]
        _without_figures, figures = extract_figures(document, searchable, temp_dir, output_dir)
        body_blocks, _ignored = extract_figures(document, body_blocks, temp_dir, output_dir)
        acknowledgment_blocks, _ignored = extract_figures(
            document, acknowledgment_blocks, temp_dir, output_dir
        )

        title = normalize_tex(pandoc_latex(document, [title_block]))
        authors = parse_authors(author_block)
        affiliations = {
            key: normalize_tex(value) for key, value in parse_affiliations(affiliation_blocks).items()
        }
        abstract = normalize_tex(pandoc_latex(document, [abstract_block]))
        body_blocks, equation_labels = prepare_body(
            body_blocks, figures, following_paragraph_indents
        )
        body = normalize_tex(pandoc_latex(document, body_blocks))
        body = normalize_crossrefs(body, figures, equation_labels)
        acknowledgment = normalize_tex(pandoc_latex(document, acknowledgment_blocks))

        bibliography = ""
        if reference_index is not None:
            bibliography = reference_environment(document, all_blocks[reference_index])

        converter_name = Path(__file__).name
        preamble = f"""% Generated from {input_docx.name} by {converter_name}
% Review the inferred author metadata and bibliography before submission.
\\documentclass[aps,{journal},{layout},superscriptaddress,floatfix]{{revtex4-2}}

\\usepackage{{graphicx}}
\\usepackage{{amsmath,amssymb,bm,mathtools}}
\\usepackage[hidelinks]{{hyperref}}

% Shrink wide display equations to one column; leave narrower ones unchanged.
\\newsavebox{{\\fitcolumnbox}}
\\newcommand{{\\fitcolumn}}[1]{{%
  \\sbox{{\\fitcolumnbox}}{{ $\\displaystyle #1$ }}%
  \\ifdim\\wd\\fitcolumnbox>\\columnwidth
    \\resizebox{{\\columnwidth}}{{!}}{{\\usebox{{\\fitcolumnbox}}}}%
  \\else
    \\usebox{{\\fitcolumnbox}}%
  \\fi
}}

\\begin{{document}}
"""
        sections = [
            preamble.rstrip(),
            frontmatter_tex(title, authors, affiliations, abstract),
            body,
        ]
        if acknowledgment:
            sections.append(
                "\\begin{acknowledgments}\n"
                + acknowledgment
                + "\n\\end{acknowledgments}"
            )
        if bibliography:
            sections.append(bibliography)
        sections.append("\\end{document}")
        manuscript = normalize_tex("\n\n".join(sections)) + "\n"
        manuscript_path = output_dir / "manuscript.tex"
        manuscript_path.write_text(manuscript, encoding="utf-8")

        metadata = {
            "source": input_docx.name,
            "journal": journal,
            "layout": layout,
            "title": block_plain(title_block),
            "authors": authors,
            "affiliations": parse_affiliations(affiliation_blocks),
            "figures": len(figures),
            "references": len(all_blocks[reference_index]["c"][1]) if reference_index is not None else 0,
            "numbered_equations": sorted(equation_labels),
            "equation_following_paragraph_indented": {
                label: following_paragraph_indents[label]
                for label in sorted(following_paragraph_indents)
                if label in equation_labels
            },
            "tracked_revision_elements_accepted": revisions_accepted,
            "math_style_segments_preserved": math_styles_preserved,
        }
        (output_dir / "conversion_report.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manuscript_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a Word manuscript to APS REVTeX 4.2, compile it, validate the "
            "PDF, and publish the PDF to 03_release."
        )
    )
    parser.add_argument("input", type=Path, help="Input .docx manuscript")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="LaTeX source directory (default: 02_converted/<name>_<journal>)",
    )
    parser.add_argument("--journal", choices=("prl", "prb"), default="prl")
    parser.add_argument("--layout", choices=("reprint", "preprint"), default="reprint")
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Generate LaTeX source without compiling or publishing a PDF",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        help="Final PDF directory (default: repository 03_release)",
    )
    parser.add_argument(
        "--release-name",
        help="Final PDF filename; .pdf is added when omitted",
    )
    parser.add_argument(
        "--passes",
        type=positive_integer,
        default=3,
        help="Number of pdflatex passes (default: 3)",
    )
    parser.add_argument(
        "--keep-build-files",
        action="store_true",
        help="Keep manuscript.pdf and LaTeX auxiliary files in the source directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_docx = args.input.resolve()
    if not input_docx.is_file() or input_docx.suffix.lower() != ".docx":
        print(f"error: not a .docx file: {input_docx}", file=sys.stderr)
        return 2
    repository_root = Path(__file__).resolve().parent
    output_dir = (
        args.output_dir
        or repository_root / "02_converted" / f"{input_docx.stem}_{args.journal}"
    ).resolve()
    try:
        manuscript = build_manuscript(input_docx, output_dir, args.journal, args.layout)
        if args.source_only:
            print(f"LaTeX source: {manuscript}")
            return 0

        filename = release_filename(input_docx, args.journal, args.release_name)
        release_dir = (args.release_dir or repository_root / "03_release").resolve()
        working_pdf, compilation = compile_manuscript(manuscript, args.passes)
        release_pdf, checksum = publish_pdf(working_pdf, release_dir, filename)
        update_release_report(output_dir, compilation, release_pdf, checksum)
        if not args.keep_build_files:
            clean_build_products(output_dir)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"LaTeX source: {manuscript}")
    print(f"Release PDF: {release_pdf}")
    print(f"SHA-256: {checksum}")
    if compilation["review_notices"]:
        summary = ", ".join(
            f"{description}: {count}"
            for description, count in compilation["review_notices"].items()
        )
        print(f"Visual-review notices: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
