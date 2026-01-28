"""
Conversation Manager: Persist chat history to Supabase.

Manages conversations and messages in the database with RLS enforcement.
"""
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from dotenv import load_dotenv
from supabase import Client, create_client

# Load environment variables from root .env (Supabase config)
root_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(root_env_path)

# Also load chat-ui .env for any overrides
chat_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(chat_env_path)


class ConversationManager:
    """Manages conversations and messages in Supabase."""

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        client: Optional[Client] = None,
    ):
        """
        Initialize Supabase client.

        Args:
            supabase_url: Supabase URL (defaults to env SUPABASE_URL)
            supabase_key: Supabase anon key (defaults to env SUPABASE_ANON_KEY)
            client: Optional pre-authenticated Supabase client (takes precedence)
        """
        if client:
            # Use provided authenticated client
            self.client = client
        else:
            # Create new client
            self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
            self.supabase_key = supabase_key or os.getenv("SUPABASE_ANON_KEY")

            if not self.supabase_url or not self.supabase_key:
                raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set in environment")

            self.client: Client = create_client(self.supabase_url, self.supabase_key)

    def create_conversation(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
        parent_conversation_id: Optional[str] = None,
    ) -> str:
        """
        Create a new conversation.

        Args:
            user_id: UUID of the user creating the conversation
            project_id: Optional UUID of the project this conversation is about
            title: Optional title for the conversation
            parent_conversation_id: Optional parent conversation ID (for branching)

        Returns:
            UUID of the created conversation

        Raises:
            Exception: If conversation creation fails
        """
        data = {
            "user_id": user_id,
            "project_id": project_id,
            "title": title,
            "parent_conversation_id": parent_conversation_id,
        }

        result = self.client.table("conversations").insert(data).execute()

        if not result.data:
            raise Exception("Failed to create conversation")

        return result.data[0]["id"]

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        parent_message_id: Optional[str] = None,
        branch_index: int = 0,
    ) -> str:
        """
        Save a message to a conversation.

        Args:
            conversation_id: UUID of the conversation
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            parent_message_id: Optional parent message ID (for branching)
            branch_index: Branch index if multiple branches from same parent

        Returns:
            UUID of the created message

        Raises:
            Exception: If message creation fails
        """
        if role not in ["user", "assistant", "system"]:
            raise ValueError(f"Invalid role: {role}. Must be 'user', 'assistant', or 'system'")

        data = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "parent_message_id": parent_message_id,
            "branch_index": branch_index,
        }

        result = self.client.table("messages").insert(data).execute()

        if not result.data:
            raise Exception("Failed to save message")

        return result.data[0]["id"]

    def load_conversation(self, conversation_id: str) -> List[dict]:
        """
        Load all messages from a conversation.

        Args:
            conversation_id: UUID of the conversation

        Returns:
            List of message dicts with keys: id, role, content, created_at

        Raises:
            Exception: If conversation doesn't exist or access is denied
        """
        result = (
            self.client.table("messages")
            .select("id, role, content, created_at, parent_message_id, branch_index")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )

        return result.data

    def list_conversations(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """
        List conversations for a user, optionally filtered by project.

        Args:
            user_id: UUID of the user
            project_id: Optional UUID to filter by project
            limit: Maximum number of conversations to return

        Returns:
            List of conversation dicts with keys: id, title, project_id, created_at, updated_at
        """
        query = (
            self.client.table("conversations")
            .select("id, title, project_id, created_at, updated_at, parent_conversation_id")
            .eq("user_id", user_id)
        )

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.order("updated_at", desc=True).limit(limit).execute()

        return result.data

    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """
        Get conversation metadata.

        Args:
            conversation_id: UUID of the conversation

        Returns:
            Conversation dict or None if not found
        """
        result = (
            self.client.table("conversations")
            .select("id, user_id, project_id, title, created_at, updated_at, parent_conversation_id")
            .eq("id", conversation_id)
            .execute()
        )

        return result.data[0] if result.data else None

    def update_conversation_title(self, conversation_id: str, title: str) -> bool:
        """
        Update conversation title.

        Args:
            conversation_id: UUID of the conversation
            title: New title

        Returns:
            True if successful
        """
        result = (
            self.client.table("conversations")
            .update({"title": title})
            .eq("id", conversation_id)
            .execute()
        )

        return bool(result.data)

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        Delete a conversation and all its messages.

        Args:
            conversation_id: UUID of the conversation

        Returns:
            True if successful

        Note:
            Messages are automatically deleted via ON DELETE CASCADE
        """
        result = (
            self.client.table("conversations")
            .delete()
            .eq("id", conversation_id)
            .execute()
        )

        return bool(result.data)

    def create_branch(
        self,
        conversation_id: str,
        from_message_id: str,
        user_id: str,
        project_id: Optional[str] = None,
    ) -> str:
        """
        Create a conversation branch from a specific message.

        This creates a new conversation with the same messages up to from_message_id,
        then future messages diverge.

        Args:
            conversation_id: Original conversation ID
            from_message_id: Message ID to branch from
            user_id: User creating the branch
            project_id: Optional project ID

        Returns:
            UUID of the new branched conversation
        """
        # Create new conversation with parent reference
        new_conversation_id = self.create_conversation(
            user_id=user_id,
            project_id=project_id,
            title=f"Branch from conversation",
            parent_conversation_id=conversation_id,
        )

        # Load messages up to branch point
        messages = self.load_conversation(conversation_id)

        # Copy messages up to (and including) from_message_id
        for msg in messages:
            self.save_message(
                conversation_id=new_conversation_id,
                role=msg["role"],
                content=msg["content"],
            )

            if msg["id"] == from_message_id:
                break

        return new_conversation_id


# Singleton instance
_conversation_manager: Optional[ConversationManager] = None


def get_conversation_manager() -> ConversationManager:
    """Get singleton ConversationManager instance."""
    global _conversation_manager
    if _conversation_manager is None:
        _conversation_manager = ConversationManager()
    return _conversation_manager
