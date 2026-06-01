#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urljoin, urlparse, parse_qs, unquote, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup


# -------------------------
# Llama.cpp server settings
# -------------------------
LLAMA_BASE_URL = "http://127.0.0.1:8033"
LLAMA_CHAT_ENDPOINT = f"{LLAMA_BASE_URL}/v1/chat/completions"

LLAMA_CONNECT_TIMEOUT = 10
LLAMA_READ_TIMEOUT = 600

# If your llama.cpp server exposes /v1/models, you can set this to that id.
# Some servers REQUIRE "model" in payload even if ignored.
LLAMA_MODEL = ""  # optional
LLAMA_TEMPERATURE = 0.7
LLAMA_TOP_P = 0.9
LLAMA_MAX_TOKENS = 300  # phase 1 doesn't need much

# Phase 2: how many times to re-ask if format is wrong
MAX_FINAL_TRIES = 3

# Max history messages used in chat mode
CHAT_HISTORY_LIMIT = 6


SYSTEM_PROMPT = """You are an agent that can use exactly one tool: web_search.

You MUST follow a 2-phase protocol:

PHASE 1 (REQUEST TOOL)
- When the user asks anything, you MUST request the tool first.
- Output MUST be ONLY one JSON object on a single line, nothing else:
  {"tool":"web_search","query":"<the exact search query>"}

PHASE 2 (FINAL ANSWER)
- You will then receive a message that starts with:
  TOOL_RESULT(web_search):
  followed by JSON: {"query": "...", "results": [{"title":...,"snippet":...,"url":...}, ...]}

- After you receive TOOL_RESULT(web_search), you MUST produce the final answer.
- The final answer MUST be plain text (NOT JSON).

Rules for the final answer:
- You MUST ONLY use information that appears in TOOL_RESULT (titles + snippets + urls).
- Use ONLY the URLs provided in TOOL_RESULT as sources for factual claims.
- If TOOL_RESULT has 0 results, say: "I don't have usable search results to answer reliably."
- You MUST write the answer ONCE. Do NOT repeat the answer.
- You MUST answer in the language of the user.
- You MUST include EXACTLY ONE "Sources:" section at the end, and list each URL only once.
- The "Sources:" section MUST include at least ONE URL.

Formatting constraints:
- Never output tool JSON in PHASE 2.
- Never output anything except the single JSON tool request in PHASE 1.

IMPORTANT:
- In PHASE 1 you MUST output valid JSON. No commentary. No extra words.
"""

CHAT_SYSTEM_PROMPT = """You are a helpful AI assistant.
- Answer the user directly and naturally.
- Do not use tools.
- Be concise but clear.
- Use the conversation history if it is provided.
- Do not invent citations or a Sources section unless the user explicitly asks for sources.
- Do not output JSON unless explicitly asked.
"""


# -------------------------
# Logging helpers
# -------------------------
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str, *, verbose: bool = False, enabled: bool = True) -> None:
    if not enabled:
        return
    prefix = "[verbose]" if verbose else "[debug]"
    print(f"{prefix} {_ts()} {msg}", file=sys.stderr)


# -------------------------
# URL normalization (kept, harmless)
# -------------------------
_STRIP_QUERY_KEYS_PREFIXES = ("utm_",)
_STRIP_QUERY_KEYS_EXACT = {
    "hl", "gl", "pli", "ref", "source", "feature", "fbclid", "gclid",
    "igshid", "mc_cid", "mc_eid",
}


def normalize_url_for_compare(u: str) -> str:
    """
    Normalize URLs so minor variations don't fail validation.
    NOTE: validation is removed; function is harmless and can be kept or deleted.
    """
    try:
        p = urlparse(u.strip())
    except Exception:
        return u.strip()

    scheme = (p.scheme or "").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or ""
    fragment = ""  # drop

    if path != "/" and path.endswith("/"):
        path = path[:-1]

    qs = parse_qs(p.query, keep_blank_values=True)

    if netloc.endswith("play.google.com") and path == "/store/apps/details":
        app_id = (qs.get("id") or [""])[0]
        kept = {}
        if app_id:
            kept["id"] = [app_id]
        query = urlencode({k: v[0] for k, v in kept.items()}, doseq=False)
        return urlunparse((scheme, netloc, path, "", query, fragment))

    kept_qs = {}
    for k, v in qs.items():
        kl = k.lower()
        if any(kl.startswith(pref) for pref in _STRIP_QUERY_KEYS_PREFIXES):
            continue
        if kl in _STRIP_QUERY_KEYS_EXACT:
            continue
        kept_qs[k] = v

    query_pairs = []
    for k in sorted(kept_qs.keys()):
        vals = kept_qs[k]
        if not vals:
            continue
        query_pairs.append((k, vals[0]))
    query = urlencode(query_pairs)

    return urlunparse((scheme, netloc, path, "", query, fragment))


