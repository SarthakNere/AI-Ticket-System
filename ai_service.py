import time
import os
import json
import logging
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

PROMPT_GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"
GENERATION_MODEL = "qwen/qwen3.8-27b"
PROMPT_ATTACK_THRESHOLD = 0.5

api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=api_key) if api_key else None


class PromptAttackDetected(ValueError):
    pass


def _generate_text(model, prompt, max_attempts=1):
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is None:
                status_code = getattr(exc, "code", None)
            if status_code == 429 or attempt == max_attempts - 1:
                raise
            time.sleep(2 ** (attempt + 1))

    if not response.choices:
        raise ValueError("AI returned an empty response.")

    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI returned an empty response.")

    return content.strip()


def _check_prompt_safety(subject, description):
    text = f"Ticket subject:\n{subject}\n\nTicket description:\n{description}"
    response = _generate_text(
        PROMPT_GUARD_MODEL,
        text,
        max_attempts=3,
    )
    score_text = response.splitlines()[0].strip()
    try:
        attack_score = float(score_text)
    except ValueError:
        label = score_text.upper().strip(" .:*_-")
        if label.startswith(("BENIGN", "SAFE")):
            return True
        if label.startswith(("MALICIOUS", "UNSAFE", "INJECTION", "JAILBREAK")):
            raise PromptAttackDetected(
                "Prompt Guard classified the ticket text as a prompt attack."
            )
        logger.warning("Prompt Guard returned an unrecognized result: %r", response)
        return False

    if not 0 <= attack_score <= 1:
        logger.warning("Prompt Guard returned an out-of-range score: %r", response)
        return False
    if attack_score >= PROMPT_ATTACK_THRESHOLD:
        raise PromptAttackDetected(
            "Prompt Guard classified the ticket text as a prompt attack."
        )
    return False


def analyze_ticket(description, subject=""):
    try:
        _check_prompt_safety(subject, description)
    except PromptAttackDetected:
        raise
    except Exception:
        logger.exception(
            "Prompt Guard screening failed; continuing with ticket analysis."
        )

    prompt = f"""
    Analyze the following customer support ticket.

    Return ONLY a valid JSON object with these fields:
    {{
        "category": "Technical, Billing, Account, or General",
        "priority": "Low, Medium, High, or Urgent",
        "sentiment": "Positive, Neutral, or Negative"
    }}

    Guidelines:
    - Technical: bugs, errors, or technical problems.
    - Billing: payments, refunds, or invoices.
    - Account: login, password, or account access.
    - General: other inquiries.
    - Urgent: critical issues requiring immediate attention.
    - High: major issues affecting the customer.
    - Medium: normal support requests.
    - Low: minor issues or general questions.
    - Determine sentiment from the customer's wording.

    Customer ticket:
    {description}
    """

    result = json.loads(_generate_text(GENERATION_MODEL, prompt, max_attempts=3))

    if not isinstance(result, dict):
        raise ValueError("AI returned an invalid response.")

    required_fields = ["category", "priority", "sentiment"]

    for field in required_fields:
        if field not in result or not isinstance(result[field], str):
            raise ValueError(f"AI response is missing a valid {field}.")

    return result


def generate_suggested_reply(subject, description, category, priority):
    prompt = f"""
    You are a professional customer support agent.

    Write a polite, empathetic, and concise draft reply
    to the customer based on their support ticket.

    Ticket subject: {subject}
    Ticket description: {description}
    Category: {category}
    Priority: {priority}

    Requirements:
    - Acknowledge the customer's issue.
    - Use a professional and helpful tone.
    - Suggest a reasonable next step.
    - Do not claim a refund, payment, or account change
      has already been completed.
    - Do not invent company policies or promises.
    - Keep the reply under 150 words.
    - Return only the reply text, without commentary.
    """

    return _generate_text(GENERATION_MODEL, prompt)