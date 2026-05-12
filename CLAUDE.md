# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the pipeline on a DCE folder
python main.py ./DCE/dce1/

# Run without LLM (keyword matching only, no API calls)
python main.py ./DCE/dce1/ --no-llm

# Run with verbose output
python main.py ./DCE/dce1/ --verbose

# Custom output directory
python main.py ./DCE/dce1/ --output ./output/

# Use a custom checklist
python main.py ./DCE/dce1/ --checklist ./checklist.json
```

**API key:** Set the `MISTRAL_API_KEY` environment variable before running the pipeline:
```bash
export MISTRAL_API_KEY=votre_clé_mistral
```
Or copy `.env.example` to `.env` and fill in the key, then source it (`source .env` or use a tool like `direnv`). The `api_key` flat file is no longer supported.

**Tesseract:** Required for scanned PDFs. Install with `brew install tesseract tesseract-lang` (macOS).

## Architecture

The pipeline runs in 6 sequential steps (see `main.py`):

1. **Load checklist** (`pipeline/checker.py`) — reads `checklist.json` into `ChecklistItem` objects
2. **List documents** (`utils/file_utils.py`) — discovers PDF/DOCX/DOC/XLSX/XLS recursively; also extracts nested ZIPs in-place before listing
3. **Extract text** (`pipeline/extractor.py`) — dispatches by extension: PyMuPDF for native PDFs, Tesseract OCR for scanned PDFs, python-docx for DOCX, OLE fallback for binary DOC, openpyxl for XLSX, xlrd for XLS
4. **Classify** (`pipeline/classifier.py`) — 3 passes per document; all passes below `CLASSIFICATION_MIN_CONFIDENCE` run and the best result wins:
   - **Pass 0 – Folder**: matches subdirectory path against `folder_keywords` (confidence 0.90, always ≥ threshold → early return)
   - **Pass 1 – Keywords**: weighted alias matching against normalized text (score ≥ `KEYWORD_CONFIDENCE_THRESHOLD` to enter candidate pool)
   - **Pass 2 – LLM**: Mistral API call with JSON-mode response; serialised via `_llm_lock` + 1.1s sleep between calls
   - Steps 3 and 4 both use `ThreadPoolExecutor(MAX_WORKERS)`
5. **Check completeness** (`pipeline/checker.py`) — joins classifications against the checklist; for `search_type="set"` all matched documents collected in `matched_documents`; for `search_type="single"` detects duplicates above `KEYWORD_CONFIDENCE_THRESHOLD` and stores them in `duplicates_warning`
6. **Report** (`pipeline/reporter.py`) — writes `output/rapport_dce_TIMESTAMP.json` and `.md`; prints Rich table to console. For `search_type="set"` items with multiple matches: the Markdown table shows `N fichiers ↓` and emits a `<details><summary>…</summary>` block per item below the phase table; the Rich console shows N + first 3 filenames in dim + `... et N-3 autres`

## Key data models (`models/schemas.py`)

- `ChecklistItem` — one expected document type. Fields: `id`, `label`, `phase` (constitution/contrat/reception), `obligatoire`, `aliases`, `search_type` ("single"|"set"), `folder_keywords`
- `ExtractedDocument` — extracted content of one file. `subdirectory` holds the relative path from the input root, used by pass 0.
- `ClassificationResult` — links a document to a checklist item, with `confidence`, `method` (folder/keyword/llm/none)
- `ChecklistMatch` — one checklist item's verdict: `found`, `matched_document` (best), `matched_documents` (all, for sets), `duplicates_warning` (other files that matched a "single" item)

## checklist.json

18 items across 3 phases. Each item has:
- `search_type: "single"` — one specific document expected
- `search_type: "set"` — a whole folder expected (CCTP, PLANS, ETUDE_SOL, CONTRAT_MOE, RAPPORT_CT_FINAL, ATTESTATIONS_DECENNALE, PV_RECEPTION)
- `folder_keywords` — substring-matched against path components (case-insensitive, normalized). Currently mapped: `CCTP` → CCTP, `PLANS` → PLANS, `ETUDE DE SOL` → ETUDE_SOL, `CONTRAT MOE` → CONTRAT_MOE, `RICT` → RAPPORT_CT_FINAL, `ARRETE PC` → PERMIS_CONSTRUIRE

## Configuration (`config.py`)

| Constant | Default | Effect |
|---|---|---|
| `MAX_WORKERS` | 4 | Thread pool size for parallel extraction and classification |
| `CLASSIFICATION_MIN_CONFIDENCE` | 0.75 | Confidence threshold to short-circuit a pass; below this, the next pass also runs and the best result wins |
| `KEYWORD_CONFIDENCE_THRESHOLD` | 0.3 | Minimum keyword score to enter the candidate pool |
| `LLM_CONFIDENCE_THRESHOLD` | 0.6 | Minimum LLM confidence to accept a match |
| `LLM_MAX_CONTEXT_CHARS` | 4000 | Characters sent to Mistral |
| `OCR_MAX_PAGES` | 2 | Pages Tesseract processes per scanned PDF |
| `SCANNED_TEXT_THRESHOLD` | 50 | Chars/page below which a PDF is considered scanned |
