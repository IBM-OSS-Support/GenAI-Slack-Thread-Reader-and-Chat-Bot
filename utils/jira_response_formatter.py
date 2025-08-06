# utils/jira_response_formatter.py

import re
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class JiraResponseFormatter:
    """
    Formats Jira MCP responses into Slack-friendly messages.
    Handles various Jira data structures and presents them clearly.
    """
    
    def __init__(self):
        self.max_description_length = 200
        self.max_issues_per_response = 10
    
    def format_response(self, tool_name: str, response_data: Dict[str, Any], query: str = "") -> str:
        """Format Jira response based on tool type"""
        try:
            if not response_data or 'content' not in response_data:
                return "❌ No data received from Jira"
            
            content = response_data['content']
            if isinstance(content, list) and len(content) > 0:
                content = content[0]
            
            if isinstance(content, dict) and 'text' in content:
                text_content = content['text']
            else:
                text_content = str(content)
            
            # Route to appropriate formatter
            if tool_name == 'search_issue':
                return self._format_search_results(text_content, query)
            elif tool_name == 'get_issue':
                return self._format_issue_details(text_content, query)
            elif tool_name == 'get_comments':
                return self._format_comments(text_content)
            elif tool_name == 'list_projects':
                return self._format_projects_list(text_content)
            elif tool_name == 'get_project':
                return self._format_project_details(text_content)
            elif tool_name == 'list_issue_types':
                return self._format_issue_types(text_content)
            else:
                return self._format_generic_response(text_content, tool_name)
                
        except Exception as e:
            logger.error(f"Error formatting Jira response: {e}")
            return f"❌ Error formatting response: {str(e)}"
    
    def _format_search_results(self, text: str, query: str = "") -> str:
        """Format issue search results"""
        try:
            issues = self._parse_issues_from_text(text)
            
            if not issues:
                return "🔍 *No issues found*\n\nTry adjusting your search criteria or check if you have access to the project."
            
            # Limit results
            total_count = len(issues)
            if total_count > self.max_issues_per_response:
                issues = issues[:self.max_issues_per_response]
            
            formatted = f"🎫 *Found {total_count} issue{'s' if total_count != 1 else ''}*"
            if total_count > self.max_issues_per_response:
                formatted += f" (showing first {self.max_issues_per_response})"
            formatted += "\n\n"
            
            for i, issue in enumerate(issues, 1):
                formatted += self._format_issue_summary(issue, i)
                
            if total_count > self.max_issues_per_response:
                formatted += f"\n📋 *{total_count - self.max_issues_per_response} more issues available.*\n"
                formatted += "Try refining your search for more specific results.\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing search results: {e}")
            return f"📋 *Jira Response:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_issue_details(self, text: str, query: str = "") -> str:
        """Format detailed issue information"""
        try:
            # Check if this includes transitions (for status change queries)
            if "Available Transitions:" in text or "Available transitions:" in text:
                return self._format_issue_with_transitions(text)
            
            issue = self._parse_single_issue(text)
            if not issue:
                return f"📋 *Issue Details:*\n{text}"
            
            formatted = f"🎫 *{issue.get('Key', 'Unknown')}* - {issue.get('Summary', 'No summary')}\n\n"
            
            # Status and type
            if issue.get('Status'):
                formatted += f"📊 *Status:* {issue['Status']}\n"
            if issue.get('Type'):
                formatted += f"🏷️ *Type:* {issue['Type']}\n"
            if issue.get('Priority'):
                formatted += f"⚡ *Priority:* {issue['Priority']}\n"
            
            # People
            if issue.get('Assignee') and issue['Assignee'] != 'Unassigned':
                formatted += f"👤 *Assignee:* {issue['Assignee']}\n"
            if issue.get('Reporter'):
                formatted += f"📝 *Reporter:* {issue['Reporter']}\n"
            
            # Dates
            if issue.get('Created'):
                created_date = self._format_date(issue['Created'])
                formatted += f"📅 *Created:* {created_date}\n"
            if issue.get('Updated'):
                updated_date = self._format_date(issue['Updated'])
                formatted += f"🔄 *Updated:* {updated_date}\n"
            
            # Project info
            if issue.get('Project'):
                formatted += f"📂 *Project:* {issue['Project']}\n"
            
            # Description
            if issue.get('Description'):
                desc = issue['Description']
                if len(desc) > self.max_description_length:
                    desc = desc[:self.max_description_length] + "..."
                formatted += f"\n📄 *Description:*\n{desc}\n"
            
            # URL if available
            if issue.get('URL'):
                formatted += f"\n🔗 <{issue['URL']}|View in Jira>\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing issue details: {e}")
            return f"📋 *Issue Details:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_issue_with_transitions(self, text: str) -> str:
        """Format issue details with available transitions"""
        try:
            # Extract issue key
            issue_key_match = re.search(r'Issue:\s*([A-Z]+-\d+)', text)
            issue_key = issue_key_match.group(1) if issue_key_match else 'Unknown'
            
            formatted = f"🔄 *Available Transitions for {issue_key}*\n\n"
            
            # Extract current status
            status_match = re.search(r'Status:\s*([^\n]+)', text)
            if status_match:
                current_status = status_match.group(1).strip()
                formatted += f"📊 *Current Status:* {current_status}\n\n"
            
            # Extract transitions
            transitions_match = re.search(r'Available [Tt]ransitions:\s*(.*?)(?:\n\n|\Z)', text, re.DOTALL)
            if transitions_match:
                transitions_text = transitions_match.group(1)
                transitions = self._parse_transitions(transitions_text)
                
                if transitions:
                    formatted += "🎯 *Available Actions:*\n"
                    for i, transition in enumerate(transitions, 1):
                        formatted += f"{i}. Move to **{transition['name']}**\n"
                    
                    formatted += f"\n💡 *To transition:* Say \"Move {issue_key} to [status name]\"\n"
                else:
                    formatted += "No transitions available for this issue.\n"
            else:
                formatted += "Could not extract transition information.\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing transitions: {e}")
            return f"🔄 *Transitions:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _parse_transitions(self, transitions_text: str) -> List[Dict[str, str]]:
        """Parse transition information from text"""
        transitions = []
        
        # Look for patterns like "- Done (ID: 123)" or "Done (id: 123)"
        pattern = r'-\s*([^(]+)\s*\([iI][dD]:\s*(\d+)\)'
        matches = re.findall(pattern, transitions_text)
        
        for match in matches:
            transitions.append({
                'name': match[0].strip(),
                'id': match[1]
            })
        
        return transitions
    
    def _format_comments(self, text: str) -> str:
        """Format issue comments"""
        try:
            # Parse comments from text
            comments = self._parse_comments_from_text(text)
            
            if not comments:
                return "💬 *No comments found*\n\nThis issue doesn't have any comments yet."
            
            formatted = f"💬 *Comments ({len(comments)})*\n\n"
            
            for i, comment in enumerate(comments[:5]):  # Show max 5 comments
                formatted += f"**Comment {i+1}:**\n"
                
                if comment.get('author'):
                    formatted += f"👤 *Author:* {comment['author']}\n"
                if comment.get('created'):
                    created_date = self._format_date(comment['created'])
                    formatted += f"📅 *Posted:* {created_date}\n"
                
                if comment.get('body'):
                    body = comment['body']
                    if len(body) > 300:
                        body = body[:300] + "..."
                    formatted += f"💭 {body}\n"
                
                formatted += "\n"
            
            if len(comments) > 5:
                formatted += f"_... and {len(comments) - 5} more comments_\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing comments: {e}")
            return f"💬 *Comments:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_projects_list(self, text: str) -> str:
        """Format projects list"""
        try:
            projects = self._parse_projects_from_text(text)
            
            if not projects:
                return "📂 *No projects found*\n\nYou may not have access to any projects or they may not be loaded."
            
            formatted = f"📂 *Available Projects ({len(projects)})*\n\n"
            
            for i, project in enumerate(projects[:15]):  # Show max 15 projects
                formatted += f"{i+1}. **{project.get('key', 'Unknown')}** - {project.get('name', 'No name')}\n"
                if project.get('type'):
                    formatted += f"   🏷️ Type: {project['type']}\n"
                formatted += "\n"
            
            if len(projects) > 15:
                formatted += f"_... and {len(projects) - 15} more projects_\n\n"
            
            formatted += "💡 *Usage:* Ask about specific projects using their key (e.g., 'show me PROJ issues')\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing projects: {e}")
            return f"📂 *Projects:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_project_details(self, text: str) -> str:
        """Format single project details"""
        try:
            project = self._parse_single_project(text)
            
            if not project:
                return f"📂 *Project Details:*\n{text}"
            
            formatted = f"📂 *{project.get('key', 'Unknown')}* - {project.get('name', 'No name')}\n\n"
            
            if project.get('description'):
                desc = project['description']
                if len(desc) > self.max_description_length:
                    desc = desc[:self.max_description_length] + "..."
                formatted += f"📄 *Description:* {desc}\n\n"
            
            if project.get('type'):
                formatted += f"🏷️ *Type:* {project['type']}\n"
            if project.get('lead'):
                formatted += f"👤 *Lead:* {project['lead']}\n"
            if project.get('url'):
                formatted += f"🔗 <{project['url']}|View in Jira>\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing project details: {e}")
            return f"📂 *Project Details:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_issue_types(self, text: str) -> str:
        """Format issue types list"""
        try:
            issue_types = self._parse_issue_types_from_text(text)
            
            if not issue_types:
                return "🏷️ *No issue types found*\n\nCould not retrieve issue types for this project."
            
            # Extract project from text if available
            project_match = re.search(r'project\s+([A-Z]+\d*)', text, re.IGNORECASE)
            project_name = project_match.group(1) if project_match else "this project"
            
            formatted = f"🏷️ *Issue Types for {project_name}*\n\n"
            
            for i, issue_type in enumerate(issue_types, 1):
                formatted += f"{i}. **{issue_type}**\n"
            
            formatted += f"\n💡 *Usage:* Use these types when creating issues in {project_name}\n"
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error parsing issue types: {e}")
            return f"🏷️ *Issue Types:*\n{text[:1000]}{'...' if len(text) > 1000 else ''}"
    
    def _format_generic_response(self, text: str, tool_name: str) -> str:
        """Format generic Jira response"""
        # Clean up the text
        cleaned_text = text.strip()
        
        # Add appropriate emoji based on tool
        emoji_map = {
            'search_issue': '🔍',
            'get_issue': '🎫',
            'get_comments': '💬',
            'list_projects': '📂',
            'get_project': '📂',
            'list_issue_types': '🏷️'
        }
        
        emoji = emoji_map.get(tool_name, '📋')
        
        # Truncate if too long
        if len(cleaned_text) > 1500:
            cleaned_text = cleaned_text[:1500] + "...\n\n_[Response truncated]_"
        
        return f"{emoji} *Jira Response:*\n\n{cleaned_text}"
    
    # Helper methods for parsing different data structures
    
    def _parse_issues_from_text(self, text: str) -> List[Dict[str, str]]:
        """Parse multiple issues from text response"""
        issues = []
        
        # Split by issue separators (=== or similar patterns)
        issue_blocks = re.split(r'===+', text)
        
        for block in issue_blocks:
            issue = self._parse_single_issue(block)
            if issue and issue.get('Key'):
                issues.append(issue)
        
        return issues
    
    def _parse_single_issue(self, text: str) -> Dict[str, str]:
        """Parse single issue from text"""
        issue = {}
        
        # Common field patterns
        patterns = {
            'Key': r'Key:\s*([A-Z]+-\d+)',
            'Summary': r'Summary:\s*(.+?)(?:\n|$)',
            'Status': r'Status:\s*(.+?)(?:\n|$)',
            'Type': r'Type:\s*(.+?)(?:\n|$)',
            'Priority': r'Priority:\s*(.+?)(?:\n|$)',
            'Assignee': r'Assignee:\s*(.+?)(?:\n|$)',
            'Reporter': r'Reporter:\s*(.+?)(?:\n|$)',
            'Created': r'Created:\s*(.+?)(?:\n|$)',
            'Updated': r'Updated:\s*(.+?)(?:\n|$)',
            'Project': r'Project:\s*(.+?)(?:\n|$)',
            'Description': r'Description:\s*(.+?)(?:\n\n|\Z)',
            'URL': r'URL:\s*(https?://[^\s]+)'
        }
        
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.DOTALL)
            if match:
                issue[field] = match.group(1).strip()
        
        return issue
    
    def _parse_projects_from_text(self, text: str) -> List[Dict[str, str]]:
        """Parse projects list from text"""
        projects = []
        
        # Look for project patterns like "KEY - Project Name"
        lines = text.split('\n')
        
        for line in lines:
            # Pattern: "PROJECT_KEY - Project Name"
            project_match = re.match(r'([A-Z]+\d*)\s*-\s*(.+)', line.strip())
            if project_match:
                projects.append({
                    'key': project_match.group(1).strip(),
                    'name': project_match.group(2).strip()
                })
        
        return projects
    
    def _parse_single_project(self, text: str) -> Dict[str, str]:
        """Parse single project details"""
        project = {}
        
        patterns = {
            'key': r'Key:\s*([A-Z]+\d*)',
            'name': r'Name:\s*(.+?)(?:\n|$)',
            'description': r'Description:\s*(.+?)(?:\n\n|\Z)',
            'type': r'Type:\s*(.+?)(?:\n|$)',
            'lead': r'Lead:\s*(.+?)(?:\n|$)',
            'url': r'URL:\s*(https?://[^\s]+)'
        }
        
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.DOTALL)
            if match:
                project[field] = match.group(1).strip()
        
        return project
    
    def _parse_issue_types_from_text(self, text: str) -> List[str]:
        """Parse issue types from text"""
        issue_types = []
        
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for patterns like "Name: Task" or "- Task" or "1. Task"
            patterns = [
                r'Name:\s*([^(\n]+?)(?:\s*\([^)]*\))?,
                r'^[\s\-*\d.]+([^(\n]+?)(?:\s*\([^)]*\))?,
                r'^([A-Za-z][^:(\n]*?)(?:\s*\([^)]*\))?
            ]
            
            for pattern in patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    issue_type = match.group(1).strip()
                    if issue_type and len(issue_type) > 0:
                        issue_types.append(issue_type)
                        break
        
        return list(set(issue_types))  # Remove duplicates
    
    def _parse_comments_from_text(self, text: str) -> List[Dict[str, str]]:
        """Parse comments from text"""
        comments = []
        
        # Split by comment separators
        comment_blocks = re.split(r'Comment \d+:|---+', text)
        
        for block in comment_blocks:
            if not block.strip():
                continue
                
            comment = {}
            
            # Extract comment fields
            author_match = re.search(r'Author:\s*(.+?)(?:\n|$)', block)
            if author_match:
                comment['author'] = author_match.group(1).strip()
            
            created_match = re.search(r'Created:\s*(.+?)(?:\n|$)', block)
            if created_match:
                comment['created'] = created_match.group(1).strip()
            
            # Extract body (everything after field declarations)
            body_match = re.search(r'Body:\s*(.+?)(?:\n\n|\Z)', block, re.DOTALL)
            if body_match:
                comment['body'] = body_match.group(1).strip()
            elif comment.get('author'):  # If we have author but no explicit body
                # Try to extract remaining text as body
                remaining = re.sub(r'Author:.*?\n|Created:.*?\n', '', block, flags=re.DOTALL)
                if remaining.strip():
                    comment['body'] = remaining.strip()
            
            if comment.get('author') or comment.get('body'):
                comments.append(comment)
        
        return comments
    
    def _format_issue_summary(self, issue: Dict[str, str], index: int) -> str:
        """Format a single issue summary for search results"""
        summary = f"**{index}. {issue.get('Key', 'Unknown')}** - {issue.get('Summary', 'No summary')}\n"
        
        # Add key details on same line to save space
        details = []
        if issue.get('Status'):
            details.append(f"📊 {issue['Status']}")
        if issue.get('Priority'):
            details.append(f"⚡ {issue['Priority']}")
        if issue.get('Assignee') and issue['Assignee'] != 'Unassigned':
            details.append(f"👤 {issue['Assignee']}")
        
        if details:
            summary += f"   {' | '.join(details)}\n"
        
        # Add URL if available
        if issue.get('URL'):
            summary += f"   🔗 <{issue['URL']}|View>\n"
        
        summary += "\n"
        return summary
    
    def _format_date(self, date_str: str) -> str:
        """Format date string for display"""
        try:
            # Try to parse common date formats
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S']:
                try:
                    dt = datetime.strptime(date_str.replace('Z', '+0000'), fmt)
                    return dt.strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    continue
            
            # If parsing fails, return original
            return date_str
            
        except Exception:
            return date_str

# Global formatter instance
_response_formatter = None

def get_response_formatter() -> JiraResponseFormatter:
    """Get or create global response formatter instance"""
    global _response_formatter
    if _response_formatter is None:
        _response_formatter = JiraResponseFormatter()
    return _response_formatter

def format_jira_response(tool_name: str, response_data: Dict[str, Any], query: str = "") -> str:
    """Convenience function to format Jira response"""
    formatter = get_response_formatter()
    return formatter.format_response(tool_name, response_data, query)