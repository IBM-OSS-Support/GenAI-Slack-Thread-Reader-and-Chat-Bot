# utils/jira_mcp_manager.py

import os
import logging
from typing import Dict, List, Optional, Any

from .jira_mcp_client import get_mcp_client, JiraMCPClient

logger = logging.getLogger(__name__)

class JiraMCPManager:
    """
    Manages MCP connection to Jira server and tool execution.
    Handles read-only Jira operations only.
    """
    
    def __init__(self):
        self.is_ready = False
        self.mcp_client: Optional[JiraMCPClient] = None
        
        # Configuration from environment
        self.enable_jira = os.getenv('ENABLE_JIRA_FEATURES', 'false').lower() == 'true'
        
    async def initialize(self) -> bool:
        """Initialize MCP connection to Jira server"""
        if not self.enable_jira:
            logger.info("Jira features disabled via ENABLE_JIRA_FEATURES")
            return False
            
        try:
            logger.info("🔧 Initializing Jira MCP manager...")
            
            # Get MCP client (will connect automatically)
            self.mcp_client = await get_mcp_client()
            
            if self.mcp_client.is_ready():
                self.is_ready = True
                tools_count = len(self.mcp_client.get_available_tools())
                logger.info(f"✅ Jira MCP manager initialized with {tools_count} tools")
                return True
            else:
                logger.error("❌ MCP client not ready")
                return False
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Jira MCP manager: {e}")
            return False
    
    async def search_issues(self, jql: str, max_results: int = 50) -> Dict[str, Any]:
        """Search for issues using JQL"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        return await self.mcp_client.call_tool('search_issue', {
            'jql': jql,
            'max_results': max_results
        })
    
    async def get_issue(self, issue_key: str, expand: Optional[str] = None) -> Dict[str, Any]:
        """Get specific issue details"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        args = {'issue_key': issue_key}
        if expand:
            args['expand'] = expand
        return await self.mcp_client.call_tool('get_issue', args)
    
    async def get_comments(self, issue_key: str) -> Dict[str, Any]:
        """Get comments for an issue"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        return await self.mcp_client.call_tool('get_comments', {
            'issue_key': issue_key
        })
    
    async def list_projects(self) -> Dict[str, Any]:
        """List available projects"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        return await self.mcp_client.call_tool('list_projects', {})
    
    async def get_project(self, project_key: str) -> Dict[str, Any]:
        """Get project details"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        return await self.mcp_client.call_tool('get_project', {
            'project_key': project_key
        })
    
    async def list_issue_types(self, project_key: str) -> Dict[str, Any]:
        """List issue types for a project"""
        if not self.mcp_client:
            raise RuntimeError("MCP client not initialized")
        return await self.mcp_client.call_tool('list_issue_types', {
            'project_key': project_key
        })
    
    def get_available_tools(self) -> List[str]:
        """Get list of available tool names"""
        if self.mcp_client:
            return self.mcp_client.get_available_tools()
        return []
    
    async def cleanup(self):
        """Clean up MCP client"""
        if self.mcp_client:
            await self.mcp_client.disconnect()
            self.mcp_client = None
        self.is_ready = False

# Global instance
_jira_manager = None

def get_jira_manager() -> JiraMCPManager:
    """Get or create global Jira manager instance"""
    global _jira_manager
    if _jira_manager is None:
        _jira_manager = JiraMCPManager()
    return _jira_manager

async def initialize_jira_if_needed() -> bool:
    """Initialize Jira manager if not already done"""
    manager = get_jira_manager()
    if not manager.is_ready:
        return await manager.initialize()
    return True