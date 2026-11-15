from __future__ import annotations

import json
import re

from config import MAIN_SLOTS, SLOTS, FOOD_DB_SITE, MET_TABLE

def new_state() -> dict:
    return {
        "profile": None,
        "meals": [],
        "activities": [],
        "slot_status": {s: "ще не було" for s in MAIN_SLOTS},
        "next_id": 1,
    }

def _new_id(state) -> int:
    state["next_id"] += 1
    return state["next_id"] - 1

def set_profile(state, weight_kg: float, height_cm: float, age: int, sex: str) -> dict:
    if sex not in ("female", "male"):
        raise ValueError("sex must be 'female' or 'male'")
    if height_cm < 3:
        height_cm = height_cm * 100
    if not 100 <= height_cm <= 230:
        raise ValueError(f"Зріст {height_cm} см нереалістичний")
    if not 25 <= weight_kg <= 250:
        raise ValueError(f"Вага {weight_kg} кг нереалістична")
    if not 10 <= age <= 100:
        raise ValueError(f"Вік {age} нереалістичний")

    bmr =10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    state["profile"] = {
        "weight_kg": weight_kg, "height_cm": height_cm, "age": age, "sex": sex,
        "bmr": round(bmr),
        "base_kcal": round(bmr * 1.3),
    }

    return {"ok": True, **state["profile"],
            "protein_target_g": [round(weight_kg * 1.6), round(weight_kg * 2.2)]}

def _calc_item(item: dict) -> dict:
    keys = ("kcal", "protein", "fat", "carbs")

    src = item.get("per_100g") if (item.get("grams") and item.get("per_100g")) else item.get("total")

    if not src:
        raise ValueError(f"Продукт '{item.get('name')}': потрібні grams+per_100g або total")

    k = item["grams"] / 100 if src is item.get("per_100g") else 1
    totals = {key: round(float(src.get(key) or 0) * k, 1) for key in keys}

    bjv_known = all(src.get(key) is not None for key in ("protein", "fat", "carbs"))

    return {"name": item.get("name", "?"), "grams": item.get("grams"),
            "source": item.get("source", "оцінка"),
            "bjv_source": item.get("bjv_source", item.get("source", "оцінка")) if bjv_known else "невідомо",
            "bjv_known": bjv_known, **totals}


def log_meal(state, slot: str, items: list[dict] | None = None, status: str = "записано") -> dict:
    if slot not in SLOTS:
        raise ValueError(f"slot має бути одним із {SLOTS}")

    if status in ("пропущено", "ще не було"):
        if slot == "перекус":
            raise ValueError("Перекус не має статусу, його просто записують або ні")
        state["slot_status"][slot] = status
        return {"ok": True, "slot": slot, "status": status}

    if not items:
        raise ValueError("Щоб записати їжу, потрібен список items")

    calc = [_calc_item(i) for i in items]
    entry = {"id": _new_id(state), "slot": slot, "items": calc,
             **{k: round(sum(i[k] for i in calc), 1) for k in ("kcal", "protein", "fat", "carbs")}}
    state["meals"].append(entry)

    if slot in MAIN_SLOTS:
        state["slot_status"][slot] = "записано"
    res = {"ok": True, "entry_id": entry["id"], "slot": slot, "items": calc,
           "kcal": entry["kcal"], "protein": entry["protein"]}

    unknown = [i["name"] for i in calc if not i["bjv_known"]]
    if unknown:
        res["need_bjv"] = unknown

    return res

def _activity_kcal(state, activity_type, minutes, rpe, steps):
    weight = state["profile"]["weight_kg"] if state["profile"] else 55.0
    if activity_type == "кроки":
        if not steps:
            raise ValueError("Для 'кроки' потрібен параметр steps")
        return {"kcal": round(steps * 0.04 * weight / 70), "srpe": 0}

    if not minutes or not rpe:
        raise ValueError("Для тренування потрібні minutes і rpe (1-10)")

    low, mid, high = MET_TABLE.get(activity_type, MET_TABLE["інше"])
    met = low if rpe <= 4 else mid if rpe <= 7 else high

    return {"kcal": round((met - 1) * weight * minutes / 60), "srpe": round(minutes * rpe), "met": met}


def log_activity(state, activity_type: str, minutes: float | None = None,
                 rpe: float | None = None, steps: int | None = None) -> dict:
    calc = _activity_kcal(state, activity_type, minutes, rpe, steps)
    entry = {"id": _new_id(state), "type": activity_type,
             "minutes": minutes, "rpe": rpe, "steps": steps, **calc}
    state["activities"].append(entry)
    return {"ok": True, "entry_id": entry["id"], **entry}


