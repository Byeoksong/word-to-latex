# Word to APS REVTeX converter

`00_word_to_revtex.py` converts a structured `.docx` manuscript into APS
REVTeX 4.2 source for **Physical Review Letters** (default) or **Physical
Review B**, compiles it, validates the final LaTeX log and PDF, and publishes
the renamed PDF to `03_release/`.

## Repository layout

- `01_source/`: original Word manuscripts; never modified by the converter
- `02_converted/`: editable LaTeX source, extracted figures, and QA records
- `03_release/`: verified PDF and submission-source ZIP
- `AGENTS.md`: full maintenance workflow, validation checklist, and known
  conversion safeguards

The three manuscript-data directories are intentionally empty in Git. Only
their `.gitkeep` placeholders are tracked; local Word files, generated LaTeX,
figures, QA reports, PDFs, and submission archives are excluded by `.gitignore`.

## Requirements

- Python 3.10 or newer
- Pandoc 3.x
- A TeX distribution containing `pdflatex`, `revtex4-2`, and the LaTeX
  packages listed in `AGENTS.md`
- Poppler's `pdfinfo` for final PDF validation

Pandoc alone is sufficient when using `--source-only`.

## Usage

```bash
python3 00_word_to_revtex.py 01_source/manuscript.docx --journal prl
```

That one command performs the complete default workflow:

1. Writes editable LaTeX and figures to
   `02_converted/manuscript_prl/`.
2. Runs `pdflatex` three times so references stabilize.
3. Rejects release-blocking log problems and verifies the PDF with `pdfinfo`.
4. Copies the PDF to `03_release/manuscript_PRL.pdf` and verifies its SHA-256.
5. Removes the temporary working PDF and LaTeX auxiliary files after success.

Use `--journal prb` for PRB. Use `--layout preprint` for a one-column review
copy; the default `reprint` layout approximates the published two-column
journal appearance.

Useful options:

```bash
# Choose the final PDF name.
python3 00_word_to_revtex.py 01_source/manuscript.docx \
  --release-name revised_manuscript.pdf

# Override the generated-source and release directories.
python3 00_word_to_revtex.py 01_source/manuscript.docx \
  -o /path/to/converted --release-dir /path/to/release

# Generate source without compiling or publishing.
python3 00_word_to_revtex.py 01_source/manuscript.docx --source-only

# Retain manuscript.pdf, .aux, .log, .out, and manuscriptNotes.bib for review.
python3 00_word_to_revtex.py 01_source/manuscript.docx --keep-build-files
```

`--passes N` changes the number of `pdflatex` passes; three is recommended.
If compilation or validation fails, no release PDF is published and the build
files are retained for diagnosis. Nonblocking notices such as underfull boxes
and REVTeX float-placement warnings are counted in `conversion_report.json`;
automatic checks do not replace the final visual review required before
submission.

## Recompiling after editing LaTeX

After editing `manuscript.tex`, compile it from its generated source directory:

```bash
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
pdflatex -interaction=nonstopmode -halt-on-error manuscript.tex
```

Running the converter again starts from Word and regenerates
`manuscript.tex`, so it can overwrite manual LaTeX edits. Keep Word as the
source of truth or preserve manual LaTeX changes separately.

## Conversion behavior

- Infers the title, superscripted author/affiliation mapping, abstract,
  acknowledgments, numbered equations, figures, and numbered reference list.
- Accepts tracked changes in a temporary DOCX copy before conversion. The
  original Word file is never modified; this also preserves equations nested
  inside Word revision markup.
- Extracts embedded figures and inserts REVTeX figure floats near their first
  occurrence in the manuscript.
- Places figures and numbered equations within one column in the two-column
  `reprint` layout. Long equations are scaled only when needed to prevent them
  from crossing the column boundary.
- Preserves commas and periods written between a displayed equation and its
  Word equation number. It reads the following Word paragraph's effective
  first-line indentation, including direct formatting and inherited paragraph
  styles, and reproduces that choice in LaTeX. If the metadata is unavailable,
  a comma is treated as a same-sentence continuation and suppresses indentation.
- Preserves bold-italic mathematical variables with `\bm`, including Greek
  symbols, while retaining explicitly bold-upright Word runs.
- Preserves Word equation-run typography that Pandoc normally drops. Roman
  labels and identifiers such as `X^{\mathrm{ABC}}`, descriptive subscripts, and
  transpose markers are emitted with `\mathrm`; bold-upright and bold-italic
  math remain distinguishable. This is detected from OMML formatting rather
  than from manuscript-specific symbol names.
- Preserves italic uppercase Greek symbols with amsmath's italic `\varGamma`-
  family commands. This covers both explicit/default OMML italic styling and
  Unicode Mathematical Italic Greek characters that would otherwise collapse
  to upright `\Gamma`-family symbols in LaTeX.
- Converts Word internal reference links into `\cite{ref...}` commands.
- Preserves unstructured references as `thebibliography`/`\bibitem` entries.
  This is safer than guessing BibTeX fields from formatted prose.
- Writes `conversion_report.json` so inferred metadata, compilation results,
  release filename, and PDF checksum can be audited.

Automatic conversion cannot infer missing information. In particular, add a
corresponding-author email manually if the Word source marks an author with `*`
but contains no email address, and verify every citation/reference against the
original before journal submission.

## Tests

Run the standard-library regression suite after changing the converter:

```bash
python3 -m unittest discover -s tests -v
```

The suite includes a safeguard that requires Pandoc's `\mathbf` output to be
normalized to `\bm`, preserving bold italic variables and bold Greek symbols.
