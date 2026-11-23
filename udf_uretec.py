#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Havelsan UYAP UDF (format_id="1.8") Uyumlu Resmî Dilekçe & Belge Üretici
========================================================================
Copyright (c) 2026 Av. Can KIZILYAR. Tüm hakları saklıdır.
"""

from __future__ import annotations

import sys
from pathlib import Path

# udf_docx_converter modül yolunu ekle
_CUR = Path(__file__).resolve().parent
for candidate in (_CUR / "udf-docx-pro", _CUR.parent / "udf-docx-pro", Path.home() / "udf-docx-pro"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import udf_docx_converter


def make_paragraph(runs_data: list, align: str = "0", space_below: str = "3.0") -> dict:
    """
    runs_data: [("Metin", bold_bool, italic_bool), ...]
    align: "0"=Sol, "1"=Orta, "2"=Sağ, "3"=İki Yana Yasla
    """
    runs = []
    for r in runs_data:
        text = r[0]
        bold = r[1] if len(r) > 1 else False
        italic = r[2] if len(r) > 2 else False
        attrs = {
            "family": "Times New Roman",
            "size": "12.0",
            "bold": "true" if bold else "false",
            "italic": "true" if italic else "false",
        }
        runs.append({"type": "text", "tag": "content", "text": text, "attrs": attrs})
    return {
        "type": "paragraph",
        "attrs": {
            "Alignment": str(align),
            "family": "Times New Roman",
            "size": "12.0",
            "SpaceBelow": str(space_below),
            "LineSpacing": "0.0",
        },
        "runs": runs,
    }


def build_petition_udf_bytes(blocks: list) -> bytes:
    """Tam uyumlu UTF-16 offset tablolu Havelsan UDF arşivi üretir."""
    doc_ir = {
        "formatId": "1.8",
        "page": {
            "mediaSizeName": "1",
            "leftMarginPt": 42.525,
            "rightMarginPt": 42.525,
            "topMarginPt": 42.525,
            "bottomMarginPt": 42.525,
            "paperOrientation": "1",
            "headerOffsetPt": 20.0,
            "footerOffsetPt": 20.0,
        },
        "blocks": blocks,
        "docProps": {},
    }
    return udf_docx_converter.build_udf_from_ir(doc_ir)
