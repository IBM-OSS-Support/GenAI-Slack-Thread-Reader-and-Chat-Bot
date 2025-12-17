#action_item_generator.py
#prompt tuned for granite

import json
import logging
import re
import os
from dotenv import load_dotenv
from typing import Literal, List, Dict, Any, Optional
from datetime import datetime, timedelta
import requests
import ollama

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
ModelType = Literal["ollama"]

class ActionItemGenerator:
    def __init__(self, slack_app=None, model_type: ModelType = "ollama", model_name: str = None):
        """Initialize LLM model"""
        self.logger = logger
        self.slack_app = slack_app
        self.model_type = model_type
        self.model_name = model_name or os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
        self.model = self.model_name
        self.ollama = ollama
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        try:
            self.logger.info(f"Action item generator initialization started (model: {model_type})")
            if model_type == "ollama":
                self._init_ollama(model_name)
            else:
                raise ValueError(f"Unsupported model type: {model_type}")
            self.logger.info("Action item generator initialization completed")
        except Exception as e:
            self.logger.error(f"Error occurred during action item generator initialization: {str(e)}", exc_info=True)
            raise

    def _init_ollama(self, model_name: str = None):
        """Initialize Ollama connection"""
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model_name = model_name or os.getenv("OLLAMA_MODEL_NAME", "granite3.3:8b")
        self.model = self.model_name
        try:
            response = requests.get(f"{self.ollama_url}/api/tags")
            if response.status_code == 200:
                self.logger.info("Successfully connected to Ollama")
            else:
                raise ConnectionError(f"Failed to connect to Ollama: {response.status_code}")
        except Exception as e:
            raise ConnectionError(f"Could not connect to Ollama at {self.ollama_url}: {str(e)}")

    def _prepare_conversation(self, messages: List[Dict[str, Any]], context_type: str = "thread") -> str:
        """
        Convert Slack messages into structured conversation text.
        """
        conversation = []
        bot_user_id = None
        
        try:
            if self.slack_app and hasattr(self.slack_app, 'client'):
                bot_user_id = self.slack_app.client.auth_test()["user_id"]
        except Exception:
            bot_user_id = None

        skip_phrases = [
            "extract from", "please extract action items", "extract action items",
            "show my tasks", "claimed by", "extracting tasks", "no action items found",
            "no messages found", "error extracting"
        ]

        for msg in messages:
            if msg.get("bot_id") or (bot_user_id and msg.get("user") == bot_user_id):
                continue
                
            text = msg.get("text", "").strip()
            if not text:
                continue
                
            if any(phrase in text.lower() for phrase in skip_phrases):
                continue

            user_id = msg.get("user", "Unknown")
            display_name = user_id  # Default to user_id
            
            # Try to get user info if slack_app is available
            if self.slack_app and hasattr(self.slack_app, 'client'):
                try:
                    info = self.slack_app.client.users_info(user=user_id)
                    user_profile = info.get("user", {}).get("profile", {})
                    display_name = (
                        user_profile.get("display_name") or
                        user_profile.get("real_name") or
                        user_profile.get("name") or
                        user_id
                    )
                except Exception:
                    display_name = user_id

            # Clean up the text
            text = re.sub(r"<@[^>]+>", "", text)  # Remove user mentions
            text = re.sub(r"<https?://[^>]+>", "", text)  # Remove URLs
            text = text.strip()
            
            if text:
                conversation.append(f"{display_name}: {text}")

        prepared_text = "\n".join(conversation)
        self.logger.info(f"=== Prepared Conversation ({context_type}) ===")
        self.logger.info(prepared_text if prepared_text else "(EMPTY CONVERSATION)")
        return prepared_text

    def _generate_with_ollama(self, prompt: str, context_type: str = None) -> str:
        """Generate response using Ollama - safely handle streaming JSON lines."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_predict": 1024
            }
        }

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=120
            )

            if response.status_code != 200:
                self.logger.error(f"Ollama API error: {response.status_code} - {response.text}")
                return ""

            # Handle both single JSON object and multiple JSON lines
            raw_text = response.text.strip()
            result_text = ""

            if raw_text.startswith('{'):
                # Single JSON object
                try:
                    data = json.loads(raw_text)
                    if "response" in data:
                        result_text = data["response"]
                except json.JSONDecodeError:
                    self.logger.error("Failed to parse Ollama response as JSON")
            else:
                # Multiple JSON lines
                for line in raw_text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            result_text += data["response"]
                    except json.JSONDecodeError:
                        continue

            return result_text.strip()

        except requests.exceptions.Timeout:
            self.logger.error("Ollama API request timed out")
            return ""
        except Exception as e:
            self.logger.error(f"Ollama API request failed: {str(e)}", exc_info=True)
            return ""

    def _calculate_weekend_date(self, today: str) -> str:
        """Calculate the next weekend date from today."""
        try:
            today_date = datetime.strptime(today, '%Y-%m-%d')
            days_until_saturday = (5 - today_date.weekday()) % 7
            if days_until_saturday == 0:
                days_until_saturday = 7
            weekend_date = today_date + timedelta(days=days_until_saturday)
            return weekend_date.strftime('%Y-%m-%d')
        except ValueError:
            self.logger.error(f"Invalid date format: {today}")
            return today

    def generate(self, conversation: str, context_type: str = "dm") -> str:
        """
        Extract actionable items from conversation text and normalize LLM output.
        """
        try:
            self.logger.info("=== Sending conversation to LLM for extraction ===")

            # Create a prompt for task extraction
            prompt = f"""
Extract actionable tasks from the following conversation. Format each task as:
* - [Person]: Task description

