"""Unit tests for :mod:`src.session_manager.channel_switch` (moto DynamoDB)."""

from __future__ import annotations

from unittest import mock

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from src.common.errors import SessionNotFoundError, ValidationError
from src.session_manager.channel_switch import (
    MAX_TURNS,
    SessionContextManager,
    handler,
)
from src.session_manager.history import ConversationTurn

TABLE_NAME = "customer-history-test"


@pytest.fixture()
def history_table():  # type: ignore[no-untyped-def]
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        table = ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "customerId", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "customerId", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield table


def _turn(i: int, role: str = "user", channel: str = "chat") -> ConversationTurn:
    return ConversationTurn(
        role=role,
        text=f"text-{i}",
        timestamp=f"2026-06-03T10:0{i % 10}:00+00:00",
        contact_id="c1",
        channel=channel,
    )


# --------------------------------------------------------------------------- #
# SessionContextManager.get
# --------------------------------------------------------------------------- #
async def test_get_returns_context(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    await mgr.update("c1", _turn(0, role="user"))
    await mgr.update("c1", _turn(1, role="assistant"))

    ctx = await mgr.get("c1")
    assert ctx.contact_id == "c1"
    assert len(ctx.turns) == 2
    assert ctx.turns[0].role == "user"
    assert ctx.channel == "chat"


async def test_get_missing_raises_session_not_found(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    with pytest.raises(SessionNotFoundError):
        await mgr.get("nope")


async def test_get_empty_contact_id_raises(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    with pytest.raises(ValidationError):
        await mgr.get("   ")


# --------------------------------------------------------------------------- #
# SessionContextManager.update
# --------------------------------------------------------------------------- #
async def test_update_appends_turn(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    await mgr.update("c1", _turn(0))
    await mgr.update("c1", _turn(1))

    item = history_table.get_item(
        Key={"customerId": "c1", "sk": "SESSION#c1"}
    )["Item"]
    assert len(item["turns"]) == 2
    assert item["sk"] == "SESSION#c1"
    assert "updatedAt" in item
    assert "expiresAt" in item


async def test_update_enforces_max_turns(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    for i in range(MAX_TURNS + 5):
        await mgr.update("c1", _turn(i))

    ctx = await mgr.get("c1")
    assert len(ctx.turns) == MAX_TURNS
    # Oldest dropped: newest turn must be present.
    assert ctx.turns[-1].text == f"text-{MAX_TURNS + 4}"
    # The first 5 turns are evicted.
    assert ctx.turns[0].text == "text-5"


async def test_update_sets_channel_from_turn(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    await mgr.update("c1", _turn(0, channel="chat"))
    await mgr.update("c1", _turn(1, channel="voice"))
    ctx = await mgr.get("c1")
    assert ctx.channel == "voice"


# --------------------------------------------------------------------------- #
# SessionContextManager.summarize
# --------------------------------------------------------------------------- #
async def test_summarize_format(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    await mgr.update("c1", _turn(0, role="user"))
    await mgr.update("c1", _turn(1, role="assistant"))

    summary = await mgr.summarize("c1", last_n=5)
    lines = summary.split("\n")
    assert lines[0] == "顧客: text-0"
    assert lines[1] == "AI: text-1"


async def test_summarize_last_n_limits(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    for i in range(6):
        await mgr.update("c1", _turn(i))
    summary = await mgr.summarize("c1", last_n=2)
    assert len(summary.split("\n")) == 2
    assert "text-5" in summary
    assert "text-4" in summary
    assert "text-0" not in summary


async def test_summarize_missing_raises(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    with pytest.raises(SessionNotFoundError):
        await mgr.summarize("nope")


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #
async def test_handler_switches_with_context(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    await mgr.update("c1", _turn(0, role="user"))
    await mgr.update("c1", _turn(1, role="assistant"))

    event = {"contactId": "c1", "channelFrom": "chat", "channelTo": "voice"}
    with mock.patch(
        "src.session_manager.channel_switch.SessionContextManager",
        return_value=mgr,
    ):
        result = await handler(event, None)

    assert result["channel_from"] == "chat"
    assert result["channel_to"] == "voice"
    assert result["turn_count"] == 2
    assert "顧客: text-0" in result["handover_summary"]
    assert "AI: text-1" in result["handover_summary"]


async def test_handler_new_session_empty_summary(history_table) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    event = {"contactId": "fresh", "channelFrom": "voice", "channelTo": "chat"}
    with mock.patch(
        "src.session_manager.channel_switch.SessionContextManager",
        return_value=mgr,
    ):
        result = await handler(event, None)

    assert result["handover_summary"] == ""
    assert result["turn_count"] == 0
    assert result["channel_to"] == "chat"


async def test_handler_missing_contact_id_raises(history_table) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        await handler({"channelFrom": "voice", "channelTo": "chat"}, None)


# --------------------------------------------------------------------------- #
# Property-based: summarize always contains each turn's text
# --------------------------------------------------------------------------- #
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    texts=st.lists(
        st.text(
            alphabet=st.characters(blacklist_characters="\n"),
            min_size=1,
            max_size=20,
        ),
        min_size=1,
        max_size=8,
    )
)
async def test_summarize_contains_every_turn_text(history_table, texts) -> None:  # type: ignore[no-untyped-def]
    mgr = SessionContextManager(table=history_table)
    cid = "pbt"
    # Reset any state from a previous example.
    history_table.delete_item(Key={"customerId": cid, "sk": f"SESSION#{cid}"})
    for i, text in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        turn = ConversationTurn(
            role=role,
            text=text,
            timestamp=f"2026-06-03T10:00:{i:02d}+00:00",
            contact_id=cid,
            channel="chat",
        )
        await mgr.update(cid, turn)

    summary = await mgr.summarize(cid, last_n=len(texts))
    kept = texts[-MAX_TURNS:]
    for text in kept:
        assert text in summary
