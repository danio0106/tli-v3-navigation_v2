import sys

file_path = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'

with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'UIMysticMapItem_C' in line and line.strip().startswith('class '):
        for j in range(i, i+30):
            print(lines[j].strip())
        break
