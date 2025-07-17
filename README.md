# String-literal Obliterator 9000 :tm:

Automatically refactor hard-coded string literals in Java source files into `public static final String` constants using an OpenAI-API compliant large-language-model (LLM) service. This can use a LOT of tokens for a production-sized codebase. Highly recommended to use a local alternative like Ollama, Llama.cpp, or vLLM to reduce costs.

---

## What it does

1. Recursively walks a Java project directory.
2. Skips common build folders (`.git`, `target`, `build`, etc.).
3. Detects every string literal that is **not** inside a comment.
4. Sends the source file to an LLM endpoint and asks it to:
   - Create a constant for every literal.
   - Replace the literal’s usage with the constant name.
   - Return the fully refactored, compilable source.
5. Overwrites the original file (or writes to `*.java.new` in dry-run mode) and optionally backs up the original.

---

## Why

- **Eliminates typos** in duplicated strings  
- **Centralizes** UI messages, keys, SQL fragments, etc.  
- **Improves performance** (shared immutable objects)  
- **Simplifies refactoring** when text needs to change

---

## Quick start

```
# 1. Install deps
pip install requests tqdm

# 2. Export your API key (optional)
export API_KEY="sk-..."

# 3. Run (dry-run to preview)
python refactor_strings.py \
    --root /path/to/java/project \
    --endpoint https://api.openai.com/v1/chat/completions \
    --model gpt-4 \
    --dry-run
```

When you’re happy with the generated `*.java.new` files:

```
python refactor_strings.py \
    --root /path/to/java/project \
    --endpoint https://api.openai.com/v1/chat/completions \
    --model gpt-4 \
    --backup
```

---

## CLI options

| Flag                 | Default | Description |
|----------------------|---------|-------------|
| `--root`             | *required* | Top-level directory to scan for `.java` files |
| `--endpoint`         | `http://localhost:8000/v1/chat/completions` | LLM chat-completions endpoint |
| `--model`            | `gpt-4` | Model name to use |
| `--api-key`          | `$API_KEY` | Bearer token sent via `Authorization` header |
| `--exclude`          | `.git target build .idea` | Directory names to skip (space-separated) |
| `--dry-run`          | | Write results to `*.java.new` instead of overwriting |
| `--backup`           | | Keep originals as `*.java.bak` when overwriting |
| `--workers`          | `4` | Concurrent LLM requests |
| `--timeout`          | `300` | HTTP timeout per request (seconds) |
| `--retries`          | `3` | Max retries on transient HTTP errors |
| `--backoff`          | `1.0` | Backoff multiplier for retries |

---

## Examples

### Using OpenAI

```
export API_KEY="sk-..."
python refactor_strings.py \
    --root ~/work/acme-service \
    --endpoint https://api.openai.com/v1/chat/completions \
    --model gpt-4 \
    --workers 8 \
    --backup
```

### Using an OpenAI-compatible API (e.g., vLLM)

```
python refactor_strings.py \
    --root ~/work/acme-service \
    --endpoint http://localhost:8000/v1/chat/completions \
    --model devstral \
    --workers 2
```

### Using a local Ollama server

```
python refactor_strings.py \
    --root ~/work/acme-service \
    --endpoint http://localhost:11434/v1/chat/completions \
    --model devstral \
    --workers 2
```

---

## Build integration

Add to your CI pipeline to make sure every PR is linted:

```
python refactor_strings.py --root . --dry-run
```

Exit-code is 0 on success; any LLM failure or parsing error is logged.

---

## Requirements

- Python ≥ 3.8  
- `requests`, `tqdm` (install with `pip install -r requirements.txt`)

---

## License

MIT