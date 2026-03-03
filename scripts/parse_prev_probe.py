"""Parse previous card_probe JSON for comparison."""
import json

with open(r"data/card_probe_20260302_154441.json", "r") as f:
    data = json.load(f)

print(f"Previous probe: {data['widget_count']} widgets")
for i, w in enumerate(data["widgets"]):
    cs = w.get("card_item_subs", {})
    eb = cs.get("EmptyBg", {})
    ei = cs.get("EmptyIcon", {})
    cm = cs.get("CardIconMask", {})
    bi = cs.get("BuffIcon", {})
    es = cs.get("EffectSwitcher", {})
    has_card = eb.get("visibility") == 1 and ei.get("visibility") == 1
    icon = cm.get("icon_tex_name", "?")
    buff = bi.get("brush_res_fname", "?")
    eff = es.get("active_index", "?")
    print(f"W{i}: addr={w['address']} EmptyBg={eb.get('visibility','?')} EmptyIcon={ei.get('visibility','?')} card={has_card} icon={icon} buff={buff} effSwitch={eff}")
