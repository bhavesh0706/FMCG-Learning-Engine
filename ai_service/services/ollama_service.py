# import requests
# import json


# OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# MODEL = "qwen2.5:7b"


# # ============================================================
# # OVERALL VIDEO SUMMARY
# # ============================================================

# PROMPT_TEMPLATE = """
# You are an FMCG customer-feedback analyst.

# Below is the complete transcript from a customer feedback video.

# Transcript:
# {feedback}

# Create a concise business summary.

# Return plain text with exactly this structure:

# Overall Sentiment:
# One sentence.

# Key Customer Feedback:
# - point 1
# - point 2
# - point 3

# Recommended Actions:
# - action 1
# - action 2

# Keep the response under 120 words.
# """


# # ============================================================
# # INDIVIDUAL PART SUMMARY
# # ============================================================

# CHUNK_PROMPT = """
# You are analyzing ONE section of an FMCG customer feedback video.

# The section may contain several minutes of conversation.

# Transcript:
# {chunk}

# Return ONLY valid JSON.

# {{
#   "summary": "One or two sentences explaining what the customer discussed in this section.",
#   "sentiment": "Praise",
#   "key_point": "The most important customer takeaway from this section."
# }}

# The sentiment MUST be exactly one of:

# Praise
# Issue
# Suggestion
# Neutral

# Rules:

# - Praise = customer is expressing satisfaction or appreciation.
# - Issue = customer is describing a problem or complaint.
# - Suggestion = customer recommends an improvement.
# - Neutral = informational discussion without a clear positive or negative opinion.
# - Do not mention information that is not present in the transcript.
# - Do not assume the product name.
# - Do not assume the customer sentiment.
# """


# # ============================================================
# # OLLAMA REQUEST
# # ============================================================

# def _call_ollama(
#     prompt: str,
#     num_predict: int,
#     temperature: float,
#     timeout: int
# ):

#     response = requests.post(
#         OLLAMA_URL,
#         json={
#             "model": MODEL,
#             "prompt": prompt,
#             "stream": False,
#             "options": {
#                 "temperature": temperature,
#                 "num_predict": num_predict
#             }
#         },
#         timeout=timeout
#     )

#     response.raise_for_status()

#     return response.json()["response"].strip()


# # ============================================================
# # OVERALL SUMMARY
# # ============================================================

# def analyze_feedback(
#     transcript: str
# ) -> str:

#     prompt = PROMPT_TEMPLATE.format(
#         feedback=transcript
#     )

#     return _call_ollama(
#         prompt=prompt,
#         num_predict=180,
#         temperature=0.2,
#         timeout=300
#     )


# # ============================================================
# # PART SUMMARY
# # ============================================================

# def summarize_chunk(
#     chunk_text: str
# ):

#     # Empty part protection
#     if not chunk_text.strip():

#         return {
#             "summary": "No spoken content was detected in this section.",
#             "sentiment": "Neutral",
#             "key_point": "No customer feedback was detected."
#         }

#     prompt = CHUNK_PROMPT.format(
#         chunk=chunk_text
#     )

#     text = call_llm(
#         prompt=prompt,
#         num_predict=120,
#         temperature=0.1,
#         timeout=180
#     )

#     # --------------------------------------------------------
#     # Remove accidental markdown JSON fences
#     # --------------------------------------------------------

#     text = text.strip()

#     if text.startswith("```json"):

#         text = text[7:]

#     elif text.startswith("```"):

#         text = text[3:]

#     if text.endswith("```"):

#         text = text[:-3]

#     text = text.strip()

#     # --------------------------------------------------------
#     # Parse JSON
#     # --------------------------------------------------------

#     try:

#         result = json.loads(text)

#         return {
#             "summary": result.get(
#                 "summary",
#                 "No summary generated."
#             ),

#             "sentiment": result.get(
#                 "sentiment",
#                 "Neutral"
#             ),

#             "key_point": result.get(
#                 "key_point",
#                 "No key takeaway generated."
#             )
#         }

#     except Exception:

#         return {
#             "summary": text,
#             "sentiment": "Neutral",
#             "key_point": "Summary generated from this section."
#         }




import json
import os
import requests


