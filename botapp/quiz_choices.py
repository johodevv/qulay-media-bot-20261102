"""Turn source answers into multiple-choice questions without inventing facts."""

from __future__ import annotations

import hashlib
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import chain

from botapp.media import MediaError

_WORD = re.compile(r"[^\W\d_]+(?:['’ʻ‘`][^\W\d_]+)*")
_CLAUSE = re.compile(r"[^.!?;\n]+")
_CITATION_END = re.compile(
    r"\b(?:мувофиқ|асосан|биноан|бўйича|muvofiq|asosan|binoan|bo'yicha),\s*", re.I
)
_INTRO_END = ("бағишланган", "bag'ishlangan", "қуйидагилар", "quyidagilar")
_FIGURES = (
    (
        "duration",
        re.compile(r"\b(\d+)\s+(?:кун|ой|йил|соат|дақиқа|kun|oy|yil|soat|daqiqa)\b", re.I),
    ),
    ("article", re.compile(r"\b(\d+)\s*[-–]\s*(?:модда|modda)", re.I)),
    ("year", re.compile(r"\b(\d{4})\s*[-–]\s*(?:йил|yil)", re.I)),
)


def _key(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


@dataclass(frozen=True)
class Gap:
    kind: str
    answer: str
    left: str
    right: str

    @property
    def context(self) -> str:
        return f"{self.left}____{self.right}".strip()


def _gap(kind: str, clause: str, start: int, end: int) -> Gap:
    left, right = clause[:start], clause[end:]
    if len(left) > 130:
        left = "… " + left[-130:].split(" ", 1)[-1]
    if len(right) > 160:
        right = right[:160].rsplit(" ", 1)[0] + "…"
    return Gap(kind, clause[start:end], left, right)


def _gaps(answer: str) -> list[Gap]:
    figures, phrases, introductions = [], [], []
    for match in _CLAUSE.finditer(answer[:3500]):
        clause = " ".join(match[0].split())
        words = list(_WORD.finditer(clause))
        if len(words) < 5:
            continue
        for kind, pattern in _FIGURES:
            for match in pattern.finditer(clause):
                if len(figures) == 12:
                    break
                # Match units when suggesting other durations.
                unit = _key(match[0]).split()[-1] if kind == "duration" else ""
                figures.append(_gap(f"{kind}:{unit}", clause, *match.span(1)))
        citation = _CITATION_END.search(clause[:220])
        if citation and _FIGURES[1][1].search(clause[: citation.start()]):
            clause = clause[citation.end() :]
            words = list(_WORD.finditer(clause))
        candidate_group = (
            introductions if any(word in clause.casefold() for word in _INTRO_END) else phrases
        )
        for size in (3, 2):
            for index in range(2, len(words) - size):
                if len(candidate_group) == (48 if candidate_group is phrases else 16):
                    break
                group = words[index : index + size]
                start, end = group[0].start(), group[-1].end()
                value = clause[start:end]
                if (
                    len(value) <= 65
                    and all(part.isalpha() for part in value.split())
                    and len(value.split()) == size
                    and len(group[-1][0]) >= 5
                ):
                    candidate_group.append(_gap(f"phrase:{size}", clause, start, end))
    return phrases + introductions + figures


def _distractors(
    gap: Gap, pool: tuple[str, ...], similar: list[str], rng: random.Random
) -> list[str]:
    correct = _key(gap.answer)
    context = f" {_key(gap.left)} {_key(gap.right)} "
    if f" {correct} " in context:
        return []
    selected = []
    # Sample first, then scan only as far as required if sampled options collide.
    for key in chain(
        rng.sample(similar, min(len(similar), 12)),
        similar,
        rng.sample(pool, min(len(pool), 12)),
        pool,
    ):
        if (
            key == correct
            or key in selected
            or f" {key} " in context
            or f" {key} " in f" {correct} "
            or f" {correct} " in f" {key} "
        ):
            continue
        selected.append(key)
        if len(selected) == 3:
            break
    return selected


def make_variant_questions(questions: list[dict]) -> list[dict]:
    """Use brief source answers directly or hide a phrase from a longer excerpt."""
    if not any("model_answer" in question for question in questions):
        return questions
    pools: dict[str, dict[str, str]] = {}
    candidates = []
    for question in questions:
        answer = question.get("model_answer", "")
        brief = " ".join(answer.split())
        gaps = _gaps(answer)
        if "\n" not in answer and 0 < len(brief) <= 100:
            gaps.insert(0, Gap("brief", brief, "", ""))
        candidates.append(gaps)
        for gap in gaps:
            if key := _key(gap.answer):
                pools.setdefault(gap.kind, {}).setdefault(key, gap.answer)

    keys = {kind: tuple(pool) for kind, pool in pools.items()}
    suffixes: dict[tuple[str, str], list[str]] = defaultdict(list)
    for kind, pool in keys.items():
        if kind.startswith("phrase:"):
            for key in pool:
                suffixes[kind, key[-3:]].append(key)

    result = []
    for index, (question, gaps) in enumerate(zip(questions, candidates, strict=True), 1):
        if "model_answer" not in question:
            result.append(question)
            continue
        seed = f"{index}:{question['question']}".encode()
        rng = random.Random(hashlib.sha256(seed).digest())
        for gap in gaps:
            key = _key(gap.answer)
            if not key:
                continue
            other = _distractors(gap, keys[gap.kind], suffixes.get((gap.kind, key[-3:]), []), rng)
            if len(other) != 3 and not (gap.kind == "brief" and other and len(keys["brief"]) < 4):
                continue
            prompt = question["question"]
            if gap.kind != "brief":
                prompt += f"\n\nManbadagi javobni to'ldiring.\n{gap.context}"
            options = [gap.answer, *(pools[gap.kind][key] for key in other)]
            rng.shuffle(options)
            result.append(
                {
                    "question": prompt,
                    "options": options,
                    "correct": options.index(gap.answer),
                    "source_answer": question["model_answer"],
                    "source_variant": True,
                }
            )
            break
        else:
            raise MediaError(
                f"{index}-savol uchun yetarli variant topilmadi. "
                "Yana savol-javob qo'shing yoki A/B/C/D variantlarini yozing."
            )
    return result
