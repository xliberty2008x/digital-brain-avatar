import re

def normalize_unicode_safe(obj):
    if isinstance(obj, str):
        # Decode only \uXXXX sequences that are not preceded by another backslash
        # The tool output usually has literal \uXXXX
        return re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), obj)
    return obj

test_str = "Привіт \\u0430 \\ud83d\\ude00"
print(f"Original: {test_str}")
fixed = normalize_unicode_safe(test_str)
print(f"Fixed: {fixed}")

# Try the old way to see if it breaks
obj = "Привіт \\u0430"
try:
    mangled = obj.encode('utf-8').decode('unicode_escape')
    print(f"Mangled old way: {mangled}")
except Exception as e:
    print(f"Old way error: {e}")
