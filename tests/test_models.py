"""Tests for the dataclass models and their JSON round-tripping."""

from agent_board.models import Agent, Channel, Event


def test_agent_round_trip_ignores_unknown_keys():
    data = {"id": "a", "name": "Alice", "blurb": "hi", "future_field": 123}
    agent = Agent.from_dict(data)
    assert agent.id == "a"
    assert agent.name == "Alice"
    restored = Agent.from_dict(agent.to_dict())
    assert restored == agent


def test_channel_round_trip():
    channel = Channel(id="c1", topic="sync", members=["a", "b"], created="t", status="open")
    assert Channel.from_dict(channel.to_dict()) == channel


def test_event_round_trip_with_artifact():
    event = Event(
        seq=1,
        ts="t",
        author="a",
        type="artifact",
        text="see file",
        artifact={"id": "x", "name": "f.txt", "size": 10},
    )
    assert Event.from_dict(event.to_dict()) == event


def test_event_defaults():
    event = Event(seq=2, ts="t", author="a", type="message")
    assert event.text is None
    assert event.artifact is None
