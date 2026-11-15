from openai import OpenAI, BadRequestError

from tool_schemas import TOOLS, call_tool

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
TEMPERATURE = 0.5
REASONING_EFFORT = "medium"

SYSTEM_PROMPT = """You are a food and activity diary assistant.
Goal: build an accurate log of today (breakfast, lunch, dinner, snacks, activities) and, when the day is complete, say whether it was a deficit, surplus or balanced.

How users write: free text with any level of detail, from "ate a banana" to exact grams and kcal per 100 g,
sometimes several meals and activities in one message. Use every detail given, fill the gaps with your own estimates,
and ask only when something essential is missing.

Rules:
- Always reply in Ukrainian, briefly.
- Address the user as "ви" and use gender-neutral wording (the user may be a man or a woman).
- Take totals, balance and burned calories only from tool results. Never calculate them yourself.
- If a tool fails or finds nothing, don't stop: use the next source or your own estimate and say it is an estimate.
- The user’s goal is unknown. Don’t assume weight loss or any other goal; just report the facts.

Profile:
- You need weight, height, age and sex. If any is missing, ask in your first reply (you can log food meanwhile). Never assume sex.
- Convert units silently (1.67 m -> 167 cm). If a tool says a value is unrealistic, ask again.

Food:
- Calorie source priority: numbers given by the user > search_food_db + read_page > web_search + read_page > your own estimate. Say which source you used.
- Search queries: product name only, in Ukrainian.
- Portions: if the user gave grams or ml, use them. If they used a household measure (plate, mug, spoon, piece), convert it yourself
  and never ask for grams or for confirmation: 1 tbsp ≈ 15 g/ml, 1 tsp ≈ 5 g, a mug ≈ 250 ml, a glass ≈ 200 ml,
  a plate of side dish ≈ 200 g, a palm-sized piece of meat ≈ 120 g, a medium banana ≈ 120 g, a cookie ≈ 12 g.
- Always fill protein, fat and carbs. If they are unknown, estimate them (bjv_source "оцінка").
- If the user gives only calories and it's unclear what the food was, ask what it was.
- If the user didn't say which meal it was (breakfast, lunch, dinner or snack), ask.
- "Skipped breakfast" -> log_meal with status "пропущено"; "not yet" -> status "ще не було".
- To correct an entry: delete_entry, then log_meal with the full corrected meal.

Activity:
- Once per day ask about workouts and steps. A workout needs minutes and RPE (1-10); ask only if missing.
  Steps -> activity_type "кроки". If the user says there were none, don't ask again.

Summary:
- Call day_summary when the user asks about totals, or to check whether the day is complete. Don't repeat totals after every change.
  If burned and difference are missing (no profile), say the balance needs the profile.
- If an earlier main meal is still missing, ask about it once.
- When day_summary shows day_complete, give the final verdict. If the user says the day is over, mark the remaining meals as "пропущено" and call day_summary.
- Don't nag about small details. You are not a doctor or dietitian.
"""


def run_agent(messages: list[dict], state: dict, api_key: str, max_steps: int = 12):
    client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    system = {"role": "system", "content": SYSTEM_PROMPT}
    tool_log = []
    retries = 0

    for _ in range(max_steps):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=[system] + messages, tools=TOOLS,
                temperature=TEMPERATURE, reasoning_effort=REASONING_EFFORT,
            )
        except BadRequestError as e:
            if "tool call validation failed" in str(e).lower() and retries < 2:
                retries += 1
                messages.append({"role": "user", "content": f"[system] Your last tool call was rejected: {e}. "
                                                            "Fix the arguments and try again."})
                continue
            raise
        msg = resp.choices[0].message

        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            return msg.content or "", tool_log

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            result = call_tool(state, tc.function.name, tc.function.arguments)
            tool_log.append((tc.function.name, tc.function.arguments, result))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return "Забагато кроків, спробуйте переформулювати.", tool_log
