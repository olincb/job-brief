"""The one way the engine talks to the model: generate(), with retries and the fallback
model inside it, and a tolerant parser for the JSON it returns."""

import http.client
import json
import sys
import time
import urllib.error
import urllib.request


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


RETRYABLE = (429, 500, 503)


def call_gemini(model, body, api_key, attempts):
    """Try one model up to `attempts` times. Returns the parsed response, or None when
    every attempt hit a retryable failure. Non-retryable errors exit immediately.
    Backoff starts at 30 seconds, doubles, and caps at five minutes, so four attempts
    span about three and a half minutes: enough to ride out a brief blip before the
    fallback model takes over."""
    req = urllib.request.Request(
        GEMINI_URL.format(model=model), data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code not in RETRYABLE:
                raise SystemExit(f"{model}: HTTP {exc.code}: {detail}")
            problem = f"HTTP {exc.code}: {detail}"
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            problem = f"unreachable: {exc}"
        if attempt < attempts - 1:
            wait = min(30 * 2 ** attempt, 300)
            print(f"{model} attempt {attempt + 1}/{attempts} failed ({problem}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
        else:
            print(f"{model} attempt {attempt + 1}/{attempts} failed ({problem}); giving up on this model", file=sys.stderr)
    return None


def generate(prompt, models, api_key, retries, json_output=False):
    """The one way the engine talks to the model. Tries each model in turn with the retry
    budget split evenly, so callers need no retry logic of their own: ranking on the daily
    run and drafting a profile at signup both come through here. 503 "model is overloaded"
    clusters on whichever model launched most recently and hits paid tiers too, so an
    older Flash as the fallback is the reliable escape hatch. Returns the model's text,
    the model that served it, and the usage metadata."""
    config = {"temperature": 0.2}
    if json_output:
        config["responseMimeType"] = "application/json"
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}).encode()
    per_model = max(1, retries // len(models))
    for model in models:
        data = call_gemini(model, body, api_key, per_model)
        if data:
            text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
            return text, model, data.get("usageMetadata", {})
    raise SystemExit(f"all {retries} attempts failed across {', '.join(models)}")


def parse_model_json(raw):
    """Tolerate a fenced block or stray prose around the object."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise SystemExit(f"model output had no JSON object:\n{raw[:500]}")
    return json.loads(raw[start:end + 1])
