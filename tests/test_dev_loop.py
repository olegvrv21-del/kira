"""Smoke tests for dev_loop tool wiring (without invoking the model)."""
import json
from pathlib import Path


def test_dev_loop_tool_spec_present():
    p = Path(__file__).resolve().parent.parent / "agent_tool_specs.json"
    specs = json.loads(p.read_text())
    names = [s["toolSpecification"]["name"] for s in specs]
    assert "dev_loop" in names
    d = next(s["toolSpecification"] for s in specs
             if s["toolSpecification"]["name"] == "dev_loop")
    props = d["inputSchema"]["json"]["properties"]
    assert "task" in props
    assert d["inputSchema"]["json"]["required"] == ["task"]


def test_dev_loop_in_host_mode_is_unsupported():
    import agent_tools
    # In host mode the tool must be present but return the sandbox-required
    # error so callers don't crash.
    assert "dev_loop" in agent_tools.TOOLS
    status, out, _ = agent_tools.run_tool("dev_loop", {"task": "x"}, "/tmp")
    assert status == "error"
    assert "sandbox" in out.lower() or "not supported" in out.lower()


def test_dev_loop_handler_exists():
    import agent_runtime
    assert hasattr(agent_runtime, "_handle_dev_loop")


def test_subagent_specs_exclude_use_subagent_only():
    import agent_runtime
    sub_names = [t["toolSpecification"]["name"] for t in agent_runtime.SUBAGENT_TOOL_SPECS]
    assert "use_subagent" not in sub_names
    # dev_loop CAN call subagent_silent internally, but the subagent itself
    # should also have dev_loop available (recursive composition is fine
    # because dev_loop in turn calls _run_subagent_silent, not use_subagent).
    assert "dev_loop" in sub_names
