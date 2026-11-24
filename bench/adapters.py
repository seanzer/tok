#!/usr/bin/env python3
"""Model adapters for the tokens-to-green benchmark.

An adapter turns (system prompt, conversation) into a Reply with the text and the token counts the provider
reports. The runner only knows this interface, so a model is pluggable.

Credentials: the real key never enters this process. A request gets a surrogate from Muse's credential helper
(`dynamic_credentials.add_surrogate_to_request`), which the egress proxy swaps for the key, only for the allowed
host. Redirects are refused. Nothing here reads, prints or stores a key.

  python3 bench/adapters.py anthropic models          list the model ids the account can use
  python3 bench/adapters.py gemini models
  python3 bench/adapters.py anthropic smoke [--model ID]   one tiny request, prints the reply and the usage
  python3 bench/adapters.py gemini smoke [--model ID]
"""
import json
import sys
import urllib.error
import urllib.request

CRED_HELPER_DIR = "/opt/muse/skills/custom-connector-setup/bin"


class AdapterError(Exception):
    """kind: not_connected | rate_limited | http | network | bad_response. Only `network` is ever retried, once."""

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


class Reply:
    def __init__(self, text, input_tokens, output_tokens, thinking_tokens=None, usage=None, stop=None):
        self.text = text
        self.input_tokens = input_tokens      # None when the provider did not report it
        self.output_tokens = output_tokens    # visible output; see each adapter for what it includes
        self.thinking_tokens = thinking_tokens  # None when not reported separately
        self.usage = usage or {}              # the provider's usage object, as received
        self.stop = stop

    def billed_tokens(self):
        """Input + output + separately reported thinking. None if a count is missing."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens + (self.thinking_tokens or 0)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise AdapterError("http", "redirect refused; check the endpoint")


def credentialed_transport(connection, host, timeout=180):
    """A transport that authenticates through the Muse connection `connection`, for `host` only."""

    def send(method, url, headers, body):
        if CRED_HELPER_DIR not in sys.path:
            sys.path.insert(0, CRED_HELPER_DIR)
        from dynamic_credentials import DynamicCredentialError, add_surrogate_to_request, read_json_response
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for attempt in (1, 2):  # one retry, for a stalled connection only
            req = urllib.request.Request(url, data=data, method=method)
            for k, v in headers.items():
                req.add_header(k, v)
            if data is not None:
                req.add_header("content-type", "application/json")
            try:
                add_surrogate_to_request(req, connection, allowed_hosts=[host])
            except (DynamicCredentialError, OSError) as e:
                raise AdapterError("not_connected", f"{connection} is not available: {e}")
            try:
                with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
                    return r.status, read_json_response(r)
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = {"body": text[:600]}
                return e.code, payload
            except DynamicCredentialError as e:
                raise AdapterError("bad_response", str(e))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
        raise AdapterError("network", f"no response from {host}: {last}")

    return send


def expect_ok(status, payload, provider):
    if status == 200:
        return payload
    detail = json.dumps(payload)[:400]
    if status == 429:
        raise AdapterError("rate_limited", f"{provider} rate limit (HTTP 429): {detail}", status)
    if status in (401, 403):
        raise AdapterError("not_connected", f"{provider} refused the credential (HTTP {status}): {detail}", status)
    raise AdapterError("http", f"{provider} HTTP {status}: {detail}", status)


class AnthropicAdapter:
    """Anthropic Messages API. `output_tokens` is what the API reports: it includes any thinking tokens, which
    this API does not report separately, so `thinking_tokens` stays None."""
    name, host, connection = "anthropic", "api.anthropic.com", "custom.anthropic"
    default_model = "claude-opus-5-5"

    def __init__(self, model=None, max_tokens=4096, transport=None):
        self.model = model or self.default_model
        self.max_tokens = max_tokens
        self.send = transport or credentialed_transport(self.connection, self.host)
        self.headers = {"anthropic-version": "2023-06-01"}

    def models(self):
        status, payload = self.send("GET", f"https://{self.host}/v1/models?limit=100", self.headers, None)
        return [m["id"] for m in expect_ok(status, payload, self.name).get("data", [])]

    def complete(self, system, messages):
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        if system:
            body["system"] = system
        status, payload = self.send("POST", f"https://{self.host}/v1/messages", self.headers, body)
        p = expect_ok(status, payload, self.name)
        text = "".join(b.get("text", "") for b in p.get("content", []) if b.get("type") == "text")
        u = p.get("usage") or {}
        cached = (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
        inp = u.get("input_tokens")
        return Reply(text, None if inp is None else inp + cached, u.get("output_tokens"), None, u, p.get("stop_reason"))


class GeminiAdapter:
    """Google Gemini generateContent. Thinking tokens (usageMetadata.thoughtsTokenCount) are recorded separately
    from the visible output (candidatesTokenCount) and both count toward billed tokens."""
    name, host, connection = "gemini", "generativelanguage.googleapis.com", "custom.google-gemini"
    default_model = "gemini-4-neon"

    def __init__(self, model=None, max_tokens=8192, transport=None):
        self.model = model or self.default_model
        self.max_tokens = max_tokens
        self.send = transport or credentialed_transport(self.connection, self.host)

    def models(self):
        status, payload = self.send("GET", f"https://{self.host}/v1beta/models?pageSize=200", {}, None)
        out = []
        for m in expect_ok(status, payload, self.name).get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", ["generateContent"]):
                out.append(m.get("name", "").split("/", 1)[-1])
        return out

    def complete(self, system, messages):
        body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                             for m in messages],
                "generationConfig": {"maxOutputTokens": self.max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"https://{self.host}/v1beta/models/{self.model}:generateContent"
        status, payload = self.send("POST", url, {}, body)
        p = expect_ok(status, payload, self.name)
        cands = p.get("candidates") or [{}]
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(x.get("text", "") for x in parts if not x.get("thought"))
        u = p.get("usageMetadata") or {}
        return Reply(text, u.get("promptTokenCount"), u.get("candidatesTokenCount", 0 if u else None),
                     u.get("thoughtsTokenCount"), u, cands[0].get("finishReason"))


class ScriptedAdapter:
    """For tests and dry runs: replies come from a function of (system, messages). No network, no tokens spent.
    Token counts are whitespace-separated word counts, labelled as such; they are not real tokenizer counts."""
    name, model = "scripted", "scripted"

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        text = self.fn(system, messages)
        words_in = len(system.split()) + sum(len(m["content"].split()) for m in messages)
        return Reply(text, words_in, len(text.split()), None, {"unit": "words, not tokens"})


ADAPTERS = {"anthropic": AnthropicAdapter, "gemini": GeminiAdapter}


def main(argv):
    if len(argv) < 2 or argv[0] not in ADAPTERS or argv[1] not in ("models", "smoke"):
        sys.stderr.write(__doc__)
        return 64
    model = argv[argv.index("--model") + 1] if "--model" in argv and argv.index("--model") + 1 < len(argv) else None
    a = ADAPTERS[argv[0]](model=model, max_tokens=256)
    try:
        if argv[1] == "models":
            for m in a.models():
                print(m)
            return 0
        r = a.complete("", [{"role": "user", "content": "Reply with the single word: ok"}])
        print(json.dumps({"provider": a.name, "model": a.model, "text": r.text, "input_tokens": r.input_tokens,
                          "output_tokens": r.output_tokens, "thinking_tokens": r.thinking_tokens,
                          "billed_tokens": r.billed_tokens(), "stop": r.stop, "usage": r.usage}))
        return 0
    except AdapterError as e:
        print(json.dumps({"provider": a.name, "model": a.model, "error": e.kind, "message": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
