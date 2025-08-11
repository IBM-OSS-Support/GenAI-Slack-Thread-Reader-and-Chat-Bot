#!/usr/bin/env python3
"""
Test script to verify Jira connection and credentials
Run this to test your Jira configuration before running the bot
"""

import os
import sys
import requests
import base64
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_jira_connection():
    """Test Jira connection with your credentials"""
    print("🔧 Testing Jira Connection...")
    print("=" * 50)
    
    # Check environment variables
    host = os.getenv("ATLASSIAN_HOST")
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("ATLASSIAN_TOKEN")
    enable_jira = os.getenv("ENABLE_JIRA_FEATURES", "false").lower() == "true"
    
    print(f"ENABLE_JIRA_FEATURES: {enable_jira}")
    print(f"ATLASSIAN_HOST: {host}")
    print(f"ATLASSIAN_EMAIL: {email}")
    print(f"ATLASSIAN_TOKEN: {'***' + token[-4:] if token and len(token) > 4 else 'Missing'}")
    print()
    
    if not enable_jira:
        print("❌ Jira features are disabled. Set ENABLE_JIRA_FEATURES=true")
        return False
    
    if not all([host, email, token]):
        print("❌ Missing required environment variables")
        return False
    
    # Create auth headers
    auth_string = f"{email}:{token}"
    auth_bytes = auth_string.encode('ascii')
    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
    
    headers = {
        'Authorization': f'Basic {auth_b64}',
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    
    # Test connection with /myself endpoint
    print("🔗 Testing connection...")
    try:
        url = f"{host}/rest/api/3/myself"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ Connected successfully!")
            print(f"   User: {user_data.get('displayName', 'Unknown')}")
            print(f"   Email: {user_data.get('emailAddress', 'Unknown')}")
            print(f"   Account ID: {user_data.get('accountId', 'Unknown')}")
        elif response.status_code == 401:
            print("❌ Authentication failed")
            print("   Check your email and API token")
            return False
        elif response.status_code == 403:
            print("❌ Access forbidden")
            print("   Check your permissions")
            return False
        else:
            print(f"❌ Connection failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
    
    print()
    
    # Test projects
    print("📂 Testing projects access...")
    try:
        url = f"{host}/rest/api/3/project"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            projects = response.json()
            print(f"✅ Found {len(projects)} projects:")
            for project in projects[:5]:  # Show first 5
                print(f"   - {project.get('key', 'Unknown')}: {project.get('name', 'No name')}")
            if len(projects) > 5:
                print(f"   ... and {len(projects) - 5} more")
        else:
            print(f"❌ Projects access failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Projects error: {e}")
        return False
    
    print()
    
    # Test issues assigned to current user
    print("🎫 Testing issues search...")
    try:
        url = f"{host}/rest/api/3/search"
        params = {
            'jql': 'assignee = currentUser()',
            'maxResults': 5,
            'fields': 'key,summary,status'
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            search_data = response.json()
            issues = search_data.get('issues', [])
            total = search_data.get('total', 0)
            print(f"✅ Found {total} issues assigned to you:")
            for issue in issues:
                key = issue.get('key', 'Unknown')
                summary = issue.get('fields', {}).get('summary', 'No summary')
                status = issue.get('fields', {}).get('status', {}).get('name', 'Unknown')
                print(f"   - {key}: {summary} ({status})")
        else:
            print(f"❌ Issues search failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Issues search error: {e}")
        return False
    
    print()
    print("🎉 All tests passed! Your Jira integration should work.")
    return True

if __name__ == "__main__":
    success = test_jira_connection()
    sys.exit(0 if success else 1)
