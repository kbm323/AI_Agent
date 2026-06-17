import sys
sys.path.insert(0, '/home/kbm/F:ai-projects/AI_Agent')

from src.glm_output_parser import (
    parse_glm_output,
    _detect_format,
    _KV_LINE_RE,
    GlmParseErrorType,
)

# Test a simple delimited case
raw = "verdict: pass\noverall_score: 0.95"
print(f"Raw: {raw!r}")
print(f"Detected format: {_detect_format(raw)}")

# Check regex
for line in raw.splitlines():
    m = _KV_LINE_RE.match(line)
    print(f"  Line: {line!r} -> match: {m is not None}")
    if m:
        print(f"    key: {m.group(1)!r}, value: {m.group(2)!r}")

result = parse_glm_output(raw)
print(f"\nSuccess: {result.success}")
print(f"Passed: {result.passed}")
print(f"Confidence: {result.confidence}")
print(f"Format detected: {result.format_detected}")
if result.error:
    print(f"Error type: {result.error.error_type}")
    print(f"Error msg: {result.error.message}")

# Also test with explicit hint
result2 = parse_glm_output(raw, format_hint='delimited')
print(f"\nWith format_hint='delimited':")
print(f"Success: {result2.success}")
print(f"Passed: {result2.passed}")
print(f"Confidence: {result2.confidence}")
print(f"Format detected: {result2.format_detected}")
if result2.error:
    print(f"Error type: {result2.error.error_type}")
    print(f"Error msg: {result2.error.message}")
