# utils/jira_integration.py

import re
import logging
from typing import Dict, Optional, Tuple, List
from enum import Enum

logger = logging.getLogger(__name__)

class JiraIntentType(Enum):
    """Enumeration of Jira intent types"""
    CREATE_ISSUE = "create_issue"
    FIND_ISSUES = "find_issues"
    MY_ISSUES = "my_issues"
    UPDATE_ISSUE = "update_issue"
    ADD_COMMENT = "add_comment"
    ASSIGN_ISSUE = "assign_issue"
    ISSUE_INFO = "issue_info"
    UNKNOWN = "unknown"

class JiraQueryDetector:
    """Detects if a query is Jira-related and extracts intent"""
    
    # Jira-specific keywords that strongly indicate a Jira query
    JIRA_KEYWORDS = {
        'jira', 'ticket', 'issue', 'bug', 'story', 'epic', 'task', 'subtask',
        'sprint', 'backlog', 'board', 'kanban', 'scrum', 'agile',
        'assignee', 'reporter', 'priority', 'severity', 'blocker', 'critical',
        'jql', 'workflow', 'transition', 'resolve', 'reopen', 'close'
    }
    
    # Action keywords that indicate what the user wants to do
    ACTION_KEYWORDS = {
        'create': ['create', 'new', 'open', 'raise', 'file', 'submit', 'add'],
        'find': ['find', 'search', 'show', 'list', 'get', 'display', 'fetch', 'retrieve'],
        'update': ['update', 'edit', 'change', 'modify', 'set'],
        'assign': ['assign', 'reassign', 'give', 'transfer'],
        'comment': ['comment', 'add comment', 'note', 'reply'],
        'my': ['my', 'mine', 'assigned to me', 'i own', 'i am working on']
    }
    
    @classmethod
    def is_jira_query(cls, text: str) -> bool:
        """
        Determines if the query is Jira-related
        
        Args:
            text: The user's query text
            
        Returns:
            Boolean indicating if this is a Jira query
        """
        text_lower = text.lower()
        
        # Check for explicit Jira keywords
        for keyword in cls.JIRA_KEYWORDS:
            if keyword in text_lower:
                return True
        
        # Check for issue key patterns (e.g., ABC-123)
        if re.search(r'\b[A-Z]+-\d+\b', text):
            return True
        
        # Check for common Jira phrases
        jira_phrases = [
            'assigned to me', 'my tasks', 'my tickets',
            'create a bug', 'file a bug', 'raise a ticket',
            'what issues', 'which tickets', 'show me issues'
        ]
        
        for phrase in jira_phrases:
            if phrase in text_lower:
                return True
        
        return False
    
    @classmethod
    def extract_intent(cls, text: str) -> Dict:
        """
        Extracts the intent and entities from a Jira query
        
        Args:
            text: The user's query text
            
        Returns:
            Dictionary containing intent type and extracted entities
        """
        text_lower = text.lower()
        intent = {
            'type': JiraIntentType.UNKNOWN,
            'entities': {},
            'confidence': 0.0
        }
        
        # Extract issue keys (e.g., ABC-123)
        issue_keys = re.findall(r'\b([A-Z]+-\d+)\b', text)
        if issue_keys:
            intent['entities']['issue_keys'] = issue_keys
        
        # Detect intent type
        if any(word in text_lower for word in cls.ACTION_KEYWORDS['create']):
            intent['type'] = JiraIntentType.CREATE_ISSUE
            intent['confidence'] = 0.9
            
            # Extract potential issue type
            if 'bug' in text_lower:
                intent['entities']['issue_type'] = 'Bug'
            elif 'story' in text_lower:
                intent['entities']['issue_type'] = 'Story'
            elif 'task' in text_lower:
                intent['entities']['issue_type'] = 'Task'
            elif 'epic' in text_lower:
                intent['entities']['issue_type'] = 'Epic'
        
        elif any(word in text_lower for word in cls.ACTION_KEYWORDS['find']):
            intent['type'] = JiraIntentType.FIND_ISSUES
            intent['confidence'] = 0.85
            
            # Extract search criteria
            if 'my' in text_lower or 'assigned to me' in text_lower:
                intent['type'] = JiraIntentType.MY_ISSUES
                intent['entities']['assignee'] = 'currentUser()'
        
        elif any(word in text_lower for word in cls.ACTION_KEYWORDS['my']):
            intent['type'] = JiraIntentType.MY_ISSUES
            intent['confidence'] = 0.9
            intent['entities']['assignee'] = 'currentUser()'
        
        elif any(word in text_lower for word in cls.ACTION_KEYWORDS['update']):
            intent['type'] = JiraIntentType.UPDATE_ISSUE
            intent['confidence'] = 0.8
        
        elif any(word in text_lower for word in cls.ACTION_KEYWORDS['assign']):
            intent['type'] = JiraIntentType.ASSIGN_ISSUE
            intent['confidence'] = 0.85
        
        elif 'comment' in text_lower:
            intent['type'] = JiraIntentType.ADD_COMMENT
            intent['confidence'] = 0.85
        
        elif issue_keys:
            # If we have issue keys but no clear action, assume they want info
            intent['type'] = JiraIntentType.ISSUE_INFO
            intent['confidence'] = 0.7
        
        # Extract additional entities
        intent['entities']['original_query'] = text
        
        return intent

