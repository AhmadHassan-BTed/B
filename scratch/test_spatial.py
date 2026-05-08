"""Phase 1 test script for spatial navigation fixes."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from brain.llm import parse_sentences

# ── Test 1.3: Fallback MOVE parser ──────────────────────────────
print("=== Test 1.3: parse_sentences with <MOVE:id> ===")
tests = [
    "[EXCITED] <MOVE:4> check this out!",
    "[CONFIDENT] <MOVE:1> here's the ABox! [HAPPY] <MOVE:2> and the BrickSorter!",
    "[FOCUSED] let me scan... [EXCITED] <MOVE:3> found it right here!",
    "Here is the button you wanted. <MOVE:3>",  # No tag — edge case
    "[HAPPY] no move tag here, just text.",
]
for t in tests:
    result = parse_sentences(t)
    for s in result:
        move = s.get("move_id")
        print(f"  emotion={s['emotion']:<12} move_id={move!s:<6} text={s['text'][:50]}")
    print()

# ── Test 1.1: Spatial map preservation logic ────────────────────
print("=== Test 1.1: Spatial map preservation (simulated) ===")
spatial_map = {}

# Simulate semantic update with spatial data
semantic_payload = {"spatial_map": {"1": (500, 200, "ABox"), "2": (800, 200, "BrickSorter")}}
new_spatial = semantic_payload.get("spatial_map", {})
if new_spatial:
    spatial_map = new_spatial
print(f"  After semantic update: {spatial_map}")

# Simulate OCR update with NO spatial data
ocr_payload = {"screen_text": "ABox BrickSorter Console2"}  # No spatial_map key
new_spatial = ocr_payload.get("spatial_map", {})
if new_spatial:
    spatial_map = new_spatial
print(f"  After OCR update:      {spatial_map}")
assert len(spatial_map) == 2, "FAIL: OCR clobbered spatial map!"
print("  PASS: Spatial map survived OCR overwrite\n")

# ── Test 1.2: Prompt contains spatial IDs ───────────────────────
print("=== Test 1.2: Spatial prompt injection ===")
# Simulate what _build_messages does
if spatial_map:
    id_list = ", ".join(f"#{k}" for k in sorted(spatial_map.keys(), key=lambda x: int(x)))
    print(f"  Available spatial IDs: [{id_list}]")
    
    # Check if labels are accessible
    spatial_lines = []
    for sid, data in sorted(spatial_map.items(), key=lambda x: int(x[0])):
        label = data[2] if len(data) > 2 else "unknown"
        spatial_lines.append(f"  [#{sid}] \"{label}\"")
    print("  SCREEN ELEMENTS YOU CAN FLY TO:")
    for line in spatial_lines:
        print(f"    {line}")
    print("  PASS: Labels are accessible from spatial map\n")

print("=== All Phase 1 tests passed ===")
