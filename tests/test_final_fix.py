import re

def normalize_unicode_final(obj):
    if isinstance(obj, dict):
        return {k: normalize_unicode_final(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_unicode_final(item) for item in obj]
    if isinstance(obj, str):
        if "\\u" in obj:
            try:
                # 1. Decode \uXXXX sequences only
                def repl(m):
                    # encode('utf-8') converts the literal backslashes to bytes
                    # decode('unicode_escape') converts b'\\u0430' to 'а'
                    return m.group(0).encode('utf-8').decode('unicode_escape')
                
                res = re.sub(r'(\\u[0-9a-fA-F]{4})+', repl, obj)
                
                # 2. Combine surrogate pairs if any (e.g. emojis)
                return res.encode('utf-16', 'surrogatepass').decode('utf-16')
            except Exception as e:
                # print(f"Error: {e}")
                return obj
    return obj

test_cases = [
    "Привіт \\u0430",
    "Emoji: \\ud83d\\ude00",
    "Mixed: Привіт \\u0430 \\ud83d\\ude00 and more",
    "Nested quotes: {\"text\": \"Привіт \\u0430\"}"
]

for tc in test_cases:
    print(f"Original: {tc}")
    fixed = normalize_unicode_final(tc)
    print(f"Fixed:    {fixed}")
    # Verify encoding
    fixed.encode('utf-8')
    print("UTF-8:    OK")
    print("-" * 20)
