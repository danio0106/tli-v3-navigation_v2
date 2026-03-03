import os
import sys

file_path = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'

def dump_class_props(class_name):
    print(f"\n--- {class_name} ---")
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        found = False
        for line in f:
            if f":{class_name}:" in line or (f".{class_name}:" in line and class_name in line.split(':')[0]) or (f"/{class_name}:" in line and class_name in line.split(':')[0]):
                if '[Offset:' in line:
                    print(line.strip())
                    found = True
        if not found:
            print("No properties found.")

dump_class_props("UIMysticMapItem_C")
dump_class_props("UUI_BasicBase_C")
dump_class_props("UserWidget")
dump_class_props("Widget")
