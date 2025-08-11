# utils/jira_config.py
# Add this new file to check and validate Jira configuration

import os
import logging

logger = logging.getLogger(__name__)

def check_jira_configuration():
    """
    Check if Jira is properly configured and return configuration status
    """
    config_status = {
        'enabled': False,
        'configured': False,
        'missing_vars': [],
        'error_message': None
    }
    
    # Check if Jira features are enabled
    enable_jira = os.getenv("ENABLE_JIRA_FEATURES", "false").lower() == "true"
    config_status['enabled'] = enable_jira
    
    if not enable_jira:
        config_status['error_message'] = "Jira features are disabled. Set ENABLE_JIRA_FEATURES=true to enable."
        return config_status
    
    # Check required environment variables
    required_vars = [
        'ATLASSIAN_HOST',
        'ATLASSIAN_EMAIL', 
        'ATLASSIAN_TOKEN'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    config_status['missing_vars'] = missing_vars
    
    if missing_vars:
        config_status['error_message'] = f"Missing required environment variables: {', '.join(missing_vars)}"
        return config_status
    
    # Validate Atlassian host format
    host = os.getenv('ATLASSIAN_HOST')
    if not host.startswith('https://'):
        config_status['error_message'] = "ATLASSIAN_HOST must start with https://"
        return config_status
    
    config_status['configured'] = True
    logger.info("✅ Jira configuration is valid")
    return config_status

def get_jira_help_message():
    """
    Return help message for Jira configuration
    """
    return """
🎫 *Jira Integration Setup Required*

To use Jira features, you need to configure the following environment variables:

**Required Environment Variables:**
• `ENABLE_JIRA_FEATURES=true`
• `ATLASSIAN_HOST=https://your-domain.atlassian.net`
• `ATLASSIAN_EMAIL=your-email@domain.com`
• `ATLASSIAN_TOKEN=your-api-token`

**To get your Atlassian API token:**
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a label and copy the generated token

**Available Jira Commands:**
• "list my projects"
• "show my issues" 
• "issues assigned to me"
• "what is ABC-123?" (replace with actual issue key)
• "my open issues"

Please contact your administrator to configure these settings.
"""