"""A registered model must load on a machine that did not train it.

`artifact_path` is a path on the filesystem of whichever process ran the
training. The hourly pregame job reissues predictions *without* retraining and
runs on a fresh GitHub runner, so it had a registry row pointing at a file that
had only ever existed somewhere else, and it failed every hour with

    FileNotFoundError: artifacts/models/jerry_logistic/v1/model.pkl

The prediction timeline the job exists to accumulate was therefore never
accumulating. These tests are about the shape of that bug rather than the fix:
loading must not depend on where the artifact was written.
"""

from __future__ import annotations

import hashlib
import pickle

import pytest

from app.core.errors import ModelNotFoundError
from app.modeling.logistic import LogisticWinModel
from app.modeling.registry import _load_registered, load_artifact_bytes


class _Version:
    """The three fields the loader reads, without needing a database."""

    def __init__(self, blob, path, sha256, name="jerry_logistic", version="v1"):
        self.artifact_blob = blob
        self.artifact_path = path
        self.artifact_sha256 = sha256
        self.name = name
        self.version = version


def _payload() -> tuple[bytes, str]:
    model = LogisticWinModel(feature_names=["a", "b"], C=0.5)
    blob = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    return blob, hashlib.sha256(blob).hexdigest()


def test_a_stored_artifact_loads_without_the_file_it_was_written_to():
    """The whole point. The path here does not exist and must not be consulted."""
    blob, digest = _payload()
    version = _Version(blob, "/nonexistent/never/written/here/model.pkl", digest)

    model = _load_registered(version)

    assert model.feature_names == ["a", "b"]
    assert model.C == 0.5


def test_a_tampered_artifact_is_refused_rather_than_executed():
    """Unpickling runs whatever the payload says to run.

    The digest has been recorded since the registry was first written, which
    makes checking it free — and a row that no longer matches what was
    registered is exactly the row that must not be loaded.
    """
    blob, digest = _payload()
    tampered = blob + b"\x00"

    with pytest.raises(ModelNotFoundError, match="does not match its recorded digest"):
        load_artifact_bytes(tampered, digest)


def test_a_row_with_no_digest_still_loads():
    """Rows registered before the digest was recorded are not locked out."""
    blob, _ = _payload()
    assert load_artifact_bytes(blob, None).feature_names == ["a", "b"]


def test_a_path_only_row_still_loads_where_the_file_is(tmp_path):
    """Backward compatibility: a row registered before the column existed."""
    blob, digest = _payload()
    path = tmp_path / "model.pkl"
    path.write_bytes(blob)

    model = _load_registered(_Version(None, str(path), digest))

    assert model.feature_names == ["a", "b"]


def test_a_path_only_row_on_the_wrong_machine_says_so(tmp_path):
    """The failure a reader actually hit, reported as what it is.

    A bare `FileNotFoundError` naming a relative directory tells whoever reads
    the log nothing about why a directory they have never had is being opened.

    The path is a file inside `tmp_path` that is deliberately never written.
    Writing the real relative path here made this test pass by accident on the
    machine that had trained the model — which is the entire bug, reproduced
    inside its own test.
    """
    version = _Version(None, str(tmp_path / "models" / "v1" / "model.pkl"), None)

    with pytest.raises(ModelNotFoundError, match="not on this machine"):
        _load_registered(version)


def test_a_row_with_neither_is_distinguished_from_one_with_a_missing_file():
    version = _Version(None, None, None)
    with pytest.raises(ModelNotFoundError, match="neither stored artifact bytes"):
        _load_registered(version)


def test_registering_a_model_captures_its_bytes():
    """`save_artifact` must hand back the payload, not only where it went.

    A path is not portable and the bytes are; if this ever returns two values
    again the registry silently goes back to storing only the path.
    """
    from app.modeling.registry import save_artifact

    path, digest, payload = save_artifact(
        LogisticWinModel(feature_names=["a"], C=1.0), "test_portability", "v0"
    )
    assert isinstance(payload, bytes) and payload
    assert hashlib.sha256(payload).hexdigest() == digest
    assert path.endswith("model.pkl")
