# utils/jira_query_processor.py

import os
import logging
import re
import requests
import base64
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

def is_jira_query(query: str) -> bool:
    """Check if query appears to be Jira-related"""
    query_lower = query.lower()
    
    # Jira keywords
    jira_keywords = {
        'issues', 'issue', 'ticket', 'tickets', 'jira', 'project', 'projects',
        'bug', 'bugs', 'story', 'stories', 'epic', 'epics', 'task', 'tasks',
        'assignee', 'reporter', 'status', 'priority', 'comment', 'comments'
    }
    
    # Check for Jira keywords
    for keyword in jira_keywords:
        if keyword in query_lower:
            return True
    
    # Check for issue key patterns (e.g., ABC-123)
    if re.search(r'[A-Z]+-\d+', query):
        return True
    
    # Check for project key patterns
    if re.search(r'project\s+[A-Z]+\d*', query_lower):
        return True
    
    return False

def get_jira_auth_headers() -> Optional[Dict[str, str]]:
    """Get authentication headers for Jira API"""
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("ATLASSIAN_TOKEN")
    
    if not email or not token:
        logger.error("Missing ATLASSIAN_EMAIL or ATLASSIAN_TOKEN")
        return None
    
    # Create basic auth
    auth_string = f"{email}:{token}"
    auth_bytes = auth_string.encode('ascii')
    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
    
    return {
        'Authorization': f'Basic {auth_b64}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

def call_jira_api(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Make a call to Jira REST API"""
    host = os.getenv("ATLASSIAN_HOST")
    if not host:
        logger.error("Missing ATLASSIAN_HOST")
        return None
    
    headers = get_jira_auth_headers()
    if not headers:
        return None
    
    url = f"{host}/rest/api/3/{endpoint}"
    
    try:
        logger.debug(f"Calling Jira API: {url}")
        response = requests.get(url, headers=headers, params=params or {}, timeout=30)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Jira API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error calling Jira API: {e}")
        return None

def search_jira_issues(jql: str, max_results: int = 20) -> Optional[Dict[str, Any]]:
    """Search for issues using JQL"""
    params = {
        'jql': jql,
        'maxResults': max_results,
        'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
    }
    
    return call_jira_api('search', params)

def get_jira_issue(issue_key: str) -> Optional[Dict[str, Any]]:
    """Get specific issue details"""
    return call_jira_api(f'issue/{issue_key}')

def list_jira_projects() -> Optional[Dict[str, Any]]:
    """List available projects"""
    return call_jira_api('project')

def format_jira_issues(issues_data: Dict[str, Any]) -> str:
    """Format Jira issues for Slack display"""
    if not issues_data or 'issues' not in issues_data:
        return "🎫 *No issues found*\n\nNo issues match your criteria."
    
    issues = issues_data['issues']
    total = issues_data.get('total', len(issues))
    
    if not issues:
        return "🎫 *No issues found*\n\nNo issues are currently assigned to you."
    
    formatted = f"🎫 *Found {total} issue{'s' if total != 1 else ''}*"
    if total > len(issues):
        formatted += f" (showing first {len(issues)})"
    formatted += "\n\n"
    
    for i, issue in enumerate(issues[:10], 1):  # Show max 10 issues
        fields = issue.get('fields', {})
        key = issue.get('key', 'Unknown')
        summary = fields.get('summary', 'No summary')
        status = fields.get('status', {}).get('name', 'Unknown')
        priority = fields.get('priority', {}).get('name', 'None')
        
        formatted += f"**{i}. {key}** - {summary}\n"
        formatted += f"   📊 *Status:* {status}"
        
        if priority and priority != 'None':
            formatted += f" | ⚡ *Priority:* {priority}"
        
        # Add assignee if different from current user
        assignee = fields.get('assignee')
        if assignee and assignee.get('displayName'):
            formatted += f" | 👤 *Assignee:* {assignee['displayName']}"
        
        formatted += "\n"
        
        # Add Jira URL
        host = os.getenv("ATLASSIAN_HOST", "")
        if host:
            jira_url = f"{host}/browse/{key}"
            formatted += f"   🔗 <{jira_url}|View in Jira>\n"
        
        formatted += "\n"
    
    if total > 10:
        formatted += f"_... and {total - 10} more issues_\n\n"
    
    formatted += "💡 *Try asking:* 'show issue ABC-123' or 'my open issues'"
    
    return formatted

def format_jira_issue(issue_data: Dict[str, Any]) -> str:
    """Format single Jira issue for Slack display"""
    if not issue_data:
        return "❌ *Issue not found*\n\nThe issue may not exist or you may not have permission to view it."
    
    fields = issue_data.get('fields', {})
    key = issue_data.get('key', 'Unknown')
    summary = fields.get('summary', 'No summary')
    status = fields.get('status', {}).get('name', 'Unknown')
    priority = fields.get('priority', {}).get('name', 'None')
    issue_type = fields.get('issuetype', {}).get('name', 'Unknown')
    
    formatted = f"🎫 *{key}* - {summary}\n\n"
    
    # Status and basic info
    formatted += f"📊 *Status:* {status}\n"
    formatted += f"🏷️ *Type:* {issue_type}\n"
    
    if priority and priority != 'None':
        formatted += f"⚡ *Priority:* {priority}\n"
    
    # People
    assignee = fields.get('assignee')
    if assignee:
        formatted += f"👤 *Assignee:* {assignee.get('displayName', 'Unknown')}\n"
    else:
        formatted += f"👤 *Assignee:* Unassigned\n"
    
    reporter = fields.get('reporter')
    if reporter:
        formatted += f"📝 *Reporter:* {reporter.get('displayName', 'Unknown')}\n"
    
    # Dates
    created = fields.get('created')
    if created:
        try:
            created_date = datetime.fromisoformat(created.replace('Z', '+00:00'))
            formatted += f"📅 *Created:* {created_date.strftime('%Y-%m-%d %H:%M')}\n"
        except:
            formatted += f"📅 *Created:* {created}\n"
    
    # Description
    description = fields.get('description')
    if description and isinstance(description, dict):
        # Handle ADF (Atlassian Document Format)
        desc_text = extract_text_from_adf(description)
        if desc_text and len(desc_text) > 10:
            if len(desc_text) > 200:
                desc_text = desc_text[:200] + "..."
            formatted += f"\n📄 *Description:*\n{desc_text}\n"
    elif description and isinstance(description, str):
        if len(description) > 200:
            description = description[:200] + "..."
        formatted += f"\n📄 *Description:*\n{description}\n"
    
    # Jira URL
    host = os.getenv("ATLASSIAN_HOST", "")
    if host:
        jira_url = f"{host}/browse/{key}"
        formatted += f"\n🔗 <{jira_url}|View in Jira>\n"
    
    return formatted

def extract_text_from_adf(adf_content: Dict[str, Any]) -> str:
    """Extract plain text from Atlassian Document Format"""
    def extract_text_recursive(node):
        if isinstance(node, dict):
            if node.get('type') == 'text':
                return node.get('text', '')
            elif 'content' in node:
                return ''.join(extract_text_recursive(child) for child in node['content'])
        elif isinstance(node, list):
            return ''.join(extract_text_recursive(item) for item in node)
        return ''
    
    return extract_text_recursive(adf_content)

def format_jira_projects(projects_data: list) -> str:
    """Format Jira projects for Slack display"""
    if not projects_data:
        return "📂 *No projects found*\n\nYou may not have access to any projects."
    
    formatted = f"📂 *Available Projects ({len(projects_data)})*\n\n"
    
    for i, project in enumerate(projects_data[:15], 1):  # Show max 15 projects
        key = project.get('key', 'Unknown')
        name = project.get('name', 'No name')
        project_type = project.get('projectTypeKey', 'Unknown')
        
        formatted += f"{i}. **{key}** - {name}\n"
        if project_type and project_type != 'Unknown':
            formatted += f"   🏷️ *Type:* {project_type}\n"
        
        # Add project URL
        host = os.getenv("ATLASSIAN_HOST", "")
        if host:
            project_url = f"{host}/projects/{key}"
            formatted += f"   🔗 <{project_url}|View Project>\n"
        
        formatted += "\n"
    
    if len(projects_data) > 15:
        formatted += f"_... and {len(projects_data) - 15} more projects_\n\n"
    
    formatted += "💡 *Try asking:* 'issues in project ABC' or 'show project XYZ details'"
    
    return formatted

def process_jira_query_sync(query: str) -> Optional[str]:
    """
    Synchronous Jira query processing with actual API calls.
    Returns None if not a Jira query or if processing fails.
    """
    enable_jira = os.getenv("ENABLE_JIRA_FEATURES", "false").lower() == "true"
    
    if not enable_jira:
        return None
    
    # Quick check if this looks like a Jira query
    if not is_jira_query(query):
        return None
    
    try:
        logger.info(f"🎫 Processing Jira query: {query}")
        query_lower = query.lower()
        
        # List projects
        if any(phrase in query_lower for phrase in ["list projects", "show projects", "available projects", "what projects"]):
            logger.debug("Fetching Jira projects...")
            projects_data = list_jira_projects()
            if projects_data:
                return format_jira_projects(projects_data)
            else:
                return "❌ *Error fetching projects*\n\nCould not retrieve projects from Jira. Please check your connection and permissions."
        
        # Issues assigned to me
        elif any(phrase in query_lower for phrase in ["my issues", "issues assigned to me", "assigned to me"]):
            logger.debug("Searching for assigned issues...")
            jql = "assignee = currentUser() ORDER BY updated DESC"
            issues_data = search_jira_issues(jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching issues*\n\nCould not search for your assigned issues. Please check your Jira connection."
        
        # Issues reported by me
        elif any(phrase in query_lower for phrase in ["issues reported by me", "i reported", "my reported issues"]):
            logger.debug("Searching for reported issues...")
            jql = "reporter = currentUser() ORDER BY created DESC"
            issues_data = search_jira_issues(jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching issues*\n\nCould not search for issues you reported."
        
        # Specific issue lookup
        elif re.search(r'[A-Z]+-\d+', query):
            issue_match = re.search(r'([A-Z]+-\d+)', query)
            if issue_match:
                issue_key = issue_match.group(1)
                logger.debug(f"Fetching issue: {issue_key}")
                issue_data = get_jira_issue(issue_key)
                if issue_data:
                    return format_jira_issue(issue_data)
                else:
                    return f"❌ *Issue {issue_key} not found*\n\nThe issue may not exist or you may not have permission to view it."
        
        # Open issues
        elif any(phrase in query_lower for phrase in ["open issues", "active issues", "my open issues"]):
            logger.debug("Searching for open issues...")
            jql = "assignee = currentUser() AND status != Done ORDER BY updated DESC"
            issues_data = search_jira_issues(jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching open issues*\n\nCould not search for open issues."
        
        # Generic Jira query
        elif is_jira_query(query):
            return (
                "🎫 *Jira Integration Active*\n\n"
                "I can help you with Jira queries! Try asking:\n\n"
                "• 'list my projects'\n"
                "• 'show my issues'\n"
                "• 'issues assigned to me'\n"
                "• 'what is ABC-123?'\n"
                "• 'my open issues'\n"
                "• 'issues I reported'\n\n"
                "💡 *Tip:* I can also look up specific issues by their key (e.g., PROJ-123)"
            )
        
        return None
        
    except Exception as e:
        logger.error(f"Jira processing error: {e}")
        return f"❌ *Jira Error*\n\nEncountered an error while processing your request: {str(e)}"