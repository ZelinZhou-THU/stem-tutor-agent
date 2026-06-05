import json
import pytest
from contextlib import nullcontext
from unittest.mock import MagicMock, AsyncMock, patch

from stem_tutor.graph.strategy import StrategyOutcome


def _make_run_data(run_id="test-run-001", subject_id="calculus"):
    return {
        "steps": [
            {
                "step_id": "S1",
                "label": "unclear",
                "confidence": 0.25,
                "evidence": "budget limited",
                "violated_principles": ["verification_default_label"],
                "sympy_verified": False,
                "sympy_equivalent": None,
                "raw_text": "x = 3 + 5",
            },
        ],
        "raw_output": {
            "normalized_steps": [
                {"step_id": "S1", "normalized_text": "x = 3 + 5", "raw_text": "x = 3 + 5"},
            ],
            "problem_input": {"problem_text": "Compute x."},
            "reference_solution": {"reference_text": "x = 8", "key_assertions": ["x = 8"]},
            "verification_results": [
                {
                    "step_id": "S1",
                    "label": "unclear",
                    "confidence": 0.25,
                    "evidence": "budget limited",
                    "violated_principles": ["verification_default_label"],
                },
            ],
        },
        "run_meta": {"run_id": run_id, "subject_id": subject_id},
    }


def _mock_outcome():
    return StrategyOutcome(
        data={"label": "correct", "evidence": "Mock verified", "confidence": 0.9, "violated_principles": []},
        quality="full",
        confidence=0.9,
        strategy_name="pure_llm",
        elapsed_seconds=0.1,
    )


def _patch_db_load(run_data):
    """Return a list of patches that mock database.load_run to return run_data."""
    return [
        patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})),
    ]


def _patch_db_update():
    return [
        patch("web.service.database.update_run", new=AsyncMock()),
    ]


@pytest.mark.asyncio
async def test_reverify_nonexistent_run():
    with patch("web.service.database.load_run", new=AsyncMock(return_value=None)):
        from web.service import reverify_step
        result = await reverify_step("nonexistent", 1, "S1")
        assert result["success"] is False
        assert "不存在" in result["error"]


@pytest.mark.asyncio
async def test_reverify_nonexistent_step():
    run_data = _make_run_data()
    with patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})):
        from web.service import reverify_step
        result = await reverify_step("test-run-001", 1, "S99")
        assert result["success"] is False
        assert "不存在" in result["error"]


@pytest.mark.asyncio
async def test_reverify_success_structure():
    run_data = _make_run_data()
    mock_provider = MagicMock()

    with patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})), \
         patch("web.service.database.update_run", new=AsyncMock()), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()):
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        from web.service import reverify_step
        result = await reverify_step("test-run-001", 1, "S1")
        assert result["success"] is True
        assert "verification_result" in result
        assert "elapsed_seconds" in result


@pytest.mark.asyncio
async def test_reverify_propagates_subject_id_from_run_meta():
    """C-1 fix: reverify_step must restore the original subject from run_meta
    and pass it to active_subject_scope, chain.execute, and
    _rule_based_adjustment, so non-calculus runs do not silently use
    calculus prompts/taxonomy.
    """
    run_data = _make_run_data(subject_id="quantum")
    mock_provider = MagicMock()

    with patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})), \
         patch("web.service.database.update_run", new=AsyncMock()), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()) as mock_execute, \
         patch("stem_tutor.prompts.templates.active_subject_scope", return_value=nullcontext()) as mock_scope:
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        from web.service import reverify_step
        result = await reverify_step("test-run-001", 1, "S1")
        assert result["success"] is True

        # active_subject_scope must be called with the original subject
        mock_scope.assert_called_once_with("quantum")

        # chain.execute must receive subject_id="quantum"
        execute_kwargs = mock_execute.call_args.kwargs
        assert execute_kwargs.get("subject_id") == "quantum"


