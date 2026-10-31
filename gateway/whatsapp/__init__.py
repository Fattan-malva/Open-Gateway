"""Gateway WhatsApp (tampilan dan metadata saja; backend menyusul)."""

AVAILABLE = False


def status() -> dict:
    return {"available": False, "message": "Gateway WhatsApp segera hadir."}