def delete_entry(state, entry_id: int) -> dict:
    for key in ("meals", "activities"):
        for idx, e in enumerate(state[key]):
            if e["id"] == entry_id:
                state[key].pop(idx)
                if key == "meals" and e["slot"] in MAIN_SLOTS and \
                        not any(m["slot"] == e["slot"] for m in state["meals"]):
                    state["slot_status"][e["slot"]] = "ще не було"
                return {"ok": True, "deleted": e}
    raise ValueError(f"Запис з id={entry_id} не знайдено")


def day_summary(state) -> dict:
    eaten = {k: round(sum(m[k] for m in state["meals"]), 1) for k in ("kcal", "protein", "fat", "carbs")}
    activity_kcal = sum(a["kcal"] for a in state["activities"])
    missing = [s for s, st in state["slot_status"].items() if st == "ще не було"]
    res = {
        "eaten": eaten,
        "activity_kcal": activity_kcal,
        "slot_status": state["slot_status"],
        "missing_meals": missing,
        "day_complete": not missing,
        "entries": [{"id": m["id"], "slot": m["slot"], "kcal": m["kcal"],
                     "items": [i["name"] for i in m["items"]]} for m in state["meals"]]
                   + [{"id": a["id"], "activity": a["type"], "kcal": a["kcal"]} for a in state["activities"]],
    }
    if not state["profile"]:
        res["note"] = "Немає профілю — витрати не порахувати. Попроси вагу, зріст, вік."
        return res

    burned = state["profile"]["base_kcal"] + activity_kcal
    diff = eaten["kcal"] - burned
    res.update(burned_kcal=burned, diff_kcal=round(diff),
               protein_per_kg=round(eaten["protein"] / state["profile"]["weight_kg"], 2))
    if res["day_complete"]:
        res["verdict"] = ("в нормі" if abs(diff) <= 0.10 * burned
                          else "дефіцит" if diff < 0 else "профіцит")
    return res

def _ddg(query: str, n: int = 5, attempts: int = 3) -> tuple[list[dict], str | None]:
    import time
    from ddgs import DDGS

    error = None
    for i in range(attempts):
        try:
            results = DDGS().text(query, max_results=n)
            return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in results], None
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            time.sleep(1 + i)
    return [], error

def search_food_db(state, query: str, limit: int = 8) -> dict:
    import requests

    try:
        r = requests.get(
            f"https://www.{FOOD_DB_SITE}/autocomplete/foodstuff-activity-meal",
            params={"query": query, "format": "json"},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"source": FOOD_DB_SITE, "results": [], "found": False, "error": f"{type(e).__name__}: {e}"}

    results = []
    for item in data:
        if item.get("clazz") != "foodstuff":
            continue
        results.append({
            "name": item.get("title"),
            "brand": item.get("brandName"),
            "kcal_per_100": float(item["value"]) if item.get("value") else None,
            "unit": item.get("unit"),
            "url": f"https://www.{FOOD_DB_SITE}/stravy/{item.get('url')}",
        })
    results = results[:limit]
    return {"source": FOOD_DB_SITE, "results": results, "found": bool(results)}


def web_search(state, query: str) -> dict:
    results, error = _ddg(query)
    res = {"results": results, "found": bool(results)}
    if error:
        res["error"] = error
    return res


_DB_FIELDS = {"kcal": "energy", "protein": "protein", "fat": "fat", "carbs": "carbohydrate",
              "sugar": "sugar", "fiber": "fiber"}


def _parse_food_db(text: str) -> dict | None:
    per_100g = {}
    for key, field in _DB_FIELDS.items():
        m = re.search(r"\{\{data\.foodstuff\." + field + r"\}\}\s*([\d.,]+)", text)
        if m:
            per_100g[key] = float(m.group(1).replace(",", "."))
    return per_100g if "kcal" in per_100g else None


def read_page(state, url: str, max_chars: int = 3000) -> dict:
    import requests
    from bs4 import BeautifulSoup

    html = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (nutri-agent student project)"}).text
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()

    if FOOD_DB_SITE in url:
        per_100g = _parse_food_db(text)
        if per_100g:
            return {"url": url, "name": title.split(" - ")[0].strip(), "per_100g": per_100g}

    text = re.sub(r"\{\{.*?\}\}", "", text)
    pos = text.lower().find("ккал")
    start = max(0, pos - 500) if pos > 0 else 0
    return {"url": url, "text": text[start:start + max_chars]}