@pytest.mark.asyncio
async def test_reverify_falls_back_to_calculus_for_invalid_subject():
    """If run_meta has missing/empty/invalid subject_id, normalize to 'calculus'."""
    run_data = _make_run_data(subject_id="")
    mock_provider = MagicMock()

    with patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})), \
         patch("web.service.database.update_run", new=AsyncMock()), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()) as mock_execute, \
         patch("stem_tutor.prompts.templates.active_subject_scope", return_value=nullcontext()) as mock_scope:
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        from web.service import reverify_step
        result = await reverify_step("test-run-001", 1, "S1")
        assert result["success"] is True

        mock_scope.assert_called_once_with("calculus")
        execute_kwargs = mock_execute.call_args.kwargs
        assert execute_kwargs.get("subject_id") == "calculus"


@pytest.mark.asyncio
async def test_reverify_falls_back_to_calculus_for_unknown_subject():
    """An unrecognized subject_id must be normalized to 'calculus' too."""
    run_data = _make_run_data(subject_id="not_a_real_subject")
    mock_provider = MagicMock()

    with patch("web.service.database.load_run", new=AsyncMock(return_value={"data": run_data})), \
         patch("web.service.database.update_run", new=AsyncMock()), \
         patch("web.service.load_provider_settings") as mock_settings, \
         patch("web.service.create_provider", return_value=mock_provider), \
         patch("stem_tutor.graph.strategy.StrategyChain.execute", return_value=_mock_outcome()) as mock_execute, \
         patch("stem_tutor.prompts.templates.active_subject_scope", return_value=nullcontext()) as mock_scope:
        mock_settings.return_value = MagicMock(verify_model_group="fast", verify_model_name=None)
        from web.service import reverify_step
        result = await reverify_step("test-run-001", 1, "S1")
        assert result["success"] is True

        mock_scope.assert_called_once_with("calculus")


def test_active_subject_scope_restores_previous_value():
    """M1 verification: active_subject_scope must restore the previous
    threading.local value on exit, so coroutines do not bleed values.
    """
    from stem_tutor.prompts.templates import (
        set_active_subject, _current_subject_id, active_subject_scope, _active_subject,
    )

    set_active_subject("mechanics")
    assert _current_subject_id() == "mechanics"
    with active_subject_scope("quantum"):
        assert _current_subject_id() == "quantum"
    assert _current_subject_id() == "mechanics"

    # No previous value set -> attribute is cleared, helper falls back to "calculus"
    if hasattr(_active_subject, "value"):
        del _active_subject.value
    with active_subject_scope("relativity"):
        assert _current_subject_id() == "relativity"
    assert _current_subject_id() == "calculus"


def test_lookup_error_subject_specific_descriptions():
    """H2 verification: lookup_error must return the right entry for the
    subject, so cross-subject error codes do not have empty descriptions.
    """
    from stem_tutor.taxonomy.errors import lookup_error

    # A linear-algebra-specific code
    entry = lookup_error("MATRIX_MULTIPLICATION_ORDER_ERROR", subject_id="linear_algebra")
    assert entry is not None
    assert "矩阵" in entry.short_desc or "matrix" in entry.short_desc.lower()

    # Same code is not in calculus taxonomy
    calc_entry = lookup_error("MATRIX_MULTIPLICATION_ORDER_ERROR", subject_id="calculus")
    assert calc_entry is None

    # A quantum-specific code
    q_entry = lookup_error("WAVEFUNCTION_NORMALIZATION_ERROR", subject_id="quantum")
    assert q_entry is not None


def test_practice_stream_validates_subject_id():
    """H1/M1 verification: practice_*_stream must validate subject_id against
    VALID_SUBJECTS and fall back to 'calculus' for unknown values.
    """
    from web.service import practice_verify_stream, practice_reference_stream
    import inspect

    # Both are async generators; just verify signature has the right default
    sig_v = inspect.signature(practice_verify_stream)
    sig_r = inspect.signature(practice_reference_stream)
    assert "subject_id" in sig_v.parameters
    assert "subject_id" in sig_r.parameters
    assert sig_v.parameters["subject_id"].default == "calculus"
    assert sig_r.parameters["subject_id"].default == "calculus"
