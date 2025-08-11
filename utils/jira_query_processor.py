# utils/jira_query_processor.py - ENHANCED WITH OAUTH

import os
import logging
import re
import requests
import base64
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from utils.jira_oauth_handler import get_oauth_handler

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

def check_user_jira_auth(user_id: str, team_id: str, channel_id: str, thread_ts: str, original_query: str = None) -> Tuple[bool, Optional[str]]:
    """
    Check if user is authenticated with Jira
    Returns: (is_authenticated, auth_url_if_not)
    """
    oauth_handler = get_oauth_handler()
    
    if oauth_handler.is_user_authenticated(user_id):
        return True, None
    
    # Generate auth URL for user with original query
    auth_url, state = oauth_handler.generate_auth_url(user_id, team_id, channel_id, thread_ts, original_query)
    return False, auth_url

def get_jira_auth_headers_for_user(user_id: str) -> Optional[Dict[str, str]]:
    """Get authentication headers for Jira API using user's OAuth token"""
    oauth_handler = get_oauth_handler()
    
    credentials = oauth_handler.get_user_credentials(user_id)
    if not credentials:
        logger.error(f"No valid credentials for user {user_id}")
        return None
    
    # For OAuth, we use Bearer token instead of Basic auth
    return {
        'Authorization': f'Bearer {credentials["token"]}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

def call_jira_api_for_user(user_id: str, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Make a call to Jira REST API with user's credentials"""
    oauth_handler = get_oauth_handler()
    
    credentials = oauth_handler.get_user_credentials(user_id)
    if not credentials:
        logger.error(f"No valid credentials for user {user_id}")
        return None
    
    headers = get_jira_auth_headers_for_user(user_id)
    if not headers:
        return None
    
    # Use user's Jira host
    host = credentials['host']
    cloud_id = credentials['cloud_id']
    
    # For Atlassian Cloud, use cloud ID in URL
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/{endpoint}"
    
    try:
        logger.debug(f"Calling Jira API for user {user_id}: {url}")
        response = requests.get(url, headers=headers, params=params or {}, timeout=30)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            logger.error(f"Jira API authentication failed for user {user_id}")
            # Token might be invalid, clear it
            oauth_handler.revoke_user_tokens(user_id)
            return None
        elif response.status_code == 403:
            logger.error(f"Jira API access forbidden for user {user_id}")
            return None
        else:
            logger.error(f"Jira API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error calling Jira API: {e}")
        return None

def search_jira_issues_for_user(user_id: str, jql: str, max_results: int = 20) -> Optional[Dict[str, Any]]:
    """Search for issues using JQL with user's credentials"""
    params = {
        'jql': jql,
        'maxResults': max_results,
        'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
    }
    
    return call_jira_api_for_user(user_id, 'search', params)

def get_jira_issue_for_user(user_id: str, issue_key: str) -> Optional[Dict[str, Any]]:
    """Get specific issue details with user's credentials"""
    return call_jira_api_for_user(user_id, f'issue/{issue_key}')

def get_jira_issue_comments_for_user(user_id: str, issue_key: str) -> Optional[Dict[str, Any]]:
    """Get comments for a specific issue with user's credentials"""
    return call_jira_api_for_user(user_id, f'issue/{issue_key}/comment')

def list_jira_projects_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """List available projects with user's credentials"""
    return call_jira_api_for_user(user_id, 'project')

def format_auth_required_message(auth_url: str) -> str:
    """Format message asking user to authenticate"""
    return (
        "🔐 *Jira Authentication Required*\n\n"
        "To access your Jira data, you need to authenticate first.\n\n"
        f"👉 <{auth_url}|Click here to connect your Jira account>\n\n"
        "_This is a secure OAuth connection. Your credentials are never shared in Slack._\n"
        "_Authentication is required for each Jira query to ensure security._"
    )

def process_jira_query_with_auth(
    query: str, 
    user_id: str, 
    team_id: str, 
    channel_id: str, 
    thread_ts: str
) -> Optional[str]:
    """
    Process Jira query with OAuth authentication
    Returns response message or None if not a Jira query
    """
    enable_jira = os.getenv("ENABLE_JIRA_FEATURES", "false").lower() == "true"
    
    if not enable_jira:
        return None
    
    # Quick check if this looks like a Jira query
    if not is_jira_query(query):
        return None
    
def process_jira_query_with_auth(
    query: str, 
    user_id: str, 
    team_id: str, 
    channel_id: str, 
    thread_ts: str
) -> Optional[str]:
    """
    Process Jira query with OAuth authentication
    Returns response message or None if not a Jira query
    """
    enable_jira = os.getenv("ENABLE_JIRA_FEATURES", "false").lower() == "true"
    
    if not enable_jira:
        return None
    
    # Quick check if this looks like a Jira query
    if not is_jira_query(query):
        return None
    
    oauth_handler = get_oauth_handler()
    
    # Check if user has active credentials (from recent OAuth callback)
    credentials = oauth_handler.get_user_credentials(user_id)
    
    if not credentials:
        # No active credentials, need to authenticate
        auth_url, state = oauth_handler.generate_auth_url(user_id, team_id, channel_id, thread_ts, query)
        return format_auth_required_message(auth_url)
    
    try:
        logger.info(f"🎫 Processing Jira query for user {user_id}: {query}")
        query_lower = query.lower()
        
        # Helper function to make API calls with user's credentials
        def call_jira_api(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
            headers = {
                'Authorization': f'Bearer {credentials["token"]}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            
            cloud_id = credentials['cloud_id']
            url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/{endpoint}"
            
            try:
                response = requests.get(url, headers=headers, params=params or {}, timeout=30)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    logger.error(f"Jira API authentication failed for user {user_id}")
                    # Clear invalid credentials
                    oauth_handler.clear_user_credentials(user_id)
                    return None
                else:
                    logger.error(f"Jira API error: {response.status_code} - {response.text}")
                    return None
                    
            except Exception as e:
                logger.error(f"Error calling Jira API: {e}")
                return None
        
        # Extract issue key for comment queries
        issue_key_match = re.search(r'([A-Z]+-\d+)', query)
        
        # Comments on specific issue
        if issue_key_match and any(word in query_lower for word in ['comment', 'comments']):
            issue_key = issue_key_match.group(1)
            logger.debug(f"Fetching comments for issue: {issue_key}")
            comments_data = call_jira_api(f'issue/{issue_key}/comment')
            if comments_data is not None:
                result = format_jira_comments(comments_data, issue_key)
                # Clear credentials after successful query
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return f"❌ *Error fetching comments for {issue_key}*\n\nCould not retrieve comments. Please check if the issue exists and you have permission to view it."
        
        # List projects
        elif any(phrase in query_lower for phrase in ["list projects", "show projects", "available projects", "what projects", "list my projects"]):
            logger.debug("Fetching Jira projects...")
            projects_data = call_jira_api('project')
            if projects_data:
                result = format_jira_projects(projects_data)
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return "❌ *Error fetching projects*\n\nCould not retrieve projects from Jira."
        
        # Issues assigned to me
        elif any(phrase in query_lower for phrase in [
            "my issues", "issues assigned to me", "assigned to me", "show my issues", 
            "show my jira issues", "my jira issues", "issues i am assigned to",
            "what are the issues i am assigned to", "what are my issues"
        ]):
            logger.debug("Searching for assigned issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            if date_filter:
                jql = f"assignee = currentUser() AND {date_filter} ORDER BY updated DESC"
            else:
                jql = "assignee = currentUser() ORDER BY updated DESC"
            
            params = {
                'jql': jql,
                'maxResults': 20,
                'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
            }
            
            issues_data = call_jira_api('search', params)
            if issues_data is not None:
                result = format_jira_issues(issues_data)
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return "❌ *Error searching issues*\n\nCould not search for your assigned issues."
        
        # Issues reported by me
        elif any(phrase in query_lower for phrase in [
            "issues reported by me", "i reported", "my reported issues", "issues i reported",
            "show issues reported by me", "show the jira issues reported by me"
        ]):
            logger.debug("Searching for reported issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            if date_filter:
                jql = f"reporter = currentUser() AND {date_filter} ORDER BY created DESC"
            else:
                jql = "reporter = currentUser() ORDER BY created DESC"
            
            params = {
                'jql': jql,
                'maxResults': 20,
                'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
            }
            
            issues_data = call_jira_api('search', params)
            if issues_data is not None:
                result = format_jira_issues(issues_data)
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return "❌ *Error searching issues*\n\nCould not search for issues you reported."
        
        # Open issues
        elif any(phrase in query_lower for phrase in ["open issues", "active issues", "my open issues"]):
            logger.debug("Searching for open issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            base_jql = "assignee = currentUser() AND status not in (Done, Closed, Resolved)"
            if date_filter:
                jql = f"{base_jql} AND {date_filter} ORDER BY updated DESC"
            else:
                jql = f"{base_jql} ORDER BY updated DESC"
            
            params = {
                'jql': jql,
                'maxResults': 20,
                'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
            }
            
            issues_data = call_jira_api('search', params)
            if issues_data is not None:
                result = format_jira_issues(issues_data)
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return "❌ *Error searching open issues*\n\nCould not search for open issues."
        
        # Specific issue lookup
        elif issue_key_match and not any(word in query_lower for word in ['comment', 'comments']):
            issue_key = issue_key_match.group(1)
            logger.debug(f"Fetching issue: {issue_key}")
            issue_data = call_jira_api(f'issue/{issue_key}')
            if issue_data:
                result = format_jira_issue(issue_data)
                oauth_handler.clear_user_credentials(user_id)
                return result
            else:
                oauth_handler.clear_user_credentials(user_id)
                return f"❌ *Issue {issue_key} not found*\n\nThe issue may not exist or you may not have permission to view it."
        
        # If we get here, it's a Jira query we don't specifically handle
        oauth_handler.clear_user_credentials(user_id)
        return None
        
    except Exception as e:
        logger.error(f"Jira processing error: {e}")
        # Always clear credentials on error
        oauth_handler.clear_user_credentials(user_id)
        return f"❌ *Jira Error*\n\nEncountered an error while processing your request: {str(e)}"
    
    try:
        logger.info(f"🎫 Processing Jira query for user {user_id}: {query}")
        query_lower = query.lower()
        
        # Extract issue key for comment queries
        issue_key_match = re.search(r'([A-Z]+-\d+)', query)
        
        # Comments on specific issue
        if issue_key_match and any(word in query_lower for word in ['comment', 'comments']):
            issue_key = issue_key_match.group(1)
            logger.debug(f"Fetching comments for issue: {issue_key}")
            comments_data = get_jira_issue_comments_for_user(user_id, issue_key)
            if comments_data is not None:
                return format_jira_comments(comments_data, issue_key)
            else:
                return f"❌ *Error fetching comments for {issue_key}*\n\nCould not retrieve comments. Please check if the issue exists and you have permission to view it."
        
        # List projects
        elif any(phrase in query_lower for phrase in ["list projects", "show projects", "available projects", "what projects", "list my projects"]):
            logger.debug("Fetching Jira projects...")
            projects_data = list_jira_projects_for_user(user_id)
            if projects_data:
                return format_jira_projects(projects_data)
            else:
                return "❌ *Error fetching projects*\n\nCould not retrieve projects from Jira. Your authentication may have expired. Please try again."
        
        # Issues assigned to me
        elif any(phrase in query_lower for phrase in [
            "my issues", "issues assigned to me", "assigned to me", "show my issues", 
            "show my jira issues", "my jira issues", "issues i am assigned to",
            "what are the issues i am assigned to", "what are my issues"
        ]):
            logger.debug("Searching for assigned issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            if date_filter:
                jql = f"assignee = currentUser() AND {date_filter} ORDER BY updated DESC"
            else:
                jql = "assignee = currentUser() ORDER BY updated DESC"
                
            issues_data = search_jira_issues_for_user(user_id, jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching issues*\n\nCould not search for your assigned issues. Your authentication may have expired."
        
        # Issues reported by me
        elif any(phrase in query_lower for phrase in [
            "issues reported by me", "i reported", "my reported issues", "issues i reported",
            "show issues reported by me", "show the jira issues reported by me"
        ]):
            logger.debug("Searching for reported issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            if date_filter:
                jql = f"reporter = currentUser() AND {date_filter} ORDER BY created DESC"
            else:
                jql = "reporter = currentUser() ORDER BY created DESC"
                
            issues_data = search_jira_issues_for_user(user_id, jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching issues*\n\nCould not search for issues you reported."
        
        # Open issues
        elif any(phrase in query_lower for phrase in ["open issues", "active issues", "my open issues"]):
            logger.debug("Searching for open issues...")
            
            # Check for date filtering
            date_filter = parse_date_from_query(query)
            base_jql = "assignee = currentUser() AND status not in (Done, Closed, Resolved)"
            if date_filter:
                jql = f"{base_jql} AND {date_filter} ORDER BY updated DESC"
            else:
                jql = f"{base_jql} ORDER BY updated DESC"
                
            issues_data = search_jira_issues_for_user(user_id, jql)
            if issues_data is not None:
                return format_jira_issues(issues_data)
            else:
                return "❌ *Error searching open issues*\n\nCould not search for open issues."
        
        # Specific issue lookup
        elif issue_key_match and not any(word in query_lower for word in ['comment', 'comments']):
            issue_key = issue_key_match.group(1)
            logger.debug(f"Fetching issue: {issue_key}")
            issue_data = get_jira_issue_for_user(user_id, issue_key)
            if issue_data:
                return format_jira_issue(issue_data)
            else:
                return f"❌ *Issue {issue_key} not found*\n\nThe issue may not exist or you may not have permission to view it."
        
        # If we get here, it's a Jira query we don't specifically handle
        return None
        
    except Exception as e:
        logger.error(f"Jira processing error: {e}")
        return f"❌ *Jira Error*\n\nEncountered an error while processing your request: {str(e)}"

# Keep all the existing formatting functions unchanged
def parse_date_from_query(query: str) -> Optional[str]:
    """Parse date-related terms from query and return JQL date string"""
    query_lower = query.lower()
    
    # Common date patterns
    if any(term in query_lower for term in ['last week', 'past week']):
        date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        return f'created >= "{date}"'
    elif any(term in query_lower for term in ['last month', 'past month']):
        date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        return f'created >= "{date}"'
    elif any(term in query_lower for term in ['today']):
        date = datetime.now().strftime('%Y-%m-%d')
        return f'created >= "{date}"'
    elif any(term in query_lower for term in ['yesterday']):
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        return f'created >= "{date}" AND created < "{datetime.now().strftime("%Y-%m-%d")}"'
    elif any(term in query_lower for term in ['this week']):
        # Get start of this week (Monday)
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        date = start_of_week.strftime('%Y-%m-%d')
        return f'created >= "{date}"'
    
    # Check for specific year mentions
    year_match = re.search(r'\b(20\d{2})\b', query)
    if year_match:
        year = year_match.group(1)
        return f'created >= "{year}-01-01" AND created < "{int(year)+1}-01-01"'
    
    return None

# Include all the existing format functions here (format_jira_issues, format_jira_issue, etc.)
# These remain unchanged from the original file

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
    
    oauth_handler = get_oauth_handler()
    
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
        
        formatted += "\n\n"
    
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
    
    return formatted

def format_jira_comments(comments_data: Dict[str, Any], issue_key: str) -> str:
    """Format Jira issue comments for Slack display"""
    if not comments_data or 'comments' not in comments_data:
        return f"💬 *No comments found on {issue_key}*\n\nThis issue doesn't have any comments yet."
    
    comments = comments_data['comments']
    total = len(comments)
    
    if not comments:
        return f"💬 *No comments found on {issue_key}*\n\nThis issue doesn't have any comments yet."
    
    formatted = f"💬 *{total} comment{'s' if total != 1 else ''} on {issue_key}*\n\n"
    
    # Show up to 5 most recent comments
    recent_comments = comments[-5:] if len(comments) > 5 else comments
    
    for i, comment in enumerate(recent_comments, 1):
        author = comment.get('author', {}).get('displayName', 'Unknown')
        created = comment.get('created', '')
        body = comment.get('body', '')
        
        # Format creation date
        try:
            created_date = datetime.fromisoformat(created.replace('Z', '+00:00'))
            date_str = created_date.strftime('%Y-%m-%d %H:%M')
        except:
            date_str = created
        
        formatted += f"**Comment {i}** by *{author}* on {date_str}:\n"
        
        # Extract text from comment body (handle ADF format)
        if isinstance(body, dict):
            comment_text = extract_text_from_adf(body)
        elif isinstance(body, str):
            comment_text = body
        else:
            comment_text = str(body)
        
        # Truncate long comments
        if len(comment_text) > 300:
            comment_text = comment_text[:300] + "..."
        
        formatted += f"💭 {comment_text}\n\n"
    
    if len(comments) > 5:
        formatted += f"_... and {len(comments) - 5} earlier comment{'s' if len(comments) - 5 != 1 else ''}_\n\n"
    
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
        
        formatted += "\n"
    
    if len(projects_data) > 15:
        formatted += f"_... and {len(projects_data) - 15} more projects_\n\n"
    
    formatted += "💡 *Try asking:* 'issues in project ABC' or 'show project XYZ details'"
    
    return formatted

# Backward compatibility function
def process_jira_query_sync(query: str) -> Optional[str]:
    """
    Legacy function for backward compatibility
    This won't work properly without user context
    """
    logger.warning("Using legacy process_jira_query_sync without user context")
    return None

def process_jira_query_with_temporary_auth(
    query: str,
    credentials: Dict,
    user_id: str
) -> Optional[str]:
    """
    Process Jira query with temporary credentials (used after OAuth callback)
    """
    try:
        logger.info(f"🎫 Processing Jira query with temporary credentials for user {user_id}: {query}")
        query_lower = query.lower()
        
        # Use the temporary credentials for API calls
        def call_jira_with_temp_creds(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
            """Make a Jira API call with temporary credentials"""
            headers = {
                'Authorization': f'Bearer {credentials["token"]}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            
            cloud_id = credentials['cloud_id']
            url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/{endpoint}"
            
            try:
                response = requests.get(url, headers=headers, params=params or {}, timeout=30)
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Jira API error: {response.status_code}")
                    return None
                    
            except Exception as e:
                logger.error(f"Error calling Jira API: {e}")
                return None
        
        # Extract issue key for comment queries
        issue_key_match = re.search(r'([A-Z]+-\d+)', query)
        
        # Process different query types
        if issue_key_match and any(word in query_lower for word in ['comment', 'comments']):
            issue_key = issue_key_match.group(1)
            comments_data = call_jira_with_temp_creds(f'issue/{issue_key}/comment')
            if comments_data is not None:
                return format_jira_comments(comments_data, issue_key)
        
        elif any(phrase in query_lower for phrase in ["list projects", "show projects", "available projects"]):
            projects_data = call_jira_with_temp_creds('project')
            if projects_data:
                return format_jira_projects(projects_data)
        
        elif any(phrase in query_lower for phrase in ["my issues", "issues assigned to me", "assigned to me"]):
            jql = "assignee = currentUser() ORDER BY updated DESC"
            params = {
                'jql': jql,
                'maxResults': 20,
                'fields': 'key,summary,status,priority,assignee,reporter,created,updated,description'
            }
            issues_data = call_jira_with_temp_creds('search', params)
            if issues_data is not None:
                return format_jira_issues(issues_data)
        
        elif issue_key_match:
            issue_key = issue_key_match.group(1)
            issue_data = call_jira_with_temp_creds(f'issue/{issue_key}')
            if issue_data:
                return format_jira_issue(issue_data)
        
        return "❌ Could not process your Jira query. Please try again."
        
    except Exception as e:
        logger.error(f"Error processing Jira query: {e}")
        return f"❌ *Jira Error*\n\n{str(e)}"