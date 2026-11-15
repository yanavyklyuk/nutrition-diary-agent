import csv
import html
import os
import tempfile

import gradio as gr

from agent import run_agent
from config import MAIN_SLOTS
from tools import new_state, day_summary

GRADIO_MAJOR = int(gr.__version__.split(".")[0])

HEADER = """
<div class="hdr">
  <div class="hdr-title">Щоденник харчування та активності</div>
  <div class="hdr-sub">Напишіть звичайними словами, що ви з'їли і як рухалися. Агент знайде калорійність у базі
  <a href="https://www.tablycjakalorijnosti.com.ua" target="_blank">tablycjakalorijnosti.com.ua</a>, переведе порції в грами
  і наприкінці дня підсумує: дефіцит, профіцит чи в нормі.</div>
  <div class="hdr-note">Дані зберігаються лише в цій вкладці. API-ключ нікуди не записується. Це не медична порада.</div>
</div>
"""

CSS = """
.gradio-container {max-width: 1280px !important; margin: 0 auto;}
.hdr {padding: 8px 4px 4px;}
.hdr-title {font-size: 26px; font-weight: 600; margin-bottom: 6px;}
.hdr-sub {font-size: 15px; opacity: .85; line-height: 1.5;}
.hdr-note {font-size: 13px; opacity: .6; margin-top: 6px;}
.dash {display: flex; flex-direction: column; gap: 14px;}
.totals {display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px;}
.metric {background: var(--block-background-fill); border: 1px solid var(--border-color-primary);
         border-radius: 14px; padding: 14px 16px;}
.metric .lbl {font-size: 13px; opacity: .65;}
.metric .val {font-size: 21px; font-weight: 600; margin-top: 2px; white-space: nowrap;}
.metric .sub {font-size: 12px; opacity: .6; margin-top: 2px;}
.metric.verdict-deficit .val {color: #d97706;}
.metric.verdict-surplus .val {color: #dc2626;}
.metric.verdict-ok .val {color: #16a34a;}
.sec-title {font-size: 15px; font-weight: 600; margin: 4px 2px 0;}
.meals {display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px;}
.card {background: var(--block-background-fill); border: 1px solid var(--border-color-primary);
       border-radius: 14px; padding: 12px 16px;}
.card-h {display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;}
.card-name {font-weight: 600; font-size: 15px;}
.card-kcal {font-weight: 600; font-size: 15px;}
.badge {font-size: 11px; padding: 2px 8px; border-radius: 999px; margin-left: 8px; font-weight: 500;}
.b-done {background: rgba(22,163,74,.12); color: #16a34a;}
.b-skip {background: rgba(107,114,128,.15); color: #6b7280;}
.b-wait {background: rgba(217,119,6,.12); color: #d97706;}
.row {display: flex; justify-content: space-between; gap: 10px; font-size: 13px; padding: 4px 0;
      border-top: 1px solid var(--border-color-primary);}
.row:first-of-type {border-top: none;}
.row .src {opacity: .5; font-size: 11px; margin-left: 6px;}
.row > span:last-child {white-space: nowrap;}
.empty {font-size: 13px; opacity: .5; padding: 4px 0;}
.hint {font-size: 13px; opacity: .7; padding: 8px 12px; border-radius: 10px; background: rgba(217,119,6,.08);}
#chat {font-size: 13px;}
"""

STATUS_BADGE = {"записано": ("b-done", "записано"), "пропущено": ("b-skip", "пропущено"),
                "ще не було": ("b-wait", "ще не було")}
VERDICT_CLASS = {"дефіцит": "verdict-deficit", "профіцит": "verdict-surplus", "в нормі": "verdict-ok"}


def _e(x) -> str:
    return html.escape(str(x))


def _metric(label, value, sub="", cls=""):
    return f'<div class="metric {cls}"><div class="lbl">{label}</div><div class="val">{value}</div><div class="sub">{sub}</div></div>'


