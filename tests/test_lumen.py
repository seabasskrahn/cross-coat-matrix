"""LUMEN: on the roster with its role undecided, reports to STEWARD, never routed work automatically,
no powerful permissions, default brain, shown on the dashboard's team/brains line."""
import json
from pathlib import Path

import pytest

from matrix import agents, brain_switch as bs, config, envelope as env, graph as g, llm, names, splitter
from matrix import keeper_dashboard as kd

ROOT = Path(__file__).resolve().parent.parent


def test_lumen_on_roster_with_role_undecided():
    role, boss = agents.ROSTER_ONLY["LUMEN"]
    assert role.startswith("Role undecided") and "Seabass" in role
    assert boss == "STEWARD"
    assert env.roster()["LUMEN"] == role
    assert agents.ROLE_LABELS["LUMEN"] == "role undecided"
    # every existing role label is still there
    for name in ["SUNDAY", "STEWARD", "BEZEL", "TAPER", "ARMOR", "DEDUCT", "AUDIT", "MARGIN", "LEDGER",
                 "FINISH", "VECTOR"]:
        assert name in agents.ROLE_LABELS


def test_lumen_in_names_file_and_defaults():
    data = json.loads((ROOT / "matrix_names.json").read_text(encoding="utf-8"))
    entry = [a for a in data["agents"] if a["id"] == "LUMEN"][0]
    assert entry["reports_to"] == "STEWARD" and entry["description"].startswith("Role undecided")
    assert [a["id"] for a in names.defaults()["agents"]][-1] == "LUMEN"


def test_lumen_is_never_routed_work_automatically():
    assert "LUMEN" not in agents.SENIOR_STAFF and "LUMEN" not in agents.SPECIALISTS
    assert all("LUMEN" not in specs for specs in agents.DELEGATES.values())
    assert "LUMEN" not in splitter.SPECIALIST_KEYWORDS and "LUMEN" not in splitter.SENIOR_OF
    # the task text even says "lumen" / "build the matrix": still never picked
    for text in ["LUMEN please build the matrix", "improve the matrix code and write code", "quote the reno"]:
        tasks, _notes = splitter.split(text)
        assert all((t.get("agent") or {}).get("name") != "LUMEN" for t in tasks)
        state = g.sunday({"message": text})
        assert state["senior"] != "LUMEN"
        picked = g.make_senior(state["senior"])({"message": text, "tasks": state["tasks"]})
        assert picked["specialist"] != "LUMEN"
    assert env.route({}, 0, []) == ("STEWARD", "senior staff", "senior fallback")


def test_lumen_has_no_powerful_permissions():
    assert agents.permissions("LUMEN") == ()
    assert "LUMEN" not in agents.ALWAYS_OUTWARD
    assert "write_code" in agents.POWERFUL_PERMISSIONS
    assert g.detect_outward("LUMEN", "just thinking about it") is None


def test_lumen_uses_the_default_brain(monkeypatch):
    assert "LUMEN" not in config.AGENT_BRAINS
    monkeypatch.delenv("BRAIN_LUMEN", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert llm.brain_for("LUMEN") == llm.default_brain()


def test_lumen_shown_on_dashboard_team_line():
    info = {"main": "gemini", "bezel": "gemini", "bezel_source": ".env",
            "agents": bs.resolved({"LLM_PROVIDER": "gemini", "GOOGLE_API_KEY": "x"}), "worker": None}
    lumen = [a for a in info["agents"] if a["agent"] == "LUMEN"][0]
    assert lumen["brain"].startswith("gemini:") and not lumen["pinned"] and lumen["note"] == "role undecided"
    page = kd.render_list([], brain=info)
    assert "LUMEN gemini:" in page and "role undecided" in page


def test_chat_hides_lumen_name():
    import chat
    assert "LUMEN" not in chat.clean("[LUMEN mock] hello")