class JiraCommandFormatter:
    """Formats Jira Plus commands based on intent"""
    
    @staticmethod
    def format_command(intent: Dict) -> Tuple[str, str]:
        """
        Formats the appropriate Jira Plus command based on intent
        
        Args:
            intent: The extracted intent dictionary
            
        Returns:
            Tuple of (command, explanation)
        """
        intent_type = intent['type']
        entities = intent.get('entities', {})
        
        if intent_type == JiraIntentType.CREATE_ISSUE:
            issue_type = entities.get('issue_type', 'Task')
            return (
                "/jira-plus create",
                f"This will open a form to create a new {issue_type}"
            )
        
        elif intent_type == JiraIntentType.MY_ISSUES:
            return (
                "/jira-plus issues assigned to me",
                "This will show all issues currently assigned to you"
            )
        
        elif intent_type == JiraIntentType.FIND_ISSUES:
            if 'issue_keys' in entities:
                key = entities['issue_keys'][0]
                return (
                    f"/jira-plus issue {key}",
                    f"This will show details for issue {key}"
                )
            else:
                return (
                    "/jira-plus find",
                    "This will open the issue search dialog"
                )
        
        elif intent_type == JiraIntentType.UPDATE_ISSUE:
            if 'issue_keys' in entities:
                key = entities['issue_keys'][0]
                return (
                    f"/jira-plus update {key}",
                    f"This will allow you to update issue {key}"
                )
            else:
                return (
                    "/jira-plus update",
                    "This will let you select an issue to update"
                )
        
        elif intent_type == JiraIntentType.ASSIGN_ISSUE:
            if 'issue_keys' in entities:
                key = entities['issue_keys'][0]
                return (
                    f"/jira-plus assign {key}",
                    f"This will let you reassign issue {key}"
                )
            else:
                return (
                    "/jira-plus assign",
                    "This will let you select an issue to reassign"
                )
        
        elif intent_type == JiraIntentType.ADD_COMMENT:
            if 'issue_keys' in entities:
                key = entities['issue_keys'][0]
                return (
                    f"/jira-plus comment {key}",
                    f"This will let you add a comment to issue {key}"
                )
            else:
                return (
                    "/jira-plus comment",
                    "This will let you select an issue to comment on"
                )
        
        elif intent_type == JiraIntentType.ISSUE_INFO:
            if 'issue_keys' in entities:
                key = entities['issue_keys'][0]
                return (
                    f"/jira-plus issue {key}",
                    f"This will show detailed information about issue {key}"
                )
            else:
                return (
                    "/jira-plus help",
                    "This will show all available Jira Plus commands"
                )
        
        else:
            return (
                "/jira-plus help",
                "This will show all available Jira Plus commands"
            )

