#!/usr/bin/env python3
"""
Phase 5: Test Conversation Manager

Tests conversation and message CRUD operations with Supabase.
Uses authenticated test user to properly test RLS policies.
"""
import os
import sys
from pathlib import Path
from uuid import uuid4

import requests
from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from auth_helper import get_test_user_client
from conversation_manager import ConversationManager


def get_current_project_id() -> str:
    """
    Get currently loaded project ID from data-server.

    Returns:
        Project ID as string

    Raises:
        Exception: If no project is loaded or server unreachable
    """
    endpoint = os.getenv("FASTAPI_ENDPOINT", "http://localhost:8001")

    try:
        response = requests.get(f"{endpoint}/projects/current", timeout=5)

        if response.status_code == 200:
            data = response.json()
            return data["project_id"]
        elif response.status_code == 404:
            raise Exception("No project currently loaded in data-server. Please load a project first.")
        else:
            response.raise_for_status()

    except requests.exceptions.ConnectionError:
        raise Exception(f"Cannot connect to data-server at {endpoint}")
    except requests.exceptions.Timeout:
        raise Exception("Data-server request timed out")
    except Exception as e:
        raise Exception(f"Failed to get current project: {e}")


def test_create_conversation():
    """Test creating a conversation."""
    print("\n=== Test 1: Create Conversation ===")

    # Login with test user
    user_id, client = get_test_user_client()
    print(f"✓ Logged in as test user: {user_id}")

    # Get currently loaded project from data-server
    try:
        project_id = get_current_project_id()
        print(f"✓ Using loaded project: {project_id}")
    except Exception as e:
        print(f"⚠ Warning: {e}")
        print("  Creating conversation without project association")
        project_id = None

    # Create conversation manager with authenticated client
    cm = ConversationManager(client=client)

    conversation_id = cm.create_conversation(
        user_id=user_id,
        project_id=project_id,
        title="Test Conversation"
    )

    print(f"✓ Created conversation: {conversation_id}")
    return user_id, project_id, conversation_id, client


def test_save_messages(conversation_id: str, client):
    """Test saving messages to a conversation."""
    print("\n=== Test 2: Save Messages ===")

    cm = ConversationManager(client=client)

    # Save user message
    msg1_id = cm.save_message(
        conversation_id=conversation_id,
        role="user",
        content="How many commits are there?"
    )
    print(f"✓ Saved user message: {msg1_id}")

    # Save assistant message
    msg2_id = cm.save_message(
        conversation_id=conversation_id,
        role="assistant",
        content="The project has 5,560 commits in total."
    )
    print(f"✓ Saved assistant message: {msg2_id}")

    # Save another user message
    msg3_id = cm.save_message(
        conversation_id=conversation_id,
        role="user",
        content="Who are the top contributors?"
    )
    print(f"✓ Saved another user message: {msg3_id}")

    return [msg1_id, msg2_id, msg3_id]


def test_load_conversation(conversation_id: str, client):
    """Test loading conversation messages."""
    print("\n=== Test 3: Load Conversation ===")

    cm = ConversationManager(client=client)

    messages = cm.load_conversation(conversation_id)

    print(f"✓ Loaded {len(messages)} messages:")
    for msg in messages:
        print(f"  - [{msg['role']}] {msg['content'][:50]}...")

    return messages


def test_list_conversations(user_id: str, project_id: str, client):
    """Test listing user conversations."""
    print("\n=== Test 4: List Conversations ===")

    cm = ConversationManager(client=client)

    # List all conversations for user
    conversations = cm.list_conversations(user_id=user_id)
    print(f"✓ Found {len(conversations)} conversation(s) for user")

    # List conversations filtered by project
    project_conversations = cm.list_conversations(
        user_id=user_id,
        project_id=project_id
    )
    print(f"✓ Found {len(project_conversations)} conversation(s) for project")

    return conversations


def test_update_conversation(conversation_id: str, client):
    """Test updating conversation title."""
    print("\n=== Test 5: Update Conversation Title ===")

    cm = ConversationManager(client=client)

    success = cm.update_conversation_title(
        conversation_id=conversation_id,
        title="Updated Test Conversation"
    )

    if success:
        print(f"✓ Updated conversation title")

        # Verify update
        conv = cm.get_conversation(conversation_id)
        print(f"  New title: {conv['title']}")
    else:
        print("✗ Failed to update title")


def test_get_conversation(conversation_id: str, client):
    """Test getting conversation metadata."""
    print("\n=== Test 6: Get Conversation Metadata ===")

    cm = ConversationManager(client=client)

    conv = cm.get_conversation(conversation_id)

    if conv:
        print(f"✓ Retrieved conversation:")
        print(f"  ID: {conv['id']}")
        print(f"  Title: {conv['title']}")
        print(f"  Project ID: {conv['project_id']}")
        print(f"  Created: {conv['created_at']}")
    else:
        print("✗ Conversation not found")


