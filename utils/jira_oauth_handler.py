# utils/jira_oauth_handler.py

import os
import json
import uuid
import time
import logging
import hashlib
import secrets
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import requests
from urllib.parse import urlencode, parse_qs, urlparse

logger = logging.getLogger(__name__)

class JiraOAuthHandler:
    """
    Handles OAuth 2.0 authentication flow for Jira/Atlassian
    Implements atomic authentication - each query requires fresh auth
    """
    
    def __init__(self):
        # OAuth app credentials (you'll need to register an OAuth app in Atlassian)
        self.client_id = os.getenv("ATLASSIAN_OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("ATLASSIAN_OAUTH_CLIENT_SECRET")
        self.redirect_uri = os.getenv("ATLASSIAN_OAUTH_REDIRECT_URI", "http://localhost:3002/oauth/callback")
        
        # OAuth endpoints
        self.auth_base_url = "https://auth.atlassian.com/authorize"
        self.token_url = "https://auth.atlassian.com/oauth/token"
        self.cloud_resource_url = "https://api.atlassian.com/oauth/token/accessible-resources"
        
        # ONLY store pending auth states temporarily - NO token storage
        self.pending_auth_states: Dict[str, Dict] = {}  # state -> user_info
        
        # Temporary storage for CURRENT query processing only
        # These are valid for the duration of a single query processing
        self.active_query_credentials: Dict[str, Dict] = {}  # user_id -> credentials for active query
        
    def generate_auth_url(self, user_id: str, team_id: str, channel_id: str, thread_ts: str, original_query: str = None) -> Tuple[str, str]:
        """
        Generate OAuth authorization URL for user to authenticate with Jira
        Returns: (auth_url, state)
        """
        # Clear any existing credentials for this user to ensure fresh auth
        if user_id in self.active_query_credentials:
            del self.active_query_credentials[user_id]
            logger.info(f"Cleared existing credentials for user {user_id}")
        
        # Generate secure random state
        state = secrets.token_urlsafe(32)
        
        # Store state with user context for callback
        self.pending_auth_states[state] = {
            'user_id': user_id,
            'team_id': team_id,
            'channel_id': channel_id,
            'thread_ts': thread_ts,
            'original_query': original_query,  # Store the original query
            'created_at': time.time(),
            'expires_at': time.time() + 600  # 10 minute expiry
        }
        
        # Clean up expired states
        self._cleanup_expired_states()
        
        # Build authorization URL
        params = {
            'audience': 'api.atlassian.com',
            'client_id': self.client_id,
            'scope': 'read:jira-work read:jira-user write:jira-work offline_access',
            'redirect_uri': self.redirect_uri,
            'state': state,
            'response_type': 'code',
            'prompt': 'consent'  # Always prompt for consent
        }
        
        auth_url = f"{self.auth_base_url}?{urlencode(params)}"
        
        logger.info(f"Generated auth URL for user {user_id}")
        return auth_url, state
    
    def handle_oauth_callback(self, code: str, state: str) -> Optional[Dict]:
        """
        Handle OAuth callback with authorization code
        Returns user context if successful
        """
        # Validate state
        if state not in self.pending_auth_states:
            logger.error(f"Invalid or expired state: {state}")
            return None
        
        user_context = self.pending_auth_states[state]
        
        # Check if state is expired
        if time.time() > user_context['expires_at']:
            logger.error(f"State expired for user {user_context['user_id']}")
            del self.pending_auth_states[state]
            return None
        
        # Exchange code for tokens
        try:
            token_data = self._exchange_code_for_token(code)
            if not token_data:
                return None
            
            # Get accessible resources (Jira sites)
            resources = self._get_accessible_resources(token_data['access_token'])
            if not resources:
                return None
            
            # Store credentials temporarily for this query only
            user_id = user_context['user_id']
            self.active_query_credentials[user_id] = {
                'access_token': token_data['access_token'],
                'resource': resources[0] if resources else {},
                'user_id': user_id,
                'created_at': time.time(),
                'query': user_context.get('original_query')
            }
            
            # Clean up state
            del self.pending_auth_states[state]
            
            logger.info(f"Successfully authenticated user {user_id} for current query")
            return user_context
            
        except Exception as e:
            logger.error(f"Error handling OAuth callback: {e}")
            return None
    
    def get_user_credentials(self, user_id: str) -> Optional[Dict]:
        """
        Get credentials for user's current active query
        Returns credentials without deleting them (they'll be cleared after query completes)
        """
        if user_id not in self.active_query_credentials:
            logger.error(f"No active credentials for user {user_id}")
            return None
        
        creds = self.active_query_credentials[user_id]
        resource = creds.get('resource', {})
        
        return {
            'email': f'user-{user_id}@oauth.atlassian.com',  # Use a placeholder email
            'token': creds['access_token'],
            'host': f"https://{resource.get('url')}",
            'cloud_id': resource.get('id'),
            'user_id': user_id
        }
    
    def clear_user_credentials(self, user_id: str):
        """
        Clear credentials for a specific user after query is complete
        """
        if user_id in self.active_query_credentials:
            del self.active_query_credentials[user_id]
            logger.info(f"Cleared credentials for user {user_id}")
    
    def is_user_authenticated(self, user_id: str) -> bool:
        """
        Always return False to force re-authentication for every query
        """
        return False  # Always require fresh authentication
    
    def clear_all_credentials(self):
        """
        Clear all active credentials (emergency cleanup)
        """
        self.active_query_credentials.clear()
        logger.info("Cleared all active credentials")
    
    # Private helper methods
    
    def _exchange_code_for_token(self, code: str) -> Optional[Dict]:
        """Exchange authorization code for access token"""
        try:
            data = {
                'grant_type': 'authorization_code',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
                'redirect_uri': self.redirect_uri
            }
            
            response = requests.post(self.token_url, data=data, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None
    
    def _get_accessible_resources(self, access_token: str) -> Optional[list]:
        """Get list of accessible Jira/Confluence sites"""
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
            response = requests.get(self.cloud_resource_url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get accessible resources: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting accessible resources: {e}")
            return None
    
    def _cleanup_expired_states(self):
        """Remove expired pending auth states"""
        current_time = time.time()
        
        # Clean expired auth states
        expired_states = [
            state for state, info in self.pending_auth_states.items()
            if current_time > info['expires_at']
        ]
        
        for state in expired_states:
            del self.pending_auth_states[state]
        
        # Clean old active credentials (older than 5 minutes)
        expired_users = [
            user_id for user_id, creds in self.active_query_credentials.items()
            if current_time - creds.get('created_at', 0) > 300
        ]
        
        for user_id in expired_users:
            del self.active_query_credentials[user_id]
        
        if expired_states or expired_users:
            logger.info(f"Cleaned up {len(expired_states)} expired states and {len(expired_users)} expired credentials")

# Global instance
_oauth_handler = None

def get_oauth_handler() -> JiraOAuthHandler:
    """Get or create global OAuth handler instance"""
    global _oauth_handler
    if _oauth_handler is None:
        _oauth_handler = JiraOAuthHandler()
    return _oauth_handler