class JiraResponseBuilder:
    """Builds formatted responses for Jira queries"""
    
    @staticmethod
    def build_handoff_response(intent: Dict, command: str, explanation: str) -> Dict:
        """
        Builds a formatted Slack message with Jira handoff
        
        Args:
            intent: The extracted intent
            command: The formatted Jira Plus command
            explanation: Explanation of what the command does
            
        Returns:
            Dictionary containing Slack blocks
        """
        original_query = intent['entities'].get('original_query', 'your request')
        intent_type = intent['type']
        confidence = intent.get('confidence', 0)
        
        # Build the response blocks
        blocks = []
        
        # Header section
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🎫 *Jira Request Detected*\n\nI understand you want to: _{original_query}_"
            }
        })
        
        # Add confidence indicator if low
        if confidence < 0.7:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "⚠️ _I'm not entirely sure about this interpretation. Please verify the command below._"
                }]
            })
        
        # Command section
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Quick action:* Copy and run this command:\n```{command}```\n_{explanation}_"
            }
        })
        
        # Add helpful tips based on intent
        # FIX: Changed from cls._get_contextual_tips to JiraResponseBuilder._get_contextual_tips
        tips = JiraResponseBuilder._get_contextual_tips(intent_type)
        if tips:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"💡 *Tips:*\n{tips}"
                }
            })
        
        # Add action buttons
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📋 Copy Command"
                    },
                    "action_id": "copy_jira_command",
                    "value": command
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "ℹ️ Jira Plus Help"
                    },
                    "action_id": "jira_plus_help",
                    "value": "/jira-plus help"
                }
            ]
        })
        
        # Footer with additional info
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "🔧 Jira Plus handles all authentication automatically | Need different help? Just ask!"
            }]
        })
        
        return {"blocks": blocks}
    
    @staticmethod
    def _get_contextual_tips(intent_type: JiraIntentType) -> Optional[str]:
        """
        Returns contextual tips based on the intent type
        
        Args:
            intent_type: The type of Jira intent
            
        Returns:
            String containing helpful tips or None
        """
        tips_map = {
            JiraIntentType.CREATE_ISSUE: (
                "• You can also create issues directly from messages using the message shortcut\n"
                "• Use workflow builder to automate issue creation"
            ),
            JiraIntentType.MY_ISSUES: (
                "• You can filter by status, project, or priority\n"
                "• Click on any issue to see full details"
            ),
            JiraIntentType.FIND_ISSUES: (
                "• Use JQL for advanced searches\n"
                "• Save frequent searches as filters in Jira"
            ),
            JiraIntentType.UPDATE_ISSUE: (
                "• You can bulk update multiple issues\n"
                "• Changes are synced instantly with Jira"
            ),
            JiraIntentType.ASSIGN_ISSUE: (
                "• Type @ to search for users\n"
                "• You can assign to yourself quickly"
            ),
            JiraIntentType.ADD_COMMENT: (
                "• Comments support markdown formatting\n"
                "• @mention users to notify them"
            )
        }
        
        return tips_map.get(intent_type)
    
    @staticmethod
    def build_error_response(error_type: str = "generic") -> Dict:
        """
        Builds an error response for Jira queries
        
        Args:
            error_type: Type of error encountered
            
        Returns:
            Dictionary containing Slack blocks
        """
        blocks = [{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "❌ *Unable to process Jira request*\n\n"
                    "Please make sure:\n"
                    "• Jira Plus is installed in this workspace (`/apps` to check)\n"
                    "• You have access to Jira\n"
                    "• Your query includes enough context\n\n"
                    "Try `/jira-plus help` for available commands."
                )
            }
        }]
        
        return {"blocks": blocks}

def process_jira_query(query: str) -> Optional[Dict]:
    """
    Main entry point for processing potential Jira queries
    
    Args:
        query: The user's query text
        
    Returns:
        Formatted Slack response dict or None if not a Jira query
    """
    try:
        # Check if this is a Jira query
        if not JiraQueryDetector.is_jira_query(query):
            return None
        
        # Extract intent
        intent = JiraQueryDetector.extract_intent(query)
        logger.info(f"Detected Jira intent: {intent}")
        
        # Format command
        command, explanation = JiraCommandFormatter.format_command(intent)
        
        # Build response
        response = JiraResponseBuilder.build_handoff_response(
            intent, command, explanation
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error processing Jira query: {e}")
        return JiraResponseBuilder.build_error_response()

def format_jira_help() -> Dict:
    """
    Returns a formatted help message for Jira Plus integration
    
    Returns:
        Dictionary containing Slack blocks
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🎫 Jira Plus Integration Help"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*How it works:*\n"
                    "When you ask me about Jira tasks, I'll help you use Jira Plus commands.\n\n"
                    "*Example queries you can ask:*\n"
                    "• _Show my Jira issues_\n"
                    "• _Create a bug for the login problem_\n"
                    "• _Find ABC-123_\n"
                    "• _Update ticket XYZ-456_\n"
                    "• _Add comment to ABC-789_\n"
                    "• _Assign ABC-111 to someone_"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Common Jira Plus commands:*\n"
                    "• `/jira-plus create` - Create new issue\n"
                    "• `/jira-plus find` - Search for issues\n"
                    "• `/jira-plus issues assigned to me` - View your issues\n"
                    "• `/jira-plus issue <KEY>` - View specific issue\n"
                    "• `/jira-plus help` - See all commands"
                )
            }
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "💡 Jira Plus handles authentication automatically once configured"
            }]
        }
    ]
    
    return {"blocks": blocks}