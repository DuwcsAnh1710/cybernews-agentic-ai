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