def _meal_card(title, entries, status=None):
    items = [i for e in entries for i in e["items"]]
    kcal = sum(i["kcal"] for i in items)
    badge = ""
    if status:
        cls, text = STATUS_BADGE[status]
        badge = f'<span class="badge {cls}">{text}</span>'
    rows = ""
    for i in items:
        grams = f" · {i['grams']:.0f} г" if i.get("grams") else ""
        rows += (f'<div class="row"><span>{_e(i["name"])}{grams}<span class="src">{_e(i["source"])}</span></span>'
                 f'<span>{i["kcal"]:.0f} ккал</span></div>')
    rows = rows or '<div class="empty">—</div>'
    return (f'<div class="card"><div class="card-h"><span class="card-name">{title}{badge}</span>'
            f'<span class="card-kcal">{kcal:.0f} ккал</span></div>{rows}</div>')


def dashboard_html(state: dict) -> str:
    s = day_summary(state)
    e = s["eaten"]
    macros = f"Б {e['protein']:.0f} · Ж {e['fat']:.0f} · В {e['carbs']:.0f} г"

    if "burned_kcal" in s:
        burned = _metric("Витрачено", f"{s['burned_kcal']} ккал",
                         f"база {state['profile']['base_kcal']} + активність {s['activity_kcal']}")
        diff = _metric("Різниця", f"{s['diff_kcal']:+d} ккал", "спожито − витрачено")
    else:
        burned = _metric("Витрачено", "—", "потрібен профіль")
        diff = _metric("Різниця", "—", "потрібен профіль")
    verdict = s.get("verdict")
    final = _metric("Підсумок дня", verdict or "день триває",
                    "" if verdict else "закрийте всі прийоми їжі", VERDICT_CLASS.get(verdict, ""))

    eaten = _metric("Спожито", f"{e['kcal']:.0f} ккал", macros)
    parts = [f'<div class="totals">{eaten}{burned}{diff}{final}</div>']
    if not state.get("profile"):
        parts.append('<div class="hint">Напишіть у чат вагу, зріст і вік, щоб порахувати витрати та баланс.</div>')

    parts.append('<div class="sec-title">Прийоми їжі</div><div class="meals">')
    for slot in MAIN_SLOTS:
        entries = [m for m in state["meals"] if m["slot"] == slot]
        parts.append(_meal_card(slot.capitalize(), entries, s["slot_status"][slot]))
    parts.append(_meal_card("Перекуси", [m for m in state["meals"] if m["slot"] == "перекус"]))
    parts.append("</div>")

    acts = state["activities"]
    rows = ""
    for a in acts:
        detail = f"{a['minutes']:.0f} хв, RPE {a['rpe']:.0f}" if a.get("minutes") else f"{a['steps']} кроків"
        rows += f'<div class="row"><span>{_e(a["type"])} · {detail}</span><span>{a["kcal"]} ккал</span></div>'
    rows = rows or '<div class="empty">—</div>'
    total_act = sum(a["kcal"] for a in acts)
    parts.append(f'<div class="sec-title">Активність</div><div class="card"><div class="card-h">'
                 f'<span class="card-name">Тренування та кроки</span><span class="card-kcal">{total_act} ккал</span></div>'
                 f'{rows}</div>')
    return f'<div class="dash">{"".join(parts)}</div>'


