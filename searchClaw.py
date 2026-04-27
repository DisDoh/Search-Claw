#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Search Claw
A small local web-search assistant for CLI, HTTP server, and WhatsApp bridge use.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request


# -------------------------
# Environment helpers
# -------------------------

def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


LLAMA_BASE_URL = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8033").rstrip("/")
LLAMA_CHAT_ENDPOINT = f"{LLAMA_BASE_URL}/v1/chat/completions"
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "")
LLAMA_TEMPERATURE = env_float("LLAMA_TEMPERATURE", 0.7)
LLAMA_TOP_P = env_float("LLAMA_TOP_P", 0.9)
LLAMA_MAX_TOKENS = env_int("LLAMA_MAX_TOKENS", 900)

SEARCH_CLAW_HOST = os.getenv("SEARCH_CLAW_HOST", "127.0.0.1")
SEARCH_CLAW_PORT = env_int("SEARCH_CLAW_PORT", 8811)
REQUIRE_SOURCES = env_bool("REQUIRE_SOURCES", True)

HTTP_CONNECT_TIMEOUT = 10
HTTP_READ_TIMEOUT = 30
LLM_READ_TIMEOUT = 600
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    content: str = ""


# -------------------------
# Logging
# -------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {ts()} {message}", file=sys.stderr)


def verbose_log(enabled: bool, label: str, data: Any) -> None:
    if enabled:
        print(f"[verbose] {ts()} {label}:", file=sys.stderr)
        if isinstance(data, str):
            print(data, file=sys.stderr)
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False), file=sys.stderr)


# -------------------------
# Web search and fetching
# -------------------------

