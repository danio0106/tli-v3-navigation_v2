import re

def search_dump():
    filepath = r'moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'
    
    print("=== MysteryCardItem_C Instances ===")
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if 'MysteryCardItem_C' in line and '[ Index:' in line and 'WidgetBlueprintGeneratedClass' not in line:
                print(line.strip())

    print("\n=== UIMysticMap / Mystery Map Classes and Arrays ===")
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        
    in_class = False
    current_class = ""
    for line in lines:
        # Detect class declaration (both UE classes and Blueprint generated classes)
        class_match = re.search(r'(Class|ScriptStruct|WidgetBlueprintGeneratedClass)\s+([^ ]*Myst[^ ]*)', line, re.IGNORECASE)
        if class_match:
            in_class = True
            current_class = line.strip()
            print(f"\nFound Class: {current_class}")
            continue
            
        if in_class:
            if line.startswith('Class ') or line.startswith('ScriptStruct ') or line.startswith('WidgetBlueprintGeneratedClass '):
                in_class = False
                # Reprocess the line that broke us out
                class_match = re.search(r'(Class|ScriptStruct|WidgetBlueprintGeneratedClass)\s+([^ ]*Myst[^ ]*)', line, re.IGNORECASE)
                if class_match:
                    in_class = True
                    current_class = line.strip()
                    print(f"\nFound Class: {current_class}")
                continue
                
            # Look for ArrayProperty or MapProperty in the class properties
            if 'ArrayProperty' in line or 'MapProperty' in line or 'MysteryCardItem_C' in line:
                print("  " + line.strip())

if __name__ == "__main__":
    search_dump()