# -------------------------
# Dedup + Sanitizer (kills repeated answer blocks)
# -------------------------
def collapse_duplicate_answer(text: str) -> str:
    """Lightweight duplicate collapse for exact A+A patterns."""
    if not text:
        return text
    raw = text.strip()

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    n = norm(raw)
    if len(n) < 40:
        return text

    L = len(raw)
    for delta in (0, -1, 1, -2, 2, -5, 5, -10, 10):
        cut = L // 2 + delta
        if 0 < cut < L:
            a = raw[:cut].strip()
            b = raw[cut:].strip()
            if a and b and norm(a) == norm(b):
                return a + "\n"
    return text


def sanitize_final_answer(text: str) -> str:
    """
    Keep only the FIRST complete answer block.
    A block is: <anything> + 'Sources:' + one or more URL lines.
    Also dedupe URLs in Sources and normalize bullet formatting.

    NOTE: This is NOT sources validation. It only cleans duplicates and formats.
    """
    if not text:
        return text

    t = text.strip()
    t = collapse_duplicate_answer(t).strip()

    m = re.search(r"(?is)\bSources:\s*\n", t)
    if not m:
        return t + "\n"

    prefix = t[:m.end()].rstrip()
    after = t[m.end():]

    urls = re.findall(r"(?im)^\s*(?:[-*]\s*)?(https?://\S+)\s*$", after)
    if not urls:
        return prefix.strip() + "\n"

    seen = set()
    deduped = []
    for u in urls:
        u = u.rstrip(").,;]}>\"'")
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    out = prefix + "\n" + "\n".join(f"- {u}" for u in deduped)
    return out.strip() + "\n"