def clean_ddg_url(url: str) -> str:
    """Extract real target URL from DuckDuckGo redirect URLs when possible."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
        return url
    except Exception:
        return url


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def duckduckgo_search(query: str, max_results: int = 6, debug: bool = False, verbose: bool = False) -> List[SearchResult]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": USER_AGENT}
    debug_log(debug, f"search GET {url}")

    response = requests.get(url, headers=headers, timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results: List[SearchResult] = []
    for result in soup.select(".result"):
        link = result.select_one("a.result__a")
        if not link:
            continue
        title = normalize_space(link.get_text(" "))
        href = clean_ddg_url(link.get("href", ""))
        snippet_el = result.select_one(".result__snippet")
        snippet = normalize_space(snippet_el.get_text(" ")) if snippet_el else ""
        if title and href.startswith("http"):
            results.append(SearchResult(title=title, url=href, snippet=snippet))
        if len(results) >= max_results:
            break

    verbose_log(verbose, "search results", [asdict(r) for r in results])
    return results


def fetch_page_text(url: str, max_chars: int = 3500, debug: bool = False) -> str:
    headers = {"User-Agent": USER_AGENT}
    debug_log(debug, f"fetch GET {url}")
    try:
        response = requests.get(url, headers=headers, timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT), allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type and content_type:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
            tag.decompose()
        text = normalize_space(soup.get_text(" "))
        return text[:max_chars]
    except Exception as exc:
        debug_log(debug, f"fetch failed for {url}: {exc}")
        return ""


def search_and_fetch(query: str, max_results: int = 6, debug: bool = False, verbose: bool = False) -> List[SearchResult]:
    results = duckduckgo_search(query, max_results=max_results, debug=debug, verbose=verbose)
    enriched: List[SearchResult] = []
    for item in results:
        item.content = fetch_page_text(item.url, debug=debug)
        enriched.append(item)
        time.sleep(0.2)
    return enriched


# -------------------------
# LLM
# -------------------------

def build_context(results: List[SearchResult]) -> str:
    blocks = []
    for idx, r in enumerate(results, start=1):
        content = r.content or r.snippet
        blocks.append(
            f"[Source {idx}]\n"
            f"Title: {r.title}\n"
            f"URL: {r.url}\n"
            f"Snippet: {r.snippet}\n"
            f"Content excerpt: {content}\n"
        )
    return "\n".join(blocks)


def load_system_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "You are Search Claw. Answer with sources from the provided context."


def llm_chat(user_message: str, results: List[SearchResult], debug: bool = False, verbose: bool = False) -> str:
    system_prompt = load_system_prompt()
    context = build_context(results)
    source_list = "\n".join(f"[{i}] {r.title} - {r.url}" for i, r in enumerate(results, start=1))

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"User question:\n{user_message}\n\n"
                f"Web context:\n{context}\n\n"
                "Answer the user. Use numbered source references like [1], [2] where useful. "
                "End with a short Sources section listing the source numbers used.\n\n"
                f"Available sources:\n{source_list}"
            ),
        },
    ]

    payload: Dict[str, Any] = {
        "messages": messages,
        "temperature": LLAMA_TEMPERATURE,
        "top_p": LLAMA_TOP_P,
        "max_tokens": LLAMA_MAX_TOKENS,
    }
    if LLAMA_MODEL:
        payload["model"] = LLAMA_MODEL

    debug_log(debug, f"llm_chat POST {LLAMA_CHAT_ENDPOINT}")
    verbose_log(verbose, "llm_chat payload", payload)

    response = requests.post(
        LLAMA_CHAT_ENDPOINT,
        json=payload,
        timeout=(HTTP_CONNECT_TIMEOUT, LLM_READ_TIMEOUT),
    )
    response.raise_for_status()
    data = response.json()
    verbose_log(verbose, "llm_chat response", data)

    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {exc}; raw={data}")


def fallback_answer(user_message: str, results: List[SearchResult]) -> str:
    if not results:
        return "I could not find useful web results for that query."
    lines = ["I found these sources, but the local LLM did not produce an answer:", ""]
    for i, r in enumerate(results, start=1):
        desc = r.snippet or (r.content[:220] + "..." if r.content else "No snippet available.")
        lines.append(f"[{i}] {r.title}\n{desc}\n{r.url}\n")
    return "\n".join(lines).strip()


def validate_answer_has_sources(answer: str) -> bool:
    if not REQUIRE_SOURCES:
        return True
    return bool(re.search(r"\[[1-9][0-9]*\]", answer)) or "source" in answer.lower()


def run_agent(user_message: str, max_results: int = 6, debug: bool = False, verbose: bool = False) -> Tuple[str, List[SearchResult]]:
    debug_log(debug, f"run_agent user_message={user_message!r}")
    results = search_and_fetch(user_message, max_results=max_results, debug=debug, verbose=verbose)
    if not results:
        return "I could not find useful web results for that query.", []

    try:
        answer = llm_chat(user_message, results, debug=debug, verbose=verbose)
        if not validate_answer_has_sources(answer):
            debug_log(debug, "answer did not include sources; retrying with stricter instruction")
            stricter_message = user_message + "\n\nImportant: include numbered source references like [1], [2]."
            answer = llm_chat(stricter_message, results, debug=debug, verbose=verbose)
        return answer, results
    except Exception as exc:
        debug_log(debug, f"LLM failed: {exc}")
        return fallback_answer(user_message, results), results


# -------------------------
# Server
# -------------------------

app = Flask(__name__)


@app.post("/message")
def message_endpoint():
    data = request.get_json(silent=True) or {}
    message = data.get("message") or data.get("text") or data.get("query")
    if not message or not isinstance(message, str):
        return jsonify({"ok": False, "error": "Missing string field: message"}), 400

    max_results = int(data.get("max_results", 6))
    debug = bool(data.get("debug", False))
    verbose = bool(data.get("verbose", False))
    answer, sources = run_agent(message, max_results=max_results, debug=debug, verbose=verbose)
    return jsonify({
        "ok": True,
        "answer": answer,
        "sources": [{"title": s.title, "url": s.url, "snippet": s.snippet} for s in sources],
    })


@app.get("/health")
def health_endpoint():
    return jsonify({"ok": True, "service": "search-claw", "time": ts()})


# -------------------------
# CLI
# -------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Claw web-search assistant")
    parser.add_argument("query", nargs="?", help="Question or search query")
    parser.add_argument("--server", action="store_true", help="Run HTTP server")
    parser.add_argument("--host", default=SEARCH_CLAW_HOST, help="Server host")
    parser.add_argument("--port", type=int, default=SEARCH_CLAW_PORT, help="Server port")
    parser.add_argument("--max-results", type=int, default=6, help="Maximum search results")
    parser.add_argument("--debug", action="store_true", help="Print debug logs")
    parser.add_argument("--verbose", action="store_true", help="Print verbose logs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.server:
        print(f"Search Claw server listening on http://{args.host}:{args.port}", file=sys.stderr)
        app.run(host=args.host, port=args.port)
        return 0

    if not args.query:
        print("Error: provide a query or use --server", file=sys.stderr)
        return 2

    answer, _sources = run_agent(args.query, max_results=args.max_results, debug=args.debug, verbose=args.verbose)
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
