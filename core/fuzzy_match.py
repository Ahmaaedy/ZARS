import re
from rapidfuzz import fuzz

AMBIGUITY_RANGE = 5


def normalize(text):
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def build_trigger_list(registry, macros):
    triggers = []
    macro_norms = set()
    for macro_id, macro in macros.list().items():
        trigger = macro.get("trigger", macro_id)
        norm = normalize(trigger)
        macro_norms.add(norm)
        triggers.append({
            "raw": trigger,
            "normalized": norm,
            "source": "macro",
            "id": macro_id,
            "type": "macro",
            "skill_path": macro.get("skill_path"),
        })
    for entry in registry.entries.values():
        for trigger in entry.triggers:
            norm = normalize(trigger)
            if norm in macro_norms:
                continue
            triggers.append({
                "raw": trigger,
                "normalized": norm,
                "source": "core",
                "id": entry.id,
                "type": "command",
            })
    return triggers


def fuzzy_match(text, registry, macros, core_threshold=85, macro_threshold=75):
    query = normalize(text)
    if not query:
        return None

    triggers = build_trigger_list(registry, macros)
    scored = []
    for t in triggers:
        score = fuzz.token_set_ratio(query, t["normalized"])
        threshold = core_threshold if t["type"] == "command" else macro_threshold
        if score >= threshold:
            adjusted = score + 1 if t["type"] == "macro" else score
            scored.append((adjusted, score, t))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    top_adjusted, top_score, top_match = scored[0]

    if len(scored) > 1:
        second_adjusted = scored[1][0]
        if top_adjusted - second_adjusted < AMBIGUITY_RANGE:
            return {
                "match": top_match,
                "score": top_score,
                "ambiguous": True,
                "alternatives": [(s, m) for _, s, m in scored[:3]],
            }

    return {
        "match": top_match,
        "score": top_score,
        "ambiguous": False,
    }
