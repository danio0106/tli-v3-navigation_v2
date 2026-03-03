import sys
import re

def main():
    dump_file = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'
    
    target_classes = [
        "Mystery_C",
        "MysteryArea_C",
        "MysteryTalenEntrance_C",
        "MysteryPlayEndItem_C"
    ]
    
    # We want to find property definitions that are either ArrayProperty or MapProperty 
    # and belong to one of our target classes
    
    with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if 'ArrayProperty' in line or 'MapProperty' in line:
                for cls in target_classes:
                    # Match something like:
                    # /Game/BluePrint/UI/Mystery/MysteryArea.MysteryArea_C:SomeArray
                    # or UMysteryArea_C
                    # Checking if the class name followed by a colon or exact match is in the string.
                    if f".{cls}:" in line or f"/{cls}:" in line:
                        print(line.strip())
                        break

if __name__ == '__main__':
    main()
