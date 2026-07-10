"""Tests for the 16 canonical states."""

from verdx.contracts.states import State


def test_has_16_states():
    assert len(State) == 16


def test_terminal_states():
    terminals = [s for s in State if s.is_terminal]
    assert set(terminals) == {
        State.Integrated,
        State.Abandoned,
        State.Superseded,
        State.Reverted,
    }


def test_active_states():
    active = [s for s in State if not s.is_terminal]
    assert len(active) == 12


def test_human_gate_states():
    gates = [s for s in State if s.is_human_gate]
    assert set(gates) == {
        State.AwaitingApproval,
        State.AwaitingReview,
        State.OverridePending,
        State.NeedsRereview,
    }


def test_building_is_not_human_gate():
    assert not State.Building.is_human_gate


def test_integrated_is_terminal():
    assert State.Integrated.is_terminal


def test_clarifying_is_not_terminal():
    assert not State.Clarifying.is_terminal


def test_state_string_values():
    for state in State:
        assert state.value == state.name
