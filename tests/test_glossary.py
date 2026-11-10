"""explain(term), the start prompt and the size of the server instructions."""
import anyio

from tailorlist_mcp import glossary, server


def test_explain_finds_terms_and_aliases():
    assert glossary.explain("listening age").startswith("Listening age:")
    assert "Median era" in glossary.explain("median era")
    fam = glossary.explain("families")
    assert fam.startswith("Genre families:") and "Rock = rock, punk" in fam
    assert "Exact duplicate" in glossary.explain("different versions")


def test_explain_lists_terms_when_nothing_matches():
    assert glossary.explain("").startswith("Terms: unique tracks, popularity")
    assert "Nothing matched 'zzz'" in glossary.explain("zzz")
    assert glossary.explain("all").count("\n\n") == len(glossary.TERMS) - 1


def test_instructions_stay_short_enough_for_claude_code():
    # Claude Code cut the old 6,875-character instructions; keep them well under that.
    assert len(server.INSTRUCTIONS) < 3000
    assert "music curator" in server.INSTRUCTIONS and "explain(term)" in server.INSTRUCTIONS


def test_start_prompt_is_registered():
    async def go():
        prompts = await server.mcp.list_prompts()
        return [p.name for p in (prompts.prompts if hasattr(prompts, "prompts") else prompts)]
    assert "start" in anyio.run(go)
    assert "Build better playlists" in server.start()
