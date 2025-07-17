#!/usr/bin/env python3
"""
refactor_strings.py

Recursively scan .java files under --root (excluding common build dirs),
detect string literals (ignoring comments), and call a chat‐style LLM
endpoint to refactor them into public static final constants. String constants 
centralize text, prevent typos, enable easy refactoring, and boost 
performance by sharing one immutable instance across the codebase.
"""

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# ----- Regex helpers -----
# Rough patterns to strip Java comments (single-line + multi-line)
_COMMENT_RE = re.compile(
    r"""
    (//[^\n]*\n)|            # single-line comments
    (/\*[^*]*\*+(?:[^/*][^*]*\*+)*/)|  # multi-line comments
    ("([^"\\]|\\.)*")       # string literals (keep them for refactor step)
    """,
    re.VERBOSE | re.DOTALL,
)

# Rough pattern to find any remaining double-quoted literal
_STRING_LITERAL_RE = re.compile(r'"([^"\\]|\\.)*"')

# ----- Functions -----


def strip_comments(java_src: str) -> str:
    """Strip comments but preserve string literals so we can detect them post-stripping."""
    def _replacer(match):
        if match.group(3) is not None:
            # it's a string literal—keep it
            return match.group(3)
        else:
            # it's a comment—remove it (replace with newline if single-line)
            return "\n" if match.group(1) else ""
    return _COMMENT_RE.sub(_replacer, java_src)


def has_string_literal(path: Path) -> bool:
    """Return True if file contains at least one string literal outside of comments."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logging.warning("Skipped %s: cannot read (%s)", path, e)
        return False

    stripped = strip_comments(text)
    return bool(_STRING_LITERAL_RE.search(stripped))


def collect_java_files(root: Path, excludes: list[str]) -> list[Path]:
    """Find all *.java under root, skipping any path that matches exclude patterns."""
    java_files = []
    for p in root.rglob("*.java"):
        if any(part in excludes for part in p.parts):
            continue
        java_files.append(p)
    return java_files


def setup_requests_session(retries: int, backoff: float, timeout: float) -> requests.Session:
    """Create a `requests.Session` with retry/backoff for idempotent calls."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.request = partial(session.request, timeout=timeout)
    return session


def atomic_write(path: Path, data: str, backup: bool = False):
    """Write to a temp file and atomically replace. Optionally back up the original."""
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        logging.info("Backed up %s -> %s", path, bak)

    # write to temp then replace
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp_path, path)  # atomic on POSIX
    logging.info("Wrote %s", path)


def refactor_file(
    path: Path,
    session: requests.Session,
    endpoint: str,
    model: str,
    api_key: str | None,
    dry_run: bool,
    backup: bool,
):
    """Send source code to LLM and overwrite (or dry-run) the file with the refactored code."""
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        logging.error("Failed to read %s: %s", path, e)
        return

    # 1) Build chat payload
    system_msg = {
        "role": "system",
        "content": (
            "You are a Java refactoring assistant. "
            "Extract every string literal into a `public static final String` constant "
            "declared at the top (after package+imports), "
            "and replace usages accordingly. "
            "Return ONLY the full, compilable refactored source code."
        ),
    }
    user_msg = {"role": "user", "content": src}
    payload = {
        "model": model,
        "messages": [system_msg, user_msg],
        "temperature": 0.0,
        "max_tokens": 4096,  # user may adjust
    }

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 2) Call LLM
    resp = session.post(endpoint, json=payload, headers=headers)
    try:
        resp.raise_for_status()
    except Exception as e:
        logging.error("LLM API error for %s: %s / %s", path, e, resp.text)
        return

    data = resp.json()
    # sanity check
    choices = data.get("choices")
    if not choices or "message" not in choices[0] or "content" not in choices[0]["message"]:
        logging.error("Unexpected response schema for %s: %s", path, data)
        return
    refactored_src = choices[0]["message"]["content"]

    # 3) Write out
    if dry_run:
        outp = path.with_suffix(path.suffix + ".new")
        outp.write_text(refactored_src, encoding="utf-8")
        logging.info("[DRY RUN] wrote to %s", outp)
    else:
        atomic_write(path, refactored_src, backup=backup)


def main():
    parser = argparse.ArgumentParser(
        description="Refactor Java string literals into constants via LLM"
    )
    parser.add_argument("--root", type=Path, required=True, help="Codebase root")
    parser.add_argument(
        "--endpoint",
        default="http://localhost:8000/v1/chat/completions",
        help="LLM chat completions endpoint",
    )
    parser.add_argument("--model", default="gpt-4", help="Model name to use")
    parser.add_argument(
        "--api-key", default=os.getenv("API_KEY"), help="Bearer token for LLM API"
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[".git", "target", "build", ".idea"],
        help="Directory names to skip",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not overwrite originals"
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="When not dry-run, back up originals as .java.bak",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker threads",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Per-request timeout (seconds)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries on transient HTTP errors",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=1.0,
        help="Retry backoff factor (exponential)",
    )
    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )

    session = setup_requests_session(args.retries, args.backoff, args.timeout)

    # 1) Gather files
    all_java = collect_java_files(args.root, args.exclude)
    candidates = [p for p in all_java if has_string_literal(p)]
    logging.info(
        "Found %d java files, %d with literals to refactor",
        len(all_java),
        len(candidates),
    )

    # 2) Refactor in parallel
    worker = partial(
        refactor_file,
        session=session,
        endpoint=args.endpoint,
        model=args.model,
        api_key=args.api_key,
        dry_run=args.dry_run,
        backup=args.backup,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, p): p for p in candidates}
        for fut in tqdm(as_completed(futures), total=len(futures), desc="Refactoring"):
            p = futures[fut]
            try:
                fut.result()
            except Exception as e:
                logging.exception("Unhandled exception for %s: %s", p, e)


if __name__ == "__main__":
    main()