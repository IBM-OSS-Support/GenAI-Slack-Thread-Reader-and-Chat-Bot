import json
import logging
import re
import os
from dotenv import load_dotenv
from typing import Literal
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
        self.model_name = model_name or os.getenv("OLLAMA_MODEL", "gemma:2b")
        self.model = self.model_name
        self.ollama = ollama
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

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
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model_name = model_name or os.getenv("OLLAMA_MODEL", "gemma:2b")
        self.model = self.model_name
        try:
            response = requests.get(f"{self.ollama_url}/api/tags")
            if response.status_code == 200:
                self.logger.info("Successfully connected to Ollama")
            else:
                raise ConnectionError(f"Failed to connect to Ollama: {response.status_code}")
        except Exception as e:
            raise ConnectionError(f"Could not connect to Ollama at {self.ollama_url}: {str(e)}")

    def _prepare_conversation(self, messages, context_type="thread"):
        """
        Convert Slack messages into structured conversation text.
        """
        conversation = []
        try:
            bot_user_id = self.slack_app.client.auth_test()["user_id"]
        except Exception:
            bot_user_id = None

        skip_phrases = [
            "extract from", "please extract action items", "extract action items",
            "show my tasks", "claimed by", "extracting tasks", "no action items found",
            "no messages found", "error extracting"
        ]

        for msg in messages:
            if msg.get("bot_id") or msg.get("user") == bot_user_id:
                continue
            text = msg.get("text", "").strip()
            if not text:
                continue
            if any(text.lower().startswith(p) for p in skip_phrases):
                continue

            user_id = msg.get("user", "Unknown")
            try:
                info = self.slack_app.client.users_info(user=user_id)
                display_name = (
                    info["user"]["profile"].get("display_name")
                    or info["user"]["profile"].get("real_name")
                    or info["user"]["profile"].get("name")
                    or user_id
                )
            except Exception:
                display_name = user_id

            text = re.sub(r"<@([A-Z0-9]+)>", "", text)
            text = re.sub(r"<https?://[^>]+>", "", text)
            text = text.strip()
            if text:
                conversation.append(f"{display_name}: {text}")

        prepared_text = "\n".join(conversation)
        self.logger.info(f"=== Prepared Conversation ({context_type}) ===")
        self.logger.info(prepared_text if prepared_text else "(EMPTY CONVERSATION)")
        return prepared_text

    def _generate_with_ollama(self, prompt: str, context_type=None) -> str:
        """Generate response using Ollama — safely handle streaming JSON lines."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,  # can stay false; some Ollama builds still return multiple lines
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

            raw_text = response.text.strip()
            result_text = ""

            # ✅ Safely parse multiple JSON lines if present
            for line in raw_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "response" in data:
                        result_text += data["response"]
                except json.JSONDecodeError:
                    continue  # skip malformed or extra lines

            return result_text.strip()

        except requests.exceptions.Timeout:
            self.logger.error("Ollama API request timed out")
            return ""
        except Exception as e:
            self.logger.error(f"Ollama API request failed: {str(e)}", exc_info=True)
            return ""


    def _calculate_weekend_date(self, today):
        today_date = datetime.strptime(today, '%Y-%m-%d')
        days_until_saturday = (5 - today_date.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        weekend_date = today_date + timedelta(days=days_until_saturday)
        return weekend_date.strftime('%Y-%m-%d')


    def generate(self, conversation: str, context_type: str = "dm") -> str:
        """
        Extract actionable items from conversation text and normalize LLM output.
        """
        try:
            self.logger.info("=== Sending conversation to LLM for extraction ===")

            # Ask Ollama or any LLM for task extraction
            output = self._generate_with_ollama(conversation, context_type=context_type)

            self.logger.debug(f"Raw LLM Output:\n{output}")

            # Normalize output
            lines = [line.strip() for line in output.splitlines() if line.strip()]

            # Regex to match possible task formats
            task_patterns = [
                r"^\*?\s*-\s*\[(.*?)\]:\s*(.+)",   # * - [User]: Task
                r"^\d+\)\s*(.+)",                  # 1) Task
                r"^-+\s*(.+)",                     # - Task
                r"^(.*?)\s*[:-]\s*(will|need|should|to)\s+(.+)",  # Hari will fix issue
            ]

            tasks = []

            for line in lines:
                for pattern in task_patterns:
                    match = re.match(pattern, line, flags=re.IGNORECASE)
                    if match:
                        if len(match.groups()) == 2:
                            user, task = match.groups()
                        elif len(match.groups()) == 3:
                            user, _, task = match.groups()
                        else:
                            user, task = "Unknown", match.group(1)
                        formatted = f"* - [{user.strip()}]: {task.strip().capitalize()}"
                        tasks.append(formatted)
                        break

            if not tasks:
                # fallback: detect actionable verbs manually
                for line in lines:
                    if re.search(r"\b(will|need|fix|prepare|test|complete|update|implement|deploy)\b", line, re.IGNORECASE):
                        user = re.search(r"^[A-Z][a-z]+", line)
                        user_name = user.group(0) if user else "Unknown"
                        formatted = f"* - [{user_name}]: {line.strip().capitalize()}"
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


    def generate_with_better_user_extraction(self, conversation: str, user_map: dict, context_type: str = "channel") -> list:
        """
        Extract actionable items from conversation text with better user name extraction.
        """
        try:
            self.logger.info("=== Sending conversation to LLM for extraction with better user detection ===")

            # Create a better prompt for user name extraction
            prompt = f"""
    Analyze the following Slack conversation and extract actionable tasks with proper user assignment.

    Conversation:
    {conversation}

    Available users in this conversation: {', '.join(set(user_map.values()))}

    Instructions:
    1. Identify all action items or tasks mentioned in the conversation
    2. For each task, identify the responsible person (who needs to do it)
    3. Use the actual user names from the conversation context
    4. If a task is assigned to someone, use their exact name from the user list
    5. Return tasks in this exact format:
    * - [Responsible Person]: Task description

    Examples:
    * - [Sanjay Srivastava]: Complete the deployment by tomorrow
    * - [Hari]: Test the integration today

    Tasks:
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
                        
                        if task:
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