# Same defaults as before; override when Ollama runs elsewhere, e.g. in Docker:
#   OLLAMA_URL=http://host.docker.internal:11434/api/generate
OLLAMA_URL = os.environ.get("OLLAMA_URL", "").strip() or "http://127.0.0.1:11434/api/generate"

# ------------------------------------------------------------
# Which language model to use (ai_service/.env)
#   USE_OLLAMA=true   -> local Ollama (OLLAMA_URL, FMCG_LLM_MODEL)    [default]
#   USE_OLLAMA=false  -> OpenAI (OPENAI_API_KEY_ENC + master key, OPENAI_MODEL)
# The OpenAI key is stored encrypted; see services/secret_store.py.
# ------------------------------------------------------------
USE_OLLAMA = os.environ.get("USE_OLLAMA", "true").strip().lower() not in ("0", "false", "no", "off")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")
OPENAI_API_KEY = ""

if not USE_OLLAMA:
    from services.secret_store import SecretError, openai_api_key

    try:
        OPENAI_API_KEY = openai_api_key()
    except SecretError as exc:
        raise SystemExit(f"USE_OLLAMA=false: {exc}") from None
    if not OPENAI_API_KEY:
        raise SystemExit(
            "USE_OLLAMA=false but no OpenAI key is set. Run "
            "`python tools/encrypt_openai_key.py` to store it encrypted in "
            "ai_service/.env, or set USE_OLLAMA=true."
        )
    if not os.environ.get("OPENAI_API_KEY_ENC", "").strip():
        print("WARNING: using the plain OPENAI_API_KEY. Run tools/encrypt_openai_key.py "
              "to store it encrypted instead.")

PROVIDER = "ollama" if USE_OLLAMA else "openai"
OLLAMA_MODEL = os.environ.get("FMCG_LLM_MODEL", "").strip() or "qwen2.5:7b"
# The model that answers, whichever provider is active.
MODEL = OLLAMA_MODEL if USE_OLLAMA else OPENAI_MODEL


# ============================================================
# OVERALL VIDEO SUMMARY
# ============================================================

PROMPT_TEMPLATE = """
You are an FMCG customer-feedback analyst.

Below is the complete transcript from a customer feedback video.

Transcript:
{feedback}

Return ONLY valid JSON.

Use exactly this structure:

{{
  "sentiment": "Praise",
  "summary": "A concise business summary of the customer's overall experience.",
  "key_feedback": [
    "point 1",
    "point 2",
    "point 3"
  ],
  "recommended_actions": [
    "action 1",
    "action 2"
  ]
}}

The "sentiment" MUST be exactly one of:

Praise
Issue
Suggestion
Neutral

Rules:

- Praise = the customer is generally satisfied with the product.
- Issue = the customer is mainly dissatisfied or reporting significant problems.
- Suggestion = the main purpose is recommending improvements.
- Neutral = the feedback is mainly informational or has no clear direction.
- Base the sentiment on the complete transcript.
- Do not guess.
- Keep the summary under 100 words.
- Use only information present in the transcript.
"""


# ============================================================
# INDIVIDUAL PART ANALYSIS
# ============================================================