# -------------------------
# Enforce Sources: minimum one (final safety net)
# -------------------------
def ensure_sources_min_one(final_text: str, tool_results: List[Dict[str, str]]) -> str:
    """
    Ensure the final answer ends with:
      Sources:
      - <url>
    and contains at least one URL. URLs are taken ONLY from tool_results.
    """
    urls: List[str] = []
    seen = set()
    for r in tool_results or []:
        u = (r.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    if not urls:
        return (final_text or "").strip() + "\n"

    t = (final_text or "").strip()
    if not t:
        return "I don't have usable search results to answer reliably.\n\nSources:\n- " + urls[0] + "\n"

    if re.search(r"(?im)^\s*Sources:\s*$", t) is None:
        return t + "\n\nSources:\n- " + urls[0] + "\n"

    parts = re.split(r"(?im)^\s*Sources:\s*$", t, maxsplit=1)
    if len(parts) == 2:
        after = parts[1]
        if re.search(r"(?im)https?://\S+", after) is None:
            return t.rstrip() + "\n- " + urls[0] + "\n"

    return t + "\n"


def has_sources_min_one(text: str) -> bool:
    if not text:
        return False
    t = text.strip()

    m = re.search(r"(?im)^\s*Sources:\s*$", t)
    if not m:
        return False

    after = t[m.end():]
    return re.search(r"(?im)https?://\S+", after) is not None


# -------------------------
# Tool: DuckDuckGo LITE search
# -------------------------
def _decode_duckduckgo_target(href: str, base_url: str) -> Optional[str]:
    if not href:
        return None

    abs_href = urljoin(base_url, href.strip())
    parsed = urlparse(abs_href)
    qs = parse_qs(parsed.query)

    uddg = (qs.get("uddg") or [None])[0]
    if uddg:
        target = unquote(uddg)
    elif parsed.scheme in ("http", "https") and not parsed.netloc.endswith("duckduckgo.com"):
        target = abs_href
    else:
        return None

    target = target.strip()
    if not target.startswith(("http://", "https://")):
        return None
    if urlparse(target).netloc.endswith("duckduckgo.com"):
        return None
    return target


def _parse_duckduckgo_results(html: str, base_url: str, max_results: int) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []
    seen: set[str] = set()

    candidate_links = []
    candidate_links.extend(soup.select("a.result__a"))
    candidate_links.extend(soup.select("a.result-link"))
    candidate_links.extend(soup.select("td.result-link a"))
    candidate_links.extend(soup.select("a[href]"))

    for a in candidate_links:
        href = (a.get("href") or "").strip()
        title = a.get_text(" ", strip=True)
        if not href or not title:
            continue

        title_l = title.lower()
        if title_l in {"next", "previous", "settings", "feedback"}:
            continue
        if "duckduckgo" in title_l and len(title_l) < 40:
            continue

        target_url = _decode_duckduckgo_target(href, base_url)
        if not target_url or target_url in seen:
            continue

        snippet = title
        result_container = a.find_parent(class_=re.compile(r"result", re.I))
        if result_container:
            snippet_el = result_container.select_one(".result__snippet, .result-snippet")
            if snippet_el:
                snippet = snippet_el.get_text(" ", strip=True) or title

        seen.add(target_url)
        results.append({"title": title, "snippet": snippet, "url": target_url})
        if len(results) >= max_results:
            break

    return results


def web_search(
    query: str,
    max_results: int = 5,
    timeout: int = 25,
    debug: bool = False,
    verbose: bool = False,
) -> List[Dict[str, str]]:
    urls = [
        "https://lite.duckduckgo.com/lite/",
        "https://html.duckduckgo.com/html/",
        "https://duckduckgo.com/html/",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36 SearchClaw/1.0",
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_error: Optional[Exception] = None
    for url in urls:
        t0 = time.time()
        try:
            resp = requests.get(url, params={"q": query}, headers=headers, timeout=timeout)
            dt = time.time() - t0

            if debug:
                log(f"web_search GET {resp.url} -> {resp.status_code} in {dt:.2f}s", enabled=True)
                if verbose:
                    log(f"web_search headers: {headers}", verbose=True, enabled=True)

            resp.raise_for_status()
            results = _parse_duckduckgo_results(resp.text, url, max_results)

            if debug:
                log(f"web_search parsed results from {url}: {len(results)}", enabled=True)
                if verbose:
                    for i, r in enumerate(results, 1):
                        log(f"result[{i}] title={r['title']!r} url={r['url']!r}", verbose=True, enabled=True)

            if results:
                return results
        except Exception as exc:
            last_error = exc
            if debug:
                log(f"web_search failed for {url}: {exc}", enabled=True)

    if last_error:
        raise last_error
    return []


# -------------------------
# Enrich results (fetch a couple pages)
# -------------------------
def _fetch_page_text(url: str, timeout: int = 20) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) SearchClaw/1.0",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = (soup.title.get_text(" ", strip=True) if soup.title else "").strip()

    text_chunks: List[str] = []
    for p in soup.select("p"):
        t = p.get_text(" ", strip=True)
        if t:
            text_chunks.append(t)
        if sum(len(x) for x in text_chunks) > 900:
            break

    combined = " ".join([x for x in [title, " ".join(text_chunks)] if x])
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined[:1400]


def enrich_results_with_page_text(
    results: List[Dict[str, str]],
    max_pages: int = 5,
    debug: bool = False,
    verbose: bool = False,
) -> List[Dict[str, str]]:
    enriched = []
    fetched = 0
    for r in results:
        rr = dict(r)
        if fetched < max_pages:
            try:
                txt = _fetch_page_text(rr["url"])
                if txt:
                    rr["snippet"] = txt
                fetched += 1
                if debug:
                    log(f"enrich fetched: {rr['url']} (snippet_len={len(rr['snippet'])})", enabled=True)
                    if verbose:
                        log(f"snippet preview: {rr['snippet'][:220]}", verbose=True, enabled=True)
            except Exception as e:
                if debug:
                    log(f"enrich fetch failed: {rr['url']} err={e}", enabled=True)
        enriched.append(rr)
    return enriched


# -------------------------
# History helpers
# -------------------------
def normalize_history(history: Optional[List[Dict[str, Any]]], limit: int = CHAT_HISTORY_LIMIT) -> List[Dict[str, str]]:
    cleaned: List[Dict[str, str]] = []
    if not history:
        return cleaned

    for item in history[-limit:]:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or item.get("content") or "").strip()

        if role not in ("user", "assistant"):
            continue
        if not text:
            continue

        cleaned.append({"role": role, "content": text})

    return cleaned


