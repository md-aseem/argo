from pathlib import Path

from google.adk.agents.llm_agent import Agent
from google.adk.skills import load_skills_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from .tools.artifacts import artifact_tools

AGENT_DIR = Path(__file__).parent

# Kept in a file so the agent's behaviour can be tuned without touching code.
INSTRUCTION = (AGENT_DIR / "instruction.md").read_text(encoding="utf-8").strip()

skill_toolset = SkillToolset(
    skills=load_skills_from_dir(AGENT_DIR / "skills"),
)

root_agent = Agent(
    model='gemini-3.8-flash',
    name='root_agent',
    description="Argo - Aseem's personal agent",
    instruction=INSTRUCTION,
    tools=[skill_toolset, *artifact_tools],
)
