"""Validated, bounded reference access to the English–Vietnamese glossary.

This module is intentionally offline.  :class:`Glossary` reads the master
JSON, keeps its original records for lossless compact export, and returns
data-only references for an OpenAI-compatible translation request.  It does
not call an endpoint, edit language packs, or interpret glossary strings as
instructions.

The integration surface is deliberately small::

    glossary = Glossary.load(path)
    references = glossary.for_items(batch)
    glossary.export_compact_json(output_path)

``for_items`` always retains every exact full-term English/alias match,
including every row in an ambiguous English group.  The entry/character
budgets apply to lower-ranked, non-exact references only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any, Optional, Union


GLOSSARY_FIELDS = (
    "English",
    "Vietnamese",
    "English synonyms / variants",
    "Category",
    "Subcategory",
    "Radiology / translation note",
    "Status",
)
DEFAULT_MAX_ENTRIES = 48
DEFAULT_MAX_CHARS = 12000


class GlossaryValidationError(ValueError):
    """Raised when the glossary is empty or malformed."""


def _json_constant(value: str) -> None:
    raise GlossaryValidationError(f"invalid JSON constant: {value}")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GlossaryValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _clean(value: Any, field_name: str, index: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise GlossaryValidationError(f"entry {index} field {field_name!r} must be a string")
    result = value.strip()
    if required and not result:
        raise GlossaryValidationError(f"entry {index} field {field_name!r} is empty")
    return result


def _validate(records: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(records, list) or not records:
        raise GlossaryValidationError("glossary root must be a non-empty JSON array")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise GlossaryValidationError(f"entry {index} must be a JSON object")
        _clean(row.get("English"), "English", index, required=True)
        _clean(row.get("Vietnamese"), "Vietnamese", index, required=True)
        for name in GLOSSARY_FIELDS[2:]:
            if name in row:
                _clean(row[name], name, index)
        # Keep values (including unknown columns) untouched for the exporter;
        # normalized trimming happens only when creating GlossaryEntry objects.
        result.append(deepcopy(row))
    return tuple(result)


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold(), re.UNICODE))


def _aliases(value: str) -> tuple[str, ...]:
    # Slash is retained because T1/T2 and similar notation is a single alias.
    seen: set[str] = set()
    result: list[str] = []
    for part in re.split(r"[;|\r\n]+", value):
        part = part.strip()
        if part and _key(part) not in seen:
            seen.add(_key(part))
            result.append(part)
    return tuple(result)


@dataclass(frozen=True)
class GlossaryEntry:
    """One normalized row; ``ordinal`` is only a stable ordering token."""

    english: str
    vietnamese: str
    aliases: tuple[str, ...] = ()
    category: str = ""
    subcategory: str = ""
    note: str = ""
    status: str = ""
    ordinal: int = field(default=0, repr=False, compare=False)
    _raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def as_reference(self) -> dict[str, Any]:
        """Return only data fields intended for a prompt reference section."""

        return {
            "english": self.english,
            "vietnamese": self.vietnamese,
            "aliases": list(self.aliases),
            "category": self.category,
            "subcategory": self.subcategory,
            "note": self.note,
            "status": self.status,
        }

    to_reference = as_reference

    def as_dict(self) -> dict[str, Any]:
        """Return the source-column row without normalized trimming."""

        return deepcopy(dict(self._raw))


class Glossary:
    """Read-only glossary adapter used by the fresh translation orchestrator."""

    def __init__(
        self,
        records: Any,
        *,
        source_path: Optional[Union[str, Path]] = None,
        source_sha256: Optional[str] = None,
    ) -> None:
        self._records = _validate(records)
        if source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise GlossaryValidationError("source_sha256 must be a lowercase SHA-256 digest")
        self.source_path = Path(source_path).resolve() if source_path is not None else None
        self.sha256 = source_sha256
        self._entries = tuple(self._entry(row, i) for i, row in enumerate(self._records))
        by_term: dict[str, list[GlossaryEntry]] = defaultdict(list)
        by_english: dict[str, list[GlossaryEntry]] = defaultdict(list)
        for entry in self._entries:
            by_english[_key(entry.english)].append(entry)
            for term in (entry.english, *entry.aliases):
                by_term[_key(term)].append(entry)
        self._by_term = {term: tuple(rows) for term, rows in by_term.items()}
        self._by_english = {term: tuple(rows) for term, rows in by_english.items()}

    @staticmethod
    def _entry(row: Mapping[str, Any], ordinal: int) -> GlossaryEntry:
        return GlossaryEntry(
            english=row["English"].strip(),
            vietnamese=row["Vietnamese"].strip(),
            aliases=_aliases((row.get("English synonyms / variants") or "")),
            category=(row.get("Category") or "").strip(),
            subcategory=(row.get("Subcategory") or "").strip(),
            note=(row.get("Radiology / translation note") or "").strip(),
            status=(row.get("Status") or "").strip(),
            ordinal=ordinal,
            _raw=row,
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Glossary":
        """Load UTF-8 JSON and bind the SHA-256 of its exact original bytes."""

        if not isinstance(path, (str, Path)):
            raise GlossaryValidationError("glossary path must be a string or Path")
        source = Path(path)
        if not source.is_file():
            raise GlossaryValidationError(f"glossary file not found: {source}")
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise GlossaryValidationError(f"cannot read glossary file: {source}") from exc
        if not raw.strip():
            raise GlossaryValidationError("glossary file is empty")
        try:
            records = json.loads(
                raw.decode("utf-8-sig"),
                object_pairs_hook=_json_object,
                parse_constant=_json_constant,
            )
        except GlossaryValidationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GlossaryValidationError(f"malformed glossary JSON: {source}") from exc
        return cls(records, source_path=source, source_sha256=hashlib.sha256(raw).hexdigest())

    @classmethod
    def from_records(cls, records: Any) -> "Glossary":
        """Construct a hashless adapter from synthetic JSON-like records."""

        return cls(records)

    @property
    def entries(self) -> tuple[GlossaryEntry, ...]:
        return self._entries

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(dict(row)) for row in self._records)

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def ambiguous_english(self) -> dict[str, tuple[GlossaryEntry, ...]]:
        """Return every row whose English term has multiple VI meanings."""

        return {
            term: rows
            for term, rows in self._by_english.items()
            if len({row.vietnamese for row in rows}) > 1
        }

    def lookup(self, term: str) -> tuple[GlossaryEntry, ...]:
        """Return all English/alias matches; ambiguity is never collapsed."""

        if not isinstance(term, str):
            raise TypeError("term must be a string")
        return self._by_term.get(_key(term), ())

    @staticmethod
    def _budget(name: str, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer or None")
        return value

    @staticmethod
    def _item_text(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{label} must be a string")
        return value

    @classmethod
    def _search_items(cls, batch: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for index, item in enumerate(batch):
            if not isinstance(item, Mapping):
                raise TypeError(f"batch item {index} must be a mapping")
            if "source" not in item:
                raise ValueError(f"batch item {index} missing source")
            source = item["source"]
            if isinstance(source, Mapping):
                source = " | ".join(cls._item_text(v, "source field") for v in source.values())
            else:
                source = cls._item_text(source, "source")
            context = cls._item_text(item.get("context", ""), "context")
            result.append((source, context))
            lines = item.get("source_lines")
            if lines is not None:
                if isinstance(lines, (str, bytes, bytearray)) or not isinstance(lines, Iterable):
                    raise TypeError(f"batch item {index} source_lines must be an iterable")
                for line in lines:
                    result.append((cls._item_text(line, "source line"), context))
        return result

    @staticmethod
    def _contains(text: str, term: str) -> bool:
        text, term = _key(text), _key(term)
        return bool(term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))

    @classmethod
    def _score(cls, entry: GlossaryEntry, source: str, context: str) -> float:
        source_tokens, context_tokens = _tokens(source), _tokens(context)
        score = 0.0
        for term in (entry.english, *entry.aliases):
            term_tokens = _tokens(term)
            if cls._contains(source, term):
                score += 100 + 4 * len(term_tokens)
            score += 14 * len(term_tokens & source_tokens)
            score += 4 * len(term_tokens & context_tokens)
        score += 2.5 * len(_tokens(entry.category + " " + entry.subcategory) & (source_tokens | context_tokens))
        return score

    def _reference_size(self, entry: GlossaryEntry) -> int:
        return len(json.dumps(entry.as_reference(), ensure_ascii=False, separators=(",", ":")))

    def for_items(
        self,
        batch: Iterable[Mapping[str, Any]],
        *,
        max_entries: Optional[int] = DEFAULT_MAX_ENTRIES,
        max_chars: Optional[int] = DEFAULT_MAX_CHARS,
    ) -> list[dict[str, Any]]:
        """Return bounded, compact reference records for source/context jobs.

        Exact source or ``source_lines`` matches are selected first and are
        exempt from both budgets.  Non-exact rows need a positive source,
        context, category, or subcategory overlap and must fit both budgets.
        Rows are ordered by glossary order for exact matches and by descending
        relevance then glossary order otherwise.
        """

        max_entries = self._budget("max_entries", max_entries)
        max_chars = self._budget("max_chars", max_chars)
        search_items = list(self._search_items(batch))
        exact: set[int] = set()
        scores: dict[int, float] = {}
        for source, context in search_items:
            exact.update(entry.ordinal for entry in self._by_term.get(_key(source), ()))
            for entry in self._entries:
                scores[entry.ordinal] = max(scores.get(entry.ordinal, 0.0), self._score(entry, source, context))

        selected = [entry for entry in self._entries if entry.ordinal in exact]
        selected_ids = {entry.ordinal for entry in selected}
        used_chars = sum(self._reference_size(entry) for entry in selected)
        candidates = sorted(
            (entry for entry in self._entries
             if entry.ordinal not in selected_ids and scores.get(entry.ordinal, 0) > 0),
            key=lambda entry: (-scores[entry.ordinal], entry.ordinal),
        )
        for entry in candidates:
            if max_entries is not None and len(selected) >= max_entries:
                break
            size = self._reference_size(entry)
            if max_chars is not None and used_chars + size > max_chars:
                continue
            selected.append(entry)
            used_chars += size
        return [entry.as_reference() for entry in selected]

    def compact_json(self) -> str:
        """Return the original records in compact JSON form."""

        return json.dumps(
            [deepcopy(dict(row)) for row in self._records],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    def export_compact_json(self, path: Union[str, Path]) -> Path:
        """Atomically export compact JSON while preserving source values/order."""

        if not isinstance(path, (str, Path)):
            raise TypeError("export path must be a string or Path")
        destination = Path(path)
        if self.source_path and destination.resolve() == self.source_path:
            raise ValueError("Export must not overwrite original glossary")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(destination.parent),
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(self.compact_json())
        try:
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination


# Compatibility name for callers that prefer the descriptive adapter class.
GlossaryAdapter = Glossary


def load_glossary(path: Union[str, Path]) -> Glossary:
    """Load a glossary through the public adapter class."""

    return Glossary.load(path)


def export_compact_json(glossary: Glossary, path: Union[str, Path]) -> Path:
    """Export compact JSON from a :class:`Glossary`."""

    if not isinstance(glossary, Glossary):
        raise TypeError("glossary must be a Glossary")
    return glossary.export_compact_json(path)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_ENTRIES",
    "GLOSSARY_FIELDS",
    "Glossary",
    "GlossaryAdapter",
    "GlossaryEntry",
    "GlossaryValidationError",
    "export_compact_json",
    "load_glossary",
]
