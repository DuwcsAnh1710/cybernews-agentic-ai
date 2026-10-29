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



