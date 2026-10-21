"""The ADK investigator must build with every scoped tool and expose no tenant parameter."""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from app.agent.adk_tools import ALL_TOOLS
from app.agent.prompt import INSTRUCTION


def test_agent_builds_and_tools_take_scope_from_context():
    agent = LlmAgent(name="investigator", model="gemini-2.5-flash", instruction=INSTRUCTION, tools=ALL_TOOLS)
    assert len(agent.tools) == 9
    for fn in ALL_TOOLS:
        decl = FunctionTool(fn)._get_declaration()
        params = set((decl.parameters.properties or {}).keys()) if decl.parameters else set()
        assert "tool_context" not in params
        assert not params & {"org_id", "incident_id", "tenant", "organization_id"}, (fn.__name__, params)