# -------------------------
# LLM call
# -------------------------
def llm_chat(
    messages: List[Dict[str, Any]],
    response_format: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    verbose: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Tuple[str, Dict[str, Any]]:
    payload: Dict[str, Any] = {
        "messages": messages,
        "temperature": LLAMA_TEMPERATURE if temperature is None else temperature,
        "top_p": LLAMA_TOP_P,
        "max_tokens": LLAMA_MAX_TOKENS if max_tokens is None else max_tokens,
        "stream": False,
        "repeat_penalty": 1.20,
        "repeat_last_n": 512,
        "frequency_penalty": 0.2,
        "presence_penalty": 0.0,
    }

    payload["model"] = LLAMA_MODEL or "default"

    if response_format is not None:
        payload["response_format"] = response_format

    if debug:
        log(f"llm_chat POST {LLAMA_CHAT_ENDPOINT}", enabled=True)
        log(
            f"llm_chat max_tokens={payload['max_tokens']} temp={payload['temperature']} top_p={LLAMA_TOP_P}",
            enabled=True
        )
        if verbose:
            log(f"llm_chat payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}", verbose=True, enabled=True)

    t0 = time.time()
    resp = requests.post(
        LLAMA_CHAT_ENDPOINT,
        json=payload,
        timeout=(LLAMA_CONNECT_TIMEOUT, LLAMA_READ_TIMEOUT),
    )
    dt = time.time() - t0

    if debug:
        log(f"llm_chat status={resp.status_code} in {dt:.2f}s", enabled=True)

    if resp.status_code >= 400 and debug:
        try:
            log(f"llm_chat error body: {resp.text}", enabled=True)
        except Exception:
            log("llm_chat error body: <unreadable>", enabled=True)

    resp.raise_for_status()
    data = resp.json()

    choice0 = (data.get("choices") or [{}])[0]
    msg = choice0.get("message") or {}
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    text = content if content else reasoning

    text = collapse_duplicate_answer(text).strip()
    if text:
        text += "\n"

    if debug and verbose:
        preview = text if len(text) < 800 else (text[:800] + " ...[truncated]")
        log(f"llm_chat text preview:\n{preview}", verbose=True, enabled=True)

    return text, data


# -------------------------
# Phase 1 parsing
# -------------------------
def _extract_json_object(text: str) -> str:
    s = (text or "").strip()
    if not s:
        raise ValueError("Empty model output for tool request.")
    if s.startswith("{") and s.endswith("}"):
        return s
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        return m.group(0).strip()
    raise ValueError(f"Could not find JSON object in model output:\n{s}")


def parse_tool_request(text: str) -> Dict[str, Any]:
    obj = json.loads(_extract_json_object(text))
    if not isinstance(obj, dict):
        raise ValueError("Tool request is not an object")
    tool = obj.get("tool")
    query = obj.get("query")
    if tool is None and isinstance(query, str) and query.strip():
        tool = "web_search"
    if tool != "web_search":
        raise ValueError(f"Unexpected tool: {tool}")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Invalid query")
    return {"tool": "web_search", "query": query.strip()}


def get_tool_request(user_message: str, debug: bool = False, verbose: bool = False) -> Dict[str, Any]:
    messages1 = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    text1, _ = llm_chat(messages1, response_format=None, debug=debug, verbose=verbose)
    if debug:
        log(f"Phase1 try1 raw text: {text1!r}", enabled=True)

    try:
        return parse_tool_request(text1)
    except Exception:
        messages1_retry = messages1 + [
            {"role": "user", "content": 'Return ONLY: {"tool":"web_search","query":"..."}'}
        ]
        text2, _ = llm_chat(messages1_retry, response_format=None, debug=debug, verbose=verbose)
        if debug:
            log(f"Phase1 try2 raw text: {text2!r}", enabled=True)
        return parse_tool_request(text2)


# -------------------------
# Phase 2 re-ask loop
# -------------------------
def get_final_answer_with_reask(
    user_message: str,
    tool_json: str,
    tool_results: List[Dict[str, str]],
    debug: bool = False,
    verbose: bool = False,
) -> str:
    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "TOOL_CALL(web_search) executed."},
    ]

    strict_rules = (
        "Return the final answer ONCE.\n"
        "MANDATORY FORMAT:\n"
        "1) Plain text answer (no JSON).\n"
        "2) End with EXACTLY one section:\n"
        "Sources:\n"
        "- https://...\n"
        "Rules:\n"
        "- The Sources section MUST include at least ONE URL.\n"
        "- Use ONLY URLs present in TOOL_RESULT.\n"
        "- Do NOT repeat the answer.\n"
    )

    last_text = ""
    for attempt in range(1, MAX_FINAL_TRIES + 1):
        if attempt == 1:
            user_instruction = (
                "TOOL_RESULT(web_search):\n"
                + tool_json
                + "\n\n"
                + strict_rules
            )
            messages = base_messages + [{"role": "user", "content": user_instruction}]
        else:
            user_instruction = (
                "Your previous answer did NOT follow the required format.\n"
                "Fix it now.\n\n"
                + strict_rules
                + "\n"
                "TOOL_RESULT(web_search):\n"
                + tool_json
                + "\n\n"
                "Previous (invalid) answer:\n"
                + last_text
                + "\n\n"
                "Return the corrected final answer now."
            )
            messages = base_messages + [{"role": "user", "content": user_instruction}]

        if debug:
            log(f"Phase2 attempt {attempt}/{MAX_FINAL_TRIES}", enabled=True)

        text, _ = llm_chat(
            messages,
            response_format=None,
            debug=debug,
            verbose=verbose,
            max_tokens=500,
            temperature=0.0,
        )
        text = sanitize_final_answer(text)
        last_text = text

        if has_sources_min_one(text):
            return text

        if debug:
            log("Phase2 output invalid (missing Sources or URL). Will re-ask.", enabled=True)

    enforced = ensure_sources_min_one(last_text, tool_results)
    return enforced


