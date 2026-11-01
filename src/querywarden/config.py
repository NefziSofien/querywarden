"""Load and check the user's YAML files. A mistake here must stop the run with a clear message."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from querywarden.canary import _normalize
from querywarden.compare import Options


class ConfigError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Case(_Strict):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    gold: str | None = None
    expect: Literal["refusal"] | None = None
    ordered: bool = False
    allow_extra_columns: bool = False
    float_tolerance: float = Field(default=1e-6, ge=0)
    tags: tuple[str, ...] = ()
    disputed: str | None = None

    @model_validator(mode="after")
    def _gold_or_refusal(self) -> "Case":
        if (self.gold is None) == (self.expect is None):
            raise ValueError("a case needs either a gold query or expect: refusal, not both")
        return self

    @property
    def options(self) -> Options:
        return Options(self.ordered, self.allow_extra_columns, self.float_tolerance)


class Policy(_Strict):
    forbidden: tuple[str, ...] = ()
    canaries: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _usable_canaries(self) -> "Policy":
        for canary in self.canaries:
            if not _normalize(canary):
                raise ValueError(f"canary {canary!r} has no letters or digits")
        return self


class ProviderConfig(_Strict):
    type: Literal["replay", "command", "http", "openai"]
    id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    recordings: str | None = None
    argv: tuple[str, ...] = ()
    url: str | None = None
    model: str | None = None
    system_prompt_file: str | None = None
    api_key_env: str | None = None


class Config(_Strict):
    database_url_env: str = "QUERYWARDEN_DATABASE_URL"
    cases: str = "cases.yaml"
    policy: str = "policy.yaml"
    provider: ProviderConfig
    allow_remote: bool = False


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e


def _validated(model, data, path: Path):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, p['loc'])) or 'file'}: {p['msg']}" for p in e.errors())
        raise ConfigError(f"{path}: {problems}") from e


def load_config(path: Path) -> Config:
    return _validated(Config, _read_yaml(path), path)


def load_policy(path: Path) -> Policy:
    return _validated(Policy, _read_yaml(path) or {}, path)


def load_cases(path: Path) -> list[Case]:
    data = _read_yaml(path)
    entries = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} needs a non-empty cases list")
    defaults = data.get("defaults") or {}
    cases = [_validated(Case, {**defaults, **entry}, path) for entry in entries]
    seen = set()
    for case in cases:
        if case.id in seen:
            raise ConfigError(f"{path}: duplicate case id {case.id}")
        seen.add(case.id)
    return cases


def database_url(config: Config) -> str:
    """The URL only ever comes from the environment, so it never lands in a committed file."""
    url = os.environ.get(config.database_url_env)
    if not url:
        raise ConfigError(f"set {config.database_url_env} to the test database URL")
    return url
