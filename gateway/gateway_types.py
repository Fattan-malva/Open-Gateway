"""Metadata jenis gateway (Discord, WhatsApp, ...)."""
from typing import Dict


GATEWAY_TYPES: Dict[str, Dict[str, str]] = {
    "discord": {
        "id": "discord",
        "name": "Discord",
        "description": "Bot Discord untuk chat, internet, gambar, dan suara.",
        "icon": "assets/icon-discord.svg",
        "available": "true",
        "coming_soon": "false",
    },
    "whatsapp": {
        "id": "whatsapp",
        "name": "WhatsApp",
        "description": "Gateway WhatsApp (tampilan saja, backend menyusul).",
        "icon": "assets/icon-whatsapp.svg",
        "available": "false",
        "coming_soon": "true",
    },
}


def gateway_type(kind: str) -> Dict[str, str]:
    return GATEWAY_TYPES.get(kind, GATEWAY_TYPES["discord"])


def gateway_types_public() -> list:
    return [
        {
            "id": value["id"],
            "name": value["name"],
            "description": value["description"],
            "icon": value["icon"],
            "available": value["available"] == "true",
            "coming_soon": value["coming_soon"] == "true",
        }
        for value in GATEWAY_TYPES.values()
    ]