# -------------------------
# Search mode agent
# -------------------------
def run_search_agent(
    user_message: str,
    max_open_pages: int = 5,
    debug: bool = False,
    verbose: bool = False,
) -> str:
    if debug:
        log(f"run_search_agent user_message={user_message!r}", enabled=True)
        log(f"timeouts connect={LLAMA_CONNECT_TIMEOUT}s read={LLAMA_READ_TIMEOUT}s", enabled=True)

    tool_req = get_tool_request(user_message, debug=debug, verbose=verbose)
    query = tool_req["query"]
    if debug:
        log(f"Phase1 tool_req parsed: {tool_req}", enabled=True)

    results = web_search(query, max_results=5, debug=debug, verbose=verbose)
    results = enrich_results_with_page_text(results, max_pages=max_open_pages, debug=debug, verbose=verbose)

    tool_payload = {"query": query, "results": results}
    tool_json = json.dumps(tool_payload, ensure_ascii=False)

    if debug:
        log(f"Tool payload size={len(tool_json)} chars", enabled=True)
        if verbose:
            log(f"Tool payload:\n{tool_json}", verbose=True, enabled=True)

    final_text = get_final_answer_with_reask(
        user_message=user_message,
        tool_json=tool_json,
        tool_results=results,
        debug=debug,
        verbose=verbose,
    )

    final_text = sanitize_final_answer(final_text)
    final_text = ensure_sources_min_one(final_text, results)
    return final_text


# -------------------------
# Chat mode agent
# -------------------------
def run_chat_agent(
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
    debug: bool = False,
    verbose: bool = False,
) -> str:
    if debug:
        log(f"run_chat_agent user_message={user_message!r}", enabled=True)

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
    ]

    cleaned_history = normalize_history(history, CHAT_HISTORY_LIMIT)
    if cleaned_history:
        messages.extend(cleaned_history)
        if debug and verbose:
            log(
                f"run_chat_agent history injected:\n{json.dumps(cleaned_history, ensure_ascii=False, indent=2)}",
                verbose=True,
                enabled=True,
            )

    if not messages or messages[-1].get("role") != "user" or messages[-1].get("content", "").strip() != user_message.strip():
        messages.append({"role": "user", "content": user_message})

    text, _ = llm_chat(
        messages,
        response_format=None,
        debug=debug,
        verbose=verbose,
        max_tokens=700,
        temperature=0.7,
    )

    text = collapse_duplicate_answer(text).strip()
    if not text:
        text = "No reply generated."
    return text + "\n"