Conversation:
{conversation}

Action items:
"""
            output = self._generate_with_ollama(prompt, context_type=context_type)
            self.logger.debug(f"Raw LLM Output:\n{output}")

            # Normalize output
            lines = [line.strip() for line in output.splitlines() if line.strip()]

            # Regex to match possible task formats
            task_patterns = [
                r"^\*?\s*-\s*\[(.*?)\]:\s*(.+)",   # * - [User]: Task
                r"^\d+\.?\s*(.+)",                 # 1. Task or 1) Task
                r"^-\s*(.+)",                      # - Task
                r"^\*\s*(.+)",                     # * Task
            ]

            tasks = []

            for line in lines:
                matched = False
                for pattern in task_patterns:
                    match = re.match(pattern, line, flags=re.IGNORECASE)
                    if match:
                        if len(match.groups()) == 2:
                            user, task = match.groups()
                            formatted = f"* - [{user.strip()}]: {task.strip()}"
                        else:
                            task = match.group(1)
                            # Try to extract user from context or use "Unknown"
                            user_match = re.search(r"\[(.*?)\]", line)
                            user = user_match.group(1) if user_match else "Unknown"
                            formatted = f"* - [{user}]: {task.strip()}"
                        
                        tasks.append(formatted)
                        matched = True
                        break
                
                # Fallback: detect actionable verbs
                if not matched and re.search(r"\b(will|need|fix|prepare|test|complete|update|implement|deploy|create|write|review)\b", line, re.IGNORECASE):
                    user_match = re.search(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?):", line)
                    user_name = user_match.group(1) if user_match else "Unknown"
                    formatted = f"* - [{user_name}]: {line.strip()}"
                    tasks.append(formatted)

            if not tasks:
                return "No actionable tasks detected."

            final_output = "\n".join(tasks)
            self.logger.info("=== LLM Extraction Completed ===")
            self.logger.info(final_output)
            return final_output

        except Exception as e:
            self.logger.error(f"Error generating action items: {e}", exc_info=True)
            return "Error generating action items."

    def generate_with_better_user_extraction(self, conversation: str, user_map: Dict[str, str], context_type: str = "channel") -> List[Dict[str, str]]:
        """
        Extract actionable items from conversation text with better user name extraction.
        """
        try:
            self.logger.info("=== Sending conversation to LLM for extraction with better user detection ===")

            # Create a better prompt for user name extraction
            prompt = f"""
You are an advanced action extraction model optimized for Slack conversations.
Your task is to identify clear, actionable tasks or commitments made during the conversation.

Conversation:
{conversation}

Participants: {', '.join(set(user_map.values()))}

---

### Your Objective
Extract every actionable task or request — anything someone has agreed to do, was assigned to do, or volunteered to do.

---

### Formatting Rules (FOLLOW EXACTLY)
* Each task must be on its own line using this exact Markdown structure:
  * - [Responsible Person]: Task description

* Use the **exact participant name** from the Participants list.
* Do **not** create a task if the responsible person is not listed in Participants.
* Write complete, natural-sounding sentences for each task.
* No numbering, summaries, or text before or after the list.
* If no actionable tasks exist, output exactly:
  No actionable tasks detected.

---

### Pronoun and Responsibility Mapping
Use the following logic to correctly identify the responsible person:

1. **First-person statements**  
   - Phrases like "I will", "I'll", "I'm going to", "I can take this", or "I'll handle it".  
   - → Responsible = **the message author** (use their exact name from Participants).

2. **Direct address (second-person)**  
   - Phrases like "Can you", "Could you", "Please handle this", etc.  
   - If the message includes an explicit **@mention** or a clear **name reference**,  
     → Responsible = **the mentioned/named person** (use exact name from Participants).  
   - If no mention or name is specified, skip the task (don't assume ownership).

3. **Collective suggestions**  
   - Phrases like "We should", "Let's", or "We can".  
   - → Responsible = **the message author** (the person proposing the action).

4. **Open or vague requests**  
   - Phrases like "Can anyone...", "Who can..".  
   - → Do **not** create a task unless a specific person later volunteers or agrees.  
   - If someone later says "I can handle that" or "I'll do it",  
     → Create a task for that person instead.

---

### Output Examples

→ Correct outputs:
* - [Sanjay Srivastava]: Complete the deployment by tomorrow  
* - [Hari]: Test the integration today  
* - [Sanjay Srivastava]: Review Hari's test results by end of day

→ Incorrect outputs:
1) Hari will fix this  
@Sanjay handle this  
Tasks: None found  
Let's work on this (no owner)  

---

### Now extract the actionable tasks:
"""
            output = self._generate_with_ollama(prompt, context_type=context_type)
            self.logger.debug(f"Raw LLM Output with better user detection:\n{output}")

            # Parse the output
            tasks = []
            lines = [line.strip() for line in output.splitlines() if line.strip()]

            for line in lines:
                if line.startswith('* - [') and ']:' in line:
                    try:
                        user_part, task_part = line.split(']:', 1)
                        user = user_part.replace('* - [', '').strip()
                        task = task_part.strip()
                        
                        # Validate user name against known users
                        valid_user = user
                        for known_user in user_map.values():
                            if known_user.lower() in user.lower() or user.lower() in known_user.lower():
                                valid_user = known_user
                                break
                        
                        if task and valid_user:
                            tasks.append({
                                "action": task,
                                "responsible": valid_user,
                                "deadline": ""
                            })
                    except ValueError:
                        continue

            return tasks

        except Exception as e:
            self.logger.error(f"Error generating action items with better user extraction: {e}", exc_info=True)
            return []