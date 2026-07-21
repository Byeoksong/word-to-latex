# Workspace Guide

This file applies to the entire repository. Read it together with `README.md`
before changing the converter or its workflow.

## Purpose

This repository converts structured Microsoft Word manuscripts into APS
REVTeX 4.2 LaTeX source for Physical Review Letters (PRL) or Physical Review B
(PRB). It also documents how to compile, inspect, and package the generated
manuscript safely.

The converter is intended to be reusable. Never encode manuscript-specific
titles, authors, affiliations, equations, citations, filenames, or validation
counts in the converter or in public repository documentation.

## Repository Layout

```text
.
├── AGENTS.md
├── README.md
├── 00_word_to_revtex.py
├── 01_source/
│   └── .gitkeep
├── 02_converted/
│   └── .gitkeep
└── 03_release/
    └── .gitkeep
```

- `00_word_to_revtex.py` is the canonical conversion implementation.
- `01_source/` holds local Word manuscripts.
- `02_converted/` holds generated LaTeX source, extracted figures, conversion
  reports, QA notes, and temporary compilation products.
- `03_release/` holds locally validated PDFs and submission-source archives.

Git tracks only `.gitkeep` in `01_source/`, `02_converted/`, and `03_release/`.
All other contents of those directories are intentionally ignored because they
may contain private or unpublished manuscript material. Never use `git add -f`
on files in those directories, and do not weaken these ignore rules without the
user's explicit approval.

## Conversion Policy

- Use the `revtex4-2` document class. The default target is `prl,reprint`; the
  CLI also supports `--journal prb` and `--layout preprint`.
- Accept Word tracked changes only in a temporary DOCX copy. Never modify the
  original source file.
- Do not emit a `\date` command. REVTeX renders an unwanted `(Dated: ...)` line
  for a populated date and an empty `(Dated:)` line for `\date{}`.
- In two-column output, place figures in ordinary `figure` environments at
  `width=\columnwidth`. Do not generate `figure*`.
- Put numbered displays in ordinary `equation` environments. Do not use
  `widetext`. Reduce only equations wider than one column with `\fitcolumn`; do
  not enlarge shorter equations.
- Preserve punctuation written between a Word display equation and its number.
  Put the punctuation inside the LaTeX display before `\tag`. Determine whether
  the following prose is indented from Word's effective first-line indentation,
  resolving direct formatting, style inheritance, and the default paragraph
  style. Use comma-based continuation only as a fallback when Word metadata is
  unavailable.
- Normalize Pandoc's outer `\mathbf` output to `\bm` after preserving explicit
  OMML upright markers. This keeps Word bold-italic variables italic, supports
  bold Greek symbols, and retains bold-upright runs through an inner `\mathrm`.
- Preserve explicit OMML math-run typography before Pandoc conversion. In
  particular, map Word plain/roman runs to `\mathrm`, retain an inner roman
  style for bold-upright runs, and leave bold-italic runs italic inside `\bm`.
  Never hard-code manuscript-specific identifiers such as acronym superscripts
  or chemical-species labels.
- Preserve equation identifiers from Word. The deliberate
  `\stepcounter{equation}` calls prevent duplicate Hyperref anchors while
  custom tags preserve source labels.
- Preserve unstructured Word references as manual `\bibitem` entries rather
  than inventing BibTeX metadata.
- Treat inferred author metadata, bibliography entries, citations, cross
  references, figure placement, and equation conversion as items requiring
  human review.

## Required Tools

### Dependencies by task level

`00_word_to_revtex.py` uses only the Python standard library. There is no
`pip install` step and no `requirements.txt`.

1. Word-to-LaTeX conversion:
   - Python 3.10 or newer
   - Pandoc 3.x, with `pandoc` on `PATH`
2. PDF compilation:
   - TeX Live, MacTeX/BasicTeX, or MiKTeX providing `pdflatex`
   - APS REVTeX 4.2, including `revtex4-2.cls`, `aps4-2.rtx`, and
     `aps10pt4-2.rtx`
   - `graphicx`, `amsmath`, `amssymb`, `bm`, `mathtools`, `hyperref`, `natbib`,
     `url`, and `textcase`
3. PDF and release validation:
   - Poppler tools `pdfinfo`, `pdftoppm`, and `pdftotext`
   - `rg` (ripgrep), `zip`, and `unzip`
   - `shasum -a 256` or an equivalent SHA-256 utility

Prefer current Pandoc 3.x and REVTeX 4.2 releases. Exact TeX Live, Pandoc, and
Poppler minor versions do not need to match between systems.

### Preparing a new computer

Check existing tools first and install only what is missing. Avoid installing
the same program through multiple systems because duplicate installations can
make `PATH` select an unexpected version.

- macOS: install Pandoc with its official installer or Homebrew. BasicTeX or
  MacTeX provides LaTeX. Poppler and ripgrep are available through Homebrew.
  After installing BasicTeX, confirm `/Library/TeX/texbin` is on `PATH`, then
  use `tlmgr` for missing TeX packages.
- Debian/Ubuntu: use the distribution package manager for Pandoc, TeX Live,
  `texlive-publishers`, `texlive-latex-recommended`, `texlive-latex-extra`,
  `poppler-utils`, `ripgrep`, `zip`, and `unzip`. Maintain an OS-packaged TeX
  Live installation through the OS package manager rather than mixing it with
  `tlmgr`.
