from pathlib import Path

from google.adk.agents.llm_agent import Agent
from google.adk.skills import load_skill_from_dir

# Kept in a file so the agent's behaviour can be tuned without touching code.
INSTRUCTION = (Path(__file__).parent / "instruction.md").read_text(encoding="utf-8").strip()

root_agent = Agent(
    model='gemini-3.8-flash',
    name='root_agent',
    description="Argo - Aseem's personal agent",
    instruction=INSTRUCTION,
)
