# utils/jira_mcp_client.py

import os
import json
import asyncio
import logging
import subprocess
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class JiraTool:
    name: str
    description: str
    input_schema: Dict[str, Any]

class JiraMCPClient:
    """
    Python-based MCP client for communicating with Jira MCP server.
    Handles the communication protocol and provides async interface.
    """
    
    def __init__(self):
        self.process = None
        self.is_connected = False
        self.session_id = 0
        self.available_tools: Dict[str, JiraTool] = {}
        
        # Configuration
        self.jira_binary_path = os.getenv('JIRA_MCP_BINARY_PATH')
        self.enable_jira = os.getenv('ENABLE_JIRA_FEATURES', 'false').lower() == 'true'
        
        # Read-only tools we support
        self.readonly_tools = {
            'search_issue', 'get_issue', 'get_comments', 
            'list_projects', 'list_issue_types', 'get_project'
        }
    
    async def connect(self) -> bool:
        """Connect to Jira MCP server"""
        if not self.enable_jira:
            logger.info("Jira features disabled via ENABLE_JIRA_FEATURES")
            return False
            
        if not self.jira_binary_path or not os.path.exists(self.jira_binary_path):
            logger.error(f"Jira MCP binary not found at: {self.jira_binary_path}")
            return False
        
        try:
            logger.info("🔧 Connecting to Jira MCP server...")
            
            # Prepare environment file path
            env_file = self._get_env_file_path()
            if not os.path.exists(env_file):
                await self._create_env_file(env_file)
            
            # Start MCP server process
            self.process = await asyncio.create_subprocess_exec(
                self.jira_binary_path, '-env', env_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Initialize connection
            await self._initialize_connection()
            
            # Discover available tools
            await self._discover_tools()
            
            self.is_connected = True
            logger.info(f"✅ Connected to Jira MCP server with {len(self.available_tools)} tools")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to Jira MCP server: {e}")
            await self.disconnect()
            return False
    
    def _get_env_file_path(self) -> str:
        """Get path to Jira MCP environment file"""
        return os.path.expanduser('~/.jira-mcp.env')
    
    async def _create_env_file(self, env_file_path: str):
        """Create Jira MCP environment file from current environment"""
        logger.info(f"Creating Jira MCP environment file: {env_file_path}")
        
        env_content = []
        
        # Required Jira environment variables
        jira_env_vars = [
            'ATLASSIAN_HOST',
            'ATLASSIAN_EMAIL', 
            'ATLASSIAN_TOKEN'
        ]
        
        for var in jira_env_vars:
            value = os.getenv(var)
            if value:
                env_content.append(f"{var}={value}")
            else:
                logger.warning(f"Missing environment variable: {var}")
        
        if env_content:
            with open(env_file_path, 'w') as f:
                f.write('\n'.join(env_content) + '\n')
            logger.info(f"Created environment file with {len(env_content)} variables")
        else:
            raise RuntimeError("No Jira environment variables found")
    
    async def _initialize_connection(self):
        """Initialize MCP connection with handshake"""
        # Send initialization request
        init_request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "clientInfo": {
                    "name": "ask-support-jira-integration",
                    "version": "1.0.0"
                }
            }
        }
        
        response = await self._send_request(init_request)
        if not response or "result" not in response:
            raise RuntimeError("Failed to initialize MCP connection")
        
        logger.debug("MCP connection initialized successfully")
    
    async def _discover_tools(self):
        """Discover available Jira tools"""
        tools_request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list"
        }
        
        response = await self._send_request(tools_request)
        
        if response and 'result' in response and 'tools' in response['result']:
            for tool_data in response['result']['tools']:
                tool_name = tool_data.get('name', '')
                
                # Only register read-only tools
                if tool_name in self.readonly_tools:
                    self.available_tools[tool_name] = JiraTool(
                        name=tool_name,
                        description=tool_data.get('description', ''),
                        input_schema=tool_data.get('inputSchema', {})
                    )
                    logger.debug(f"   - Registered read-only tool: {tool_name}")
        
        logger.info(f"Discovered {len(self.available_tools)} read-only Jira tools")
    
    async def _send_request(self, request: Dict) -> Optional[Dict]:
        """Send JSON-RPC request to MCP server"""
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP process not available")
        
        try:
            # Send request
            request_json = json.dumps(request) + '\n'
            self.process.stdin.write(request_json.encode())
            await self.process.stdin.drain()
            
            # Read response
            response_line = await self.process.stdout.readline()
            if not response_line:
                raise RuntimeError("No response from MCP server")
            
            response = json.loads(response_line.decode().strip())
            
            if 'error' in response:
                error_msg = response['error'].get('message', 'Unknown MCP error')
                raise RuntimeError(f"MCP error: {error_msg}")
            
            return response
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from MCP server: {e}")
            raise RuntimeError(f"Invalid JSON response: {e}")
        except Exception as e:
            logger.error(f"MCP request failed: {e}")
            raise
    
    def _next_id(self) -> int:
        """Get next request ID"""
        self.session_id += 1
        return self.session_id
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a Jira tool with given arguments"""
        if not self.is_connected:
            raise RuntimeError("Not connected to Jira MCP server")
        
        if tool_name not in self.available_tools:
            raise ValueError(f"Tool '{tool_name}' not available or not read-only")
        
        try:
            logger.debug(f"🔧 Calling Jira tool: {tool_name} with args: {arguments}")
            
            tool_request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            response = await self._send_request(tool_request)
            
            if response and 'result' in response:
                logger.debug(f"✅ Tool {tool_name} completed successfully")
                return response['result']
            else:
                raise RuntimeError(f"Invalid response from tool {tool_name}")
                
        except Exception as e:
            logger.error(f"❌ Tool execution failed: {tool_name} - {e}")
            raise
    
    async def disconnect(self):
        """Disconnect from MCP server"""
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                
            except asyncio.TimeoutError:
                logger.warning("MCP process did not terminate gracefully, killing...")
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.error(f"Error disconnecting from MCP server: {e}")
            finally:
                self.process = None
        
        self.is_connected = False
        self.available_tools.clear()
        logger.info("Disconnected from Jira MCP server")
    
    def get_available_tools(self) -> List[str]:
        """Get list of available tool names"""
        return list(self.available_tools.keys())
    
    def is_ready(self) -> bool:
        """Check if client is ready to handle requests"""
        return self.is_connected and len(self.available_tools) > 0

# Global client instance
_mcp_client = None

async def get_mcp_client() -> JiraMCPClient:
    """Get or create global MCP client instance"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = JiraMCPClient()
        await _mcp_client.connect()
    return _mcp_client

async def cleanup_mcp_client():
    """Clean up global MCP client"""
    global _mcp_client
    if _mcp_client:
        await _mcp_client.disconnect()
        _mcp_client = None