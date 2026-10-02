"""
The same tools as an MCP server, for teams that already run a Slack agent that can use MCP
(Claude in Slack, or any MCP-capable agent). Their agent handles Slack; this server gives it the
close data, the owners file and the question log.

    python -m agent mcp            # stdio, for a local MCP client
    python -m agent mcp --http     # streamable HTTP on 127.0.0.1:8765/mcp, behind your own auth

Point the host agent's instructions at agent/RULES.md (also served here as the `close_rules` prompt).
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import tools as T


def build(cfg_path=None, host="127.0.0.1", port=8765):
    ag = T.Agent(cfg_path)
    mcp = FastMCP("close-agent", host=host, port=port)
    ctx = {}

    @mcp.prompt()
    def close_rules() -> str:
        """How the close agent must behave: answer from data, tag owners, log everything."""
        from .brain import system_prompt
        return system_prompt(ag)

    @mcp.tool()
    def close_overview() -> dict:
        """Start here: close month, checks needing attention, last 3 months, forecast, questions waiting on owners."""
        return T.close_overview(ag, ctx)

    @mcp.tool()
    def list_tables(names: list[str] | None = None) -> dict:
        """Tables and columns you can query in the close warehouse."""
        return T.list_tables(ag, ctx, names)

    @mcp.tool()
    def run_sql(sql: str) -> dict:
        """One read-only DuckDB SELECT on the close warehouse (max 200 rows)."""
        return T.run_sql(ag, ctx, sql)

    @mcp.tool()
    def trace_cell(sheet: str = "", cell: str = "", label: str = "") -> dict:
        """Follow a workbook cell back to the Data_ sheet, table and SQL file it comes from. Or find a cell by label."""
        return T.trace_cell(ag, ctx, sheet or None, cell or None, label or None)

    @mcp.tool()
    def read_assumption(number: int) -> dict:
        """Read assumption N (1-14) from LOGIC_REVIEW.md."""
        return T.read_assumption(ag, ctx, number)

    @mcp.tool()
    def find_owner(area: str = "", check_no: int | None = None, assumption_no: int | None = None, text: str = "") -> dict:
        """Who owns a topic, and the Slack mention to tag them with."""
        return T.find_owner(ag, ctx, area or None, check_no, assumption_no, text or None)

    @mcp.tool()
    def search_log(text: str, area: str = "") -> dict:
        """Search past questions, answers and owner decisions. Do this before answering or escalating."""
        return T.search_log(ag, ctx, text, area)

    @mcp.tool()
    def log_question(question: str, asked_by: str, channel: str = "", thread_link: str = "",
                     area: str = "", problem: str = "", related_to: str = "") -> dict:
        """Open a log entry for a new question. asked_by: name and Slack mention of the asker."""
        c = {"asker": asked_by, "channel": channel, "thread_link": thread_link}
        return T.log_question(ag, c, question, area, problem, related_to)

    @mcp.tool()
    def record_answer(question_id: str, answer: str, evidence: str = "") -> dict:
        """Record the answer you are posting and its evidence (SQL or cells)."""
        return T.record_answer(ag, {}, question_id, answer, evidence)

    @mcp.tool()
    def escalate(question_id: str, area: str, reason: str, question_for_owner: str, agent_answer_so_far: str = "") -> dict:
        """Ask an area's owner. reason: judgment_call | sources_disagree | contradicts_prior_decision |
        unexplained_flag | would_change_numbers | data_not_available. Returns the mention to post."""
        return T.escalate(ag, ctx, question_id, area, reason, question_for_owner, agent_answer_so_far)

    @mcp.tool()
    def record_owner_answer(question_id: str, answer: str, answered_by: str, decision: str = "", change_type: str = "none") -> dict:
        """Record an owner's reply and what it changes (none | explanation_only | config_change |
        input_cell_change | source_data_fix | billing_correction | process_change)."""
        return T.record_owner_answer(ag, {}, question_id, answer, answered_by, decision, change_type)

    @mcp.tool()
    def record_outcome(question_id: str, worked: str, note: str = "", reported_by: str = "") -> dict:
        """Record whether the answer solved the problem: yes | no | pending."""
        return T.record_outcome(ag, {"asker": reported_by}, question_id, worked, note)

    return mcp


def main(cfg_path=None, http=False):
    mcp = build(cfg_path)
    mcp.run(transport="streamable-http" if http else "stdio")


if __name__ == "__main__":
    main(Path(__file__).parent / "agent.yaml")