def test_branching(conversation_id: str, user_id: str, project_id: str, message_ids: list, client):
    """Test conversation branching."""
    print("\n=== Test 7: Conversation Branching ===")

    cm = ConversationManager(client=client)

    # Create branch from first message
    branch_id = cm.create_branch(
        conversation_id=conversation_id,
        from_message_id=message_ids[0],
        user_id=user_id,
        project_id=project_id
    )

    print(f"✓ Created branch: {branch_id}")

    # Load branch messages
    branch_messages = cm.load_conversation(branch_id)
    print(f"  Branch has {len(branch_messages)} message(s)")

    return branch_id


def test_delete_conversation(conversation_id: str, client):
    """Test deleting a conversation."""
    print("\n=== Test 8: Delete Conversation ===")

    cm = ConversationManager(client=client)

    success = cm.delete_conversation(conversation_id)

    if success:
        print(f"✓ Deleted conversation: {conversation_id}")

        # Verify deletion
        conv = cm.get_conversation(conversation_id)
        if conv is None:
            print("  Verified: Conversation no longer exists")
        else:
            print("  Warning: Conversation still exists after delete")
    else:
        print("✗ Failed to delete conversation")


def test_rls_enforcement(client):
    """Test Row-Level Security policies."""
    print("\n=== Test 9: RLS Enforcement ===")

    cm = ConversationManager(client=client)

    # Get authenticated user
    user_response = client.auth.get_user()
    auth_user_id = user_response.user.id
    print(f"  Authenticated user: {auth_user_id}")

    # Try to create conversation with DIFFERENT user_id (should fail with RLS)
    different_user_id = str(uuid4())

    try:
        conv_id = cm.create_conversation(
            user_id=different_user_id,  # Different from authenticated user!
            title="Unauthorized Conversation"
        )
        print(f"✗ RLS FAILED: Created conversation with different user_id")
    except Exception as e:
        if "row-level security" in str(e):
            print(f"✓ RLS correctly blocked creation with mismatched user_id")
        else:
            print(f"✗ Unexpected error: {e}")

    # Create conversation with CORRECT user_id (should succeed)
    try:
        conv_id = cm.create_conversation(
            user_id=auth_user_id,  # Matches authenticated user
            title="Authorized Conversation"
        )
        print(f"✓ RLS allowed creation with matching user_id: {conv_id}")
        return conv_id
    except Exception as e:
        print(f"✗ Failed to create with correct user_id: {e}")
        return None


def cleanup_test_data(conversation_ids: list, client):
    """Clean up test data."""
    print("\n=== Cleanup ===")

    cm = ConversationManager(client=client)

    for conv_id in conversation_ids:
        try:
            cm.delete_conversation(conv_id)
            print(f"✓ Cleaned up conversation: {conv_id}")
        except Exception as e:
            print(f"⚠ Failed to cleanup {conv_id}: {e}")


def main():
    """Run all conversation manager tests."""
    print("=" * 60)
    print("Phase 5: Testing Conversation Manager")
    print("=" * 60)

    conversation_ids = []

    try:
        # Test 1: Create conversation (also logs in and returns client)
        user_id, project_id, conversation_id, client = test_create_conversation()
        conversation_ids.append(conversation_id)

        # Test 2: Save messages
        message_ids = test_save_messages(conversation_id, client)

        # Test 3: Load conversation
        messages = test_load_conversation(conversation_id, client)

        # Test 4: List conversations
        conversations = test_list_conversations(user_id, project_id, client)

        # Test 5: Update conversation
        test_update_conversation(conversation_id, client)

        # Test 6: Get conversation metadata
        test_get_conversation(conversation_id, client)

        # Test 7: Branching
        branch_id = test_branching(conversation_id, user_id, project_id, message_ids, client)
        conversation_ids.append(branch_id)

        # Test 8: Delete conversation (skip to preserve data for now)
        # test_delete_conversation(branch_id, client)

        # Test 9: RLS enforcement
        rls_conv_id = test_rls_enforcement(client)
        if rls_conv_id:
            conversation_ids.append(rls_conv_id)

        print("\n" + "=" * 60)
        print("✓ Phase 5 tests complete!")
        print("\nNote: Test conversations were created in the database.")
        print("Run cleanup to remove them (not done automatically).")

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Ask if user wants to cleanup
        response = input("\nCleanup test data? [y/N]: ").strip().lower()
        if response == "y":
            _, client = get_test_user_client()
            cleanup_test_data(conversation_ids, client)


if __name__ == "__main__":
    main()
