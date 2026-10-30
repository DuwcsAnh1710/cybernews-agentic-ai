# CyberPulse AI

CyberPulse AI is an automated cybersecurity news intelligence and multimedia pipeline.

## Prerequisites

- Python 3.11 or higher
- PowerShell (Windows) or bash (Linux/macOS)

## Setup Instructions

1. **Create and activate a virtual environment**:

   On Windows (PowerShell):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   On Linux/macOS:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install package with development dependencies**:
   ```powershell
   pip install -e ".[dev]"
   ```

3. **Configure Environment Variables (Optional)**:
   Copy `.env.example` to `.env` and fill in secrets if available:
   ```powershell
   Copy-Item .env.example .env
   ```

## LLM setup (Gemini API key)

To enable live LLM intelligence features with Google Gemini:
1. Create a free API key in [Google AI Studio](https://aistudio.google.com/).
2. Put `GEMINI_API_KEY="your-key-here"` into `.env` (this file is git-ignored and must never be committed). The model is set in `config/settings.toml` (`[llm].model`); only set `GEMINI_MODEL` in `.env` if you deliberately want to override it.
3. **Data usage note**: On the Google AI Studio free tier, Google may use submitted content to improve its products. This is acceptable for CyberPulse AI because pipeline inputs are public cybersecurity news articles and RSS feeds. Never send private, sensitive, or confidential data.

To verify your key and client connectivity:
```powershell
cyberpulse llm-check
```

## Running Tests

Run the test suite with verbose output:
```powershell
pytest -v
```

## CLI Usage

Show available commands and global options:
```powershell
cyberpulse --help
```

Display package version:
```powershell
cyberpulse version
```

Validate configuration files and secret availability:
```powershell
cyberpulse check-config
```

Custom configuration file paths:
```powershell
cyberpulse check-config --settings path/to/settings.toml --sources path/to/sources.toml
```

Initialize database:
```powershell
cyberpulse init-db
```
Or with custom settings path:
```powershell
cyberpulse init-db --settings path/to/settings.toml
```

### Fetch news (raw)

Fetch raw articles from enabled RSS/Atom feeds:
```powershell
cyberpulse fetch
```

Fetch with a preview of up to 2 items per source:
```powershell
cyberpulse fetch --limit 2
```

Custom configuration file paths:
```powershell
cyberpulse fetch --settings path/to/settings.toml --sources path/to/sources.toml --limit 3
```

### Collect articles

Fetch, normalize, deduplicate, and persist articles into SQLite:
```powershell
cyberpulse collect
```

Dry-run mode (runs fetch and normalization without saving to database):
```powershell
cyberpulse collect --dry-run
```

With custom configuration file paths:
```powershell
cyberpulse collect --settings path/to/settings.toml --sources path/to/sources.toml
```

### Research Agent (Agent 1)

The News Research Agent ranks candidate articles from SQLite by newsworthiness for a Vietnamese cybersecurity and IT audience using an LLM.

**Agent Contract**:
- **Role**: News Research Agent.
- **Input**: `run_id: str`, `candidates: list[Article]`, `settings: Settings`.
- **Output**: `ResearchResult` (`models.ResearchResult`).
- **Tools**: `storage.list_recent_articles` (read-only SQLite query), `LLMClient.generate_json` (structured ranking).
- **Completion condition**: `status="ok"` with exactly N items, or `status="insufficient"` with notes detailing drop counts and diagnostic info.
- **Failure rules**: LLM errors (`LLMError` subclasses) propagate unchanged; hallucinated IDs and duplicate IDs from the LLM are dropped and recorded in notes; diversity cap `max_per_source` is enforced; the agent never invents or modifies articles.

Run the research agent from CLI:
```powershell
cyberpulse research
```

Or with custom configuration file paths:
```powershell
cyberpulse research --settings path/to/settings.toml --sources path/to/sources.toml
```

### Editorial Agent (Agent 2)

The Editorial Agent transforms a validated `ResearchResult` into a comprehensive Vietnamese `EditorialPackage` (written digest, multi-platform captions, two-host podcast script, and vertical short video script) where every block strictly cites its source article reference.

**Agent Contract**:
- **Role**: Content Analysis & Editorial Agent.
- **Input**: `ResearchResult` with $\ge 1$ item.
- **Output**: `EditorialPackage` (`models.EditorialPackage`).
- **Tools**: `LLMClient.generate_json` (2 calls: "editorial_written", "editorial_scripts").
- **Completion condition**: A valid `EditorialPackage` whose every citation maps to an article in the input.
- **Failure rules**: LLM errors propagate unchanged; unknown citation refs raise `EditorialRefError` (no partial package is returned); no URLs or IDs are produced by the LLM.
- **Explicit non-claim**: This agent does NOT verify facts. Source-consistency checks are the validator's job (next task).

**Configuration (`config/settings.toml`)**:
```toml
[editorial]
podcast_target_words = 600
video_max_total_sec = 90
caption_platforms = ["facebook", "youtube", "tiktok"]
podcast_speakers = ["Minh", "Lan"]
```

Run the editorial agent from CLI:
```powershell
cyberpulse editorial --research output/research/<run_id>.json
```

Or with custom settings:
```powershell
cyberpulse editorial --research output/research/<run_id>.json --settings path/to/settings.toml
```

### Validator (Grounding & Source-Consistency)

The Grounding Validator inspects an `EditorialPackage` against its corresponding `ResearchResult` to enforce structural integrity, deterministic source-consistency against RSS excerpts, and advisory LLM claim verification.

**Validation Layers**:

1. **Layer 1 — Structural Checks (Deterministic)**:
   - **Can detect**: Run ID mismatches (`E_RUN_ID_MISMATCH`), unknown citations (`E_CITE_UNKNOWN`), mismatching section counts (`E_SECTION_COUNT`), sections not citing rank-ordered research items (`E_SECTION_CITE`), uncovered research articles (`E_ARTICLE_UNCOVERED`), leaked URLs, emails, or 16-hex article IDs in presentation text (`E_URL_OR_ID_IN_TEXT`), missing or duplicate caption platforms (`E_CAPTION_PLATFORM`), platform character limit breaches (`E_CAPTION_LENGTH`), invalid hashtag count or formats (`E_HASHTAG_FORMAT`), unconfigured podcast speakers (`E_SPEAKER_UNKNOWN`), excessive total video duration (`E_VIDEO_TOTAL`), invalid scene durations outside 4–20s (`E_SCENE_DURATION`), and on-screen text over 60 characters (`E_ONSCREEN_LENGTH`). Issues warnings for podcast length (`W_PODCAST_LENGTH`) and section length (`W_SECTION_LENGTH`).
   - **Cannot detect**: Semantic distortion, unsupported numbers, or fabricated facts.

2. **Layer 2 — Source-Consistency Checks (Deterministic)**:
   - **Can detect**: Unsupported CVE identifiers (`E_CVE_UNSUPPORTED`), unsupported numbers not grounded in cited article titles/summaries (`E_NUMBER_UNSUPPORTED`), and unsupported Latin-script acronyms, CamelCase, or capitalized terms (`W_TERM_UNSUPPORTED`). Normalization maps English number words zero–twenty to digits, equates comma/dot decimals, and exempts the research item count, parenthesized glossary text `(...)`, and allowlisted terms / speaker names.
   - **Cannot detect**: Vietnamese semantic drift, subtle misattributions, or real-world truth outside the provided RSS title/summary excerpts.

3. **Layer 3 — LLM Claim Review (Advisory)**:
   - **Can detect**: Claims not stated in cited sources (`W_CLAIM_NOT_IN_SOURCE`), misattributions across articles/organizations (`W_CLAIM_MISATTRIBUTED`), and assertions overstated relative to source wording (`W_CLAIM_OVERSTATED`). Enforces deterministic post-filtering to drop findings with unknown paths or non-substring quotes.
   - **Cannot detect**: Independent real-world truth; issues outside the RSS title/summary context.

**Configuration (`config/settings.toml`)**:
```toml
[validator]
llm_review = true
podcast_word_tolerance = 0.2
section_words_min = 60
section_words_max = 120
term_allowlist = ["Trung"]
```

**Run validator from CLI**:
```powershell
cyberpulse validate --research output/research/<run_id>.json --editorial output/editorial/<run_id>.json
```

Skip Layer 3 LLM claim review:
```powershell
cyberpulse validate --research output/research/<run_id>.json --editorial output/editorial/<run_id>.json --no-llm-review
```

With custom settings:
```powershell
cyberpulse validate --research output/research/<run_id>.json --editorial output/editorial/<run_id>.json --settings path/to/settings.toml
```

**Exit codes**:
- `0`: Validated successfully (`passed=true`, warnings allowed).
- `8`: Validation failed (`passed=false`, errors present).
- `7`: Invalid inputs (file missing or malformed JSON).
- `1`: Configuration error (`ConfigError`).

### Workbench UI (Streamlit Inspection & Review)

The CyberPulse AI Workbench is a local web UI designed for human-in-the-loop inspection, cross-referencing Vietnamese outputs against English RSS sources, and recording auditable approval decisions or quick text revisions.

**Key Architecture & Security Rules**:
- **Client layer only**: Reads artifacts and writes review records/text edits only. Never initiates fetch, collect, research, editorial, or LLM network calls.
- **Local binding**: Binds strictly to `127.0.0.1` (`localhost`).
- **Read-only database**: Accesses SQLite in read-only mode (`mode=ro`).
- **XSS prevention**: All untrusted source and LLM text is HTML-escaped before inserting `<mark>` highlights.
- **Review governance**: Approval requires fresh validation results (`freshness == "fresh"`), zero errors (`passed == true`), and explicit checkbox acknowledgment of any warnings.
- **Quick edits**: Text edits create an unalterable `<run_id>.original.json` backup on first edit, re-render the preview markdown, and mark validation as stale until re-run.

**Launch Workbench**:
```powershell
cyberpulse ui
```

With custom port or settings:
```powershell
cyberpulse ui --port 8501 --settings path/to/settings.toml
```

Print the launch command without running:
```powershell
cyberpulse ui --print-command
```





