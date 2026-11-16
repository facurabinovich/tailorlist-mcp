"""The tour script: its tool calls are real, and the prompt and the tool say the same thing."""
import re

import anyio

from tailorlist_mcp import queries, server, tour


def _registered():
    async def go():
        tools = await server.mcp.list_tools()
        return {t.name for t in (tools.tools if hasattr(tools, "tools") else tools)}
    return anyio.run(go)


def test_every_tool_named_in_the_script_is_registered():
    used = set(re.findall(r"\b([a-z_]+)\(", tour.tour_text()))
    assert {"collection_stats", "compare_playlists", "fun_facts", "what_can_i_do", "explain"} <= used
    assert used <= _registered()


def test_collection_stats_calls_use_real_parameters_and_dimensions():
    text = tour.tour_text()
    for group in re.findall(r'group_by="(\w+)"', text):
        assert group in queries.DIMENSIONS
    for call in re.findall(r"collection_stats\(([^)]*)\)", text):
        assert set(re.findall(r"(\w+)=", call)) <= {"group_by", "split_by", "metrics", "filters", "limit"}


def test_prompt_and_tool_return_the_same_text():
    assert server.tour() == tour.tour_text() == server.tour_prompt()


def test_the_prompt_is_registered():
    async def go():
        prompts = await server.mcp.list_prompts()
        return {p.name for p in (prompts.prompts if hasattr(prompts, "prompts") else prompts)}
    assert {"start", "tour"} <= anyio.run(go)


def test_chapters_and_chart_style():
    text = tour.tour_text()
    assert 6 <= len(tour.CHAPTERS) <= 8
    assert len(re.findall(r"^\d+\. ", text, re.M)) == len(tour.CHAPTERS)
    assert 'explain("chart style")' in text
    assert "next?" in text and "artifact" in text
