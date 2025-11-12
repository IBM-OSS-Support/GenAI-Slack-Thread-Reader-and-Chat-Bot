import json
import requests

OLLAMA_API_URL = "http://localhost:11434/api/generate"

def llama_infer(prompt: str, model: str = "llama3.2:latest") -> str:
    response = requests.post(OLLAMA_API_URL, json={
        "model": model,
        "prompt": prompt,
        "stream": False
    })
    response.raise_for_status()
    return response.json().get("response", "").strip()


def extract_action_items_llm(messages, user_map):
    conversation = ""
    for msg in messages:
        user_id = msg.get("user")
        text = msg.get("text", "").strip()
        if user_id in user_map and text:
            conversation += f"{user_map[user_id]}: {text}\n"

    prompt = f"""
You are an assistant that extracts actionable tasks from a Slack conversation.

Conversation:
{conversation}

Instructions:
- Identify all action items or tasks.
- For each, identify the responsible person.
- Return strictly JSON:
[
  {{"action": "<task text>", "responsible": "<person>"}}
]
"""

    try:
        result = llama_infer(prompt)
        json_start = result.find("[")
        json_end = result.rfind("]") + 1
        return json.loads(result[json_start:json_end])
    except Exception as e:
        return [{"error": str(e)}]
