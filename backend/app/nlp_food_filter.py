from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


# Very small seed set (you can extend this anytime)
SEED_FOOD = [
    "chicken breast", "milk", "eggs", "banana", "peanut butter", "rice", "lentils",
    "tofu", "salmon", "yogurt", "broccoli", "spinach", "oats", "almonds",
    "paneer", "whey protein", "black beans", "chickpeas", "beef", "pork",
]

SEED_JUNK = [
    "asdf", "lol", "help", "??", "12345", "protein pls", "cheap", "best food",
    "hi", "test", "aaaaaa", "food", "eat", "random", "i want", "how much",
]

_nonword_re = re.compile(r"[^a-z0-9\s\-']+")


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = _nonword_re.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@dataclass
class NlpResult:
    label: str   # "food" or "junk"
    score: float # probability of "food" (0..1)
    reason: str


def build_model() -> Pipeline:
    """
    Tiny model: TF-IDF over character ngrams + logistic regression.
    This works decently for short queries with typos.
    """
    X = [*SEED_FOOD, *SEED_JUNK]
    y = [1] * len(SEED_FOOD) + [0] * len(SEED_JUNK)

    model = Pipeline(
        steps=[
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))),
            ("clf", LogisticRegression(max_iter=300)),
        ]
    )
    model.fit(X, y)
    return model


_MODEL = build_model()


def classify_query(q: str) -> NlpResult:
    norm = _normalize(q)

    # Cheap hard rules first (saves false positives)
    if len(norm) < 3:
        return NlpResult("junk", 0.0, "too_short")
    if norm.isdigit():
        return NlpResult("junk", 0.0, "digits_only")

    proba_food = float(_MODEL.predict_proba([norm])[0][1])

    label = "food" if proba_food >= 0.60 else "junk"
    reason = "ml_threshold>=0.60" if label == "food" else "ml_threshold<0.60"
    return NlpResult(label, proba_food, reason)
