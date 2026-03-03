import os

file_path = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'

target_keywords = ['slot', 'rendertransform', 'index', 'mapid', 'position', 'id', 'canvas']

def search_props(class_name):
    print(f"=== Properties for {class_name} ===")
    found_any = False
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if f":{class_name}:" in line or f".{class_name}:" in line or f".{class_name}" in line and class_name in line.split(':')[0]:
                if '[Offset:' in line:
                    lower_line = line.lower()
                    if any(kw in lower_line for kw in target_keywords):
                        print(line.strip())
                        found_any = True
    if not found_any:
        print("  (No relevant properties found)")
    print()

search_props('UIMysticMapItem_C')
search_props('UUI_BasicBase_C')
search_props('UUI_BasicBase')
search_props('UserWidget')
search_props('Widget')
search_props('CanvasPanelSlot')

