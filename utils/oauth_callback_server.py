# utils/oauth_callback_server.py

import os
import logging
import threading
from flask import Flask, request, redirect, render_template_string
from utils.jira_oauth_handler import get_oauth_handler
from utils.slack_api import send_message
from slack_sdk import WebClient

logger = logging.getLogger(__name__)

# OAuth callback Flask app
oauth_app = Flask(__name__)

# HTML templates for callback pages
SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Jira Connected</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .success { color: #4CAF50; font-size: 48px; }
        .message { margin-top: 20px; font-size: 18px; }
        .note { margin-top: 30px; color: #666; }
    </style>
</head>
<body>
    <div class="success">✅</div>
    <h1>Successfully Connected to Jira!</h1>
    <div class="message">You can now close this window and return to Slack.</div>
    <div class="note">Your Jira query will be processed automatically.</div>
    <script>
        // Auto-close window after 5 seconds
        setTimeout(function() {
            window.close();
        }, 5000);
    </script>
</body>
</html>
"""

ERROR_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Connection Failed</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .error { color: #f44336; font-size: 48px; }
        .message { margin-top: 20px; font-size: 18px; }
        .details { margin-top: 20px; color: #666; }
    </style>
</head>
<body>
    <div class="error">❌</div>
    <h1>Failed to Connect to Jira</h1>
    <div class="message">{{ error_message }}</div>
    <div class="details">Please return to Slack and try again.</div>
</body>
</html>
"""

@oauth_app.route('/oauth/callback')
def oauth_callback():
    """Handle OAuth callback from Atlassian"""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        logger.error(f"OAuth error: {error}")
        return render_template_string(ERROR_TEMPLATE, error_message=f"Authorization denied: {error}")
    
    if not code or not state:
        logger.error("Missing code or state in OAuth callback")
        return render_template_string(ERROR_TEMPLATE, error_message="Invalid callback parameters")
    
    oauth_handler = get_oauth_handler()
    
    # Handle the OAuth callback
    user_context = oauth_handler.handle_oauth_callback(code, state)
    
    if not user_context:
        return render_template_string(ERROR_TEMPLATE, error_message="Failed to complete authentication")
    
    # Send notification to Slack that auth is complete
    try:
        # Get the appropriate Slack client for the team
        from app import get_client_for_team
        client = get_client_for_team(user_context['team_id'])
        
        # Notify user in Slack
        send_message(
            client,
            user_context['channel_id'],
            "✅ *Jira Connected Successfully!*\n\nProcessing your query now...",
            thread_ts=user_context['thread_ts'],
            user_id=user_context['user_id']
        )
        
        # Re-process the original Jira query if it was stored
        original_query = user_context.get('original_query')
        if original_query:
            # Import here to avoid circular dependency
            from utils.jira_query_processor import process_jira_query_with_auth
            
            # Process the query - credentials are already stored in oauth_handler
            jira_response = process_jira_query_with_auth(
                original_query,
                user_id=user_context['user_id'],
                team_id=user_context['team_id'],
                channel_id=user_context['channel_id'],
                thread_ts=user_context['thread_ts']
            )
            
            if jira_response:
                # Send the actual Jira response
                send_message(
                    client,
                    user_context['channel_id'],
                    jira_response,
                    thread_ts=user_context['thread_ts'],
                    user_id=user_context['user_id']
                )
            
            # Clear credentials after query is processed
            oauth_handler.clear_user_credentials(user_context['user_id'])
        
    except Exception as e:
        logger.error(f"Error notifying Slack after OAuth: {e}")
        # Try to at least notify of the error
        try:
            send_message(
                client,
                user_context['channel_id'],
                f"❌ *Error processing your request*\n\nAuthentication succeeded but couldn't process your query: {str(e)}",
                thread_ts=user_context['thread_ts'],
                user_id=user_context['user_id']
            )
        except:
            pass
    finally:
        # Always clear credentials after processing
        if 'user_id' in user_context:
            oauth_handler.clear_user_credentials(user_context['user_id'])
    
    return render_template_string(SUCCESS_TEMPLATE)

@oauth_app.route('/health/oauth')
def oauth_health():
    """Health check endpoint for OAuth server"""
    return "OK", 200

def run_oauth_server():
    """Run the OAuth callback server"""
    port = int(os.getenv("OAUTH_CALLBACK_PORT", 3002))
    oauth_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )

def start_oauth_server_thread():
    """Start OAuth server in a background thread"""
    thread = threading.Thread(target=run_oauth_server, daemon=True)
    thread.start()
    logger.info("OAuth callback server started")