- Windows: use the official Pandoc installer or
  `winget install --source winget --exact --id JohnMacFarlane.Pandoc`. Install
  MiKTeX or TeX Live, then confirm that REVTeX 4.2 and the packages above are
  available. Open a new PowerShell window after installation to refresh
  `PATH`.

Installation commands can change. An agent preparing a new machine must detect
the operating system and verify current commands against official Pandoc, TeX
Users Group, and APS REVTeX documentation. Inform the user before requesting
administrator access, downloading a large TeX distribution, or making global
system changes.

### Environment checks

Run these commands from the repository root:

```bash
python3 --version
pandoc --version
pdflatex --version
kpsewhich revtex4-2.cls
kpsewhich aps4-2.rtx
kpsewhich graphicx.sty
kpsewhich amsmath.sty
kpsewhich amssymb.sty
kpsewhich bm.sty
kpsewhich mathtools.sty
kpsewhich hyperref.sty
kpsewhich natbib.sty
kpsewhich url.sty
kpsewhich textcase.sty
pdfinfo -v
pdftoppm -v
pdftotext -v
rg --version
zip -v
unzip -v
```

An empty `kpsewhich` result means the corresponding TeX package is missing.
Confirm all required commands before running the converter.

## Standard Workflow

Run the regression suite after changing the converter:

```bash
python3 -m unittest discover -s tests -v
```

Place a local DOCX in `01_source/`, then run from the repository root:

```bash
python3 00_word_to_revtex.py 01_source/<manuscript>.docx \
  --journal prl --layout reprint \
  -o 02_converted/<manuscript>_prl
```

Compile three times so cross references stabilize:

```bash
cd 02_converted/<manuscript>_prl
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
cd ../..
```

When testing converter changes, do not overwrite a reviewed conversion. Use a
temporary output directory outside the repository or under `/tmp`:

```bash
qa_workdir=$(mktemp -d /tmp/word_to_revtex_qa.XXXXXX)
python3 00_word_to_revtex.py 01_source/<manuscript>.docx \
  --journal prl --layout reprint -o "$qa_workdir/prl"
```

If PRB behavior changes, also convert with `--journal prb` and compile the
result at least twice.

## Required Validation

Complete every relevant check before delivering generated artifacts.

1. Conversion inventory
   - Compare title, authors, affiliations, abstract, figures, numbered
     equations, references, and accepted tracked changes against the Word
     source and `conversion_report.json`.
2. LaTeX structure
   - Confirm there is no unintended `figure*` or `widetext` environment.
   - Confirm figures use `\includegraphics[width=\columnwidth]` when the
     requested policy is single-column placement.
   - Confirm bold math uses `\bm`, and no generated `\mathbf` remains unless
     upright bold was explicitly requested.
   - Compare representative Word math runs against LaTeX for italic, roman,
     bold-italic, and bold-upright distinctions, including superscripts and
     subscripts. Confirm no internal math-style sentinels remain.
   - Confirm every display equation and its number remain within one column.
   - Confirm displayed-equation punctuation and post-equation paragraph
     indentation match Word, including period-terminated displays whose prose
     deliberately remains unindented.
   - Confirm the generated title block contains no `\date` command or
     `(Dated: ...)` text.
3. Compilation log
   - Reject `Overfull` boxes, undefined citations/references,
     multiply-defined labels, and duplicate Hyperref destinations.
   - `Underfull \hbox`, `No file manuscript.bbl` for a manual bibliography,
     and REVTeX float-placement warnings may be acceptable only after visual
     review confirms that the output is complete and readable.
4. PDF structure and visual QA
   - Use `pdfinfo` to confirm page creation, page size, and page count.
   - Render every page with
     `pdftoppm -png -r 150 manuscript.pdf <temporary-path>/page`.
   - Inspect every rendered page for clipping, overlap, missing glyphs,
     unintended blank pages, broken reference flow, and material crossing
     column boundaries.
5. Packaging
   - Confirm the release PDF SHA-256 matches the validated working PDF copied
     into place.
   - Package only the files required to recompile the manuscript, normally
     `manuscript.tex` and `figures/`.
   - Validate the archive with `unzip -tq` and record the review results in a
     local QA report under `02_converted/`.

## Cleanup Rules

- Do not leave temporary DOCX probes, rendered QA PNGs, smoke-test directories,
  or other scratch output in the repository root. Use `/tmp` when possible.
- `.aux`, `.log`, `.out`, `manuscriptNotes.bib`, and temporary working PDFs are
  build products. Remove them after reviewing logs and copying final artifacts.
- Never delete source manuscripts or reviewed artifacts merely because Git
  ignores them. Ignored files remain user data on the local filesystem.
- List exact paths before deleting anything and prefer recoverable deletion
  when available.

## Public Repository Safety

Before every commit:

1. Run `git status --short --ignored`.
2. Confirm only `.gitkeep` is tracked below `01_source/`, `02_converted/`, and
   `03_release/`.
3. Search staged public files for manuscript names, titles, author names,
   affiliations, acknowledgments, unpublished scientific text, and other
   identifying details.
4. Inspect the staged diff with `git diff --cached` before committing.
5. Never commit credentials, access tokens, private URLs, manuscript source,
   figures, generated PDFs, or submission archives.