CHUNK_PROMPT = """
You are analyzing ONE section of an FMCG customer feedback video.

The section may contain several minutes of conversation.

Transcript:
{chunk}

Return ONLY valid JSON.
Do not return markdown.
Do not return explanations outside the JSON.

Use exactly this structure:

{{
  "summary": "One or two sentences explaining what the customer discussed in this section.",
  "sentiment": "Praise",
  "key_point": "The most important customer takeaway from this section.",
  "feedback": [
    {{
      "product": "Product name mentioned by the customer, or null if not identifiable.",
      "aspect": "Specific customer feedback aspect.",
      "sentiment": "Positive",
      "severity": "Strong",
      "confidence": 0.95,
      "evidence": "Short phrase from the transcript supporting this feedback."
    }}
  ]
}}

The top-level sentiment MUST be exactly one of:

Praise
Issue
Suggestion
Neutral

Rules for the top-level sentiment:

- Praise = customer is expressing satisfaction or appreciation.
- Issue = customer is describing a problem or complaint.
- Suggestion = customer recommends an improvement.
- Neutral = informational discussion without a clear positive or negative opinion.

For each item inside "feedback":

The sentiment MUST be exactly one of:

Positive
Positive with concerns
Neutral
Negative

The severity MUST be exactly one of:

Strong
Moderate
Mild
None

Severity rules:

- Strong = very clear, extreme, repeated, or highly important feedback.
- Moderate = noticeable feedback or meaningful concern.
- Mild = small or limited feedback.
- None = no meaningful intensity.

Confidence:

- Must be a number from 0.0 to 1.0.
- Confidence represents how strongly the transcript supports the extracted feedback.
- Do not invent information.

Evidence:

- Must be directly supported by the transcript.
- Keep it short.
- Do not invent evidence.

Aspect rules:

- Extract the most specific meaningful aspect mentioned.
- Examples:
  "too sweet" -> "sweetness"
  "packet tears easily" -> "packaging durability"
  "too expensive" -> "price"
  "not available in stores" -> "availability"
  "very fresh" -> "freshness"
- Do not create an aspect that is not supported by the transcript.

Product rules:

- Use the product name only when the transcript identifies it.
- If the product cannot be identified, use null.
- Never guess the product name.

Important:

- Create one feedback item for each distinct customer opinion.
- A sentence may produce multiple feedback items if it contains separate opinions.
- Do not duplicate the same feedback item.
- If there is no meaningful customer feedback, return an empty feedback array.
- Do not assume sentiment.
- Extract all materially distinct customer opinions.
- Prefer concise evidence.
- Keep each evidence value under 25 words.
- Do not repeat similar opinions.
- Maximum 10 feedback items in one section.
- Finish the JSON completely before stopping.
"""


# ============================================================
# LANGUAGE MODEL REQUEST (Ollama or OpenAI, see USE_OLLAMA)
# ============================================================

def call_llm(
    prompt: str,
    num_predict: int,
    temperature: float,
    timeout: int,
    json_mode: bool = False,
    ollama_model: str | None = None,
    ollama_extra: dict | None = None,
) -> str:
    """Send one prompt to the active provider and return the answer text.

    ollama_model / ollama_extra (e.g. keep_alive, num_ctx) apply to Ollama only.
    """
    if USE_OLLAMA:
        return _call_ollama(prompt, num_predict, temperature, timeout, json_mode,
                            model=ollama_model, extra=ollama_extra)
    return _call_openai(prompt, num_predict, temperature, timeout, json_mode)


def _call_ollama(
    prompt: str,
    num_predict: int,
    temperature: float,
    timeout: int,
    json_mode: bool = False,
    model: str | None = None,
    extra: dict | None = None,
):
    extra = dict(extra or {})
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            **extra.pop("options", {}),
        },
        **extra,
    }

    # JSON mode is used only for structured part analysis.
    if json_mode:
        payload["format"] = "json"

    response = requests.post(
        OLLAMA_URL,
        json=payload,
        timeout=timeout
    )

    response.raise_for_status()

    response_data = response.json()

    return response_data["response"].strip()


def _call_openai(
    prompt: str,
    num_predict: int,
    temperature: float,
    timeout: int,
    json_mode: bool = False,
):
    messages = [{"role": "user", "content": prompt}]
    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": num_predict,
    }
    if json_mode:
        # OpenAI's JSON mode needs the word "JSON" in the messages.
        messages.insert(0, {"role": "system", "content": "Respond only with one valid JSON object."})
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:300]
        raise RuntimeError(f"OpenAI error {response.status_code}: {detail}")

    content = response.json()["choices"][0]["message"].get("content") or ""
    return content.strip()


# ============================================================
# OVERALL SUMMARY
# ============================================================

