"""Smoke tests for prompts.py – verify templates render without error."""

from prompts import (
    FORENSIC_QUERY_SYSTEM_PROMPT,
    FORENSIC_QUERY_USER_TEMPLATE,
    RESTORE_BATCH_USER_TEMPLATE,
    RESTORE_SYSTEM_PROMPT,
    RESTORE_USER_TEMPLATE,
)


def test_restore_system_prompt_non_empty():
    assert len(RESTORE_SYSTEM_PROMPT) > 100


def test_restore_user_template_renders():
    rendered = RESTORE_USER_TEMPLATE.format(fragment="some noisy text")
    assert "some noisy text" in rendered
    assert "MEMORY FRAGMENT" in rendered


def test_restore_batch_template_renders():
    rendered = RESTORE_BATCH_USER_TEMPLATE.format(content="chunk1\n---CHUNK---\nchunk2")
    assert "chunk1" in rendered
    assert "MEMORY DATA" in rendered


def test_forensic_query_system_prompt_non_empty():
    assert "createdTime" in FORENSIC_QUERY_SYSTEM_PROMPT


def test_forensic_query_user_template_renders():
    rendered = FORENSIC_QUERY_USER_TEMPLATE.format(
        memory_data='{"text":"hi","from":"A","createdTime":1234}',
        query="List all messages.",
    )
    assert "List all messages." in rendered
    assert "MEMORY DATA" in rendered
