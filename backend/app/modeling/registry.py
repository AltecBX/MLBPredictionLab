"""Model artifact registry.

MLflow-compatible layout (one directory per version holding the artifact plus a
metadata JSON) without requiring an MLflow server in Phase 1. Activation is an
explicit step separate from registration (MODELING_PLAN.md §9).
"""

from __future__ import annotations

import hashlib
import json
import pickle
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ModelNotFoundError
from app.core.logging import get_logger
from app.db.models import ModelVersion
from app.modeling.logistic import LogisticWinModel

log = get_logger(__name__)


def artifact_root() -> Path:
    root = Path(settings.model_artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_path(name: str, version: str) -> Path:
    directory = artifact_root() / name / version
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "model.pkl"


def save_artifact(
    model: LogisticWinModel, name: str, version: str
) -> tuple[str, str, bytes]:
    """Write the artifact to disk and hand back its bytes as well as its path.

    The bytes are what actually travels. A path is only meaningful on the
    machine that wrote it, and the registry is read by processes that never ran
    the training — see `ModelVersion.artifact_blob`. The file is still written
    because it is convenient to inspect locally and costs nothing.
    """
    path = _artifact_path(name, version)
    payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (path.parent / "metadata.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "sha256": digest,
                "feature_names": model.feature_names,
                **model.to_metadata(),
            },
            indent=2,
            default=str,
        )
    )
    return str(path), digest, payload


def load_artifact(path: str) -> LogisticWinModel:
    data = Path(path).read_bytes()
    return pickle.loads(data)  # noqa: S301 - artifact written by this application


def load_artifact_bytes(payload: bytes, expected_sha256: str | None) -> LogisticWinModel:
    """Unpickle a stored artifact, checking it is the one that was registered.

    The digest is not decoration. Unpickling executes whatever the payload says
    to execute, so a row that has been altered since it was written must not be
    loaded — and the registry has recorded the hash since it was first written,
    which makes the check free.
    """
    if expected_sha256:
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise ModelNotFoundError(
                f"Stored artifact does not match its recorded digest "
                f"(expected {expected_sha256[:12]}…, got {actual[:12]}…). "
                f"Refusing to load it."
            )
    return pickle.loads(payload)  # noqa: S301 - digest-checked, written by this app


def register_model(
    session: Session,
    model: LogisticWinModel,
    *,
    name: str,
    version: str,
    feature_set_version: str,
    train_start: date | None,
    train_end: date | None,
    metrics: dict[str, Any],
    hyperparameters: dict[str, Any],
    notes: str | None = None,
    activate: bool = False,
) -> ModelVersion:
    path, digest, payload = save_artifact(model, name, version)

    existing = session.scalar(
        select(ModelVersion).where(
            ModelVersion.name == name, ModelVersion.version == version
        )
    )
    if existing is not None:
        raise ValueError(f"Model version {name}:{version} already registered.")

    row = ModelVersion(
        name=name,
        version=version,
        algorithm=model.algorithm,
        feature_set_version=feature_set_version,
        train_start_date=train_start,
        train_end_date=train_end,
        train_rows=model.train_rows,
        hyperparameters=hyperparameters,
        calibration_method=model.calibrator.method if model.calibrator else None,
        calibration_params=(model.calibrator.params if model.calibrator else {}),
        metrics=metrics,
        feature_names=list(model.feature_names),
        artifact_path=path,
        artifact_blob=payload,
        artifact_sha256=digest,
        git_sha=settings.git_sha,
        is_active=False,
        notes=notes,
    )
    session.add(row)
    session.flush()

    if activate:
        activate_version(session, row.id)
    log.info("model.registered", name=name, version=version, rows=model.train_rows)
    return row


def activate_version(session: Session, model_version_id: int) -> None:
    row = session.get(ModelVersion, model_version_id)
    if row is None:
        raise ModelNotFoundError(f"Model version {model_version_id} not found.")
    session.execute(
        update(ModelVersion)
        .where(ModelVersion.name == row.name, ModelVersion.id != row.id)
        .values(is_active=False)
    )
    session.flush()
    row.is_active = True
    session.flush()
    log.info("model.activated", name=row.name, version=row.version)


def get_active_version(session: Session, name: str | None = None) -> ModelVersion:
    name = name or settings.active_model_name
    row = session.scalar(
        select(ModelVersion).where(
            ModelVersion.name == name, ModelVersion.is_active.is_(True)
        )
    )
    if row is None:
        raise ModelNotFoundError(
            f"No active model version registered under {name!r}. Run `make train`."
        )
    return row


def _load_registered(version: ModelVersion) -> LogisticWinModel:
    """The stored bytes first, the path only as a fallback.

    Order matters. The blob is portable and the path is not, so a process that
    did not do the training gets a working model instead of a `FileNotFoundError`
    naming a directory it has never had. The path is kept as a fallback so rows
    registered before the column existed still load on the machine that trained
    them, and the error when neither works says which of the two is missing
    rather than surfacing a bare filesystem error.
    """
    if version.artifact_blob:
        return load_artifact_bytes(version.artifact_blob, version.artifact_sha256)

    if not version.artifact_path:
        raise ModelNotFoundError(
            f"Model version {version.name}:{version.version} has neither stored "
            f"artifact bytes nor a path. Run `make train` to register one."
        )
    try:
        return load_artifact(version.artifact_path)
    except FileNotFoundError as exc:
        raise ModelNotFoundError(
            f"Model version {version.name}:{version.version} was registered "
            f"before artifacts were stored in the database, and its file "
            f"{version.artifact_path!r} is not on this machine. Retrain to "
            f"register a portable artifact."
        ) from exc


def load_active_model(session: Session, name: str | None = None) -> tuple[ModelVersion, LogisticWinModel]:
    version = get_active_version(session, name)
    model = _load_registered(version)
    if list(model.feature_names) != list(version.feature_names):
        raise ModelNotFoundError(
            f"Artifact for {version.name}:{version.version} does not match its "
            f"registered feature list."
        )
    return version, model


def next_version(session: Session, name: str) -> str:
    total = session.query(ModelVersion).filter(ModelVersion.name == name).count()
    return f"v{total + 1}"