def export_csv(state: dict) -> str:
    path = os.path.join(tempfile.gettempdir(), "diary.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "тип", "прийом / активність", "продукт", "грами", "ккал", "білки", "жири", "вуглеводи", "джерело"])
        for m in state["meals"]:
            for i in m["items"]:
                w.writerow([m["id"], "їжа", m["slot"], i["name"], i["grams"], i["kcal"],
                            i["protein"], i["fat"], i["carbs"], i["source"]])
        for a in state["activities"]:
            w.writerow([a["id"], "активність", a["type"], "", "", a["kcal"], "", "", "",
                        f"{a['minutes']} хв, RPE {a['rpe']}" if a["minutes"] else f"{a['steps']} кроків"])
    return path


def respond(user_text, chat, llm_messages, state, api_key):
    user_text = (user_text or "").strip()
    if not user_text:
        return "", chat, llm_messages, state, dashboard_html(state)

    chat = chat + [{"role": "user", "content": user_text}]
    api_key = (api_key or "").strip() or os.getenv("GROQ_API_KEY", "")
    if not api_key:
        chat.append({"role": "assistant", "content": "Вставте, будь ласка, Groq API-ключ у поле вище."})
        return "", chat, llm_messages, state, dashboard_html(state)

    llm_messages.append({"role": "user", "content": user_text})
    try:
        answer, tool_log = run_agent(llm_messages, state, api_key)
    except Exception as e:
        llm_messages.pop()
        chat.append({"role": "assistant", "content": f"Помилка API: {e}"})
        return "", chat, llm_messages, state, dashboard_html(state)

    for name, args, result in tool_log:     # згорнуті блоки: клік — і видно запит та відповідь інструмента
        chat.append({"role": "assistant", "metadata": {"title": f"🔧 {name}", "status": "done"},
                     "content": f"**Запит:** `{args}`\n\n**Відповідь:** `{result[:800]}`"})
    chat.append({"role": "assistant", "content": answer})
    return "", chat, llm_messages, state, dashboard_html(state)


def new_day():
    state = new_state()
    return [], [], state, dashboard_html(state), gr.update(value=None, visible=False)


THEME = gr.themes.Soft(primary_hue="rose", neutral_hue="slate", radius_size="lg")
blocks_kw = {"title": "Щоденник харчування"}
launch_kw = {}
if GRADIO_MAJOR < 6:
    blocks_kw.update(theme=THEME, css=CSS)
else:
    launch_kw.update(theme=THEME, css=CSS)
chat_kw = {"type": "messages"} if GRADIO_MAJOR < 6 else {}

with gr.Blocks(**blocks_kw) as demo:
    llm_messages = gr.State([])
    state = gr.State(new_state)

    gr.HTML(HEADER)
    with gr.Row(equal_height=False):
        with gr.Column(scale=3):
            dashboard = gr.HTML(dashboard_html(new_state()))
            with gr.Row():
                export_btn = gr.Button("Завантажити щоденник (CSV)", size="sm")
                new_day_btn = gr.Button("Почати новий день", size="sm")
            export_file = gr.File(label="Файл щоденника", interactive=False, visible=False)
        with gr.Column(scale=2, min_width=320):
            api_key = gr.Textbox(label="Groq API-ключ", type="password", placeholder="gsk_...", lines=1)
            chat = gr.Chatbot(elem_id="chat", height=330, show_label=False, **chat_kw)
            with gr.Row():
                box = gr.Textbox(placeholder="Що ви з'їли або як тренувалися?", show_label=False,
                                 scale=5, container=False, autofocus=True)
                send = gr.Button("➤", variant="primary", scale=1, min_width=50)
            gr.Examples(["мені 20 років, 55 кг, 167 см", "на сніданок банан і кава з молоком",
                         "тренування 90 хв, важкість 7", "8000 кроків", "обід пропущено", "підсумок дня"],
                        inputs=box, label="Приклади")

    inputs = [box, chat, llm_messages, state, api_key]
    outputs = [box, chat, llm_messages, state, dashboard]
    box.submit(respond, inputs, outputs)
    send.click(respond, inputs, outputs)
    export_btn.click(lambda st: gr.update(value=export_csv(st), visible=True), state, export_file)
    new_day_btn.click(new_day, None, [chat, llm_messages, state, dashboard, export_file])

if __name__ == "__main__":
    # Render (і подібні хостинги) передає порт у змінній PORT і вимагає слухати всі адреси (0.0.0.0)
    demo.launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", "7860")), **launch_kw)
