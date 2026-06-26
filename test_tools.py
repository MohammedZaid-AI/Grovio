print("Starting...")

try:
    from ai.tools.tool_registry import TOOLS
    print("Imported tool_registry successfully")
except Exception as e:
    print("IMPORT ERROR")
    print(type(e).__name__)
    print(e)
    exit()

print("TOOLS =", TOOLS)
print("COUNT =", len(TOOLS))

for t in TOOLS:
    print(t.name)