def fix_surrogates(s):
    try:
        # This combines surrogate pairs into a single character
        return s.encode('utf-16', 'surrogatepass').decode('utf-16')
    except Exception:
        return s

test_str = "Emoji surrogate: \ud83d\ude00"
print(f"Length before: {len(test_str)}")
fixed = fix_surrogates(test_str)
print(f"Length after:  {len(fixed)}")
print(f"Fixed string:  {fixed}")
print(f"Final hex:     {fixed.encode('utf-8').hex()}")
