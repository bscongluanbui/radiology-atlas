"""Application roles, separate from operating-system users and anatomical identity."""
ROLE_LABELS = {"root": "Root · Quản trị hệ thống", "admin": "Admin · Xem toàn bộ", "standard": "Standard · Vùng được cấp"}


def can_manage(user):
    return bool(user and user.get("active") and user.get("role") == "root")


def can_view_module(user, canonical_key):
    if not user or not user.get("active"):
        return False
    if user.get("role") in ("root", "admin"):
        return True
    if user.get("role") != "standard":
        return False
    region = canonical_key.split("/", 1)[0]
    # Keys have already been resolved to the source catalogue by the gateway.
    return region in user.get("regions", ()) or canonical_key in user.get("modules", ())
