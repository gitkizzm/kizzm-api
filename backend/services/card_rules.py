import re


def type_line(card: dict) -> str:
    return (card.get("type_line") or "").lower()


def oracle_text(card: dict) -> str:
    return (card.get("oracle_text") or "").lower()


def is_background(card: dict) -> bool:
    return "background" in type_line(card)


def has_choose_a_background(card: dict) -> bool:
    return "choose a background" in oracle_text(card)


def has_friends_forever(card: dict) -> bool:
    return "friends forever" in oracle_text(card)


def partner_with_target_name(card: dict) -> str | None:
    m = re.search(r"partner with ([^\n\(]+)", card.get("oracle_text") or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def get_image_url(card: dict, key: str) -> str | None:
    iu = card.get("image_uris")
    if isinstance(iu, dict) and iu.get(key):
        return iu[key]

    faces = card.get("card_faces")
    if isinstance(faces, list) and len(faces) > 0:
        fu = faces[0].get("image_uris")
        if isinstance(fu, dict) and fu.get(key):
            return fu[key]

    return None