def analyze_feedback(
    transcript: str
):

    prompt = PROMPT_TEMPLATE.format(
        feedback=transcript
    )

    text = call_llm(
        prompt=prompt,
        num_predict=350,
        temperature=0.1,
        timeout=300,
        json_mode=True
    )

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:].strip()

    elif text.startswith("```"):
        text = text[3:].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    try:

        result = json.loads(text)

        sentiment = result.get(
            "sentiment",
            "Neutral"
        )

        if sentiment not in {
            "Praise",
            "Issue",
            "Suggestion",
            "Neutral"
        }:
            sentiment = "Neutral"

        summary = str(
            result.get(
                "summary",
                "No overall summary generated."
            )
        ).strip()

        key_feedback = result.get(
            "key_feedback",
            []
        )

        recommended_actions = result.get(
            "recommended_actions",
            []
        )

        return {
            "sentiment": sentiment,
            "summary": summary,
            "key_feedback": (
                key_feedback
                if isinstance(key_feedback, list)
                else []
            ),
            "recommended_actions": (
                recommended_actions
                if isinstance(recommended_actions, list)
                else []
            )
        }

    except (
        json.JSONDecodeError,
        TypeError,
        ValueError
    ):

        return {
            "sentiment": "Neutral",
            "summary": (
                "The overall customer feedback "
                "was analyzed successfully."
            ),
            "key_feedback": [],
            "recommended_actions": []
        }


# ============================================================
# NORMALIZE FEEDBACK
# ============================================================

def _normalize_feedback(
    feedback_items
):
    if not isinstance(feedback_items, list):
        return []

    normalized = []

    for item in feedback_items:

        if not isinstance(item, dict):
            continue

        sentiment = item.get(
            "sentiment",
            "Neutral"
        )

        if sentiment not in {
            "Positive",
            "Positive with concerns",
            "Neutral",
            "Negative"
        }:
            sentiment = "Neutral"

        severity = item.get(
            "severity",
            "None"
        )

        if severity not in {
            "Strong",
            "Moderate",
            "Mild",
            "None"
        }:
            severity = "None"

        confidence = item.get(
            "confidence",
            0.0
        )

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(
            0.0,
            min(1.0, confidence)
        )

        product = item.get("product")

        if product is not None:
            product = str(product).strip()

            if not product:
                product = None

        aspect = str(
            item.get(
                "aspect",
                ""
            )
        ).strip()

        evidence = str(
            item.get(
                "evidence",
                ""
            )
        ).strip()

        # Do not keep unusable feedback entries.
        if not aspect:
            continue

        normalized.append(
            {
                "product": product,
                "aspect": aspect,
                "sentiment": sentiment,
                "severity": severity,
                "confidence": confidence,
                "evidence": evidence
            }
        )

    return normalized


# ============================================================
# PART SUMMARY
# ============================================================

def summarize_chunk(
    chunk_text: str
):

    # --------------------------------------------------------
    # Empty part protection
    # --------------------------------------------------------

    if not chunk_text.strip():

        return {
            "summary": (
                "No spoken content was detected in this section."
            ),
            "sentiment": "Neutral",
            "key_point": (
                "No customer feedback was detected."
            ),
            "feedback": []
        }

    prompt = CHUNK_PROMPT.format(
        chunk=chunk_text
    )

    # --------------------------------------------------------
    # Structured JSON response from Ollama
    # --------------------------------------------------------

    text = call_llm(
        prompt=prompt,
        num_predict=900,
        temperature=0.1,
        timeout=300,
        json_mode=True
    )

    # --------------------------------------------------------
    # Remove accidental markdown fences
    # --------------------------------------------------------

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:].strip()

    elif text.startswith("```"):
        text = text[3:].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:

        result = json.loads(text)

        top_sentiment = result.get(
            "sentiment",
            "Neutral"
        )

        if top_sentiment not in {
            "Praise",
            "Issue",
            "Suggestion",
            "Neutral"
        }:
            top_sentiment = "Neutral"

        feedback_items = _normalize_feedback(
            result.get(
                "feedback",
                []
            )
        )

        return {
            # These are the fields your existing UI uses.
            "summary": str(
                result.get(
                    "summary",
                    "No summary generated."
                )
            ).strip(),

            "sentiment": top_sentiment,

            "key_point": str(
                result.get(
                    "key_point",
                    "No key takeaway generated."
                )
            ).strip(),

            # Structured data for the future rating engine.
            "feedback": feedback_items
        }

    except (json.JSONDecodeError, TypeError, ValueError):

        # IMPORTANT:
        # Never put the raw JSON text into "summary".
        # The frontend should never display malformed JSON.
        return {
            "summary": (
                "The customer feedback was analyzed, "
                "but the structured section analysis "
                "could not be completed."
            ),
            "sentiment": "Neutral",
            "key_point": (
                "Structured feedback analysis was incomplete."
            ),
            "feedback": []
        }