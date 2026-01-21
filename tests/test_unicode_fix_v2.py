import re

def normalize_unicode_v3(obj):
    if not isinstance(obj, str):
        return obj
    
    # Matching sequences of \uXXXX
    pattern = re.compile(r'(\\u[0-9a-fA-F]{4})+')
    
    def decode_match(match):
        try:
            # We take the literal string part like '\u0430' and decode it
            return match.group(0).encode('ascii').decode('unicode_escape')
        except Exception:
            return match.group(0)
            
    return pattern.sub(decode_match, obj)

test_cases = [
    "Привіт \\u0430",
    "Emoji: \\ud83d\\ude00",
    "Mixed: Привіт \\u0430 \\ud83d\\ude00 and more",
    "Already clean: Привіт а"
]

for tc in test_cases:
    print(f"Original: {tc}")
    fixed = normalize_unicode_v3(tc)
    print(f"Fixed:    {fixed}")
    # Verify we can encode it back to utf-8 (this was failing before)
    try:
        fixed.encode('utf-8')
        print("Encode:   OK")
    except Exception as e:
        print(f"Encode:   FAILED ({e})")
    print("-" * 20)
