# Contributing to Video Vault 4K

Thank you for your interest in contributing to **Video Vault 4K**! We welcome bug reports, feature enhancements, documentation improvements, and performance optimizations.

---

## Code of Conduct

This project adheres to the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code of conduct.

---

## Getting Started

### 1. Prerequisites
- Python 3.10+
- Git
- FFmpeg (automatically installed via `imageio-ffmpeg` or detected on system `PATH`)

### 2. Fork and Clone
```bash
git clone https://github.com/lakshit/video-vault-4k.git
cd video-vault-4k
```

### 3. Create a Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Dependencies
Install in editable/development mode:
```bash
pip install -e ".[dev]"
```

---

## Development Workflow

### Running Tests
We enforce strict bit-for-bit lossless verification on every commit. Run the full pytest suite:
```bash
pytest -v
```

To run a specific test module:
```bash
pytest tests/test_roundtrip.py -v
pytest tests/test_microsoft_player.py -v
```

### Code Style & Formatting
We follow standard PEP 8 formatting conventions with modern tooling:
```bash
# Check code style with ruff
ruff check .

# Format code with black
black .
```

---

## Submitting Pull Requests

1. **Create a branch**:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```
2. **Make your changes**:
   - Write clear, concise, and documented Python code.
   - Maintain bit-for-bit lossless guarantees (`original_bytes == decoded_bytes`).
   - Add automated test cases in `tests/` covering your changes.
3. **Verify tests pass**:
   - Run `pytest -v` and ensure all tests pass cleanly without errors.
4. **Commit your changes**:
   - Write meaningful, imperative commit messages (e.g., `feat: add AV1 lossless container profile`, `fix: handle edge-case in zero-byte PAX tar headers`).
5. **Open a Pull Request**:
   - Push your branch to GitHub and open a Pull Request against `main`.
   - Fill out the PR template thoroughly.

---

## Reporting Issues & Requesting Features

- **Bug Reports**: Please use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.yml) and include full reproduction steps, operating system, Python version, and container format.
- **Feature Requests**: Open an issue using the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.yml) describing the use case and desired API or GUI behavior.

---

## Architecture Guidelines

- **Zero Data Loss**: Any change affecting encoding/decoding must never truncate, alter, or resample byte values.
- **Constant Memory Footprint**: Streaming chunking must be preserved for arbitrary multi-gigabyte datasets; avoid buffering whole files in RAM.
- **Cross-Platform Compatibility**: Code should run seamlessly on Windows, Linux, and macOS.