# -------------------------
# Dispatcher
# -------------------------
def run_agent(
    user_message: str,
    mode: str = "search",
    history: Optional[List[Dict[str, Any]]] = None,
    max_open_pages: int = 5,
    debug: bool = False,
    verbose: bool = False,
) -> str:
    mode = (mode or "search").strip().lower()

    if mode == "chat":
        return run_chat_agent(user_message, history=history, debug=debug, verbose=verbose)

    if mode == "search":
        return run_search_agent(user_message, max_open_pages=max_open_pages, debug=debug, verbose=verbose)

    return f"Unsupported mode: {mode}\n"


# -------------------------
# HTTP Server mode (for WhatsApp bridge)
# -------------------------
def serve_http(host: str = "127.0.0.1", port: int = 8811, debug: bool = False, verbose: bool = False) -> None:
    """
    Minimal HTTP JSON server:
      POST /message
      Body: {"text": "...", "mode": "chat"|"search", "prefix": "!"|"?", "history": [...]}
      Response: {"reply": "..."}
    Uses ONLY stdlib (http.server) to avoid extra deps.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "SearchClawHTTP/1.2"

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/message":
                self._send(404, {"error": "not_found"})
                return

            try:
                n = int(self.headers.get("Content-Length", "0"))
            except Exception:
                n = 0

            raw = self.rfile.read(n) if n > 0 else b""
            try:
                obj = json.loads(raw.decode("utf-8") if raw else "{}")
            except Exception:
                self._send(400, {"error": "invalid_json"})
                return

            text = str(obj.get("text") or "").strip()
            mode = str(obj.get("mode") or "search").strip().lower()
            prefix = str(obj.get("prefix") or "").strip()
            history = obj.get("history") or []
            try:
                max_open_pages = int(obj.get("max_open_pages") or 5)
            except Exception:
                max_open_pages = 5
            max_open_pages = max(0, min(max_open_pages, 5))

            if not isinstance(history, list):
                history = []

            if not text:
                self._send(400, {"error": "missing_text"})
                return

            if len(text) > 6000:
                text = text[:6000]

            if mode not in ("chat", "search"):
                if prefix == "!":
                    mode = "chat"
                elif prefix == "?":
                    mode = "search"
                else:
                    mode = "search"

            if debug:
                log(
                    f"HTTP /message text={text!r} mode={mode!r} prefix={prefix!r} history_len={len(history)} max_open_pages={max_open_pages}",
                    enabled=True,
                )

            try:
                reply = run_agent(text, mode=mode, history=history, max_open_pages=max_open_pages, debug=debug, verbose=verbose)
                if len(reply) > 3500:
                    reply = reply[:3500] + "\n\n[truncated]"
                self._send(200, {"reply": reply, "mode": mode})
            except requests.RequestException as e:
                self._send(502, {"error": "network_error", "detail": str(e)})
            except Exception as e:
                self._send(500, {"error": "agent_error", "detail": str(e)})

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[http] {_ts()} {self.address_string()} - {fmt % args}", file=sys.stderr)

    httpd = HTTPServer((host, port), Handler)
    print(f"[http] listening on http://{host}:{port}/message", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SearchClaw agent (CLI + optional HTTP server)")
    parser.add_argument("question", nargs="?", help="User question to ask the agent (CLI mode)")
    parser.add_argument("--mode", choices=["chat", "search"], default="search", help="Agent mode in CLI mode")
    parser.add_argument("--history-json", default="", help="JSON list for chat history in CLI mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs to stderr")
    parser.add_argument("--verbose", action="store_true", help="Enable very verbose logs")

    parser.add_argument("--server", action="store_true", help="Run as local HTTP server for WhatsApp bridge")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8811, help="Server port (default: 8811)")

    args = parser.parse_args()

    if args.server:
        serve_http(host=args.host, port=args.port, debug=args.debug, verbose=args.verbose)
        return

    if not args.question:
        parser.error("question is required in CLI mode (or use --server)")

    history: List[Dict[str, Any]] = []
    if args.history_json:
        try:
            parsed = json.loads(args.history_json)
            if isinstance(parsed, list):
                history = parsed
        except Exception as e:
            print(f"[history parse error] {e}", file=sys.stderr)
            sys.exit(2)

    try:
        print(run_agent(args.question, mode=args.mode, history=history, debug=args.debug, verbose=args.verbose))
    except requests.RequestException as e:
        print(f"[network error] {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
