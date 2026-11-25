"""Tests for the speaker-model measurement harness.

The harness decides which model ships and what its thresholds are, so a bug
in it is a bug in every attribution the product later makes. It had one: the
two thresholds were derived from the wrong distributions, which on cleanly
separated data produced an owner threshold hugging the impostor scores and no
uncertain band - so an ambiguous clip would have been called "confidently
you". These tests exist so that cannot come back.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "measure_speaker_models",
    Path(__file__).resolve().parent.parent / "scripts" / "measure_speaker_models.py",
)
assert _spec and _spec.loader
measure = importlib.util.module_from_spec(_spec)
sys.modules["measure_speaker_models"] = measure
_spec.loader.exec_module(measure)


# Cleanly separated, as a good model on good audio would be.
CLEAN_OWNER = [0.92, 0.93, 0.94, 0.95, 0.96]
CLEAN_OTHER = [0.01, 0.03, 0.05, 0.07, 0.09]

# Badly overlapping, as a wrong model or bad audio would be.
OVERLAP_OWNER = [0.40, 0.50, 0.55, 0.60, 0.70]
OVERLAP_OTHER = [0.35, 0.45, 0.52, 0.58, 0.65]


def test_each_threshold_comes_from_the_right_distribution() -> None:
    """The bug this file exists for.

    OWNER_THRESHOLD must sit near the bottom of the OWNER scores, and
    OTHER_THRESHOLD near the top of the IMPOSTOR scores. Swapping them puts
    the owner boundary down among the impostors.
    """
    owner_t = measure.owner_threshold_from(CLEAN_OWNER, 0.05)
    other_t = measure.other_threshold_from(CLEAN_OTHER, 0.01)

    assert owner_t == pytest.approx(min(CLEAN_OWNER), abs=1e-3)
    assert other_t == pytest.approx(max(CLEAN_OTHER), abs=1e-3)
    assert owner_t > other_t, "the uncertain band is inverted"


def test_clean_separation_leaves_a_wide_uncertain_band() -> None:
    """A clip landing in the gap must resolve to "unknown", not to the user."""
    owner_t = measure.owner_threshold_from(CLEAN_OWNER, 0.05)
    other_t = measure.other_threshold_from(CLEAN_OTHER, 0.01)

    assert owner_t - other_t > 0.5
    midpoint = (owner_t + other_t) / 2
    assert other_t <= midpoint < owner_t, "midpoint should be uncertain"


def test_the_owner_threshold_is_never_down_among_the_impostors() -> None:
    owner_t = measure.owner_threshold_from(CLEAN_OWNER, 0.05)
    assert owner_t > max(CLEAN_OTHER)


def test_overlapping_distributions_invert_rather_than_pretend() -> None:
    """Honest failure: with real overlap there is no conservative pair.

    The caller detects `other_t > owner_t` and falls back to the EER point
    with a warning, instead of emitting thresholds that look principled.
    """
    owner_t = measure.owner_threshold_from(OVERLAP_OWNER, 0.05)
    other_t = measure.other_threshold_from(OVERLAP_OTHER, 0.01)
    assert other_t > owner_t


def test_eer_is_zero_when_separation_is_clean() -> None:
    eer, threshold = measure.equal_error_rate(CLEAN_OWNER, CLEAN_OTHER)
    assert eer == pytest.approx(0.0)
    assert max(CLEAN_OTHER) < threshold <= min(CLEAN_OWNER)


def test_eer_is_substantial_when_distributions_overlap() -> None:
    eer, _ = measure.equal_error_rate(OVERLAP_OWNER, OVERLAP_OTHER)
    assert eer > 0.15, "an overlapping model must not score as usable"


def test_eer_is_symmetric_in_its_error_definition() -> None:
    """Swapping the populations should mirror, not change magnitude wildly."""
    forward, _ = measure.equal_error_rate(CLEAN_OWNER, CLEAN_OTHER)
    assert forward == pytest.approx(0.0)


def test_centroid_is_normalised() -> None:
    result = measure.centroid([[1.0, 0.0], [0.0, 1.0]])
    norm = sum(value * value for value in result) ** 0.5
    assert norm == pytest.approx(1.0)


def test_centroid_refuses_vectors_that_cancel_out() -> None:
    with pytest.raises(SystemExit):
        measure.centroid([[1.0, 0.0], [-1.0, 0.0]])


def test_collect_ignores_non_audio_and_is_ordered(tmp_path: Path) -> None:
    (tmp_path / "b.m4a").write_bytes(b"x")
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "sub").mkdir()
    found = [path.name for path in measure.collect(tmp_path)]
    assert found == ["a.wav", "b.m4a"]


def test_collect_on_a_missing_folder_is_empty_not_an_error(
    tmp_path: Path,
) -> None:
    assert measure.collect(tmp_path / "nope") == []


def test_only_the_fake_driver_is_wired_up() -> None:
    """Guards the deliberate absence.

    If someone adds a real driver, this test should fail and be updated
    alongside `factory.py` - which is where the measurement discipline is
    written down.
    """
    driver = measure.build_driver("fake")
    assert driver.model_id == "fake-embed-v1"
    with pytest.raises(SystemExit):
        measure.build_driver("titanet")
