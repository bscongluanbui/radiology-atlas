"""Presentation metadata only. Module identities/modalities remain source-owned."""
REGIONS = {
    "BRAIN": {"label": "Brain", "vi": "Não bộ", "description": "Khám phá giải phẫu não bộ qua các chuỗi MRI, CT và hình minh họa.", "x": 50, "y": 7},
    "HEAD AND NECK": {"label": "Head and neck", "vi": "Đầu và cổ", "description": "Các module giải phẫu đầu, mặt, cổ và cơ quan cảm giác.", "x": 50, "y": 18},
    "SPINE": {"label": "Spine", "vi": "Cột sống", "description": "Cột sống và tủy sống trên hình ảnh học và hình minh họa.", "x": 52, "y": 38},
    "WHOLE BODY": {"label": "Whole body", "vi": "Toàn thân", "description": "Các module khảo sát và liên hệ giải phẫu toàn thân.", "x": 12, "y": 18},
    "THORAX": {"label": "Thorax", "vi": "Lồng ngực", "description": "Giải phẫu lồng ngực, phổi, trung thất, thành ngực và tim.", "x": 44, "y": 29},
    "ABDOMEN AND PELVIS": {"label": "Abdomen and pelvis", "vi": "Bụng và chậu", "description": "Khám phá các module vùng bụng và chậu theo loại hình ảnh học.", "x": 50, "y": 49},
    "UPPER LIMB": {"label": "Upper limb", "vi": "Chi trên", "description": "Giải phẫu chi trên từ vai đến bàn tay.", "x": 20, "y": 40},
    "LOWER LIMB": {"label": "Lower limb", "vi": "Chi dưới", "description": "Giải phẫu chi dưới từ vùng háng đến bàn chân.", "x": 60, "y": 74},
}

MODALITY_GROUPS = (
    ("MRI", ("MRI",)),
    ("CT", ("CT", "CT arthrogram")),
    ("X-ray & vascular", ("Radiography", "Angiography")),
    ("Hybrid imaging", ("PET-CT",)),
    ("Anatomical views", ("Illustrations", "Endoscopy", "Laparoscopy", "Photography")),
)


def group_catalogue(modules):
    groups = []
    keys = list(REGIONS) + sorted({m["region"] for m in modules} - set(REGIONS))
    for key in keys:
        rows = [m for m in modules if m["region"] == key]
        if not rows:
            continue
        meta = REGIONS.get(key, {"label": key.title(), "vi": key.title(), "description": ""})
        groups.append({"key": key, "slug": key.lower().replace(" ", "-"), **meta,
                       "modules": rows, "ready": sum(bool(m["captured"]) for m in rows)})
    return groups
