from pathlib import Path

from google.adk.agents.llm_agent import Agent

from .tools.artifacts import artifact_tools
from .tools.sandbox import bind_sandbox, report_reset, sandbox_tools

AGENT_DIR = Path(__file__).parent

# Kept in a file so the agent's behaviour can be tuned without touching code.
INSTRUCTION = (AGENT_DIR / "instruction.md").read_text(encoding="utf-8").strip()


async def report_error(tool, args, tool_context, error):
    """Show a failed tool call to the model instead of ending the turn."""
    return {"status": "error", "error": f"{type(error).__name__}: {error}"}


root_agent = Agent(
    model='gemini-3.8-flash',
    name='root_agent',
    description="Argo - Aseem's personal agent",
    instruction=INSTRUCTION,
    tools=[*sandbox_tools, *artifact_tools],
    before_tool_callback=bind_sandbox,  # Points sandbox tools at this conversation.
    after_tool_callback=report_reset,
    on_tool_error_callback=report_error,
)
