"""Сборка книги прогона: черновик assumptions.draft.yaml + все фрагменты fragments/*.yaml.

Блоки сливаются рекурсивно, траектории и листья заменяются целиком (траектория фрагмента не смешивается с
ключами черновика); предложения осей (axes_proposals, reverse_dcf_proposals) в книгу не идут.
"""
from __future__ import annotations

import copy
import sys

import yaml

from common import REPO

sys.path.insert(0, str(REPO))
from model.book_schema import is_trajectory  # noqa: E402

ASSUME = REPO / "data" / "assumptions"
MINE = "financing-valuation.yaml"


def merge(base: dict, frag: dict) -> dict:
    for k, val in frag.items():
        if (isinstance(val, dict) and isinstance(base.get(k), dict) and not is_trajectory(val)
                and not is_trajectory(base[k])):
            merge(base[k], val)
        else:
            base[k] = copy.deepcopy(val)
    return base


def build_book() -> tuple[dict, list[str]]:
    A = yaml.safe_load((ASSUME / "assumptions.draft.yaml").read_text(encoding="utf-8"))
    used = []
    for path in sorted((ASSUME / "fragments").glob("*.yaml"), key=lambda p: (p.name == MINE, p.name)):
        F = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        F.pop("axes_proposals", None)
        F.pop("reverse_dcf_proposals", None)
        merge(A, F)
        used.append(path.name)
    return A, used
