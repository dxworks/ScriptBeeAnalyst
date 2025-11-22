import chainlit as cl
from langchain_core.messages import HumanMessage, SystemMessage
from main import app, AgentState

persona_context = """You are the Oracle Agent. Your purpose is to analyze the development status and history
    of software projects for debugging, metrics, and reporting.

    You have read-only access to project data via the `query_server` tool. That server provides aggregated
    project artifacts such as static-analysis results, git history, GitHub activity, and issue-tracker data.

    Rules:
    1. When a user question requires project-specific data, ALWAYS call the `query_server` tool.
    2. The format of the parameter query fo the tool `query_server` MUST be in plain text, what exactly interests you 
        from the server, you can, if needed redirect the user input whole question to this function.
    3. Treat `query_server` responses as factual input; validate unexpected values and surface anomalies.
    4. Provide a short, human-readable answer and a concise reasoning summary describing:
       - which tool(s) you called,
       - what query you sent,
       - key outputs from the tool(s),
       - and your final confidence level (low/medium/high).
    5. Provide a brief, **reasoning summary**
    6. Be concise, factual, and explicit about any assumptions you made. If you could not answer, explain why and recommend next steps.
    """

shared_state: AgentState = {
    "messages": [SystemMessage(content=persona_context)],
}

@cl.on_chat_start
async def on_chat_start():
    await cl.Message(content="🧠 **Oracle Agent** ready! Ask me about your project’s metrics or history.").send()

@cl.on_message
async def on_message(message: cl.Message):
    global shared_state
    user_msg = HumanMessage(content=message.content)
    shared_state["messages"].append(user_msg)

    thought_process = cl.Message

    result = await app.ainvoke(shared_state)
    shared_state.update(result)

    if "messages" in result and result["messages"]:
        response = result["messages"][-1].content
    else:
        response = "⚠️ No response."

    await cl.Message(content=response).send()
