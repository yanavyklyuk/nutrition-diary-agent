import json

import tools
from config import SLOTS, MET_TABLE, FOOD_DB_SITE

_NUTR = {"type": "object",
         "properties": {"kcal": {"type": "number"},
                        **{k: {"type": ["number", "null"], "description": "null if unknown"}
                           for k in ("protein", "fat", "carbs")}},
         "required": ["kcal"]}
_ITEM = {"type": "object", "properties": {
    "name": {"type": "string"},
    "grams": {"type": "number", "description": "portion weight in grams: the user's number or your estimate from household measures"},
    "per_100g": {**_NUTR, "description": "kcal and macros per 100 g (from the food database, a website, a label or your estimate)"},
    "total": {**_NUTR, "description": "kcal and macros for the whole portion, when the user gave calories for the portion"},
    "source": {"type": "string", "description": "where the kcal came from: 'користувачка' | 'база' | website URL | 'оцінка'"},
    "bjv_source": {"type": "string", "description": "where the macros came from, if different from source "
                   "(e.g. kcal from the user, macros 'оцінка')"}},
    "required": ["name", "source"]}


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


TOOLS = [
    _fn("set_profile", "Save the user's profile and compute the daily baseline (BMR) and protein target. "
        "Values only in kg, cm and years.",
        {"weight_kg": {"type": "number", "description": "weight in kilograms"},
         "height_cm": {"type": "number", "description": "height in centimeters (1.67 m -> 167)"},
         "age": {"type": "integer", "description": "age in full years"},
         "sex": {"type": "string", "enum": ["female", "male"], "description": "ask the user, never assume"}},
        ["weight_kg", "height_cm", "age", "sex"]),
    _fn("log_meal", "Log a meal (list of items) or mark a main meal slot as 'пропущено' (skipped) / 'ще не було' (not yet).",
        {"slot": {"type": "string", "enum": SLOTS},
         "status": {"type": "string", "enum": ["записано", "пропущено", "ще не було"]},
         "items": {"type": "array", "items": _ITEM}},
        ["slot"]),
    _fn("log_activity", "Log a workout (minutes + rpe 1-10) or daily steps (activity_type='кроки' + steps).",
        {"activity_type": {"type": "string", "enum": list(MET_TABLE) + ["кроки"]},
         "minutes": {"type": "number"},
         "rpe": {"type": "number", "description": "perceived exertion 1-10"},
         "steps": {"type": "integer"}},
        ["activity_type"]),
    _fn("delete_entry", "Delete a meal or activity entry by its id.",
        {"entry_id": {"type": "integer"}}, ["entry_id"]),
    _fn("day_summary", "Day summary: eaten, burned, difference, status of each main meal, entry ids, and the verdict once the day is complete.", {}),
    _fn("search_food_db", f"Search the food database {FOOD_DB_SITE}. Returns matching products with name, brand, kcal per 100 g/ml "
        "and page url. Pick the best match and open its url with read_page to get macros.",
        {"query": {"type": "string", "description": "product name only, in Ukrainian, "
                   "e.g. 'сир кисломолочний 5%' or 'печиво Марія'"}}, ["query"]),
    _fn("web_search", "Search the web for a product's calories. Returns links with snippets.",
        {"query": {"type": "string", "description": "product name only, in Ukrainian, "
                   "e.g. 'сир кисломолочний 5%' or 'печиво Марія'"}}, ["query"]),
    _fn("read_page", "Open a page from search results. For the food database returns per_100g values; for other sites returns page text.",
        {"url": {"type": "string", "description": "a URL from search results"}}, ["url"]),
]

FUNCTIONS = {t["function"]["name"]: getattr(tools, t["function"]["name"]) for t in TOOLS}

def call_tool(state: dict, name: str, args_json: str) -> str:
    try:
        result = FUNCTIONS[name](state, **json.loads(args_json or "{}"))
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return json.dumps(result, ensure_ascii=False)
