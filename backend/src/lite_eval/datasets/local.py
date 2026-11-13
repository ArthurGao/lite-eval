"""A dataset directory: dataset.yaml + cases.jsonl + optional corpus/ (design §5.2, §18.6)."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lite_eval.canonical import canonical_json, sha256_hex
from lite_eval.contracts import Case, DatasetRef, RunWarning
from lite_eval.errors import ConfigError
from lite_eval.files import read_jsonl
from lite_eval.redact import scan_rules

_HASH_PREFIX = b"lite-eval-dataset-v1\n"
CORPUS = "corpus"


class SecretAllow(BaseModel):
    """One false positive of the load-time secret scan, allowed by exact match (design §18.12)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    rule: str
    match_sha256: str
    reason: str = Field(min_length=1)


class DatasetMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    pack: str | None = None
    description: str = ""
    license: str | None = None
    synthetic: bool = False
    secret_scan_allow: list[SecretAllow] = Field(default_factory=list)


@dataclass(frozen=True)
class Dataset:
    meta: DatasetMeta
    root: Path
    cases: tuple[Case, ...]
    case_hashes: dict[str, str]
    hash: str
    warnings: tuple[RunWarning, ...] = field(default=())

    @property
    def ref(self) -> DatasetRef:
        return DatasetRef(
            name=self.meta.name,
            version=self.meta.version,
            hash=self.hash,
            pack=self.meta.pack,
            synthetic=self.meta.synthetic,
        )

    @property
    def corpus_dir(self) -> Path:
        return self.root / CORPUS


def _corpus_files(corpus: Path) -> list[tuple[str, Path]]:
    if not corpus.exists():
        return []
    files = []
    for path in corpus.rglob("*"):
        if path.is_symlink():
            raise ConfigError(f"dataset corpus may not contain symbolic links: {path}")
        if path.is_file():
            files.append((path.relative_to(corpus).as_posix(), path))
    return sorted(files)


def dataset_hash(meta: DatasetMeta, case_hashes: dict[str, str], corpus: Path) -> str:
    """Design §18.6: prefix, canonical {name, version, pack}, case hashes, corpus files."""
    h = hashlib.sha256(_HASH_PREFIX)
    h.update(
        canonical_json({"name": meta.name, "version": meta.version, "pack": meta.pack}).encode()
    )
    for case_id in sorted(case_hashes):
        h.update(case_hashes[case_id].encode())
    for rel, path in _corpus_files(corpus):
        with path.open("rb") as f:  # streamed: a corpus file may be large
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        h.update(f"{rel}\0{digest}".encode())
    return h.hexdigest()


def _read_cases(path: Path) -> list[Case]:
    if not path.is_file():
        raise ConfigError(f"dataset has no cases.jsonl: {path}")
    cases = read_jsonl(path, Case)
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ConfigError(f"{path}: duplicate case id {case.id!r}")
        seen.add(case.id)
    if not cases:
        raise ConfigError(f"dataset has no cases: {path}")
    return cases


def _scan_secrets(meta: DatasetMeta, cases: list[Case]) -> list[RunWarning]:
    scanner = scan_rules()
    allowed = {(a.case_id, a.rule, a.match_sha256) for a in meta.secret_scan_allow}
    used: set[tuple[str, str, str]] = set()
    blocked: list[str] = []
    for case in cases:
        text = canonical_json({"input": case.input, "expected": case.expected, "tags": case.tags})
        for finding in scanner.findings(text):
            key = (case.id, finding.rule, sha256_hex(finding.match))
            if key in allowed:
                used.add(key)
            else:
                blocked.append(f"case {case.id!r}: rule {finding.rule}, match_sha256 {key[2]}")
    if blocked:
        raise ConfigError(
            "dataset looks like it contains secrets (design §18.12); remove them, or allow a "
            "false positive in dataset.yaml secret_scan_allow with a reason:\n  "
            + "\n  ".join(blocked)
        )
    warnings = []
    for a in meta.secret_scan_allow:
        if (a.case_id, a.rule, a.match_sha256) in used:
            code, what = "secret_scan_allowed", f"secret scan allowed: {a.reason}"
        else:
            code, what = "secret_scan_allow_unused", "secret_scan_allow entry matched nothing"
        warnings.append(
            RunWarning(code=code, message=f"{what} (case {a.case_id!r}, rule {a.rule})")
        )
    return warnings


def load_dataset(root: Path) -> Dataset:
    meta_path = root / "dataset.yaml"
    if not meta_path.is_file():
        raise ConfigError(f"not a dataset directory (no dataset.yaml): {root}")
    try:
        meta = DatasetMeta.model_validate(yaml.safe_load(meta_path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{meta_path}: {exc}") from exc
    cases = _read_cases(root / "cases.jsonl")
    warnings = _scan_secrets(meta, cases)
    case_hashes = {c.id: c.content_hash() for c in cases}
    return Dataset(
        meta=meta,
        root=root,
        cases=tuple(sorted(cases, key=lambda c: c.id)),
        case_hashes=case_hashes,
        hash=dataset_hash(meta, case_hashes, root / CORPUS),
        warnings=tuple(warnings),
    )
