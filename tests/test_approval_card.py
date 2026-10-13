"""The approval card (server/approval_card.py): its title and text, the reply
mapping, and the input digest."""

import pytest

from server.approval_card import (
	APPROVAL_SUGGESTIONS, DEFAULT_DENY_MESSAGE, _code_span, decide_reply, hook_output, input_digest, render_card,
)


def test_digest_ignores_key_order_and_keeps_unicode():
	a = input_digest({"command": "echo h\u00e9llo", "description": "d"})
	b = input_digest({"description": "d", "command": "echo h\u00e9llo"})
	assert a == b
	assert a != input_digest({"command": "echo hello", "description": "d"})


@pytest.mark.parametrize("reply", ["Allow", "allow", " yes ", "Y", "approve", "Approved", "ok", "OKAY", "Yes.", "Yes !"])
def test_allow_replies(reply):
	assert decide_reply(reply) == {"behavior": "allow"}


@pytest.mark.parametrize("reply", ["Deny", "no", "N", "", "   "])
def test_deny_replies_use_the_default_message(reply):
	assert decide_reply(reply) == {"behavior": "deny", "message": DEFAULT_DENY_MESSAGE}


def test_free_text_denies_with_johns_words():
	assert decide_reply("  use the staging bucket instead ") == {
		"behavior": "deny", "message": "use the staging bucket instead",
	}


def test_hook_output_shape():
	assert hook_output({"behavior": "allow"}) == {
		"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}},
	}


def test_suggestions_and_default_message():
	assert APPROVAL_SUGGESTIONS == ["Allow", "Deny"]
	assert DEFAULT_DENY_MESSAGE == "John denied this from his phone."


def test_bash_card():
	title, text = render_card("Bash", {"command": "echo hi > x.txt", "description": "Write a marker"}, in_subagent=False)
	assert title == "Approve Bash?"
	assert text.split("\n")[0] == "Approve Bash: echo hi > x.txt"
	assert "```sh\necho hi > x.txt\n```" in text
	assert "\n\nWrite a marker\n\n" in text
	assert text.endswith("Reply Allow or Deny, or tell Claude what to do instead.")


def test_bash_summary_takes_the_first_line_and_cuts_it():
	_, text = render_card("Bash", {"command": "x" * 100 + "\nsecond line"}, in_subagent=False)
	assert text.split("\n")[0] == "Approve Bash: " + "x" * 77 + "..."


def test_subagent_marker():
	_, text = render_card("Bash", {"command": "ls"}, in_subagent=True)
	assert text.split("\n")[0] == "Approve Bash in a subagent: ls"


def test_write_card_shows_twenty_lines_and_counts_the_rest():
	content = "\n".join(f"line {i}" for i in range(25))
	_, text = render_card("Write", {"file_path": "C:/w/a.txt", "content": content}, in_subagent=False)
	assert text.split("\n")[0] == "Approve Write: C:/w/a.txt"
	assert "25 lines:" in text
	assert "line 19" in text
	assert "line 20" not in text
	assert "(5 more lines)" in text


def test_write_card_shows_the_path_in_a_code_span():
	_, text = render_card("Write", {"file_path": "C:/Work/s/server/__init__.py", "content": "x"}, in_subagent=False)
	assert "Path: `C:/Work/s/server/__init__.py`\n\n1 line:" in text


def test_write_card_counts_one_line_and_one_more_line():
	_, text = render_card("Write", {"file_path": "a", "content": "x"}, in_subagent=False)
	assert "1 line:" in text
	assert "more line" not in text
	content = "\n".join(f"line {i}" for i in range(21))
	_, text = render_card("Write", {"file_path": "a", "content": content}, in_subagent=False)
	assert "(1 more line)" in text


def test_edit_card_shows_the_path_in_a_code_span():
	_, text = render_card(
		"Edit", {"file_path": "C:\\Work\\proj\\_build\\x.py", "old_string": "a", "new_string": "b"}, in_subagent=False,
	)
	assert "Path: `C:\\Work\\proj\\_build\\x.py`\n\nReplace:" in text


def test_long_edit_old_string_is_cut():
	_, text = render_card("Edit", {"file_path": "a", "old_string": "z" * 2000, "new_string": "b"}, in_subagent=False)
	assert "\n... (500 more characters)" in text


def test_code_span_outlasts_a_backtick_in_the_text():
	assert _code_span("a`b") == "``a`b``"
	assert _code_span("plain") == "`plain`"
	assert _code_span("`edge") == "`` `edge ``"


def test_edit_card():
	_, text = render_card(
		"Edit", {"file_path": "C:/w/a.py", "old_string": "a = 1", "new_string": "a = 2"}, in_subagent=False,
	)
	assert text.split("\n")[0] == "Approve Edit: C:/w/a.py"
	assert "Replace:\n\n```\na = 1\n```\n\nWith:\n\n```\na = 2\n```" in text


def test_other_tools_show_their_input_as_json():
	_, text = render_card("mcp__atlassian__createJiraIssue", {"summary": "s"}, in_subagent=False)
	assert text.split("\n")[0] == "Approve mcp__atlassian__createJiraIssue"
	assert '```json\n{\n  "summary": "s"\n}\n```' in text


def test_long_input_is_cut():
	_, text = render_card("Bash", {"command": "y" * 2000}, in_subagent=False)
	assert "\n... (500 more characters)" in text


def test_fence_outlasts_backticks_in_the_content():
	_, text = render_card("Bash", {"command": "echo ```x```"}, in_subagent=False)
	assert "````sh\necho ```x```\n````" in text
