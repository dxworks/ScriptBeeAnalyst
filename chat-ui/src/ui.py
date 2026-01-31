"""
Chainlit UI: Chat interface integrated with orchestrator and conversation history.

This is the main entry point for the Chainlit chat application.
"""
import logging
import os
from datetime import datetime
from pathlib import Path

import chainlit as cl
import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage

from auth_helper import get_test_user_client
from conversation_manager import ConversationManager
from orchestrator import create_orchestrator

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

FASTAPI_ENDPOINT = os.getenv("FASTAPI_ENDPOINT", "http://localhost:8001")
USE_MOCK_AGENTS = os.getenv("USE_MOCK_AGENTS", "true").lower() == "true"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_current_project_info():
    """
    Get currently loaded project info from data-server.

    Returns:
        dict with project info or None if no project loaded
    """
    try:
        response = requests.get(f"{FASTAPI_ENDPOINT}/projects/current", timeout=5)

        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception:
        return None


@cl.on_chat_start
async def on_chat_start():
    """
    Initialize chat session.

    - Check for loaded project
    - Authenticate user (for now, use test user)
    - Create new conversation in database
    - Store conversation_id and orchestrator in session
    """
    # Show welcome message
    await cl.Message(
        content="🤖 **Project Analyst** starting up...",
    ).send()

    # Check project status
    project_info = get_current_project_info()

    if project_info:
        project_id = project_info.get("project_id")
        stats = project_info.get("stats", {})

        await cl.Message(
            content=f"""✅ **Project Loaded**

**Project ID**: `{project_id}`

**Statistics**:
- 📝 Git commits: {stats.get('git_commits', 0):,}
- 🐛 Jira issues: {stats.get('jira_issues', 0):,}
- 🔀 GitHub PRs: {stats.get('github_prs', 0):,}

You can now ask questions about this project!
""",
        ).send()
    else:
        project_id = None
        await cl.Message(
            content="""⚠️ **No Project Loaded**

Please load a project first via the web UI to start analyzing data.

You can still ask general questions, but data queries won't work until a project is loaded.
""",
        ).send()

    # Authenticate user (for now, using test user)
    # TODO: In production, get real user from Chainlit auth
    try:
        user_id, supabase_client = get_test_user_client()
    except Exception as e:
        await cl.Message(
            content=f"⚠️ Authentication failed: {e}\n\nConversation history will not be saved.",
        ).send()
        user_id = None
        supabase_client = None

    # Create conversation in database
    conversation_id = None
    if user_id and supabase_client:
        try:
            cm = ConversationManager(client=supabase_client)

            # Generate title with timestamp
            title = f"Chat - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            conversation_id = cm.create_conversation(
                user_id=user_id,
                project_id=project_id,
                title=title,
            )

            await cl.Message(
                content=f"💾 Conversation saved (ID: `{conversation_id}`)",
            ).send()
        except Exception as e:
            await cl.Message(
                content=f"⚠️ Failed to create conversation: {e}",
            ).send()

    # Create orchestrator
    # Use mocked agents during development to save costs (controlled by USE_MOCK_AGENTS env var)
    logger.info(f"Creating orchestrator with mock_agents={USE_MOCK_AGENTS}")
    orchestrator = create_orchestrator(mock_agents=USE_MOCK_AGENTS)

    # Store in session
    cl.user_session.set("orchestrator", orchestrator)
    cl.user_session.set("conversation_id", conversation_id)
    cl.user_session.set("user_id", user_id)
    cl.user_session.set("supabase_client", supabase_client)
    cl.user_session.set("project_id", project_id)
    cl.user_session.set("messages", [])

    # Final ready message
    await cl.Message(
        content="✨ Ready! Ask me anything about your project.",
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """
    Handle incoming user messages.

    - Save user message to database
    - Pass to orchestrator
    - Show status updates during processing
    - Save assistant response to database
    - Display response to user
    """
    # Get session data
    orchestrator = cl.user_session.get("orchestrator")
    conversation_id = cl.user_session.get("conversation_id")
    user_id = cl.user_session.get("user_id")
    supabase_client = cl.user_session.get("supabase_client")
    messages = cl.user_session.get("messages", [])

    # Save user message to database
    if conversation_id and supabase_client:
        try:
            cm = ConversationManager(client=supabase_client)
            cm.save_message(
                conversation_id=conversation_id,
                role="user",
                content=message.content,
            )
        except Exception as e:
            print(f"Warning: Failed to save user message: {e}")

    # Add user message to state
    messages.append(HumanMessage(content=message.content))

    # Show thinking status
    status_msg = cl.Message(content="🤔 Thinking...")
    await status_msg.send()

    # Log user message
    logger.info(f"[USER MESSAGE] {message.content}")

    # Invoke orchestrator
    try:
        state = {
            "messages": messages,
            "mock_agents": USE_MOCK_AGENTS,  # Controlled by env var
        }

        result = await orchestrator.ainvoke(state)

        # Update messages
        messages = result["messages"]
        cl.user_session.set("messages", messages)

        # Get last message (should be AI response)
        last_message = messages[-1]

        if isinstance(last_message, AIMessage):
            response_content = last_message.content
            logger.info(f"[AI RESPONSE] {response_content}")

            # Save assistant message to database
            if conversation_id and supabase_client:
                try:
                    cm = ConversationManager(client=supabase_client)
                    cm.save_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=response_content,
                    )
                except Exception as e:
                    print(f"Warning: Failed to save assistant message: {e}")

            # Update status message with response
            status_msg.content = response_content
            await status_msg.update()

        else:
            status_msg.content = "⚠️ Unexpected response type"
            await status_msg.update()

    except Exception as e:
        status_msg.content = f"❌ Error: {str(e)}"
        await status_msg.update()
        print(f"Error in orchestrator: {e}")
        import traceback
        traceback.print_exc()
