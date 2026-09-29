"""Книга прогона листа и сборка «черновик + фрагменты».

build_book() — канон: data/assumptions/assumptions.yaml (его читает ядро); на нём меряют model_check.py и
band_check.py. merged_book() — черновик assumptions.draft.yaml + фрагменты листов evidence/<область>/fragment.yaml
(выходы листов): check_fragment.py сверяет, что сборка совпадает с каноном. Блоки сливаются рекурсивно, траектории
и листья заменяются целиком (траектория фрагмента не смешивается с ключами черновика); предложения осей
(axes_proposals, reverse_dcf_proposals) в книгу не идут.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

from common import REPO

sys.path.insert(0, str(REPO))
from model.book_schema import is_trajectory  # noqa: E402

ASSUME = REPO / "data" / "assumptions"
EVIDENCE = ASSUME / "evidence"
MINE = "financing-valuation"


def fragments() -> list[Path]:
    """Фрагменты листов evidence/<область>/fragment.yaml; свой — последним (итоговые списки осей)."""
    return sorted(EVIDENCE.glob("*/fragment.yaml"), key=lambda p: (p.parent.name == MINE, p.parent.name))


def merge(base: dict, frag: dict) -> dict:
    for k, val in frag.items():
        if (isinstance(val, dict) and isinstance(base.get(k), dict) and not is_trajectory(val)
                and not is_trajectory(base[k])):
            merge(base[k], val)
        else:
            base[k] = copy.deepcopy(val)
    return base


def build_book() -> tuple[dict, list[str]]:
    """Канон книги (assumptions.yaml) и подпись источника."""
    A = yaml.safe_load((ASSUME / "assumptions.yaml").read_text(encoding="utf-8"))
    return A, ["assumptions.yaml"]


def merged_book() -> tuple[dict, list[str]]:
    """Черновик + все фрагменты листов (для сверки с каноном)."""
    A = yaml.safe_load((ASSUME / "assumptions.draft.yaml").read_text(encoding="utf-8"))
    used = []
    for path in fragments():
        F = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        F.pop("axes_proposals", None)
        F.pop("reverse_dcf_proposals", None)
        merge(A, F)
        used.append(f"{path.parent.name}/{path.name}")
    return A, used
