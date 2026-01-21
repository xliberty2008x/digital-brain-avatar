import re

def normalize_unicode_safe(obj):
    if isinstance(obj, str):
        # We need to handle \uXXXX. Note that in Python strings, 
        # a literal backslash is represented as \\.
        # So we look for \\u followed by 4 hex digits.
        return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), obj)
    return obj

test_str = "Привіт \\u0430 \\ud83d\\ude00"
print(f"Original: {test_str}")
fixed = normalize_unicode_safe(test_str)
print(f"Fixed: {fixed}")

# Try the old way to see if it mangles Cyrillic
obj = "Привіт \\u0430"
try:
    # This is what I was doing:
    mangled = obj.encode('utf-8').decode('unicode_escape')
    print(f"Mangled old way: {mangled} (Length: {len(mangled)})")
    for char in mangled:
        print(f"  {char}: U+{ord(char):04X}")
except Exception as e:
    print(f"Old way error: {e}")
