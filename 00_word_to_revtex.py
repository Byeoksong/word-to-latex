#!/usr/bin/env python3
"""Convert a structured Word manuscript to an APS REVTeX source package.

The converter deliberately keeps unstructured Word references as ``\\bibitem``
entries.  This avoids inventing or corrupting bibliographic metadata while still
producing APS-compatible, numbered citations.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable


PANDOC = shutil.which("pandoc")

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
    "𝛼": r"\ensuremath{\alpha}",
    "β": r"\ensuremath{\beta}",
    "γ": r"\ensuremath{\gamma}",
    "δ": r"\ensuremath{\delta}",
    "μ": r"\ensuremath{\mu}",
    "𝜇": r"\ensuremath{\mu}",
    "Ω": r"\ensuremath{\Omega}",
    "𝛺": r"\ensuremath{\Omega}",
    "Δ": r"\ensuremath{\Delta}",
    "θ": r"\ensuremath{\theta}",
    "∞": r"\ensuremath{\infty}",
    "→": r"\ensuremath{\rightarrow}",
    "↔": r"\ensuremath{\leftrightarrow}",
}


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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
    # Pandoc maps bold Word math to \mathbf, which forces Latin variables
    # upright. Preserve the source's bold emphasis while retaining mathematical
    # italics (and supporting bold Greek symbols) with the bm package instead.
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


def equation_block(block: dict[str, Any]) -> tuple[str, str] | None:
    if block.get("t") not in {"Para", "Plain"}:
        return None
    content = block.get("c", [])
    if not content or content[0].get("t") != "Math":
        return None
    tail = inlines_plain(content[1:])
    match = re.fullmatch(r"[,.]?\s*\((\d+[a-z]?)\)", tail, flags=re.IGNORECASE)
    if not match:
        return None
    return content[0]["c"][1], match.group(1)


def make_equation(math: str, label: str) -> dict[str, Any]:
    environment = "equation"
    display_math = math
    if len(math) > 115:
        # Keep display equations inside one column, but never enlarge a short
        # equation merely because its source contains verbose LaTeX commands.
        display_math = "\\fitcolumn{" + math + "}"
    equation = (
        "\\stepcounter{equation}\n"
        f"\\begin{{{environment}}}\n"
        f"{display_math}\n"
        f"\\tag{{{label}}}\\label{{eq:{label}}}\n"
        f"\\end{{{environment}}}"
    )
    return {"t": "RawBlock", "c": ["latex", equation]}


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
    blocks: list[dict[str, Any]], figures: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], set[str]]:
    converted: list[dict[str, Any]] = []
    equation_labels: set[str] = set()
    pending = {figure["number"]: figure for figure in figures}
    for block in blocks:
        equation = equation_block(block)
        if equation:
            math, label = equation
            equation_labels.add(label)
            converted.append(make_equation(math, label))
        else:
            converted.append(block)
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
        json_path = temp_dir / "document.json"
        run(
            [
                PANDOC,
                str(accepted_docx),
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
        body_blocks, equation_labels = prepare_body(body_blocks, figures)
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
            "tracked_revision_elements_accepted": revisions_accepted,
        }
        (output_dir / "conversion_report.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manuscript_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Word manuscript into an APS REVTeX 4.2 source package."
    )
    parser.add_argument("input", type=Path, help="Input .docx manuscript")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output source directory")
    parser.add_argument("--journal", choices=("prl", "prb"), default="prl")
    parser.add_argument("--layout", choices=("reprint", "preprint"), default="reprint")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_docx = args.input.resolve()
    if not input_docx.is_file() or input_docx.suffix.lower() != ".docx":
        print(f"error: not a .docx file: {input_docx}", file=sys.stderr)
        return 2
    output_dir = (args.output_dir or Path(f"{input_docx.stem}_{args.journal}")).resolve()
    try:
        manuscript = build_manuscript(input_docx, output_dir, args.journal, args.layout)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(manuscript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
